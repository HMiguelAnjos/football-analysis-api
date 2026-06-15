"""
Estimativa de gols esperados (lambda) de uma partida a partir da forma dos times.

Abordagem clássica de força ofensiva/defensiva relativa à média da liga:

    attack_strength(time)  = gols_marcados_média / média_liga
    defense_strength(time) = gols_sofridos_média / média_liga

    lambda_home = league_home_avg * attack(home) * defense(away)
    lambda_away = league_away_avg * attack(away) * defense(home)

Quando há xG disponível, fazemos um blend com os gols reais (xG é mais estável
e preditivo que gols puros em amostras curtas). Splits casa/fora e descanso
entram como ajustes multiplicativos leves.

PURO e determinístico — só matemática sobre os números de TeamForm.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.providers.base import TeamForm

# Médias default da liga (gols/jogo) quando não temos a média real calculada.
# Valores típicos do futebol europeu de elite.
DEFAULT_LEAGUE_HOME_AVG = 1.50
DEFAULT_LEAGUE_AWAY_AVG = 1.15

# Peso do xG no blend com gols reais (0 = só gols, 1 = só xG).
XG_BLEND_WEIGHT = 0.5

# Vantagem de jogar em casa adicional (multiplicador) quando não há split.
HOME_ADVANTAGE = 1.10

# Shrinkage (regressão à média) pra amostras pequenas: a média do time é
# puxada pra média da liga com peso K de "jogos virtuais". Com poucos jogos
# (início de torneio), evita previsões absurdas (ex.: 93% após 1 jogo); com
# muitos jogos, o efeito sustenta-se quase só no dado real.
SHRINK_K = 5.0


def _shrink(value: float, matches: int, prior: float, k: float = SHRINK_K) -> float:
    """Média ponderada entre o valor observado (peso = nº de jogos) e o prior
    da liga (peso = K). Poucos jogos → perto do prior; muitos → perto do real."""
    n = max(matches, 0)
    return (value * n + prior * k) / (n + k)


@dataclass
class LeagueAverages:
    home_goals_avg: float = DEFAULT_LEAGUE_HOME_AVG
    away_goals_avg: float = DEFAULT_LEAGUE_AWAY_AVG


def _blend_xg(goals: float, xg: Optional[float], weight: float = XG_BLEND_WEIGHT) -> float:
    """Combina gols reais com xG. Se não há xG, devolve só os gols."""
    if xg is None:
        return goals
    return (1 - weight) * goals + weight * xg


def _attack_for(form: TeamForm, *, home: bool) -> float:
    """Gols marcados/jogo do time, preferindo o split de mando quando existe
    e fazendo blend com xG quando disponível."""
    base = form.goals_for
    if home and form.home_goals_for is not None:
        base = form.home_goals_for
    elif not home and form.away_goals_for is not None:
        base = form.away_goals_for
    return max(_blend_xg(base, form.xg), 0.05)


def _defense_for(form: TeamForm, *, home: bool) -> float:
    """Gols sofridos/jogo do time (split de mando + blend com xGA)."""
    base = form.goals_against
    if home and form.home_goals_against is not None:
        base = form.home_goals_against
    elif not home and form.away_goals_against is not None:
        base = form.away_goals_against
    return max(_blend_xg(base, form.xga), 0.05)


def _rest_adjust(rest_days: Optional[int]) -> float:
    """Ajuste leve por descanso. <3 dias = fadiga (-3%); >6 dias = fresco (+2%)."""
    if rest_days is None:
        return 1.0
    if rest_days < 3:
        return 0.97
    if rest_days > 6:
        return 1.02
    return 1.0


def expected_goals(
    home_form: TeamForm,
    away_form: TeamForm,
    *,
    league: Optional[LeagueAverages] = None,
) -> tuple[float, float]:
    """Devolve (lambda_home, lambda_away): gols esperados de cada lado.

    Modelo de força relativa. Sem dados (matches_played==0), cai nas médias
    da liga — nunca explode nem zera.
    """
    lg = league or LeagueAverages()

    # Médias observadas, com shrinkage pra média da liga (amostra pequena).
    home_attack = _shrink(_attack_for(home_form, home=True), home_form.matches_played, lg.home_goals_avg)
    home_defense = _shrink(_defense_for(home_form, home=True), home_form.matches_played, lg.away_goals_avg)
    away_attack = _shrink(_attack_for(away_form, home=False), away_form.matches_played, lg.away_goals_avg)
    away_defense = _shrink(_defense_for(away_form, home=False), away_form.matches_played, lg.home_goals_avg)

    # Força relativa à média da liga.
    home_att_str = home_attack / lg.home_goals_avg
    away_def_str = away_defense / lg.home_goals_avg
    away_att_str = away_attack / lg.away_goals_avg
    home_def_str = home_defense / lg.away_goals_avg

    lambda_home = lg.home_goals_avg * home_att_str * away_def_str
    lambda_away = lg.away_goals_avg * away_att_str * home_def_str

    # Vantagem de casa só quando NÃO temos splits (senão já está embutida).
    if home_form.home_goals_for is None:
        lambda_home *= HOME_ADVANTAGE

    # Ajuste de descanso.
    lambda_home *= _rest_adjust(home_form.rest_days)
    lambda_away *= _rest_adjust(away_form.rest_days)

    # Clampa em faixa sã (0.1 .. 5 gols esperados).
    lambda_home = min(max(lambda_home, 0.1), 5.0)
    lambda_away = min(max(lambda_away, 0.1), 5.0)
    return lambda_home, lambda_away
