"""Testes de normalização dos providers (parsing puro, sem rede)."""

from __future__ import annotations

from src.providers.api_football.provider import ApiFootballProvider
from src.providers.base import Match, Team
from src.providers.odds.provider import TheOddsApiProvider


def test_api_football_parse_match():
    payload = {
        "fixture": {"id": 12345, "date": "2026-06-14T18:00:00+00:00",
                    "status": {"short": "NS", "elapsed": None},
                    "venue": {"name": "Anfield"}},
        "league": {"id": 39, "name": "Premier League", "season": 2025},
        "teams": {"home": {"id": 40, "name": "Liverpool", "logo": "x"},
                  "away": {"id": 50, "name": "Manchester City", "logo": "y"}},
        "goals": {"home": None, "away": None},
    }
    m = ApiFootballProvider.parse_match(payload)
    assert m is not None
    assert m.id == 12345
    assert m.status == "scheduled"
    assert m.home_team.name == "Liverpool"
    assert m.venue == "Anfield"


def test_api_football_parse_match_live_and_finished():
    base = {
        "fixture": {"id": 1, "date": "2026-06-14T18:00:00+00:00",
                    "status": {"short": "2H", "elapsed": 67}},
        "league": {"id": 39, "name": "PL", "season": 2025},
        "teams": {"home": {"id": 40, "name": "A"}, "away": {"id": 50, "name": "B"}},
        "goals": {"home": 1, "away": 0},
    }
    assert ApiFootballProvider.parse_match(base).status == "live"
    base["fixture"]["status"]["short"] = "FT"
    assert ApiFootballProvider.parse_match(base).status == "finished"


def test_api_football_parse_standings():
    payload = [{
        "league": {"standings": [[
            {"rank": 1, "team": {"id": 50, "name": "Man City"}, "points": 24,
             "goalsDiff": 15,
             "all": {"played": 10, "win": 8, "draw": 0, "lose": 2,
                     "goals": {"for": 25, "against": 10}}},
        ]]}
    }]
    rows = ApiFootballProvider.parse_standings(payload)
    assert len(rows) == 1
    assert rows[0].rank == 1
    assert rows[0].team.name == "Man City"
    assert rows[0].points == 24


def test_odds_normalize_event():
    match = Match(
        id=1001, league_id=39, league_name="PL", season=2025, utc_kickoff=None,
        status="scheduled", home_team=Team(id=40, name="Liverpool"),
        away_team=Team(id=50, name="Manchester City"),
    )
    payload = {
        "bookmakers": [{
            "key": "bet365", "title": "Bet365",
            "markets": [
                {"key": "h2h", "outcomes": [
                    {"name": "Liverpool", "price": 2.10},
                    {"name": "Manchester City", "price": 3.40},
                    {"name": "Draw", "price": 3.60},
                ]},
                {"key": "totals", "outcomes": [
                    {"name": "Over", "price": 1.80, "point": 2.5},
                    {"name": "Under", "price": 2.05, "point": 2.5},
                ]},
                {"key": "btts", "outcomes": [
                    {"name": "Yes", "price": 1.70},
                    {"name": "No", "price": 2.15},
                ]},
            ],
        }],
    }
    odds = TheOddsApiProvider.normalize_event(payload, match)
    assert "1x2" in odds.markets
    h2h = {s.name: s.price for s in odds.markets["1x2"].selections}
    assert h2h["home"] == 2.10
    assert h2h["away"] == 3.40
    assert h2h["draw"] == 3.60

    ou = odds.markets["over_under"].selections
    assert all(s.line == 2.5 for s in ou)
    assert {s.name for s in ou} == {"over", "under"}

    btts = {s.name for s in odds.markets["btts"].selections}
    assert btts == {"yes", "no"}
