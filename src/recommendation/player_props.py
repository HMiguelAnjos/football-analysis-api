"""
Recomendações de PLAYER PROPS pela projeção do modelo (estilo NBA: projeta a
stat do jogador ajustada pela força do adversário e recomenda over/sim quando
a probabilidade é forte) — SEM depender de odds de casa.

Fluxo por jogador:
  1. stat por jogo (gols, finalizações, finalizações no gol, assistências);
  2. ajuste pelo confronto: scaler = gols_esperados_do_time / média_do_time
     (adversário fraco → time marca mais que a média → scaler > 1 → projeção
     do jogador sobe; adversário forte → scaler < 1 → desce);
  3. projeção = stat_por_jogo × scaler;
  4. linha sintética + probabilidade via Poisson; recomenda quando passa o
     threshold de confiança.

PURO: entra PlayerSchema + forma, sai PropPick. Sem rede/banco.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from src.probability.markets import poisson_over_under
from src.probability.poisson import poisson_pmf
from src.probability.team_strength import expected_goals
from src.providers.base import TeamForm
from src.schemas.football_schemas import PlayerSchema

# Quantos jogadores por time avaliar (os mais relevantes por finalização/gol).
TOP_N_PER_TEAM = 4
# Thresholds de probabilidade pra virar recomendação por mercado.
MIN_PROB = {
    "player_shots_on_target": 0.55,
    "player_shots": 0.55,
    "anytime_scorer": 0.45,
    "player_assists": 0.45,
}
_MARKET_LABEL = {
    "player_shots_on_target": "finalizações no gol",
    "player_shots": "finalizações",
    "anytime_scorer": "marcar a qualquer momento",
    "player_assists": "assistências",
}


@dataclass
class PropPick:
    player_name: str
    team: str
    number: Optional[int]      # número da camisa (None se a fonte não trouxe)
    market: str
    selection: str            # rótulo legível (ex.: "Salah — Mais de 2.5 ...")
    line: Optional[float]
    projection: float
    model_probability: float
    fair_odd: float
    confidence_score: float
    recommendation_reason: str


def _scaler(team_lambda: float, team_avg_goals: float) -> float:
    base = team_lambda / max(team_avg_goals, 0.3)
    return min(max(base, 0.6), 1.6)


def _per_game(stat: float, appearances: int) -> float:
    return stat / appearances if appearances > 0 else 0.0


def _opp_label(scaler: float) -> str:
    if scaler >= 1.12:
        return "adversário frágil defensivamente"
    if scaler <= 0.9:
        return "adversário forte defensivamente"
    return "confronto equilibrado"


def _over_pick(player: PlayerSchema, team: str, market: str, per_game: float,
               scaler: float, opp: str) -> Optional[PropPick]:
    proj = per_game * scaler
    if proj < 0.5:
        return None
    # Linha sintética logo abaixo da projeção (meia-linha, sem push).
    line = max(0.5, math.floor(proj) - 0.5)
    if proj - line < 0.4:           # projeção pouco acima da linha → sobe a linha
        line = max(0.5, line - 1.0)
    prob = poisson_over_under(proj, line)["over"]
    if prob < MIN_PROB[market]:
        return None
    label = _MARKET_LABEL[market]
    return PropPick(
        player_name=player.name, team=team, number=getattr(player, "number", None),
        market=market,
        selection=f"{player.name} — Mais de {line:g} {label}",
        line=line, projection=round(proj, 2), model_probability=round(prob, 4),
        fair_odd=round(1 / prob, 2) if prob > 0 else 0.0,
        confidence_score=round(min(prob, 0.97) * 100, 1),
        recommendation_reason=(
            f"{player.name} faz {per_game:.1f} {label}/jogo; {opp} "
            f"(projeção {proj:.1f}). Modelo: {prob*100:.0f}% de superar {line:g}."
        ),
    )


def _scorer_pick(player: PlayerSchema, team: str, goals_pg: float,
                 scaler: float, opp: str) -> Optional[PropPick]:
    proj = goals_pg * scaler
    if proj <= 0.05:
        return None
    prob = 1.0 - poisson_pmf(0, proj)
    if prob < MIN_PROB["anytime_scorer"]:
        return None
    return PropPick(
        player_name=player.name, team=team, number=getattr(player, "number", None),
        market="anytime_scorer",
        selection=f"{player.name} — Marcar a qualquer momento",
        line=None, projection=round(proj, 2), model_probability=round(prob, 4),
        fair_odd=round(1 / prob, 2) if prob > 0 else 0.0,
        confidence_score=round(min(prob, 0.97) * 100, 1),
        recommendation_reason=(
            f"{player.name} faz {goals_pg:.2f} gol/jogo; {opp}. "
            f"Modelo: {prob*100:.0f}% de marcar."
        ),
    )


def _team_props(players: list[PlayerSchema], team: str, scaler: float) -> list[PropPick]:
    opp = _opp_label(scaler)
    picks: list[PropPick] = []
    # Finalizadores no gol (top por finalizações no gol).
    for p in sorted(players, key=lambda x: x.shots_on_target or 0, reverse=True)[:TOP_N_PER_TEAM]:
        pick = _over_pick(p, team, "player_shots_on_target",
                          _per_game(p.shots_on_target, p.appearances), scaler, opp)
        if pick:
            picks.append(pick)
    # Goleadores (top por gols → marcar a qualquer momento).
    for p in sorted(players, key=lambda x: x.goals or 0, reverse=True)[:TOP_N_PER_TEAM]:
        pick = _scorer_pick(p, team, _per_game(p.goals, p.appearances), scaler, opp)
        if pick:
            picks.append(pick)
    return picks


def generate_player_props(
    *,
    match,
    home_form: TeamForm,
    away_form: TeamForm,
    home_players: list[PlayerSchema],
    away_players: list[PlayerSchema],
) -> list[PropPick]:
    """Player props recomendadas pro jogo, projetando a stat de cada jogador
    ajustada pela força defensiva do adversário."""
    lam_h, lam_a = expected_goals(home_form, away_form)
    sh = _scaler(lam_h, home_form.goals_for)
    sa = _scaler(lam_a, away_form.goals_for)
    picks = _team_props(home_players, match.home_team.name, sh)
    picks += _team_props(away_players, match.away_team.name, sa)
    picks.sort(key=lambda x: x.model_probability, reverse=True)
    return picks
