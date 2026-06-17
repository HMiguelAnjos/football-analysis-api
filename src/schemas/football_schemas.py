"""
Schemas públicos dos dados de futebol — ESPELHAM o contrato do frontend
(football-analysis-front/src/types/index.ts).

Campos opcionais de propósito: a UI degrada com elegância quando o backend
ainda não popula algo. Renomear/remover campo = breaking change com o front.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


# ─── Times ───────────────────────────────────────────────────────────────
class TeamRefSchema(BaseModel):
    id: int
    name: str
    short_name: Optional[str] = None
    logo_url: Optional[str] = None


class TeamFormStatsSchema(BaseModel):
    recent_form: Optional[str] = None
    matches_played: Optional[int] = None
    wins: Optional[int] = None
    draws: Optional[int] = None
    losses: Optional[int] = None
    goals_for: Optional[float] = None
    goals_against: Optional[float] = None
    xg: Optional[float] = None
    xga: Optional[float] = None


class MatchSummarySchema(BaseModel):
    id: int
    kickoff: Optional[str] = None
    league_name: Optional[str] = None
    home: TeamRefSchema
    away: TeamRefSchema
    home_score: Optional[int] = None
    away_score: Optional[int] = None
    status: Optional[str] = None


class TeamSchema(TeamRefSchema):
    country: Optional[str] = None
    league_id: Optional[int] = None
    league_name: Optional[str] = None
    stats: Optional[TeamFormStatsSchema] = None
    recent_matches: Optional[list[MatchSummarySchema]] = None
    upcoming_matches: Optional[list[MatchSummarySchema]] = None


# ─── Ligas ───────────────────────────────────────────────────────────────
class LeagueSchema(BaseModel):
    id: int
    name: str
    country: str = ""
    logo_url: Optional[str] = None
    season: Optional[str] = None
    matches_today: Optional[int] = None
    teams_count: Optional[int] = None


# ─── Jogos ───────────────────────────────────────────────────────────────
class MatchMainOddsSchema(BaseModel):
    home: Optional[float] = None
    draw: Optional[float] = None
    away: Optional[float] = None
    over: Optional[float] = None
    under: Optional[float] = None
    over_under_line: Optional[float] = None


class MatchSchema(BaseModel):
    id: int
    league_id: Optional[int] = None
    league_name: Optional[str] = None
    country: Optional[str] = None
    season: Optional[str] = None
    kickoff: Optional[str] = None
    status: str
    minute: Optional[int] = None
    home: TeamRefSchema
    away: TeamRefSchema
    home_score: Optional[int] = None
    away_score: Optional[int] = None
    odds: Optional[MatchMainOddsSchema] = None
    # ── Torneio (preenchido no contexto Copa do Mundo) ────────────────────
    context: str = "general"
    stage: Optional[str] = None
    group: Optional[str] = None
    venue: Optional[str] = None
    city: Optional[str] = None
    extra_time_home: Optional[int] = None
    extra_time_away: Optional[int] = None
    penalty_home: Optional[int] = None
    penalty_away: Optional[int] = None
    winner: Optional[str] = None


class MatchListResponse(BaseModel):
    date: Optional[str] = None
    matches: list[MatchSchema]


# ─── Torneio: grupos, classificação, chaveamento, contexto ────────────────
class StandingSchema(BaseModel):
    rank: int
    team: TeamRefSchema
    points: int
    played: int
    win: int
    draw: int
    lose: int
    goals_for: int
    goals_against: int
    goal_diff: int
    group: Optional[str] = None
    form: Optional[str] = None


class GroupSchema(BaseModel):
    name: str
    standings: list[StandingSchema]


class BracketTieSchema(BaseModel):
    """Um confronto do mata-mata."""
    match_id: int
    stage: str
    home: TeamRefSchema
    away: TeamRefSchema
    home_score: Optional[int] = None
    away_score: Optional[int] = None
    penalty_home: Optional[int] = None
    penalty_away: Optional[int] = None
    winner: Optional[str] = None
    status: str
    kickoff: Optional[str] = None


class BracketStageSchema(BaseModel):
    stage: str
    label: str
    ties: list[BracketTieSchema]


class ContextSchema(BaseModel):
    key: str
    label: str
    active: bool = False
    features: list[str] = []


class MarketLineSchema(BaseModel):
    """Uma seleção de mercado com probabilidade do modelo + valor (se há odd)."""
    market: str
    selection: str
    line: Optional[float] = None
    model_prob: float
    fair_odd: float
    odd: Optional[float] = None
    edge: Optional[float] = None
    confidence: Optional[float] = None


# ─── Jogadores ───────────────────────────────────────────────────────────
class PlayerSchema(BaseModel):
    id: int
    name: str
    team: Optional[str] = None
    team_id: Optional[int] = None
    position: Optional[str] = None
    number: Optional[int] = None      # número da camisa
    nt_appearances: int = 0           # jogos pela seleção (proxy de titular)
    appearances: int = 0
    goals: int = 0
    assists: int = 0
    xg: Optional[float] = None
    xa: Optional[float] = None
    shots: float = 0.0
    shots_on_target: float = 0.0
    minutes: int = 0
    yellow_cards: Optional[int] = None
    red_cards: Optional[int] = None
    status: Optional[str] = None
    # ── Parâmetros analíticos extra ───────────────────────────────────────
    rating: Optional[float] = None
    key_passes: int = 0
    passes: int = 0
    pass_accuracy: Optional[float] = None
    dribbles: int = 0
    dribbles_attempts: int = 0
    tackles: int = 0
    interceptions: int = 0
    duels: int = 0
    duels_won: int = 0
    fouls_drawn: int = 0
    fouls_committed: int = 0
    penalty_scored: int = 0
    goals_per90: float = 0.0
    shots_per90: float = 0.0
    # Índices compostos (0–100) — preenchidos no ranking por índice.
    ipo: Optional[float] = None
    icj: Optional[float] = None
    idef: Optional[float] = None
    iip: Optional[float] = None


# ─── Estatísticas de partida (tela de análise) ───────────────────────────
class TeamMatchStatsSchema(BaseModel):
    team: TeamRefSchema
    recent_form: Optional[str] = None
    home_away_form: Optional[str] = None
    goals_for: Optional[float] = None
    goals_against: Optional[float] = None
    xg: Optional[float] = None
    xga: Optional[float] = None
    shots: Optional[float] = None
    shots_on_target: Optional[float] = None
    corners: Optional[float] = None
    cards: Optional[float] = None


class MatchInjurySchema(BaseModel):
    player_name: str
    team_side: str
    reason: Optional[str] = None
    status: Optional[str] = None


class LineupSlotSchema(BaseModel):
    player_name: str
    position: Optional[str] = None
    number: Optional[int] = None
    is_starter: bool = True


class MatchStatisticsSchema(BaseModel):
    match_id: int
    home: TeamMatchStatsSchema
    away: TeamMatchStatsSchema
    injuries: Optional[list[MatchInjurySchema]] = None
    probable_lineup_home: Optional[list[LineupSlotSchema]] = None
    probable_lineup_away: Optional[list[LineupSlotSchema]] = None
    # Recomendação do modelo pra esta partida (preenchida pelo engine).
    recommendation: Optional["RecommendationOut"] = None
    model_note: Optional[str] = None
    updated_at: Optional[str] = None


# ─── Odds ────────────────────────────────────────────────────────────────
class OddsEntrySchema(BaseModel):
    bookmaker: str
    market: str
    selection: str
    line: Optional[float] = None
    odd: float
    previous_odd: Optional[float] = None
    movement: Optional[float] = None
    updated_at: Optional[str] = None


class MatchOddsSchema(BaseModel):
    match_id: int
    entries: list[OddsEntrySchema]


class OddsBoardItemSchema(BaseModel):
    match: MatchSummarySchema
    best: OddsEntrySchema
    entries: list[OddsEntrySchema]


# ─── Recomendações ───────────────────────────────────────────────────────
class RecommendationOut(BaseModel):
    id: int
    match: str
    match_id: Optional[int] = None
    league: Optional[str] = None
    market: str
    selection: str
    line: Optional[float] = None
    odd: Optional[float] = None
    fair_odd: Optional[float] = None
    model_prob: Optional[float] = None
    implied_prob: Optional[float] = None
    edge: Optional[float] = None
    confidence: Optional[str] = None
    status: Optional[str] = None
    reason: Optional[str] = None
    bookmaker: Optional[str] = None
    created_by_name: Optional[str] = None
    created_at: str
    # Contexto + torneio.
    context: str = "general"
    stage: Optional[str] = None
    group: Optional[str] = None
    # Kickoff do jogo (ISO) — usado pra agrupar/ordenar por jogo no front.
    kickoff: Optional[str] = None
    # Props de jogador: time do jogador + número da camisa (descrição melhor).
    team: Optional[str] = None
    player_number: Optional[int] = None


# ─── Entradas ao vivo (analista) ─────────────────────────────────────────
class LivePickOut(BaseModel):
    id: int
    match: str
    match_id: Optional[int] = None
    league: Optional[str] = None
    market: str
    selection: str
    line: Optional[float] = None
    odd: Optional[float] = None
    confidence: Optional[str] = None
    reason: Optional[str] = None
    status: str
    analyst_name: Optional[str] = None
    created_at: str
    settled_at: Optional[str] = None


class LivePickCreate(BaseModel):
    match: str
    match_id: Optional[int] = None
    league: Optional[str] = None
    market: str
    selection: str
    line: Optional[float] = None
    odd: Optional[float] = None
    confidence: Optional[str] = None
    reason: str = ""


class LivePickUpdate(BaseModel):
    status: Optional[str] = None
    odd: Optional[float] = None
    confidence: Optional[str] = None
    reason: Optional[str] = None


# ─── Resultados / performance ────────────────────────────────────────────
class PickResultOut(BaseModel):
    id: int
    match: str
    league: Optional[str] = None
    market: str
    selection: str
    odd: Optional[float] = None
    result: str
    profit: Optional[float] = None
    analyst_name: Optional[str] = None
    created_at: str
    settled_at: Optional[str] = None


class PerfBreakdownItem(BaseModel):
    key: str
    label: Optional[str] = None
    won: int = 0
    lost: int = 0
    push: int = 0
    pending: int = 0
    total: int = 0
    hit_rate: Optional[float] = None
    roi: Optional[float] = None
    profit: Optional[float] = None


class PerfTotals(BaseModel):
    total: int = 0
    won: int = 0
    lost: int = 0
    push: int = 0
    pending: int = 0
    hit_rate: Optional[float] = None
    roi: Optional[float] = None
    profit: Optional[float] = None


class PerfTimelinePoint(BaseModel):
    period: str
    profit: float = 0.0
    cumulative_profit: Optional[float] = None
    picks: Optional[int] = None
    hit_rate: Optional[float] = None


class PerformanceSummary(BaseModel):
    totals: PerfTotals
    by_market: list[PerfBreakdownItem] = []
    by_league: list[PerfBreakdownItem] = []
    by_analyst: Optional[list[PerfBreakdownItem]] = None
    timeline: Optional[list[PerfTimelinePoint]] = None


# ─── Requests do engine ──────────────────────────────────────────────────
class GenerateRequest(BaseModel):
    date: Optional[str] = None
    match_ids: Optional[list[int]] = None
    min_edge: Optional[float] = None
    min_confidence: Optional[float] = None
    persist: bool = True


# RecommendationOut é referenciado por MatchStatisticsSchema antes de ser
# definido (forward ref) — resolve agora que ambos existem.
MatchStatisticsSchema.model_rebuild()
