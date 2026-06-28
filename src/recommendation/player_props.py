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
    "player_tackles": 0.55,
}
_MARKET_LABEL = {
    "player_shots_on_target": "finalizações no gol",
    "player_shots": "finalizações",
    "anytime_scorer": "marcar a qualquer momento",
    "player_assists": "assistências",
    "player_tackles": "desarmes",
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


def _tackle_opp_label(opp_attack_scaler: float) -> str:
    # Desarme é defensivo: quanto MAIS o adversário ataca, mais o time desarma.
    if opp_attack_scaler >= 1.12:
        return "adversário ataca muito"
    if opp_attack_scaler <= 0.9:
        return "adversário ataca pouco"
    return "ritmo equilibrado"


def _best_line(proj: float, floor: float) -> Optional[float]:
    """A MAIOR meia-linha (0.5, 1.5, 2.5, …) cuja prob de OVER ainda fica ≥ floor.
    É a linha 'que paga' mais alta que segue provável — em vez de uma linha baixa
    quase-certa que não paga nada. None se nem o 0.5 passa o piso."""
    best: Optional[float] = None
    for k in range(0, 9):
        line = k + 0.5
        p = poisson_over_under(proj, line)["over"]
        if p >= floor:
            best = line          # ainda provável → tenta subir mais
        else:
            break                # prob só cai com linha maior → para
    return best


def _over_pick(player: PlayerSchema, team: str, market: str, per_game: float,
               scaler: float, opp: str) -> Optional[PropPick]:
    proj = per_game * scaler
    if proj < 0.5:
        return None
    # Linha "de verdade": a mais alta que ainda passa o piso de confiança. Pra
    # quem tem volume (Casemiro 2.5 desarmes) sobe pra over 1.5/2.5; pra quem
    # tem pouco volume, fica em 0.5 (porque ele realmente não faz mais).
    line = _best_line(proj, MIN_PROB[market])
    if line is None:
        return None
    prob = poisson_over_under(proj, line)["over"]
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


# Expectativa de GOLS DE PÊNALTI por jogo pro batedor OFICIAL do time (≈ pênaltis
# concedidos/jogo × taxa de conversão). Some à projeção de gol só do batedor.
MATCH_PEN_GOALS = 0.15


def _scorer_pick(player: PlayerSchema, team: str, open_play_pg: float,
                 scaler: float, opp: str, *, is_penalty_taker: bool = False) -> Optional[PropPick]:
    # Gols de bola rolando (escalados pelo confronto) + pênalti só pro batedor
    # oficial — evita dar crédito de pênalti a quem não bate.
    proj = open_play_pg * scaler + (MATCH_PEN_GOALS if is_penalty_taker else 0.0)
    if proj <= 0.05:
        return None
    prob = 1.0 - poisson_pmf(0, proj)
    if prob < MIN_PROB["anytime_scorer"]:
        return None
    pen_txt = " · cobra os pênaltis" if is_penalty_taker else ""
    return PropPick(
        player_name=player.name, team=team, number=getattr(player, "number", None),
        market="anytime_scorer",
        selection=f"{player.name} — Marcar a qualquer momento",
        line=None, projection=round(proj, 2), model_probability=round(prob, 4),
        fair_odd=round(1 / prob, 2) if prob > 0 else 0.0,
        confidence_score=round(min(prob, 0.97) * 100, 1),
        recommendation_reason=(
            f"{player.name} faz {open_play_pg:.2f} gol(bola rolando)/jogo{pen_txt}; "
            f"{opp}. Modelo: {prob*100:.0f}% de marcar."
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

    # Batedor de pênalti OFICIAL = mais pênaltis convertidos na temporada (auto).
    taker = max(players, key=lambda x: x.penalty_scored or 0, default=None)
    taker_id = taker.id if (taker and (taker.penalty_scored or 0) >= 1) else None

    # Goleadores: top por gols + o batedor de pênalti (mesmo que não seja top).
    scorers = sorted(players, key=lambda x: x.goals or 0, reverse=True)[:TOP_N_PER_TEAM]
    if taker_id is not None and all(p.id != taker_id for p in scorers):
        scorers = [*scorers, taker]
    for p in scorers:
        open_play = max((p.goals or 0) - (p.penalty_scored or 0), 0)
        pick = _scorer_pick(p, team, _per_game(open_play, p.appearances), scaler, opp,
                            is_penalty_taker=(p.id == taker_id))
        if pick:
            picks.append(pick)
    return picks


# Desarme depende mais do papel/minutos do jogador do que do ataque do rival.
# Então o ajuste por adversário é SUAVE (comprime o scaler ofensivo p/ perto de
# 1.0) — assim a projeção fica perto da média real do jogador (Casemiro ~2.5).
TACKLE_SCALER_STRENGTH = 0.4


def _soften_scaler(scaler: float) -> float:
    soft = 1.0 + (scaler - 1.0) * TACKLE_SCALER_STRENGTH
    return min(max(soft, 0.8), 1.2)


def _tackles_props(players: list[PlayerSchema], team: str,
                   opp_attack_scaler: float) -> list[PropPick]:
    """Desarmes (DEFENSIVO): top desarmadores do time. Projeção perto da média
    real (ajuste suave pelo ataque do adversário)."""
    opp = _tackle_opp_label(opp_attack_scaler)
    soft = _soften_scaler(opp_attack_scaler)
    picks: list[PropPick] = []
    for p in sorted(players, key=lambda x: x.tackles or 0, reverse=True)[:TOP_N_PER_TEAM]:
        pick = _over_pick(p, team, "player_tackles",
                          _per_game(p.tackles or 0, p.appearances), soft, opp)
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
    ajustada pela força do adversário (ataque para finalizações/gols; ataque do
    rival para desarmes)."""
    lam_h, lam_a = expected_goals(home_form, away_form)
    sh = _scaler(lam_h, home_form.goals_for)
    sa = _scaler(lam_a, away_form.goals_for)
    picks = _team_props(home_players, match.home_team.name, sh)
    picks += _team_props(away_players, match.away_team.name, sa)
    # Desarmes: o time da CASA desarma o ataque do VISITANTE (escala sa) e vice-versa.
    picks += _tackles_props(home_players, match.home_team.name, sa)
    picks += _tackles_props(away_players, match.away_team.name, sh)
    picks.sort(key=lambda x: x.model_probability, reverse=True)
    return picks
