"""
Modelo de gols Poisson / Dixon-Coles.

A base de quase todo mercado de futebol é a distribuição do placar. Modelamos
gols do mandante e do visitante como Poisson independentes com médias
(lambda_home, lambda_away), e aplicamos a correção de Dixon-Coles (1997) pros
placares baixos (0-0, 1-0, 0-1, 1-1), onde a independência pura erra — empates
de poucos gols são mais frequentes do que o Poisson puro prevê.

Tudo aqui é PURO: sem rede, sem cache, sem estado. Entrada = números, saída =
matriz de probabilidades de placar. Os mercados (markets.py) derivam tudo dessa
matriz.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

MAX_GOALS = 10


def poisson_pmf(k: int, lam: float) -> float:
    """P(X = k) pra X ~ Poisson(lam)."""
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam) * lam**k / math.factorial(k)


def _dc_tau(i: int, j: int, lam: float, mu: float, rho: float) -> float:
    """Fator de correção de Dixon-Coles pros placares baixos.

    rho ∈ aprox [-0.2, 0]. rho=0 → sem correção (Poisson puro).
    """
    if i == 0 and j == 0:
        return 1.0 - lam * mu * rho
    if i == 0 and j == 1:
        return 1.0 + lam * rho
    if i == 1 and j == 0:
        return 1.0 + mu * rho
    if i == 1 and j == 1:
        return 1.0 - rho
    return 1.0


@dataclass
class ScoreMatrix:
    """Matriz P[i][j] = prob. do placar (mandante i × visitante j).

    Normalizada (soma ≈ 1) sobre o grid 0..max_goals. lambda_home/away são
    as médias de gols esperadas usadas pra gerar a matriz — guardadas pra
    debug e pros mercados de "expected goals".
    """
    matrix: list[list[float]]
    lambda_home: float
    lambda_away: float
    max_goals: int

    def prob(self, home: int, away: int) -> float:
        if 0 <= home <= self.max_goals and 0 <= away <= self.max_goals:
            return self.matrix[home][away]
        return 0.0


def build_score_matrix(
    lambda_home: float,
    lambda_away: float,
    *,
    rho: float = -0.05,
    max_goals: int = MAX_GOALS,
) -> ScoreMatrix:
    """Constrói a matriz de placar com correção de Dixon-Coles e normaliza.

    Args:
        lambda_home: gols esperados do mandante.
        lambda_away: gols esperados do visitante.
        rho: parâmetro de dependência de Dixon-Coles (default -0.05, valor
             empírico típico pro futebol europeu).
        max_goals: truncamento do grid (10 cobre ~99.99% da massa).
    """
    lam = max(lambda_home, 1e-6)
    mu = max(lambda_away, 1e-6)

    matrix = [[0.0] * (max_goals + 1) for _ in range(max_goals + 1)]
    total = 0.0
    for i in range(max_goals + 1):
        pi = poisson_pmf(i, lam)
        for j in range(max_goals + 1):
            p = pi * poisson_pmf(j, mu) * _dc_tau(i, j, lam, mu, rho)
            # tau pode deixar negativo em cantos extremos de rho — clampa.
            p = max(p, 0.0)
            matrix[i][j] = p
            total += p

    if total > 0:
        for i in range(max_goals + 1):
            for j in range(max_goals + 1):
                matrix[i][j] /= total

    return ScoreMatrix(
        matrix=matrix,
        lambda_home=lambda_home,
        lambda_away=lambda_away,
        max_goals=max_goals,
    )
