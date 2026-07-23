"""
Orquestração da recomendação de ESCANTEIOS (Fase 1, pré-jogo).

LÊ as features rolantes já computadas (worker) do Postgres, calcula H2H e a média
de estilo da liga, roda o modelo PURO (probability.corners) e devolve o schema do
front. Nada de recalcular feature em request — só leitura + modelo.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src import config
from src.db.models import FootballMatchCornerStats, FootballTeamCornerFeatures
from src.probability import corners as corner_model
from src.schemas.football_schemas import CornerHalfSchema, CornerPredictionOut
from src.services.football.data_service import FootballDataService

logger = logging.getLogger(__name__)


def _load_features(db: Session, team_id: int, context: str):
    return db.scalar(select(FootballTeamCornerFeatures).where(
        FootballTeamCornerFeatures.team_id == team_id,
        FootballTeamCornerFeatures.context == context))


def _to_model(f: FootballTeamCornerFeatures) -> corner_model.TeamCornerFeatures:
    return corner_model.TeamCornerFeatures(
        media_favor_l5=f.media_favor_l5 or 0.0,
        media_contra_l5=f.media_contra_l5 or 0.0,
        media_favor_l10=f.media_favor_l10,
        media_contra_l10=f.media_contra_l10,
        indice_estilo_ofensivo=f.indice_estilo_ofensivo,
        prop_1t=f.prop_1t or corner_model.DEFAULT_PROP_1T,
        prop_2t=f.prop_2t or corner_model.DEFAULT_PROP_2T,
        sample_size=f.sample_size or 0,
    )


def _league_style_avg(db: Session, league_id: Optional[int]) -> Optional[float]:
    """Média do índice de estilo ofensivo da liga — referência da regra 3."""
    if not league_id:
        return None
    return db.scalar(select(func.avg(FootballTeamCornerFeatures.indice_estilo_ofensivo))
                     .where(FootballTeamCornerFeatures.league_id == league_id,
                            FootballTeamCornerFeatures.indice_estilo_ofensivo.isnot(None)))


def _h2h_corners(db: Session, home_id: int, away_id: int) -> tuple[Optional[float], int]:
    """Média de escanteios TOTAIS nos últimos 5 confrontos diretos (da ótica do
    mandante, pra não contar o jogo 2×). (media, n_jogos)."""
    rows = db.scalars(select(FootballMatchCornerStats).where(
        FootballMatchCornerStats.team_id == home_id,
        FootballMatchCornerStats.opponent_id == away_id)
        .order_by(FootballMatchCornerStats.match_date.desc()).limit(5)).all()
    if not rows:
        return None, 0
    totals = [(r.corners_for + r.corners_against) for r in rows]
    return round(sum(totals) / len(totals), 2), len(totals)


def corner_prediction(db: Session, data: FootballDataService, match_id: int,
                      context: str = "general") -> Optional[CornerPredictionOut]:
    """Previsão de escanteios do jogo. None se o jogo não existe; devolve schema
    com `note` quando as features ainda não foram computadas pelo worker."""
    m = data.match_domain(match_id, context=context)
    if m is None:
        return None

    label = f"{m.home_team.name} x {m.away_team.name}"
    hf = _load_features(db, m.home_team.id, context)
    af = _load_features(db, m.away_team.id, context)
    if not hf or not af:
        return CornerPredictionOut(
            match_id=m.id, match=label, league=m.league_name,
            expected_total=0.0, by_half=CornerHalfSchema(first_half=0.0, second_half=0.0),
            home_expected=0.0, away_expected=0.0, line=0.0, prob_over=0.0,
            confidence=0.0, sample_size=0, used_h2h=False, used_style=False,
            note="Features de escanteio ainda não computadas para este jogo.")

    h2h_media, h2h_games = _h2h_corners(db, m.home_team.id, m.away_team.id)
    pred = corner_model.predict(
        _to_model(hf), _to_model(af),
        h2h_media=h2h_media, h2h_games=h2h_games,
        league_style_avg=_league_style_avg(db, m.league_id),
        style_boost=config.CORNER_STYLE_BOOST,
    )
    if pred is None:
        return CornerPredictionOut(
            match_id=m.id, match=label, league=m.league_name,
            expected_total=0.0, by_half=CornerHalfSchema(first_half=0.0, second_half=0.0),
            home_expected=0.0, away_expected=0.0, line=0.0, prob_over=0.0,
            confidence=0.0, sample_size=0, used_h2h=False, used_style=False,
            note="Sem dado suficiente de escanteio para prever.")

    return CornerPredictionOut(
        match_id=m.id, match=label, league=m.league_name,
        expected_total=pred.expected_total,
        by_half=CornerHalfSchema(first_half=pred.expected_1t, second_half=pred.expected_2t),
        home_expected=pred.home_corners, away_expected=pred.away_corners,
        line=pred.line, prob_over=pred.prob_over, confidence=pred.confidence,
        sample_size=pred.sample, used_h2h=pred.used_h2h, used_style=pred.used_style,
    )
