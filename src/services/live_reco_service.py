"""
Serviço de recomendações AO VIVO (foco escanteios) — orquestra:
  • tracker de deltas (últimos ~10 min) a partir de snapshots periódicos;
  • geração via motor puro (recommendation/live_engine);
  • persistência com UPSERT (não duplica a cada tick) na football_live_recommendations;
  • settlement automático dos mercados de over escanteios (linha vs total final);
  • leitura/atualização (status, resultado) pros endpoints.

Best-effort com o banco: sem Postgres, a geração simplesmente não persiste.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import FootballLiveRecommendation
from src.recommendation.live_engine import (
    AVOID_ENTRY, CORNERS_OVER, TEAM_CORNERS_OVER,
    LiveStats, LivePick, TeamLive, classify_live,
)

logger = logging.getLogger(__name__)

# Campos cumulativos rastreados pra calcular deltas (últimos ~10 min).
_DELTA_KEYS = ("corner_kicks", "total_shots", "shots_insidebox", "blocked_shots", "expected_goals")


def _g(d: dict, key: str) -> float:
    try:
        return float(d.get(key, 0) or 0)
    except (TypeError, ValueError):
        return 0.0


class LiveStatsTracker:
    """Mantém o histórico (minuto → stats cumulativas) por jogo e devolve os
    deltas dos últimos ~10 min. Em memória (vive no worker)."""

    def __init__(self) -> None:
        self._hist: dict[int, list[tuple[int, dict]]] = {}

    def update(self, match_id: int, minute: int, cur: dict) -> dict:
        """cur = {'home': {...cumulativo...}, 'away': {...}}; devolve deltas no
        MESMO formato (diferença vs o snapshot de ~10 min atrás)."""
        hist = self._hist.setdefault(match_id, [])
        hist.append((minute, cur))
        if len(hist) > 40:
            del hist[: len(hist) - 40]
        # Snapshot mais recente com pelo menos ~10 min de diferença.
        past = None
        for mn, snap in hist[:-1]:
            if minute - mn >= 10:
                past = snap
        if past is None:
            past = hist[0][1]
        deltas: dict[str, dict] = {}
        for side in ("home", "away"):
            c, p = cur.get(side, {}), past.get(side, {})
            deltas[side] = {k: max(_g(c, k) - _g(p, k), 0.0) for k in _DELTA_KEYS}
        return deltas


def _team_live(name: str, cur: dict, dl: dict) -> TeamLive:
    return TeamLive(
        name=name,
        corners=int(_g(cur, "corner_kicks")),
        total_shots=int(_g(cur, "total_shots")),
        shots_on=int(_g(cur, "shots_on_goal")),
        shots_insidebox=int(_g(cur, "shots_insidebox")),
        blocked_shots=int(_g(cur, "blocked_shots")),
        possession=_g(cur, "ball_possession"),
        xg=_g(cur, "expected_goals"),
        d_corners=dl.get("corner_kicks", 0.0),
        d_shots=dl.get("total_shots", 0.0),
        d_shots_insidebox=dl.get("shots_insidebox", 0.0),
        d_blocked=dl.get("blocked_shots", 0.0),
        d_xg=dl.get("expected_goals", 0.0),
    )


def build_live_stats(m, stats, deltas: dict) -> LiveStats:
    """Match + MatchStatistics(home/away dicts) + deltas → LiveStats do motor."""
    return LiveStats(
        minute=m.minute or 0,
        home=_team_live(m.home_team.name, stats.home, deltas.get("home", {})),
        away=_team_live(m.away_team.name, stats.away, deltas.get("away", {})),
        home_score=int(m.home_goals or 0),
        away_score=int(m.away_goals or 0),
    )


def upsert_live_rec(db: Session, m, context: str, pick: LivePick) -> None:
    """Grava/atualiza a recomendação ao vivo (1 por jogo+tipo+linha enquanto
    pendente). Não persiste avoid_entry."""
    if pick.rec_type == AVOID_ENTRY:
        return
    stmt = select(FootballLiveRecommendation).where(
        FootballLiveRecommendation.match_id == m.id,
        FootballLiveRecommendation.rec_type == pick.rec_type,
        FootballLiveRecommendation.result == "pending",
    )
    if pick.line is None:
        stmt = stmt.where(FootballLiveRecommendation.line.is_(None))
    else:
        stmt = stmt.where(FootballLiveRecommendation.line == pick.line)
    row = db.scalars(stmt).first()
    now = datetime.now(timezone.utc)
    if row is None:
        db.add(FootballLiveRecommendation(
            context=context, match_id=m.id, league=m.league_name or "",
            home_team=m.home_team.name, away_team=m.away_team.name,
            minute=m.minute, home_score=m.home_goals, away_score=m.away_goals,
            rec_type=pick.rec_type, market=pick.market, line=pick.line, odd=pick.odd,
            confidence=pick.confidence, recommendation=pick.recommendation,
            reason=pick.reason, stats_used=pick.stats_used,
            status="pending", result="pending",
        ))
    else:                                   # atualiza estado/score/confiança
        row.minute = m.minute
        row.home_score = m.home_goals
        row.away_score = m.away_goals
        row.market = pick.market
        row.odd = pick.odd
        row.confidence = pick.confidence
        row.recommendation = pick.recommendation
        row.reason = pick.reason
        row.stats_used = pick.stats_used
        row.updated_at = now
    db.commit()


def generate_for_live_matches(db: Session, data_service, tracker: LiveStatsTracker,
                              contexts: list[str]) -> dict:
    """Um ciclo: pra cada jogo ao vivo, monta o snapshot, classifica e persiste."""
    stats_out = {"matches": 0, "saved": 0}
    for ctx in contexts:
        for m in data_service.live_matches(ctx):
            st = data_service._live_stats(m, ctx)
            if st is None:
                continue
            cur = {"home": st.home, "away": st.away}
            deltas = tracker.update(m.id, m.minute or 0, cur)
            pick = classify_live(build_live_stats(m, st, deltas))
            stats_out["matches"] += 1
            if pick.rec_type != AVOID_ENTRY:
                try:
                    upsert_live_rec(db, m, ctx, pick)
                    stats_out["saved"] += 1
                except Exception:  # noqa: BLE001 — banco fora não derruba o worker
                    db.rollback()
                    logger.warning("live reco: falha persistindo jogo %s", m.id)
    return stats_out


def settle_finished(db: Session, data_service) -> dict:
    """Liquida over escanteios pendentes de jogos ENCERRADOS (total final vs linha)."""
    rows = list(db.scalars(select(FootballLiveRecommendation).where(
        FootballLiveRecommendation.result == "pending",
        FootballLiveRecommendation.rec_type.in_([CORNERS_OVER, TEAM_CORNERS_OVER]),
    )).all())
    out = {"settled": 0}
    by_match: dict[int, list] = {}
    for r in rows:
        by_match.setdefault(r.match_id, []).append(r)
    for match_id, recs in by_match.items():
        m = data_service.match_domain(match_id, context=recs[0].context)
        if m is None or m.status != "finished":
            continue
        stats = data_service._live_stats(m, recs[0].context) if hasattr(data_service, "_live_stats") else None
        if stats is None:
            continue
        total_corners = _g(stats.home, "corner_kicks") + _g(stats.away, "corner_kicks")
        for r in recs:
            if r.rec_type == CORNERS_OVER and r.line is not None:
                r.result = "green" if total_corners > r.line else "red"
                r.status = "settled"
                r.settled_at = datetime.now(timezone.utc)
                out["settled"] += 1
            # team_corners_over: settle manual (não sabemos o lado só pelo texto).
    db.commit()
    return out


# ── Leitura / atualização (endpoints) ────────────────────────────────────

def list_by_match(db: Session, match_id: int) -> list[FootballLiveRecommendation]:
    return list(db.scalars(select(FootballLiveRecommendation)
                           .where(FootballLiveRecommendation.match_id == match_id)
                           .order_by(FootballLiveRecommendation.confidence.desc())).all())


def list_pending(db: Session, context: str | None = None) -> list[FootballLiveRecommendation]:
    stmt = select(FootballLiveRecommendation).where(
        FootballLiveRecommendation.result == "pending")
    if context:
        stmt = stmt.where(FootballLiveRecommendation.context == context)
    return list(db.scalars(stmt.order_by(FootballLiveRecommendation.confidence.desc())).all())


def update_status(db: Session, rec_id: int, status: str) -> FootballLiveRecommendation | None:
    row = db.get(FootballLiveRecommendation, rec_id)
    if row is None:
        return None
    row.status = status
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    return row


def set_result(db: Session, rec_id: int, result: str) -> FootballLiveRecommendation | None:
    row = db.get(FootballLiveRecommendation, rec_id)
    if row is None:
        return None
    row.result = result                       # green | red | void | pending
    if result != "pending":
        row.status = "settled"
        row.settled_at = datetime.now(timezone.utc)
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    return row
