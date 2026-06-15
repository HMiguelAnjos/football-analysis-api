"""Smoke test: a app importa e registra as rotas esperadas."""

from __future__ import annotations


def test_app_imports_and_has_routes():
    from src.main import app
    paths = {r.path for r in app.routes}
    expected = {
        "/health",
        "/auth/login",
        "/football/matches/today",
        "/football/matches/{match_id}",
        "/football/matches/{match_id}/odds",
        "/football/recommendations",
        "/football/recommendations/live",
        "/football/recommendations/generate",
        "/football/pick-results",
        "/football/leagues",
    }
    missing = expected - paths
    assert not missing, f"rotas faltando: {missing}"
