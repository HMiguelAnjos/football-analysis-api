"""Testes de conversão odd↔prob e cálculo de edge."""

from __future__ import annotations

from src.probability.edge import (
    confidence_score,
    edge,
    fair_odd,
    implied_probability,
    remove_vig,
)


def test_implied_probability():
    assert implied_probability(2.0) == 0.5
    assert implied_probability(4.0) == 0.25


def test_fair_odd_inverse_of_prob():
    assert fair_odd(0.25) == 4.0
    assert abs(fair_odd(0.5) - 2.0) < 1e-9


def test_remove_vig_sums_to_one():
    # Mercado 1X2 com margem: implícitas somam > 1.
    fair = remove_vig([2.0, 3.5, 4.0])
    assert abs(sum(fair) - 1.0) < 1e-9


def test_edge_positive_when_model_beats_odd():
    # Modelo 60%, odd 2.0 → EV = 0.6*2 - 1 = 0.2.
    assert abs(edge(0.6, 2.0) - 0.2) < 1e-9
    # Modelo 40%, odd 2.0 → EV negativo.
    assert edge(0.4, 2.0) < 0


def test_confidence_score_bounded_and_monotonic():
    low = confidence_score(edge_value=0.01, model_probability=0.5,
                           matches_sample=2, has_xg=False)
    high = confidence_score(edge_value=0.12, model_probability=0.5,
                            matches_sample=10, has_xg=True)
    assert 0 <= low <= 100
    assert 0 <= high <= 100
    assert high > low
