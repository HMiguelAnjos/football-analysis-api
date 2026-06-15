"""Testes do FootballDataService + GenerationService em modo fixtures.

Sem env vars de provider, o registry cai automaticamente pra fixtures — então
estes testes exercitam o caminho real offline.
"""

from __future__ import annotations

from src.services.football.data_service import FootballDataService
from src.services.football.generation_service import GenerationService
from src.services import recommendation_service as rec_svc


def test_matches_returns_front_schema():
    svc = FootballDataService()
    date, matches = svc.matches(date="2026-06-14")
    assert date == "2026-06-14"
    assert len(matches) >= 1
    m = matches[0]
    # Formato do front: home/away (refs), kickoff, status.
    assert m.home.name
    assert m.away.name
    assert m.status


def test_match_odds_entries_and_stats():
    svc = FootballDataService()
    odds = svc.match_odds(1001)
    assert odds is not None
    assert odds.entries and odds.entries[0].market
    stats = svc.match_statistics(1001)
    assert stats is not None
    assert stats.home.team.name


def test_teams_and_players_lists():
    svc = FootballDataService()
    teams = svc.teams()
    assert len(teams) >= 1 and teams[0].name
    teams_filtered = svc.teams(search="liverpool")
    assert all("liverpool" in t.name.lower() for t in teams_filtered)

    players = svc.players(team_id=40)
    assert all(p.team_id == 40 for p in players)


def test_leagues_and_player():
    svc = FootballDataService()
    leagues = svc.leagues()
    assert leagues and leagues[0].name
    player = svc.player(301)
    assert player is not None
    assert player.goals >= 0


def test_generation_persists(db_session):
    gen = GenerationService(FootballDataService())
    result = gen.generate(db_session, date="2026-06-14", min_edge=-1.0, min_confidence=0.0)
    assert result["matches_analyzed"] >= 1
    assert result["generated"] >= 1
    assert result["persisted"] >= 1
    assert len(rec_svc.list_recommendations(db_session)) == result["persisted"]


def test_generation_idempotent(db_session):
    gen = GenerationService(FootballDataService())
    gen.generate(db_session, date="2026-06-14", min_edge=-1.0, min_confidence=0.0)
    before = len(rec_svc.list_recommendations(db_session))
    second = gen.generate(db_session, date="2026-06-14", min_edge=-1.0, min_confidence=0.0)
    after = len(rec_svc.list_recommendations(db_session))
    assert after == before
    assert second["persisted"] == 0
