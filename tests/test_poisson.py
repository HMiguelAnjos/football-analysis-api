"""Testes do modelo Poisson / Dixon-Coles."""

from __future__ import annotations

import math

from src.probability.poisson import build_score_matrix, poisson_pmf


def test_poisson_pmf_known_values():
    # P(0; lam=2) = e^-2 ≈ 0.1353
    assert poisson_pmf(0, 2.0) == math.exp(-2.0)
    assert abs(poisson_pmf(1, 2.0) - 2.0 * math.exp(-2.0)) < 1e-12


def test_score_matrix_normalized():
    sm = build_score_matrix(1.5, 1.2)
    total = sum(sum(row) for row in sm.matrix)
    assert abs(total - 1.0) < 1e-9


def test_score_matrix_higher_lambda_more_goals():
    weak = build_score_matrix(0.5, 0.5)
    strong = build_score_matrix(3.0, 3.0)
    # Prob de 0-0 deve cair quando os lambdas sobem.
    assert strong.prob(0, 0) < weak.prob(0, 0)


def test_dixon_coles_zero_rho_is_pure_poisson():
    sm = build_score_matrix(1.4, 1.1, rho=0.0)
    # Com rho=0, P(i,j) ≈ P(i)·P(j). Diferença ínfima vem do truncamento do
    # grid + renormalização (cauda > 10 gols redistribuída) — tolerância 1e-6.
    expected = poisson_pmf(2, 1.4) * poisson_pmf(1, 1.1)
    assert abs(sm.prob(2, 1) - expected) < 1e-6
