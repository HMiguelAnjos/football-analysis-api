"""
Modelos do banco — Football Analytics API.

- User: autenticação/roles (reaproveitado, sem mudança de domínio).
- FootballRecommendation: recomendação de valor (engine OU analista) com todo
  o modelo de edge + ciclo de vida (pending → hit/miss/push).
- FootballPickResult: ledger imutável de resultados (snapshot no settlement)
  pra relatórios de performance — separado do working set mutável acima.
- Football{Match,Team,Player,Odds}: tabelas de SNAPSHOT/cache (payload JSON)
  que persistem dados de provider entre restarts e cortam chamadas externas.

Sem Alembic: create_all() cria as tabelas no boot; ALTERs idempotentes em
src/db/database.py acompanham mudanças de coluna.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import (
    Boolean, DateTime, Float, Index, Integer, JSON, String, Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.db.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ─── Auth ────────────────────────────────────────────────────────────────


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="user")
    plan: Mapped[str] = mapped_column(String(32), nullable=False, default="free")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )


# ─── Recomendações de valor ──────────────────────────────────────────────


class FootballRecommendation(Base):
    """Recomendação de aposta de valor.

    Origem (`source`):
      • engine  — gerada automaticamente pelo motor de probabilidade.
      • analyst — criada manualmente por um analista/admin.

    Ciclo (`status`):
      • pending — aguardando o jogo terminar.
      • hit     — green (a seleção bateu).
      • miss     — red (não bateu).
      • push    — anulada (devolve a aposta — ex: handicap exato).
      • void    — não foi possível liquidar.
    """
    __tablename__ = "football_recommendations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Contexto de competição: 'general' | 'world_cup'. Isola picks da Copa dos
    # picks de futebol geral (filtro em todas as queries).
    context: Mapped[str] = mapped_column(String(20), nullable=False, default="general", index=True)
    stage: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    group: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)

    # ── Identificação do jogo / aposta ───────────────────────────────────
    match_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    league: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    home_team: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    away_team: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    market: Mapped[str] = mapped_column(String(40), nullable=False)
    selection: Mapped[str] = mapped_column(String(80), nullable=False)
    line: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    bookmaker: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)

    # ── Modelo de edge ───────────────────────────────────────────────────
    odd: Mapped[float] = mapped_column(Float, nullable=False)
    fair_odd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    implied_probability: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    model_probability: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    edge: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    confidence_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    recommendation_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # ── Ciclo de vida / settlement ───────────────────────────────────────
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="engine")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    was_shown_to_user: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    actual_result: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True,
    )
    settled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    kickoff_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── Autoria (quando source=analyst) ──────────────────────────────────
    created_by_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_by_name: Mapped[str] = mapped_column(String(120), nullable=False, default="")

    __table_args__ = (
        # 1 linha por (match, market, selection, line, bookmaker) — UPSERT
        # skip evita flood quando o engine roda em polling.
        UniqueConstraint(
            "match_id", "market", "selection", "line", "bookmaker",
            name="uq_football_rec_match_market_selection_line_book",
        ),
        Index("idx_football_rec_status_generated", "status", "generated_at"),
        Index("idx_football_rec_active_generated", "is_active", "generated_at"),
    )


class FootballPickResult(Base):
    """Ledger IMUTÁVEL de resultados liquidados (snapshot no settlement).

    Escrito pelo worker quando uma recomendação fecha. Base dos relatórios de
    performance — separado do working set pra preservar histórico mesmo se a
    recomendação for editada/removida.
    """
    __tablename__ = "football_pick_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recommendation_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    context: Mapped[str] = mapped_column(String(20), nullable=False, default="general", index=True)
    stage: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    match_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    match: Mapped[str] = mapped_column(String(180), nullable=False, default="")
    league: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="engine")
    analyst_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    market: Mapped[str] = mapped_column(String(40), nullable=False)
    selection: Mapped[str] = mapped_column(String(80), nullable=False)
    line: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    odd: Mapped[float] = mapped_column(Float, nullable=False)
    edge: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    confidence_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # hit|miss|push|void
    actual_result: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    # Retorno em unidades (stake=1): hit=odd-1, miss=-1, push/void=0.
    profit_units: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    was_shown_to_user: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    settled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True,
    )

    __table_args__ = (
        UniqueConstraint("recommendation_id", name="uq_football_pick_result_rec"),
        Index("idx_football_pick_result_market", "market", "settled_at"),
    )


# ─── Recomendações AO VIVO (in-play, foco escanteios) ────────────────────


class FootballLiveRecommendation(Base):
    """Recomendação gerada DURANTE a partida (in-play), priorizando mercados de
    escanteios. Persiste tudo o que sustentou a decisão (stats ao vivo) pra
    auditoria e settlement.

    rec_type: corners_over | team_corners_over | next_corner | shots_on_target
              | goal_pressure | avoid_entry
    status:   pending (ainda valendo) | settled | expired
    result:   pending | green | red | void
    """
    __tablename__ = "football_live_recommendations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    context: Mapped[str] = mapped_column(String(20), nullable=False, default="general", index=True)

    # ── Jogo + estado no momento da recomendação ─────────────────────────
    match_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    league: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    home_team: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    away_team: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    minute: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    home_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    away_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # ── Recomendação ─────────────────────────────────────────────────────
    rec_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    market: Mapped[str] = mapped_column(String(60), nullable=False)
    line: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    odd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)  # 0-10
    recommendation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    stats_used: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)

    # ── Ciclo de vida / resultado ────────────────────────────────────────
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", index=True)
    result: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False,
    )
    settled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # 1 linha por (jogo, tipo, linha) — re-gerar no mesmo tick faz UPDATE
        # (atualiza minuto/confiança/stats), não duplica.
        UniqueConstraint("match_id", "rec_type", "line",
                         name="uq_football_live_rec_match_type_line"),
        Index("idx_football_live_rec_status", "status", "created_at"),
    )


# ─── Snapshot / cache de dados de provider (corta chamadas externas) ──────


class FootballTeam(Base):
    __tablename__ = "football_teams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # team_id do provider
    name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    short_name: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    logo: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    payload: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False,
    )


class FootballPlayer(Base):
    __tablename__ = "football_players"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # player_id do provider
    name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    team_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    payload: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False,
    )


class FootballMatch(Base):
    __tablename__ = "football_matches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # fixture_id do provider
    league_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    season: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="scheduled")
    kickoff_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    home_team_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    away_team_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    payload: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False,
    )

    __table_args__ = (
        Index("idx_football_matches_league_kickoff", "league_id", "kickoff_at"),
    )


class FootballOdds(Base):
    __tablename__ = "football_odds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    match_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False,
    )
    payload: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
