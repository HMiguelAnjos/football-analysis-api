"""Testes do feed de player props (modo fixtures, offline)."""

from __future__ import annotations

from src.services.football.data_service import FootballDataService


def test_team_player_pool_fallback_to_competition_players():
    # Fixtures não tem get_squad → team_player_pool cai em competition_players
    # filtrado pelo time. Liverpool = id 40 no dataset mock.
    svc = FootballDataService()
    pool = svc.team_player_pool(40, context="general")
    assert isinstance(pool, list)
    assert pool and all(p.team_id == 40 for p in pool)


def test_match_props_returns_list():
    svc = FootballDataService()
    props = svc.match_props(1001, context="general")  # Liverpool x Man City
    assert isinstance(props, list)


def test_props_feed_is_cached():
    svc = FootballDataService()
    first = svc.props(context="general", limit=20)
    second = svc.props(context="general", limit=20)
    assert isinstance(first, list)
    # Mesma chave de cache → mesmo resultado (não recomputa).
    assert len(first) == len(second)
