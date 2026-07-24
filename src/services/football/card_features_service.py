"""
Features rolantes de CARTÃO (Fase A) — lógica do worker.

Por time: últimos N jogos → cartões (por tempo, dos eventos) + faltas + árbitro →
persiste bruto → médias DECAÍDAS L5/L10 + proporção 1T/2T real + faltas L5 →
UPSERT em football_team_card_features. A média do árbitro é recomputada da tabela
bruta (agregando por árbitro), sem chamada extra.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from src import config
from src.db.models import (
    FootballMatchCardStats, FootballRefereeStats, FootballTeamCardFeatures,
)
from src.probability import cards as card_model
from src.services.football.data_service import FootballDataService

logger = logging.getLogger(__name__)


def _raw_row(m, stats: dict, events: dict, team_id: int,
             season: Optional[int]) -> Optional[dict]:
    is_home = m.home_team.id == team_id
    own = stats.get("home" if is_home else "away") or {}
    team_ev = (events.get("teams") or {}).get(team_id) or {}
    total_match = sum(d.get("total", 0) for d in (events.get("teams") or {}).values())
    cf = team_ev.get("total")
    if cf is None and not own:
        return None
    return {
        "match_id": m.id, "team_id": team_id,
        "opponent_id": (m.away_team.id if is_home else m.home_team.id),
        "is_home": is_home, "league_id": m.league_id, "season": season,
        "match_date": m.utc_kickoff,
        "cards_for": int(cf or 0),
        "cards_for_1t": int(team_ev.get("1t", 0) or 0),
        "cards_for_2t": int(team_ev.get("2t", 0) or 0),
        "fouls": own.get("fouls"),
        "referee": (m.referee or None),
        "match_total_cards": int(total_match) if events.get("teams") else None,
    }


def _upsert_raw(db: Session, row: dict) -> None:
    existing = db.scalar(select(FootballMatchCardStats).where(
        FootballMatchCardStats.match_id == row["match_id"],
        FootballMatchCardStats.team_id == row["team_id"]))
    if existing:
        for k, v in row.items():
            setattr(existing, k, v)
    else:
        db.add(FootballMatchCardStats(**row))


def _upsert_features(db: Session, team_id: int, context: str, feat: dict) -> None:
    existing = db.scalar(select(FootballTeamCardFeatures).where(
        FootballTeamCardFeatures.team_id == team_id,
        FootballTeamCardFeatures.context == context))
    if existing:
        for k, v in feat.items():
            setattr(existing, k, v)
    else:
        db.add(FootballTeamCardFeatures(team_id=team_id, context=context, **feat))


def refresh_team_features(db: Session, data: FootballDataService, team_id: int,
                          *, context: str = "general",
                          league_id: Optional[int] = None) -> bool:
    season = data._league_season(league_id, context)
    getter = getattr(data._football(context), "get_recent_results", None)
    matches = []
    if getter is not None:
        try:
            matches = getter(team_id, config.CARD_FEATURES_LAST_N * 2) or []
        except Exception:  # noqa: BLE001
            matches = []
    matches = sorted(
        [m for m in matches if m.status == "finished" and m.utc_kickoff],
        key=lambda m: m.utc_kickoff, reverse=True)[: config.CARD_FEATURES_LAST_N]

    raws: list[dict] = []
    for m in matches:
        stats = data._match_stats_cached(m.id, context)
        events = data.match_card_events(m.id, context)
        row = _raw_row(m, stats or {}, events or {}, team_id, season)
        if row and (row["cards_for"] or row["fouls"] is not None):
            _upsert_raw(db, row)
            raws.append(row)
    if not raws:
        return False

    hl = config.CARD_DECAY_HALFLIFE
    cards = [r["cards_for"] for r in raws]
    fouls = [r["fouls"] for r in raws if r["fouls"] is not None]
    sum_1t = sum(r["cards_for_1t"] for r in raws)
    sum_2t = sum(r["cards_for_2t"] for r in raws)
    tot = sum_1t + sum_2t
    feat = {
        "league_id": league_id, "season": season,
        "media_favor_l5": card_model.decayed_mean(cards[:5], hl),
        "media_favor_l10": card_model.decayed_mean(cards[:10], hl),
        "media_faltas_l5": card_model.decayed_mean(fouls[:5], hl) if fouls else None,
        "prop_1t": round(sum_1t / tot, 4) if tot else card_model.DEFAULT_PROP_1T,
        "prop_2t": round(sum_2t / tot, 4) if tot else card_model.DEFAULT_PROP_2T,
        "sample_size": len(raws),
    }
    _upsert_features(db, team_id, context, feat)
    db.commit()
    return True


def recompute_referee_stats(db: Session) -> int:
    """Recalcula a média de cartões por jogo de cada árbitro a partir da tabela
    bruta (1 jogo conta 1×, não 2× pelas 2 linhas). Idempotente."""
    sub = select(
        FootballMatchCardStats.referee,
        FootballMatchCardStats.match_id,
        func.max(FootballMatchCardStats.match_total_cards).label("total"),
    ).where(FootballMatchCardStats.referee.isnot(None),
            FootballMatchCardStats.match_total_cards.isnot(None)) \
     .group_by(FootballMatchCardStats.referee, FootballMatchCardStats.match_id).subquery()
    rows = db.execute(select(
        sub.c.referee, func.count().label("matches"),
        func.sum(sub.c.total).label("total_cards"),
    ).group_by(sub.c.referee)).all()
    n = 0
    for referee, matches, total_cards in rows:
        avg = round(total_cards / matches, 3) if matches else None
        existing = db.scalar(select(FootballRefereeStats).where(
            FootballRefereeStats.referee == referee))
        if existing:
            existing.matches, existing.total_cards, existing.avg_cards = matches, total_cards, avg
        else:
            db.add(FootballRefereeStats(referee=referee, matches=matches,
                                        total_cards=total_cards, avg_cards=avg))
        n += 1
    db.commit()
    return n


def refresh_upcoming(db: Session, data: FootballDataService,
                     context: str = "general") -> dict:
    teams: dict[int, Optional[int]] = {}
    for m in data._upcoming_matches(context, only_future=True):
        teams.setdefault(m.home_team.id, m.league_id)
        teams.setdefault(m.away_team.id, m.league_id)
    done = errors = 0
    for team_id, league_id in teams.items():
        try:
            if refresh_team_features(db, data, team_id, context=context, league_id=league_id):
                done += 1
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            errors += 1
            logger.warning("card features: time %s falhou (%s)", team_id, exc)
    refs = recompute_referee_stats(db)
    return {"teams": len(teams), "updated": done, "errors": errors, "referees": refs}
