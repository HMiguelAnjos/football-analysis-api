"""Conversores domínio (providers.base) → schemas Pydantic do front.

Isola a tradução num lugar só — os services chamam estes helpers e devolvem
schemas no formato exato que o frontend espera.
"""

from __future__ import annotations

import statistics
from datetime import datetime
from typing import Optional

from src.providers.base import (
    League,
    Lineup,
    Match,
    MatchOdds,
    MatchStatistics,
    PlayerSeasonStats,
    Team,
    TeamForm,
)
from src.schemas.football_schemas import (
    LeagueSchema,
    LineupSlotSchema,
    MatchMainOddsSchema,
    MatchSchema,
    MatchSummarySchema,
    MatchOddsSchema,
    OddsEntrySchema,
    PlayerSchema,
    TeamFormStatsSchema,
    TeamMatchStatsSchema,
    TeamRefSchema,
    TeamSchema,
)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def team_ref(t: Team) -> TeamRefSchema:
    return TeamRefSchema(id=t.id, name=t.name, short_name=t.short_name or None,
                         logo_url=t.logo or None)


def _form_stats(form: Optional[TeamForm]) -> Optional[TeamFormStatsSchema]:
    if form is None:
        return None
    return TeamFormStatsSchema(
        recent_form=form.recent_form, matches_played=form.matches_played or None,
        wins=form.wins, draws=form.draws, losses=form.losses,
        goals_for=form.goals_for, goals_against=form.goals_against,
        xg=form.xg, xga=form.xga,
    )


def main_odds(odds: Optional[MatchOdds]) -> Optional[MatchMainOddsSchema]:
    """Extrai as odds principais (1x2 + O/U linha principal) do snapshot,
    fazendo média entre books por seleção."""
    if odds is None:
        return None

    def _avg(market: str, selection: str) -> Optional[float]:
        mo = odds.markets.get(market)
        if mo is None:
            return None
        prices = [s.price for s in mo.selections if s.name == selection]
        return round(statistics.mean(prices), 2) if prices else None

    ou = odds.markets.get("over_under")
    ou_line = None
    over = under = None
    if ou is not None and ou.selections:
        # Usa a linha mais comum (tipicamente 2.5).
        lines = [s.line for s in ou.selections if s.line is not None]
        ou_line = statistics.mode(lines) if lines else None
        overs = [s.price for s in ou.selections if s.name == "over" and s.line == ou_line]
        unders = [s.price for s in ou.selections if s.name == "under" and s.line == ou_line]
        over = round(statistics.mean(overs), 2) if overs else None
        under = round(statistics.mean(unders), 2) if unders else None

    out = MatchMainOddsSchema(
        home=_avg("1x2", "home"), draw=_avg("1x2", "draw"), away=_avg("1x2", "away"),
        over=over, under=under, over_under_line=ou_line,
    )
    # Se nada veio, devolve None (front trata).
    if all(v is None for v in out.model_dump().values()):
        return None
    return out


def match_to_schema(
    m: Match, odds: Optional[MatchOdds] = None,
    *, main: Optional[MatchMainOddsSchema] = None, context: str = "general",
) -> MatchSchema:
    """`main` (1x2 já agregado) tem prioridade sobre `odds` (snapshot completo)
    — usado pra embutir odds inline na lista sem carregar todos os mercados."""
    return MatchSchema(
        id=m.id, league_id=m.league_id or None, league_name=m.league_name or None,
        season=str(m.season) if m.season else None, kickoff=_iso(m.utc_kickoff),
        status=m.status, minute=m.minute,
        home=team_ref(m.home_team), away=team_ref(m.away_team),
        home_score=m.home_goals, away_score=m.away_goals,
        odds=main if main is not None else main_odds(odds),
        context=context, stage=m.stage, group=m.group,
        venue=m.venue or None, city=m.city or None,
        extra_time_home=m.extra_time_home, extra_time_away=m.extra_time_away,
        penalty_home=m.penalty_home, penalty_away=m.penalty_away, winner=m.winner,
    )


def standing_to_schema(s) -> "StandingSchema":
    from src.schemas.football_schemas import StandingSchema
    return StandingSchema(
        rank=s.rank, team=team_ref(s.team), points=s.points, played=s.played,
        win=s.win, draw=s.draw, lose=s.lose, goals_for=s.goals_for,
        goals_against=s.goals_against, goal_diff=s.goal_diff,
        group=s.group, form=s.form,
    )


def match_summary(m: Match) -> MatchSummarySchema:
    return MatchSummarySchema(
        id=m.id, kickoff=_iso(m.utc_kickoff), league_name=m.league_name or None,
        home=team_ref(m.home_team), away=team_ref(m.away_team),
        home_score=m.home_goals, away_score=m.away_goals, status=m.status,
    )


def team_to_schema(
    t: Team, *, form: Optional[TeamForm] = None,
    league: Optional[League] = None,
) -> TeamSchema:
    return TeamSchema(
        id=t.id, name=t.name, short_name=t.short_name or None, logo_url=t.logo or None,
        country=(league.country if league else None) or None,
        league_id=(league.id if league else None),
        league_name=(league.name if league else None),
        stats=_form_stats(form),
    )


def league_to_schema(
    lg: League, *, matches_today: Optional[int] = None,
    teams_count: Optional[int] = None,
) -> LeagueSchema:
    return LeagueSchema(
        id=lg.id, name=lg.name, country=lg.country or "",
        logo_url=lg.logo or None, season=str(lg.season) if lg.season else None,
        matches_today=matches_today, teams_count=teams_count,
    )


def player_to_schema(p: PlayerSeasonStats, *, team_name: Optional[str] = None) -> PlayerSchema:
    return PlayerSchema(
        id=p.player_id, name=p.name, team=team_name or (p.team_name or None),
        team_id=p.team_id or None, position=p.position or None, number=p.number,
        nt_appearances=p.nt_appearances,
        appearances=p.appearances, goals=p.goals, assists=p.assists,
        xg=p.xg, xa=p.xa, shots=p.shots, shots_on_target=p.shots_on_target,
        minutes=p.minutes, yellow_cards=p.yellow_cards or None,
        red_cards=p.red_cards or None, rating=p.rating,
        key_passes=p.key_passes, passes=p.passes, pass_accuracy=p.pass_accuracy,
        dribbles=p.dribbles, dribbles_attempts=p.dribbles_attempts,
        tackles=p.tackles, interceptions=p.interceptions,
        duels=p.duels, duels_won=p.duels_won,
        fouls_drawn=p.fouls_drawn, fouls_committed=p.fouls_committed,
        penalty_scored=p.penalty_scored,
        goals_per90=round(p.goals_per90, 3),
        shots_per90=round(p.shots_per90, 3),
    )


def team_match_stats(
    team: Team, form: Optional[TeamForm], side: Optional[dict],
) -> TeamMatchStatsSchema:
    """Combina forma (médias temporada) + stats do jogo (side dict) numa visão
    pra tela de análise."""
    side = side or {}
    return TeamMatchStatsSchema(
        team=team_ref(team),
        recent_form=form.recent_form if form else None,
        goals_for=form.goals_for if form else None,
        goals_against=form.goals_against if form else None,
        xg=form.xg if form else None,
        xga=form.xga if form else None,
        shots=side.get("total_shots") or side.get("shots") or (form.shots_for if form else None),
        shots_on_target=side.get("shots_on_goal") or side.get("shots_on_target")
        or (form.shots_on_target_for if form else None),
        corners=side.get("corner_kicks") or side.get("corners")
        or (form.corners_for if form else None),
        cards=(side.get("yellow_cards", 0) + side.get("red_cards", 0)) or
        (form.cards_for if form else None),
    )


def odds_entries(odds: MatchOdds) -> list[OddsEntrySchema]:
    """Achata o snapshot (markets→selections) numa lista de cotações."""
    out: list[OddsEntrySchema] = []
    for market_key, mo in odds.markets.items():
        for s in mo.selections:
            out.append(OddsEntrySchema(
                bookmaker=s.bookmaker, market=market_key, selection=s.name,
                line=s.line, odd=s.price,
            ))
    return out


def odds_to_schema(odds: MatchOdds) -> MatchOddsSchema:
    return MatchOddsSchema(match_id=odds.match_id, entries=odds_entries(odds))


def lineup_slots(lu: Lineup) -> list[LineupSlotSchema]:
    slots = []
    for p in lu.starters:
        slots.append(LineupSlotSchema(player_name=p.name, position=p.position or None,
                                      number=p.number, is_starter=True))
    for p in lu.substitutes:
        slots.append(LineupSlotSchema(player_name=p.name, position=p.position or None,
                                      number=p.number, is_starter=False))
    return slots
