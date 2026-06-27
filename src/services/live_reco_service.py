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
    AVOID_ENTRY, CORNERS_OVER, GOAL_PRESSURE, NEXT_CORNER, SHOTS_ON_TARGET,
    TEAM_CORNERS_OVER, LiveStats, LivePick, TeamLive, classify_live_all,
)

logger = logging.getLogger(__name__)

# Campos cumulativos rastreados pra calcular deltas (últimos ~10 min).
_DELTA_KEYS = ("corner_kicks", "total_shots", "shots_on_goal", "shots_insidebox",
               "blocked_shots", "expected_goals")


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
        d_shots_on=dl.get("shots_on_goal", 0.0),
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
            picks = classify_live_all(build_live_stats(m, st, deltas))
            stats_out["matches"] += 1
            for pick in picks:                       # corners + chutes/gol do time
                try:
                    upsert_live_rec(db, m, ctx, pick)
                    stats_out["saved"] += 1
                except Exception:  # noqa: BLE001 — banco fora não derruba o worker
                    db.rollback()
                    logger.warning("live reco: falha persistindo jogo %s", m.id)
    return stats_out


def _side_of(r: FootballLiveRecommendation) -> str | None:
    """Lado do time citado na recomendação (home/away) a partir do texto."""
    txt = f"{r.market or ''} {r.recommendation or ''}"
    if r.home_team and r.home_team in txt:
        return "home"
    if r.away_team and r.away_team in txt:
        return "away"
    return None


def _score_at(r: FootballLiveRecommendation, side: str) -> int:
    """Gols do lado no momento da recomendação (do stats_used['score'])."""
    raw = (r.stats_used or {}).get("score", "")
    try:
        h, a = str(raw).split("-")
        return int(h if side == "home" else a)
    except (ValueError, AttributeError):
        return 0


def settle_finished(db: Session, data_service) -> dict:
    """Liquida TODAS as recomendações ao vivo pendentes de jogos ENCERRADOS,
    comparando a linha/estado da entrada com as estatísticas FINAIS do jogo."""
    rows = list(db.scalars(select(FootballLiveRecommendation).where(
        FootballLiveRecommendation.result == "pending",
    )).all())
    out = {"settled": 0}
    by_match: dict[int, list] = {}
    for r in rows:
        by_match.setdefault(r.match_id, []).append(r)

    for match_id, recs in by_match.items():
        m = data_service.match_domain(match_id, context=recs[0].context)
        if m is None or m.status != "finished":
            continue
        stats = data_service._live_stats(m, recs[0].context)
        if stats is None:
            continue
        corners = {"home": _g(stats.home, "corner_kicks"), "away": _g(stats.away, "corner_kicks")}
        sot = {"home": _g(stats.home, "shots_on_goal"), "away": _g(stats.away, "shots_on_goal")}
        goals = {"home": int(m.home_goals or 0), "away": int(m.away_goals or 0)}
        total_corners = corners["home"] + corners["away"]

        for r in recs:
            side = _side_of(r)
            result = None
            if r.rec_type == CORNERS_OVER and r.line is not None:
                result = "green" if total_corners > r.line else "red"
            elif r.rec_type == TEAM_CORNERS_OVER and side and r.line is not None:
                result = "green" if corners[side] > r.line else "red"
            elif r.rec_type == SHOTS_ON_TARGET and side and r.line is not None:
                result = "green" if sot[side] > r.line else "red"
            elif r.rec_type == NEXT_CORNER and side:
                at = (r.stats_used or {}).get(f"corners_{side}", 0) or 0
                result = "green" if corners[side] > at else "red"
            elif r.rec_type == GOAL_PRESSURE and side:
                result = "green" if goals[side] > _score_at(r, side) else "red"
            if result is None:
                continue
            r.result = result
            r.status = "settled"
            r.settled_at = datetime.now(timezone.utc)
            out["settled"] += 1
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
