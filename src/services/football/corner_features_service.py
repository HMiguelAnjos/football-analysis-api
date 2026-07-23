"""
Recálculo das FEATURES ROLANTES de escanteio (Fase 1) — lógica do worker.

Fluxo por time: últimos N jogos → stats brutas por jogo (persiste) → médias
DECAÍDAS L5/L10 (favor/contra) + índice de estilo ofensivo → UPSERT em
football_team_corner_features. O endpoint depois só LÊ (nada em request).

Sem escanteio por tempo na api-football → prop_1t/2t ficam na proporção fixa da
liga (probability.corners.DEFAULT_PROP_*). flag_pressiona_perdendo é Fase 2.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from src import config
from src.db.models import FootballMatchCornerStats, FootballTeamCornerFeatures
from src.probability import corners as corner_model
from src.services.football.data_service import FootballDataService

logger = logging.getLogger(__name__)


def _raw_row(m, stats: dict, team_id: int, season: Optional[int]) -> Optional[dict]:
    """Registro bruto de escanteio de UM jogo pro time. None se sem escanteio."""
    is_home = m.home_team.id == team_id
    own = stats.get("home" if is_home else "away") or {}
    opp = stats.get("away" if is_home else "home") or {}
    cf, ca = own.get("corner_kicks"), opp.get("corner_kicks")
    if cf is None and ca is None:
        return None
    return {
        "match_id": m.id, "team_id": team_id,
        "opponent_id": (m.away_team.id if is_home else m.home_team.id),
        "is_home": is_home, "league_id": m.league_id, "season": season,
        "match_date": m.utc_kickoff,
        "corners_for": int(cf or 0), "corners_against": int(ca or 0),
        "total_shots": own.get("total_shots"),
        "blocked_shots": own.get("blocked_shots"),
        "shots_insidebox": own.get("shots_insidebox"),
        "possession": own.get("ball_possession"),
        "goals_for": (m.home_goals if is_home else m.away_goals),
        "goals_against": (m.away_goals if is_home else m.home_goals),
    }


def _upsert_raw(db: Session, row: dict) -> None:
    existing = db.scalar(select(FootballMatchCornerStats).where(
        FootballMatchCornerStats.match_id == row["match_id"],
        FootballMatchCornerStats.team_id == row["team_id"]))
    if existing:
        for k, v in row.items():
            setattr(existing, k, v)
    else:
        db.add(FootballMatchCornerStats(**row))


def _upsert_features(db: Session, team_id: int, context: str, feat: dict) -> None:
    existing = db.scalar(select(FootballTeamCornerFeatures).where(
        FootballTeamCornerFeatures.team_id == team_id,
        FootballTeamCornerFeatures.context == context))
    if existing:
        for k, v in feat.items():
            setattr(existing, k, v)
    else:
        db.add(FootballTeamCornerFeatures(team_id=team_id, context=context, **feat))


def _avg_style(raws: list[dict]) -> Optional[float]:
    """Índice de estilo ofensivo médio do time nos jogos coletados."""
    vals = []
    for r in raws:
        idx = corner_model.offensive_style_index(
            r.get("total_shots") or 0, r.get("shots_insidebox") or 0,
            r.get("possession") or 0)
        if idx is not None:
            vals.append(idx)
    return round(sum(vals) / len(vals), 4) if vals else None


def refresh_team_features(db: Session, data: FootballDataService, team_id: int,
                          *, context: str = "general",
                          league_id: Optional[int] = None) -> bool:
    """Recalcula e persiste as features rolantes de UM time. True se gravou."""
    season = data._league_season(league_id, context)
    getter = getattr(data._football(context), "get_recent_results", None)
    matches = []
    if getter is not None:
        try:
            matches = getter(team_id, config.CORNER_FEATURES_LAST_N * 2) or []
        except Exception:  # noqa: BLE001
            matches = []
    # Só finalizados, MAIS RECENTE PRIMEIRO (o decaimento espera essa ordem).
    matches = sorted(
        [m for m in matches if m.status == "finished" and m.utc_kickoff],
        key=lambda m: m.utc_kickoff, reverse=True)[: config.CORNER_FEATURES_LAST_N]

    raws: list[dict] = []
    for m in matches:
        stats = data._match_stats_cached(m.id, context)
        if not stats:
            continue
        row = _raw_row(m, stats, team_id, season)
        if row:
            _upsert_raw(db, row)
            raws.append(row)
    if not raws:
        return False

    favor = [r["corners_for"] for r in raws]        # já recente-primeiro
    contra = [r["corners_against"] for r in raws]
    hl = config.CORNER_DECAY_HALFLIFE
    feat = {
        "league_id": league_id, "season": season,
        "media_favor_l5": corner_model.decayed_mean(favor[:5], hl),
        "media_favor_l10": corner_model.decayed_mean(favor[:10], hl),
        "media_contra_l5": corner_model.decayed_mean(contra[:5], hl),
        "media_contra_l10": corner_model.decayed_mean(contra[:10], hl),
        "prop_1t": corner_model.DEFAULT_PROP_1T,
        "prop_2t": corner_model.DEFAULT_PROP_2T,
        "indice_estilo_ofensivo": _avg_style(raws),
        "flag_pressiona_perdendo": None,            # Fase 2
        "sample_size": len(raws),
    }
    _upsert_features(db, team_id, context, feat)
    db.commit()
    return True


def refresh_upcoming(db: Session, data: FootballDataService,
                     context: str = "general") -> dict:
    """Recalcula as features dos times com jogo nos próximos dias (o conjunto que
    o endpoint vai consultar). Best-effort: um time ruim não derruba o lote."""
    teams: dict[int, Optional[int]] = {}
    for m in data._upcoming_matches(context, only_future=True):
        teams.setdefault(m.home_team.id, m.league_id)
        teams.setdefault(m.away_team.id, m.league_id)
    done = errors = 0
    for team_id, league_id in teams.items():
        try:
            if refresh_team_features(db, data, team_id, context=context,
                                     league_id=league_id):
                done += 1
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            errors += 1
            logger.warning("corner features: time %s falhou (%s)", team_id, exc)
    return {"teams": len(teams), "updated": done, "errors": errors}
