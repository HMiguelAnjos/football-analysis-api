"""
Modelos internos normalizados + interfaces (Protocols) dos providers.

Estes dataclasses são o CONTRATO INTERNO do sistema: services e engines só
falam essa linguagem, nunca o payload cru de um provider específico. Trocar
api-football por sportmonks (ou qualquer outro) = escrever um novo provider
que devolve estes mesmos tipos. Zero acoplamento a uma fonte.

Os Protocols definem o que cada categoria de provider precisa oferecer. Não
exigem herança — qualquer classe com os métodos certos satisfaz (duck typing
verificado estaticamente).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# Modelos de domínio (normalizados, independentes de provider)
# ---------------------------------------------------------------------------


@dataclass
class League:
    id: int
    name: str
    country: str = ""
    season: int = 0
    logo: str = ""


@dataclass
class Team:
    id: int
    name: str
    short_name: str = ""
    logo: str = ""


@dataclass
class Match:
    id: int
    league_id: int
    league_name: str
    season: int
    utc_kickoff: Optional[datetime]
    status: str                      # "scheduled" | "live" | "finished"
    home_team: Team
    away_team: Team
    home_goals: Optional[int] = None
    away_goals: Optional[int] = None
    minute: Optional[int] = None     # minuto do jogo, se ao vivo
    venue: str = ""
    # ── Campos de torneio (preenchidos só no contexto Copa do Mundo) ──────
    stage: Optional[str] = None      # "group" | "round_of_16" | "quarter" | ...
    group: Optional[str] = None      # "A", "B", ... (fase de grupos)
    city: str = ""
    extra_time_home: Optional[int] = None
    extra_time_away: Optional[int] = None
    penalty_home: Optional[int] = None
    penalty_away: Optional[int] = None
    winner: Optional[str] = None     # "home" | "away" | None (empate/sem def.)


@dataclass
class TeamForm:
    """Forma recente agregada de um time — base do modelo de probabilidade.

    Médias por jogo (todas opcionais; None = fonte não forneceu). xg/xga vêm
    do provider de xG quando disponível; caem em goals_for/against quando não.
    """
    team_id: int
    matches_played: int = 0
    goals_for: float = 0.0           # média de gols marcados / jogo
    goals_against: float = 0.0       # média de gols sofridos / jogo
    xg: Optional[float] = None       # expected goals / jogo
    xga: Optional[float] = None      # expected goals against / jogo
    # Resultados acumulados + sequência recente ("WWDLW", recente à esquerda).
    wins: Optional[int] = None
    draws: Optional[int] = None
    losses: Optional[int] = None
    recent_form: Optional[str] = None
    # Splits casa/fora (médias).
    home_goals_for: Optional[float] = None
    home_goals_against: Optional[float] = None
    away_goals_for: Optional[float] = None
    away_goals_against: Optional[float] = None
    # Médias auxiliares pros mercados secundários.
    corners_for: Optional[float] = None
    corners_against: Optional[float] = None
    cards_for: Optional[float] = None
    shots_for: Optional[float] = None
    shots_on_target_for: Optional[float] = None
    possession: Optional[float] = None
    # Pontos por jogo nos últimos N (proxy de forma). 0-3.
    points_per_game: Optional[float] = None
    rest_days: Optional[int] = None  # descanso desde o último jogo


@dataclass
class PlayerSeasonStats:
    player_id: int
    name: str
    team_id: int
    position: str = ""
    number: Optional[int] = None     # número da camisa (vem do elenco/squad)
    appearances: int = 0
    minutes: int = 0
    goals: int = 0
    assists: int = 0
    shots: float = 0.0               # total na temporada
    shots_on_target: float = 0.0
    xg: Optional[float] = None
    xa: Optional[float] = None
    # ── Parâmetros analíticos extra (api-football) ────────────────────────
    rating: Optional[float] = None        # nota média
    key_passes: int = 0                   # passes que viram finalização
    passes: int = 0
    pass_accuracy: Optional[float] = None
    dribbles: int = 0                     # dribles certos
    dribbles_attempts: int = 0
    tackles: int = 0
    interceptions: int = 0
    duels: int = 0
    duels_won: int = 0
    fouls_drawn: int = 0                  # faltas sofridas
    fouls_committed: int = 0              # faltas cometidas
    yellow_cards: int = 0
    red_cards: int = 0
    penalty_scored: int = 0
    team_name: str = ""

    @property
    def goals_per90(self) -> float:
        return self.goals / (self.minutes / 90) if self.minutes else 0.0

    @property
    def shots_per90(self) -> float:
        return self.shots / (self.minutes / 90) if self.minutes else 0.0

    @property
    def shots_on_target_per90(self) -> float:
        return self.shots_on_target / (self.minutes / 90) if self.minutes else 0.0

    @property
    def assists_per90(self) -> float:
        return self.assists / (self.minutes / 90) if self.minutes else 0.0


@dataclass
class Standing:
    rank: int
    team: Team
    points: int
    played: int
    win: int
    draw: int
    lose: int
    goals_for: int
    goals_against: int
    goal_diff: int
    group: Optional[str] = None      # "A", "B"... no contexto de grupos
    form: Optional[str] = None       # "WWDLW" (recente à esquerda)


@dataclass
class Group:
    """Um grupo da fase de grupos + sua classificação."""
    name: str                        # "Group A"
    standings: list[Standing] = field(default_factory=list)


@dataclass
class MatchStatistics:
    """Estatísticas agregadas de UM jogo (placar ao vivo / final)."""
    match_id: int
    home: dict[str, float] = field(default_factory=dict)
    away: dict[str, float] = field(default_factory=dict)


@dataclass
class LineupPlayer:
    player_id: int
    name: str
    number: Optional[int] = None
    position: str = ""
    is_starter: bool = True


@dataclass
class Lineup:
    match_id: int
    team_id: int
    formation: str = ""
    starters: list[LineupPlayer] = field(default_factory=list)
    substitutes: list[LineupPlayer] = field(default_factory=list)


@dataclass
class Injury:
    player_id: int
    name: str
    team_id: int
    reason: str = ""
    type: str = ""                   # "injury" | "suspension"


@dataclass
class Selection:
    """Uma cotação de mercado: seleção + preço de UM bookmaker."""
    bookmaker: str
    name: str                        # ex: "Home", "Over", "Yes", "Liverpool"
    price: float                     # odd decimal
    line: Optional[float] = None     # handicap/total (ex: 2.5)


@dataclass
class MarketOdds:
    """Todas as cotações de UM mercado, de todos os books."""
    market: str                      # ex: "h2h", "totals", "btts"
    selections: list[Selection] = field(default_factory=list)


@dataclass
class MatchOdds:
    """Snapshot de odds de um jogo, por mercado."""
    match_id: int
    fetched_at: float = 0.0          # epoch
    markets: dict[str, MarketOdds] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Interfaces (Protocols)
# ---------------------------------------------------------------------------


@runtime_checkable
class FootballDataProvider(Protocol):
    """Fonte de jogos, times, jogadores, stats, lineups, standings."""

    name: str

    def get_matches_by_date(self, date: str) -> list[Match]: ...

    def get_season_matches(self, league_id: int, season: int) -> list[Match]:
        """Todos os jogos de uma competição/temporada (torneios). Vazio se n/a."""
        ...

    def get_match(self, match_id: int) -> Optional[Match]: ...

    def get_match_statistics(self, match_id: int) -> Optional[MatchStatistics]: ...

    def get_team(self, team_id: int) -> Optional[Team]: ...

    def get_team_form(self, team_id: int, last_n: int = 10) -> Optional[TeamForm]: ...

    def get_recent_results(self, team_id: int, last_n: int = 15) -> list[Match]:
        """Últimos N jogos FINALIZADOS do time, em TODAS as competições (base dos
        ratings de força). Vazio quando a fonte não suporta."""
        ...

    def get_live_matches(self) -> list[Match]:
        """TODOS os jogos ao vivo agora, numa única chamada barata (base do
        refresh de odds por evento). Vazio quando a fonte não suporta."""
        ...

    def get_player(self, player_id: int) -> Optional[PlayerSeasonStats]: ...

    def get_leagues(self) -> list[League]: ...

    def get_standings(self, league_id: int, season: int) -> list[Standing]: ...

    def get_groups(self, league_id: int, season: int) -> list[Group]:
        """Classificação agrupada por grupo (torneios). Vazio se não aplicável."""
        ...

    def get_lineups(self, match_id: int) -> list[Lineup]: ...

    def get_injuries(self, team_id: int) -> list[Injury]: ...


@runtime_checkable
class OddsProvider(Protocol):
    """Fonte de odds de futebol."""

    name: str

    def get_match_odds(self, match: Match) -> Optional[MatchOdds]: ...

    def get_h2h_odds(self, matches: list[Match]) -> dict[int, dict[str, float]]:
        """1x2 (home/draw/away) em LOTE pros jogos dados — barato, pra exibir
        odds inline no card. Devolve {match_id: {home, draw, away}}."""
        ...


@runtime_checkable
class XgProvider(Protocol):
    """Fonte de métricas avançadas (xG/xA) — opcional."""

    name: str

    def get_team_xg(self, team_id: int) -> Optional[tuple[float, float]]:
        """Devolve (xg_por_jogo, xga_por_jogo) ou None."""
        ...
