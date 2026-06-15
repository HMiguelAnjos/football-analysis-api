"""
Mappers dos modelos do banco → schemas do front (recomendações, live-picks,
pick-results, performance). Centraliza o de↔para de status/confiança.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import FootballPickResult, FootballRecommendation
from src.schemas.football_schemas import (
    LivePickOut,
    PerfBreakdownItem,
    PerformanceSummary,
    PerfTotals,
    PickResultOut,
    RecommendationOut,
)

# Status do settlement (interno) → status exibido na recomendação.
_REC_STATUS = {"pending": "pending", "hit": "won", "miss": "lost",
               "push": "push", "void": "void"}
# → resultado do pick (win/loss/push/pending).
_RESULT_STATUS = {"pending": "pending", "hit": "win", "miss": "loss",
                  "push": "push", "void": "push"}


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def confidence_label(score: Optional[float]) -> Optional[str]:
    if score is None:
        return None
    if score >= 70:
        return "high"
    if score >= 45:
        return "medium"
    return "low"


def confidence_score(label: Optional[str]) -> Optional[float]:
    return {"low": 30.0, "medium": 55.0, "high": 80.0}.get((label or "").lower())


def _match_label(rec: FootballRecommendation) -> str:
    if rec.home_team and rec.away_team:
        return f"{rec.home_team} x {rec.away_team}"
    return rec.home_team or rec.away_team or ""


def rec_to_out(rec: FootballRecommendation) -> RecommendationOut:
    edge = rec.edge
    # FE interpreta edge como model_prob − implied_prob; usa quando disponível.
    if rec.model_probability is not None and rec.implied_probability is not None:
        edge = round(rec.model_probability - rec.implied_probability, 4)
    return RecommendationOut(
        id=rec.id, match=_match_label(rec), match_id=rec.match_id,
        league=rec.league or None, market=rec.market, selection=rec.selection,
        line=rec.line, odd=rec.odd or None, fair_odd=rec.fair_odd,
        model_prob=rec.model_probability, implied_prob=rec.implied_probability,
        edge=edge, confidence=confidence_label(rec.confidence_score),
        status=_REC_STATUS.get(rec.status, rec.status),
        reason=rec.recommendation_reason or None, bookmaker=rec.bookmaker,
        created_by_name=rec.created_by_name or None,
        created_at=_iso(rec.generated_at) or "",
        context=rec.context, stage=rec.stage, group=rec.group,
    )


def candidate_to_out(c) -> RecommendationOut:
    """Candidato do engine (não persistido) → schema do front."""
    edge = c.edge
    if c.model_probability is not None and c.implied_probability is not None:
        edge = round(c.model_probability - c.implied_probability, 4)
    return RecommendationOut(
        id=0, match=f"{c.home_team} x {c.away_team}", match_id=c.match_id,
        league=c.league or None, market=c.market, selection=c.selection,
        line=c.line, odd=c.odd, fair_odd=c.fair_odd,
        model_prob=c.model_probability, implied_prob=c.implied_probability,
        edge=edge, confidence=confidence_label(c.confidence_score),
        status="pending", reason=c.recommendation_reason or None,
        bookmaker=c.bookmaker, created_at="",
        stage=c.stage, group=c.group,
    )


def prop_to_out(pick, match) -> RecommendationOut:
    """PropPick (projeção de player prop) → schema do front."""
    return RecommendationOut(
        id=0, match=f"{match.home_team.name} x {match.away_team.name}",
        match_id=match.id, league=match.league_name or None, market=pick.market,
        selection=pick.selection, line=pick.line, odd=None, fair_odd=pick.fair_odd,
        model_prob=pick.model_probability, implied_prob=None, edge=None,
        confidence=confidence_label(pick.confidence_score), status="pending",
        reason=pick.recommendation_reason, bookmaker=None, created_at="",
        stage=match.stage, group=match.group, kickoff=_iso(match.utc_kickoff),
        team=pick.team, player_number=pick.number,
    )


def livepick_to_out(rec: FootballRecommendation) -> LivePickOut:
    if not rec.is_active:
        status = "cancelled"
    else:
        status = {"pending": "active", "hit": "won", "miss": "lost",
                  "push": "void", "void": "void"}.get(rec.status, "active")
    return LivePickOut(
        id=rec.id, match=_match_label(rec), match_id=rec.match_id,
        league=rec.league or None, market=rec.market, selection=rec.selection,
        line=rec.line, odd=rec.odd, confidence=confidence_label(rec.confidence_score),
        reason=rec.recommendation_reason or None, status=status,
        analyst_name=rec.created_by_name or None,
        created_at=_iso(rec.generated_at) or "", settled_at=_iso(rec.settled_at),
    )


def pickresult_to_out(r: FootballPickResult) -> PickResultOut:
    return PickResultOut(
        id=r.id, match=r.match or "", league=r.league or None, market=r.market,
        selection=r.selection, odd=r.odd,
        result=_RESULT_STATUS.get(r.status, r.status), profit=r.profit_units,
        analyst_name=r.analyst_name, created_at=_iso(r.settled_at) or "",
        settled_at=_iso(r.settled_at),
    )


def performance_summary(db: Session, context: str = "general") -> PerformanceSummary:
    rows = list(db.scalars(
        select(FootballPickResult).where(FootballPickResult.context == context)
    ).all())

    def _bucket():
        return {"won": 0, "lost": 0, "push": 0, "pending": 0, "profit": 0.0}

    totals = _bucket()
    by_market: dict[str, dict] = {}
    by_league: dict[str, dict] = {}

    for r in rows:
        m = by_market.setdefault(r.market, _bucket())
        lg = by_league.setdefault(r.league or "—", _bucket())
        for agg in (totals, m, lg):
            if r.status == "hit":
                agg["won"] += 1
            elif r.status == "miss":
                agg["lost"] += 1
            elif r.status == "push":
                agg["push"] += 1
            else:
                agg["void_or_pending"] = agg.get("void_or_pending", 0)
            agg["profit"] += r.profit_units or 0.0

    def _rate(agg) -> Optional[float]:
        denom = agg["won"] + agg["lost"]
        return round(agg["won"] / denom, 4) if denom else None

    def _finalize_total(agg) -> PerfTotals:
        staked = agg["won"] + agg["lost"] + agg["push"]
        return PerfTotals(
            total=staked, won=agg["won"], lost=agg["lost"], push=agg["push"],
            pending=0, hit_rate=_rate(agg),
            roi=round(agg["profit"] / staked, 4) if staked else None,
            profit=round(agg["profit"], 3),
        )

    def _finalize_item(key, agg) -> PerfBreakdownItem:
        staked = agg["won"] + agg["lost"] + agg["push"]
        return PerfBreakdownItem(
            key=key, label=key, won=agg["won"], lost=agg["lost"], push=agg["push"],
            pending=0, total=staked, hit_rate=_rate(agg),
            roi=round(agg["profit"] / staked, 4) if staked else None,
            profit=round(agg["profit"], 3),
        )

    return PerformanceSummary(
        totals=_finalize_total(totals),
        by_market=[_finalize_item(k, a) for k, a in by_market.items()],
        by_league=[_finalize_item(k, a) for k, a in by_league.items()],
    )
