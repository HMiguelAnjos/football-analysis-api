"""
Provider de FIXTURES (mock determinístico).

Implementa FootballDataProvider, OddsProvider e XgProvider com dados estáticos
em memória. É o fallback quando faltam chaves de API — garante que a API sobe e
os testes rodam 100% offline. Dois jogos de exemplo com forma, odds e xG
coerentes pra exercitar o engine de ponta a ponta.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from src import config
from src.providers.base import (
    Group,
    Injury,
    League,
    Lineup,
    LineupPlayer,
    Match,
    MatchOdds,
    MatchStatistics,
    MarketOdds,
    PlayerSeasonStats,
    Selection,
    Standing,
    Team,
    TeamForm,
)

name = "fixtures"

_LIVERPOOL = Team(id=40, name="Liverpool", short_name="LIV")
_CITY = Team(id=50, name="Manchester City", short_name="MCI")
_ARSENAL = Team(id=42, name="Arsenal", short_name="ARS")
_CHELSEA = Team(id=49, name="Chelsea", short_name="CHE")

_PL = League(id=39, name="Premier League", country="England", season=2025)

_TEAMS = {t.id: t for t in (_LIVERPOOL, _CITY, _ARSENAL, _CHELSEA)}


def _kickoff(hours_from_now: float) -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=hours_from_now)


_MATCHES: dict[int, Match] = {
    1001: Match(
        id=1001, league_id=39, league_name="Premier League", season=2025,
        utc_kickoff=_kickoff(3), status="scheduled",
        home_team=_LIVERPOOL, away_team=_CITY, venue="Anfield",
    ),
    1002: Match(
        id=1002, league_id=39, league_name="Premier League", season=2025,
        utc_kickoff=_kickoff(5), status="scheduled",
        home_team=_ARSENAL, away_team=_CHELSEA, venue="Emirates Stadium",
    ),
}

_FORMS: dict[int, TeamForm] = {
    40: TeamForm(team_id=40, matches_played=10, goals_for=2.3, goals_against=1.0,
                 xg=2.1, xga=1.1, home_goals_for=2.7, home_goals_against=0.8,
                 away_goals_for=1.9, away_goals_against=1.2, corners_for=6.2,
                 cards_for=1.6, shots_for=15.0, shots_on_target_for=6.0,
                 points_per_game=2.2, rest_days=5,
                 wins=7, draws=1, losses=2, recent_form="WWDWL"),
    50: TeamForm(team_id=50, matches_played=10, goals_for=2.5, goals_against=1.1,
                 xg=2.4, xga=1.0, home_goals_for=2.8, home_goals_against=0.9,
                 away_goals_for=2.2, away_goals_against=1.3, corners_for=6.8,
                 cards_for=1.4, shots_for=16.0, shots_on_target_for=6.5,
                 points_per_game=2.3, rest_days=4,
                 wins=8, draws=0, losses=2, recent_form="WWWDW"),
    42: TeamForm(team_id=42, matches_played=10, goals_for=1.9, goals_against=1.0,
                 xg=1.8, xga=1.1, home_goals_for=2.1, home_goals_against=0.7,
                 away_goals_for=1.6, away_goals_against=1.3, corners_for=5.5,
                 cards_for=1.8, shots_for=13.0, shots_on_target_for=5.0,
                 points_per_game=2.1, rest_days=6,
                 wins=6, draws=3, losses=1, recent_form="DWWDW"),
    49: TeamForm(team_id=49, matches_played=10, goals_for=1.6, goals_against=1.4,
                 xg=1.5, xga=1.5, home_goals_for=1.8, home_goals_against=1.2,
                 away_goals_for=1.4, away_goals_against=1.6, corners_for=5.0,
                 cards_for=2.0, shots_for=12.0, shots_on_target_for=4.2,
                 points_per_game=1.5, rest_days=3,
                 wins=4, draws=3, losses=3, recent_form="LWDLD"),
}

_PLAYERS: dict[int, PlayerSeasonStats] = {
    301: PlayerSeasonStats(player_id=301, name="Mohamed Salah", team_id=40,
                           position="Attacker", appearances=10, minutes=880,
                           goals=8, assists=5, shots=38, shots_on_target=20,
                           xg=7.2, xa=4.1),
    302: PlayerSeasonStats(player_id=302, name="Erling Haaland", team_id=50,
                           position="Attacker", appearances=10, minutes=860,
                           goals=11, assists=2, shots=42, shots_on_target=24,
                           xg=10.1, xa=1.8),
}


# ── Copa do Mundo (contexto world_cup, league_id=1) ─────────────────────────
_BRAZIL = Team(id=6, name="Brazil", short_name="BRA")
_FRANCE = Team(id=2, name="France", short_name="FRA")
_ARGENTINA = Team(id=26, name="Argentina", short_name="ARG")
_ENGLAND = Team(id=10, name="England", short_name="ENG")

_TEAMS.update({t.id: t for t in (_BRAZIL, _FRANCE, _ARGENTINA, _ENGLAND)})

_WC = "FIFA World Cup"
_WC_LID = 1

_WC_MATCHES: dict[int, Match] = {
    2001: Match(
        id=2001, league_id=_WC_LID, league_name=_WC, season=2026,
        utc_kickoff=_kickoff(2), status="scheduled",
        home_team=_BRAZIL, away_team=_FRANCE, venue="Estadio Azteca",
        stage="group", group="A", city="Mexico City",
    ),
    2002: Match(
        id=2002, league_id=_WC_LID, league_name=_WC, season=2026,
        utc_kickoff=_kickoff(6), status="scheduled",
        home_team=_ARGENTINA, away_team=_ENGLAND, venue="MetLife Stadium",
        stage="round_of_16", city="New York",
    ),
    2003: Match(
        id=2003, league_id=_WC_LID, league_name=_WC, season=2026,
        utc_kickoff=_kickoff(-30), status="finished",
        home_team=_FRANCE, away_team=_ENGLAND, venue="SoFi Stadium",
        stage="quarter", city="Los Angeles",
        home_goals=1, away_goals=1, extra_time_home=1, extra_time_away=1,
        penalty_home=4, penalty_away=3, winner="home",
    ),
}

_WC_FORMS: dict[int, TeamForm] = {
    6: TeamForm(team_id=6, matches_played=6, goals_for=2.2, goals_against=0.7,
                xg=2.0, xga=0.8, wins=5, draws=1, losses=0, recent_form="WWWDW"),
    2: TeamForm(team_id=2, matches_played=6, goals_for=2.0, goals_against=0.9,
                xg=1.9, xga=1.0, wins=4, draws=1, losses=1, recent_form="WDWLW"),
    26: TeamForm(team_id=26, matches_played=6, goals_for=1.9, goals_against=0.8,
                 xg=1.8, xga=0.9, wins=4, draws=2, losses=0, recent_form="WDWWD"),
    10: TeamForm(team_id=10, matches_played=6, goals_for=1.7, goals_against=1.0,
                 xg=1.6, xga=1.1, wins=4, draws=0, losses=2, recent_form="WLWWL"),
}
_FORMS.update(_WC_FORMS)

_WC_GROUPS: list[Group] = [
    Group(name="Group A", standings=[
        Standing(rank=1, team=_BRAZIL, points=7, played=3, win=2, draw=1, lose=0,
                 goals_for=6, goals_against=2, goal_diff=4, group="A", form="WWD"),
        Standing(rank=2, team=_FRANCE, points=6, played=3, win=2, draw=0, lose=1,
                 goals_for=5, goals_against=3, goal_diff=2, group="A", form="WLW"),
    ]),
]


def _odds_for(match: Match) -> MatchOdds:
    """Odds mock coerentes (com pequena margem) pros principais mercados."""
    markets: dict[str, MarketOdds] = {
        "1x2": MarketOdds("1x2", [
            Selection("bet365", "home", 2.10),
            Selection("bet365", "draw", 3.60),
            Selection("bet365", "away", 3.40),
        ]),
        "double_chance": MarketOdds("double_chance", [
            Selection("bet365", "home_draw", 1.33),
            Selection("bet365", "home_away", 1.30),
            Selection("bet365", "draw_away", 1.75),
        ]),
        "over_under": MarketOdds("over_under", [
            Selection("bet365", "over", 1.80, line=2.5),
            Selection("bet365", "under", 2.05, line=2.5),
        ]),
        "btts": MarketOdds("btts", [
            Selection("bet365", "yes", 1.70),
            Selection("bet365", "no", 2.15),
        ]),
    }
    return MatchOdds(match_id=match.id, fetched_at=time.time(), markets=markets)


# --- FootballDataProvider ---------------------------------------------------

def get_matches_by_date(date: str) -> list[Match]:
    # Geral + Copa do Mundo; o data_service filtra por contexto (league_ids).
    return list(_MATCHES.values()) + list(_WC_MATCHES.values())


def get_match(match_id: int) -> Optional[Match]:
    return _MATCHES.get(match_id) or _WC_MATCHES.get(match_id)


def get_season_matches(league_id: int, season: int) -> list[Match]:
    if league_id == _WC_LID:
        return list(_WC_MATCHES.values())
    return [m for m in _MATCHES.values() if m.league_id == league_id]


def get_match_statistics(match_id: int) -> Optional[MatchStatistics]:
    if match_id not in _MATCHES:
        return None
    return MatchStatistics(
        match_id=match_id,
        home={"shots": 14, "shots_on_target": 6, "corners": 7, "possession": 55,
              "yellow_cards": 1, "fouls": 10},
        away={"shots": 11, "shots_on_target": 4, "corners": 5, "possession": 45,
              "yellow_cards": 2, "fouls": 12},
    )


def get_team(team_id: int) -> Optional[Team]:
    return _TEAMS.get(team_id)


def get_teams(league_id: Optional[int] = None, search: Optional[str] = None) -> list[Team]:
    wc_ids = {6, 2, 26, 10}
    if league_id == _WC_LID:
        teams = [_TEAMS[i] for i in wc_ids]
    elif league_id is not None:
        teams = [t for t in _TEAMS.values() if t.id not in wc_ids]
    else:
        teams = list(_TEAMS.values())
    if search:
        s = search.strip().lower()
        teams = [t for t in teams if s in t.name.lower()]
    return teams


def get_groups(league_id: Optional[int] = None, season: Optional[int] = None) -> list[Group]:
    return _WC_GROUPS if league_id == _WC_LID else []


def get_players(team_id: Optional[int] = None, search: Optional[str] = None) -> list[PlayerSeasonStats]:
    players = list(_PLAYERS.values())
    if team_id is not None:
        players = [p for p in players if p.team_id == team_id]
    if search:
        s = search.strip().lower()
        players = [p for p in players if s in p.name.lower()]
    return players


def get_competition_players(league_id: int, season: int) -> list[PlayerSeasonStats]:
    return list(_PLAYERS.values())


def get_top_scorers(league_id: int, season: int) -> list[PlayerSeasonStats]:
    return sorted(_PLAYERS.values(), key=lambda p: p.goals, reverse=True)


def get_top_assists(league_id: int, season: int) -> list[PlayerSeasonStats]:
    return sorted(_PLAYERS.values(), key=lambda p: p.assists, reverse=True)


def get_team_form(team_id: int, last_n: int = 10, league_id: Optional[int] = None,
                  season: Optional[int] = None) -> Optional[TeamForm]:
    return _FORMS.get(team_id)


def get_player(player_id: int) -> Optional[PlayerSeasonStats]:
    return _PLAYERS.get(player_id)


def get_leagues() -> list[League]:
    return [_PL]


def get_standings(league_id: int, season: int) -> list[Standing]:
    teams = [_CITY, _LIVERPOOL, _ARSENAL, _CHELSEA]
    out = []
    for i, t in enumerate(teams, start=1):
        out.append(Standing(
            rank=i, team=t, points=25 - i * 2, played=10,
            win=8 - i, draw=1, lose=i, goals_for=25 - i * 2,
            goals_against=8 + i, goal_diff=(25 - i * 2) - (8 + i),
        ))
    return out


def get_lineups(match_id: int) -> list[Lineup]:
    m = _MATCHES.get(match_id)
    if m is None:
        return []
    return [
        Lineup(match_id=match_id, team_id=m.home_team.id, formation="4-3-3",
               starters=[LineupPlayer(player_id=301, name="Mohamed Salah",
                                      number=11, position="RW")]),
        Lineup(match_id=match_id, team_id=m.away_team.id, formation="4-2-3-1",
               starters=[LineupPlayer(player_id=302, name="Erling Haaland",
                                      number=9, position="ST")]),
    ]


def get_injuries(team_id: int) -> list[Injury]:
    return []


# --- OddsProvider -----------------------------------------------------------

def get_match_odds(match: Match) -> Optional[MatchOdds]:
    return _odds_for(match)


def get_h2h_odds(matches: list[Match]) -> dict[int, dict[str, float]]:
    out: dict[int, dict[str, float]] = {}
    for m in matches:
        odds = _odds_for(m)
        mo = odds.markets.get("1x2")
        if mo is None:
            continue
        out[m.id] = {s.name: s.price for s in mo.selections
                     if s.name in ("home", "draw", "away")}
    return out


# --- XgProvider -------------------------------------------------------------

def get_team_xg(team_id: int) -> Optional[tuple[float, float]]:
    form = _FORMS.get(team_id)
    if form is None or form.xg is None or form.xga is None:
        return None
    return (form.xg, form.xga)
