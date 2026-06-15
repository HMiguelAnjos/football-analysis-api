"""Provider de fixtures — reexporta as funções do módulo provider pra que
`from src.providers import fixtures` satisfaça os Protocols diretamente."""

from src.providers.fixtures.provider import (  # noqa: F401
    get_competition_players,
    get_groups,
    get_h2h_odds,
    get_injuries,
    get_leagues,
    get_lineups,
    get_match,
    get_match_odds,
    get_match_statistics,
    get_matches_by_date,
    get_player,
    get_players,
    get_season_matches,
    get_standings,
    get_team,
    get_team_form,
    get_team_xg,
    get_teams,
    get_top_assists,
    get_top_scorers,
    name,
)
