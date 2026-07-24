"""
Orquestração da recomendação de CARTÕES (Fase A, pré-jogo).

LÊ as features rolantes + a média do árbitro (Postgres), resolve as flags de
contexto (clássico via config, decisivo via heurística de tabela/mata-mata),
roda o modelo PURO (probability.cards) e devolve o schema do front.
"""

from __future__ import annotations

import logging
import math
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src import config
from src.db.models import FootballRefereeStats, FootballTeamCardFeatures
from src.probability import cards as card_model
from src.schemas.football_schemas import CardPredictionOut, CornerHalfSchema, PenduradoOut
from src.services.football.data_service import FootballDataService

logger = logging.getLogger(__name__)

# Limiar de suspensão do Brasileirão: pendurado = 2 amarelos (3º = gancho).
SUSPENSION_YELLOWS = 3


def _next_is_away(team_id: int, current, season_matches) -> bool:
    cur = current.utc_kickoff
    cands = [mm for mm in season_matches
             if mm.id != current.id and mm.status != "finished"
             and team_id in (mm.home_team.id, mm.away_team.id)
             and not (cur and mm.utc_kickoff and mm.utc_kickoff <= cur)]
    if not cands:
        return False
    cands.sort(key=lambda x: (x.utc_kickoff is None, x.utc_kickoff))
    return cands[0].away_team.id == team_id


def pendurado_effects(data: FootballDataService, m, context: str):
    """Regra do pendurado (Fase B) — SÓ Série A/B. Devolve (home_extra,
    away_extra, [PenduradoOut], [log dict]). Extra = soma dos deltas por lado,
    que entram na expectativa de cartão do time."""
    if m.league_id not in config.BRAZIL_LEAGUE_IDS:
        return 0.0, 0.0, [], []
    season = data._league_season(m.league_id, context)
    home_ctx, away_ctx = data._card_contexts(m, season, context)
    smatches = data._season_matches_cached(m.league_id, season, context)
    outs: list[PenduradoOut] = []
    logs: list[dict] = []

    def side(team_id, team_name, ctx) -> float:
        strategic = bool(ctx.get("strategic"))
        next_away = _next_is_away(team_id, m, smatches)
        pool = data.team_player_pool(team_id, context, season=season, league_id=m.league_id)
        extra = 0.0
        for p in pool:
            yc = p.yellow_cards or 0
            if not (yc > 0 and yc % SUSPENSION_YELLOWS == SUSPENSION_YELLOWS - 1):
                continue
            rate = (yc / p.appearances) if p.appearances else 0.0
            base = 1.0 - math.exp(-rate)          # P(≥1 amarelo) sem a regra
            eff, reason, adj, delta = card_model.pendurado_effect(
                base, strategic=strategic, next_away=next_away,
                boost=config.CARD_PENDURADO_BOOST, damp=config.CARD_PENDURADO_DAMP)
            extra += delta
            outs.append(PenduradoOut(player=p.name, team=team_name, effect=eff, reason=reason))
            logs.append({"player_id": int(p.id), "player_name": p.name, "team_id": team_id,
                         "effect": eff, "reason": reason,
                         "base_prob": round(base, 4), "adjusted_prob": adj})
        return extra

    he = side(m.home_team.id, m.home_team.name, home_ctx)
    ae = side(m.away_team.id, m.away_team.name, away_ctx)
    return he, ae, outs, logs


def _feat(db: Session, team_id: int, context: str):
    return db.scalar(select(FootballTeamCardFeatures).where(
        FootballTeamCardFeatures.team_id == team_id,
        FootballTeamCardFeatures.context == context))


def _to_model(f: FootballTeamCardFeatures) -> card_model.TeamCardFeatures:
    return card_model.TeamCardFeatures(
        media_favor_l5=f.media_favor_l5 or 0.0,
        media_favor_l10=f.media_favor_l10,
        media_faltas_l5=f.media_faltas_l5,
        prop_1t=f.prop_1t or card_model.DEFAULT_PROP_1T,
        prop_2t=f.prop_2t or card_model.DEFAULT_PROP_2T,
        sample_size=f.sample_size or 0)


def _league_avg(db: Session, league_id: Optional[int], col) -> Optional[float]:
    if not league_id:
        return None
    return db.scalar(select(func.avg(col)).where(
        FootballTeamCardFeatures.league_id == league_id, col.isnot(None)))


def _referee_avg(db: Session, referee: str) -> Optional[float]:
    if not referee:
        return None
    row = db.scalar(select(FootballRefereeStats).where(
        FootballRefereeStats.referee == referee))
    # Só confia na média com um mínimo de jogos (senão cai no fallback da liga).
    return row.avg_cards if (row and row.matches >= 3) else None


def _is_classico(home_id: int, away_id: int) -> bool:
    return tuple(sorted((home_id, away_id))) in set(config.CLASSICO_PAIRS)


def _is_decisivo(data: FootballDataService, m, context: str) -> bool:
    """Os dois times na zona de título (top-4) OU de rebaixamento (bottom-4,
    liga de 20). Não uso `stage` (o parser rotula 'knockout' até em rodada de
    liga); cartão é só Série A/B, que são LIGAS, então a tabela basta."""
    season = data._league_season(m.league_id, context)
    ranks = data._standings_rank(m.league_id, season, context)
    rh, ra = ranks.get(m.home_team.id), ranks.get(m.away_team.id)
    if not (rh and ra):
        return False
    return (rh <= 4 and ra <= 4) or (rh >= 17 and ra >= 17)


def _empty(m, label, note) -> CardPredictionOut:
    return CardPredictionOut(
        match_id=m.id, match=label, league=m.league_name, expected_total=0.0,
        by_half=CornerHalfSchema(first_half=0.0, second_half=0.0),
        home_expected=0.0, away_expected=0.0, line=0.0, prob_over=0.0,
        confidence=0.0, sample_size=0, referee_factor=1.0, used_context=False,
        note=note)


def card_prediction(db: Session, data: FootballDataService, match_id: int,
                    context: str = "general") -> Optional[CardPredictionOut]:
    m = data.match_domain(match_id, context=context)
    if m is None:
        return None
    label = f"{m.home_team.name} x {m.away_team.name}"
    hf, af = _feat(db, m.home_team.id, context), _feat(db, m.away_team.id, context)
    if not hf or not af:
        return _empty(m, label, "Features de cartão ainda não computadas para este jogo.")

    col_cards = FootballTeamCardFeatures.media_favor_l5
    col_fouls = FootballTeamCardFeatures.media_faltas_l5
    # Regra 5 (Fase B): efeito líquido dos pendurados por lado.
    home_extra, away_extra, pendurados, _ = pendurado_effects(data, m, context)
    pred = card_model.predict(
        _to_model(hf), _to_model(af),
        referee_avg=_referee_avg(db, m.referee),
        league_cards_avg=_league_avg(db, m.league_id, col_cards),
        league_fouls_avg=_league_avg(db, m.league_id, col_fouls),
        is_classico=_is_classico(m.home_team.id, m.away_team.id),
        is_decisivo=_is_decisivo(data, m, context),
        context_boost=config.CARD_CONTEXT_BOOST, away_boost=config.CARD_AWAY_BOOST,
        home_extra=home_extra, away_extra=away_extra,
    )
    if pred is None:
        return _empty(m, label, "Sem dado suficiente de cartão para prever.")

    return CardPredictionOut(
        match_id=m.id, match=label, league=m.league_name,
        expected_total=pred.expected_total,
        by_half=CornerHalfSchema(first_half=pred.expected_1t, second_half=pred.expected_2t),
        home_expected=pred.home_cards, away_expected=pred.away_cards,
        line=pred.line, prob_over=pred.prob_over, confidence=pred.confidence,
        sample_size=pred.sample, referee_factor=pred.referee_factor,
        used_context=pred.used_context, pendurados=pendurados)
