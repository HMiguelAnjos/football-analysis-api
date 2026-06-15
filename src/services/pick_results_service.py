"""
PickResultsService — liquidação das recomendações + relatórios de performance.

settle_finished: varre recomendações pending, busca o resultado final dos jogos
que terminaram e marca hit/miss/push/void, gravando um snapshot imutável em
football_pick_results.

performance_breakdown / summarize: agregações pro dashboard (accuracy, ROI por
mercado, etc.) a partir do ledger.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import FootballPickResult, FootballRecommendation
from src.recommendation.settlement import MatchResult, profit_units, settle
from src.services import recommendation_service as rec_svc

logger = logging.getLogger(__name__)


def _result_from_match(match) -> Optional[MatchResult]:
    """Constrói MatchResult de um Match de domínio finalizado."""
    if match.home_goals is None or match.away_goals is None:
        return None
    return MatchResult(home_goals=int(match.home_goals), away_goals=int(match.away_goals))


def settle_finished(db: Session, data_service) -> dict:
    """Liquida recomendações pending cujos jogos terminaram.

    `data_service` precisa expor match_domain(match_id) e match_statistics.
    Best-effort por jogo: erro num não impede os outros.
    """
    grouped = rec_svc.pending_by_match(db)
    settled = errors = 0

    for match_id, recs in grouped.items():
        try:
            match = data_service.match_domain(match_id)
            if match is None or match.status != "finished":
                continue
            result = _result_from_match(match)
            if result is None:
                continue
            # Enriquecimento opcional com escanteios/cartões das stats.
            try:
                stats = data_service._football().get_match_statistics(match_id)
                if stats is not None:
                    result.corners = (stats.home.get("corner_kicks") or stats.home.get("corners", 0)) + \
                                     (stats.away.get("corner_kicks") or stats.away.get("corners", 0))
                    result.cards = (stats.home.get("yellow_cards", 0) + stats.home.get("red_cards", 0)
                                    + stats.away.get("yellow_cards", 0) + stats.away.get("red_cards", 0))
            except Exception:  # noqa: BLE001
                pass

            for rec in recs:
                status = settle(rec.market, rec.selection, rec.line, result)
                rec.status = status
                rec.settled_at = datetime.now(timezone.utc)
                rec.actual_result = f"{result.home_goals}-{result.away_goals}"
                _write_ledger(db, rec, status)
                settled += 1
            db.commit()
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            errors += 1
            logger.warning("settle: jogo %s falhou (%s)", match_id, exc)

    return {"settled": settled, "errors": errors}


def _write_ledger(db: Session, rec: FootballRecommendation, status: str) -> None:
    """Grava (idempotente) o snapshot imutável no ledger."""
    existing = db.scalar(
        select(FootballPickResult).where(
            FootballPickResult.recommendation_id == rec.id
        )
    )
    if existing is not None:
        return
    db.add(FootballPickResult(
        recommendation_id=rec.id,
        context=rec.context,
        stage=rec.stage,
        match_id=rec.match_id,
        match=f"{rec.home_team} x {rec.away_team}".strip(" x"),
        league=rec.league,
        source=rec.source,
        analyst_name=rec.created_by_name or None,
        market=rec.market,
        selection=rec.selection,
        line=rec.line,
        odd=rec.odd,
        edge=rec.edge,
        confidence_score=rec.confidence_score,
        status=status,
        actual_result=rec.actual_result,
        profit_units=profit_units(status, rec.odd),
        was_shown_to_user=rec.was_shown_to_user,
    ))


def _accuracy(won: int, lost: int) -> float:
    denom = won + lost
    return round(won / denom, 4) if denom else 0.0


def performance_breakdown(db: Session, *, only_shown: bool = False) -> dict:
    """Agregações de performance a partir do ledger imutável."""
    q = select(FootballPickResult)
    if only_shown:
        q = q.where(FootballPickResult.was_shown_to_user.is_(True))
    rows = list(db.scalars(q).all())

    def _empty():
        return {"won": 0, "lost": 0, "push": 0, "void": 0, "profit_units": 0.0}

    totals = _empty()
    by_market: dict[str, dict] = {}

    for r in rows:
        bucket = by_market.setdefault(r.market, _empty())
        for agg in (totals, bucket):
            if r.status == "hit":
                agg["won"] += 1
            elif r.status == "miss":
                agg["lost"] += 1
            elif r.status == "push":
                agg["push"] += 1
            else:
                agg["void"] += 1
            agg["profit_units"] += r.profit_units or 0.0

    def _finalize(agg: dict) -> dict:
        staked = agg["won"] + agg["lost"] + agg["push"]
        return {
            **agg,
            "profit_units": round(agg["profit_units"], 3),
            "accuracy": _accuracy(agg["won"], agg["lost"]),
            "roi": round(agg["profit_units"] / staked, 4) if staked else 0.0,
            "total": staked + agg["void"],
        }

    return {
        "totals": _finalize(totals),
        "by_market": {m: _finalize(a) for m, a in by_market.items()},
    }


def summarize(db: Session) -> dict:
    """Resumo executivo simples (contagens + accuracy + ROI global)."""
    return performance_breakdown(db)["totals"]
