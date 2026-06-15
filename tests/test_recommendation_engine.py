"""Testes do engine de recomendação (ponta a ponta com dados de fixtures)."""

from __future__ import annotations

from src.providers import fixtures
from src.recommendation.engine import generate_recommendations


def _setup():
    match = fixtures.get_match(1001)
    home = fixtures.get_team_form(match.home_team.id)
    away = fixtures.get_team_form(match.away_team.id)
    odds = fixtures.get_match_odds(match)
    return match, home, away, odds


def test_engine_produces_candidates_with_positive_edge():
    match, home, away, odds = _setup()
    recs = generate_recommendations(
        match=match, home_form=home, away_form=away, odds=odds,
        min_edge=-1.0, min_confidence=0.0,  # afrouxa pra inspecionar tudo
    )
    assert recs, "engine deveria gerar candidatos com thresholds soltos"
    for r in recs:
        # Coerência do modelo de edge.
        assert r.odd > 1.0
        assert 0 <= r.model_probability <= 1
        assert abs(r.edge - (r.model_probability * r.odd - 1)) < 1e-3
        assert r.fair_odd > 1.0
        assert r.recommendation_reason


def test_engine_respects_min_edge_filter():
    match, home, away, odds = _setup()
    loose = generate_recommendations(
        match=match, home_form=home, away_form=away, odds=odds,
        min_edge=-1.0, min_confidence=0.0,
    )
    strict = generate_recommendations(
        match=match, home_form=home, away_form=away, odds=odds,
        min_edge=0.10, min_confidence=0.0,
    )
    assert len(strict) <= len(loose)
    assert all(r.edge >= 0.10 for r in strict)


def test_engine_sorted_by_edge_desc():
    match, home, away, odds = _setup()
    recs = generate_recommendations(
        match=match, home_form=home, away_form=away, odds=odds,
        min_edge=-1.0, min_confidence=0.0,
    )
    edges = [r.edge for r in recs]
    assert edges == sorted(edges, reverse=True)
