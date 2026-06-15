"""
Probabilidades de mercado derivadas da matriz de placar (e modelos Poisson
independentes pra escanteios, cartões e player props).

Cada função devolve um dict {seleção: probabilidade} já normalizado quando
o mercado é exaustivo (ex: 1X2 soma 1). Os nomes das seleções batem com os
do provider de odds pra parear direto no edge.

PURO: entrada = ScoreMatrix / médias, saída = probabilidades.
"""

from __future__ import annotations

from src.probability.poisson import poisson_pmf
from src.probability.poisson import ScoreMatrix

# ---------------------------------------------------------------------------
# Mercados derivados da matriz de placar
# ---------------------------------------------------------------------------


def match_winner(sm: ScoreMatrix) -> dict[str, float]:
    """1X2 — vitória mandante / empate / vitória visitante."""
    home = draw = away = 0.0
    for i in range(sm.max_goals + 1):
        for j in range(sm.max_goals + 1):
            p = sm.matrix[i][j]
            if i > j:
                home += p
            elif i == j:
                draw += p
            else:
                away += p
    return {"home": home, "draw": draw, "away": away}


def double_chance(sm: ScoreMatrix) -> dict[str, float]:
    """Dupla chance: 1X (casa ou empate), 12 (casa ou fora), X2 (empate ou fora)."""
    w = match_winner(sm)
    return {
        "home_draw": w["home"] + w["draw"],
        "home_away": w["home"] + w["away"],
        "draw_away": w["draw"] + w["away"],
    }


def draw_no_bet(sm: ScoreMatrix) -> dict[str, float]:
    """Empate anula — renormaliza 1X2 sem o empate."""
    w = match_winner(sm)
    denom = w["home"] + w["away"]
    if denom <= 0:
        return {"home": 0.5, "away": 0.5}
    return {"home": w["home"] / denom, "away": w["away"] / denom}


def over_under(sm: ScoreMatrix, line: float) -> dict[str, float]:
    """Over/Under no total de gols pra uma linha (ex: 2.5)."""
    over = under = 0.0
    for i in range(sm.max_goals + 1):
        for j in range(sm.max_goals + 1):
            total = i + j
            if total > line:
                over += sm.matrix[i][j]
            else:
                under += sm.matrix[i][j]
    return {"over": over, "under": under}


def btts(sm: ScoreMatrix) -> dict[str, float]:
    """Ambas marcam (both teams to score) — yes/no."""
    yes = 0.0
    for i in range(1, sm.max_goals + 1):
        for j in range(1, sm.max_goals + 1):
            yes += sm.matrix[i][j]
    return {"yes": yes, "no": 1.0 - yes}


def asian_handicap(sm: ScoreMatrix, line: float) -> dict[str, float]:
    """Handicap asiático (linha inteira ou meia) do ponto de vista do mandante.

    `line` é o handicap aplicado ao mandante (ex: -1.0, +0.5, -0.25). Devolve
    {home, away} já renormalizado pelos pushes (linhas inteiras podem empatar
    e devolver a aposta — excluímos o push da base, padrão de mercado).
    Quarter lines (.25/.75) são a média de duas linhas adjacentes.
    """
    # Quarter line: média das duas meias-linhas vizinhas.
    if abs((line * 2) % 1) > 1e-9:  # termina em .25 ou .75
        lo = line - 0.25
        hi = line + 0.25
        a = asian_handicap(sm, lo)
        b = asian_handicap(sm, hi)
        return {"home": (a["home"] + b["home"]) / 2, "away": (a["away"] + b["away"]) / 2}

    home = away = push = 0.0
    for i in range(sm.max_goals + 1):
        for j in range(sm.max_goals + 1):
            p = sm.matrix[i][j]
            margin = (i + line) - j
            if margin > 0:
                home += p
            elif margin < 0:
                away += p
            else:
                push += p
    denom = home + away
    if denom <= 0:
        return {"home": 0.5, "away": 0.5}
    return {"home": home / denom, "away": away / denom}


def team_totals(sm: ScoreMatrix, line: float, *, home: bool) -> dict[str, float]:
    """Over/Under nos gols de UM time específico."""
    over = under = 0.0
    for i in range(sm.max_goals + 1):
        for j in range(sm.max_goals + 1):
            goals = i if home else j
            if goals > line:
                over += sm.matrix[i][j]
            else:
                under += sm.matrix[i][j]
    return {"over": over, "under": under}


def correct_score(sm: ScoreMatrix, top_n: int = 10) -> list[tuple[str, float]]:
    """Top-N placares mais prováveis. [(("2-1"), prob), ...] decrescente."""
    scores = [
        (f"{i}-{j}", sm.matrix[i][j])
        for i in range(sm.max_goals + 1)
        for j in range(sm.max_goals + 1)
    ]
    scores.sort(key=lambda x: x[1], reverse=True)
    return scores[:top_n]


# ---------------------------------------------------------------------------
# Mercados via Poisson independente (escanteios, cartões)
# ---------------------------------------------------------------------------


def poisson_over_under(expected_total: float, line: float) -> dict[str, float]:
    """Over/Under genérico pra qualquer contagem ~ Poisson (escanteios,
    cartões, chutes...). `expected_total` é a média esperada do evento."""
    lam = max(expected_total, 1e-6)
    under = 0.0
    # P(total <= floor(line)) cobre o under (linhas .5 não têm push).
    k = 0
    while k <= line:
        under += poisson_pmf(int(k), lam)
        k += 1
    under = min(max(under, 0.0), 1.0)
    return {"over": 1.0 - under, "under": under}


# ---------------------------------------------------------------------------
# Player props (Poisson sobre a taxa por-90 do jogador)
# ---------------------------------------------------------------------------


def anytime_scorer(goals_per90: float, expected_minutes: float = 90.0) -> float:
    """P(jogador marcar ≥1) = 1 - P(0 gols), Poisson sobre gols esperados
    no tempo previsto em campo."""
    lam = goals_per90 * (expected_minutes / 90.0)
    return 1.0 - poisson_pmf(0, max(lam, 1e-9))


def player_over_line(per90_rate: float, line: float, expected_minutes: float = 90.0) -> dict[str, float]:
    """Over/Under pra uma stat de jogador (chutes, finalizações no alvo,
    assistências...) modelada como Poisson sobre a taxa por-90 escalada
    pelos minutos esperados."""
    lam = max(per90_rate * (expected_minutes / 90.0), 1e-9)
    return poisson_over_under(lam, line)
