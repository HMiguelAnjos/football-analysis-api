"""Schemas das recomendações de futebol — contratos públicos da API.

RecommendationOut é contrato público (front consome) — mudar/remover campo é
breaking change.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator

# Mercados aceitos numa entrada manual de analista.
VALID_MARKETS = {
    "1x2", "double_chance", "dnb", "over_under", "btts", "asian_handicap",
    "team_total_home", "team_total_away", "corners", "cards",
    "anytime_scorer", "player_shots", "player_shots_on_target", "player_assists",
    "correct_score",
}


class RecommendationOut(BaseModel):
    id: int
    match_id: int
    league: str
    home_team: str
    away_team: str
    market: str
    selection: str
    line: Optional[float] = None
    bookmaker: Optional[str] = None
    odd: float
    fair_odd: Optional[float] = None
    implied_probability: Optional[float] = None
    model_probability: Optional[float] = None
    edge: Optional[float] = None
    confidence_score: Optional[float] = None
    recommendation_reason: str = ""
    source: str = "engine"
    status: str = "pending"
    was_shown_to_user: bool = True
    actual_result: Optional[str] = None
    generated_at: datetime
    settled_at: Optional[datetime] = None
    created_by_name: str = ""

    class Config:
        from_attributes = True


class RecommendationCreate(BaseModel):
    """Entrada MANUAL de um analista (source=analyst). Os campos de modelo
    (fair_odd/edge/...) ficam vazios — é uma recomendação editorial."""
    match_id: int
    league: str = ""
    home_team: str = Field(min_length=1, max_length=80)
    away_team: str = Field(min_length=1, max_length=80)
    market: str
    selection: str = Field(min_length=1, max_length=80)
    line: Optional[float] = None
    odd: float = Field(gt=1.0, lt=1000)
    bookmaker: Optional[str] = Field(default=None, max_length=40)
    confidence_score: Optional[float] = Field(default=None, ge=0, le=100)
    recommendation_reason: str = Field(default="", max_length=500)

    @field_validator("market")
    @classmethod
    def _valid_market(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in VALID_MARKETS:
            raise ValueError(
                f"market inválido (use um de: {', '.join(sorted(VALID_MARKETS))})"
            )
        return v


class GenerateRequest(BaseModel):
    """Dispara a geração de recomendações pelo engine.

    Sem corpo = jogos do dia (data atual UTC). Com `date` ou `match_ids`,
    restringe o escopo. `min_edge`/`min_confidence` sobrescrevem os thresholds.
    """
    date: Optional[str] = Field(default=None, description="YYYY-MM-DD (UTC)")
    match_ids: Optional[list[int]] = None
    min_edge: Optional[float] = None
    min_confidence: Optional[float] = Field(default=None, ge=0, le=100)
    persist: bool = True


class GenerateResponse(BaseModel):
    generated: int
    persisted: int
    matches_analyzed: int
    recommendations: list[RecommendationOut]
