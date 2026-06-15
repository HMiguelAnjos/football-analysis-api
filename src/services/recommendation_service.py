"""
RecommendationService — persistência das recomendações de futebol.

CRUD + UPSERT-skip sobre `football_recommendations`. Permissão é checada na
borda (main.py: require_permission) — aqui é só persistência.

UPSERT-skip: o engine pode rodar em polling; se já existe linha pra
(match, market, selection, line, bookmaker), preserva o PRIMEIRO snapshot
(o momento mais acionável) em vez de duplicar/sobrescrever.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import FootballRecommendation
from src.recommendation.engine import RecommendationCandidate


def _key_filter(c: RecommendationCandidate):
    return (
        FootballRecommendation.match_id == c.match_id,
        FootballRecommendation.market == c.market,
        FootballRecommendation.selection == c.selection,
        FootballRecommendation.line.is_(c.line) if c.line is None
        else FootballRecommendation.line == c.line,
        FootballRecommendation.bookmaker.is_(c.bookmaker) if c.bookmaker is None
        else FootballRecommendation.bookmaker == c.bookmaker,
    )


def upsert_from_candidate(
    db: Session,
    candidate: RecommendationCandidate,
    *,
    context: str = "general",
    was_shown_to_user: bool = True,
    kickoff_at: Optional[datetime] = None,
) -> tuple[FootballRecommendation, bool]:
    """Insere a recomendação do engine se ainda não existe. Devolve
    (linha, created). created=False quando já havia (skip)."""
    existing = db.scalar(select(FootballRecommendation).where(*_key_filter(candidate)))
    if existing is not None:
        return existing, False

    rec = FootballRecommendation(
        context=context, stage=candidate.stage, group=candidate.group,
        match_id=candidate.match_id,
        league=candidate.league,
        home_team=candidate.home_team,
        away_team=candidate.away_team,
        market=candidate.market,
        selection=candidate.selection,
        line=candidate.line,
        bookmaker=candidate.bookmaker,
        odd=candidate.odd,
        fair_odd=candidate.fair_odd,
        implied_probability=candidate.implied_probability,
        model_probability=candidate.model_probability,
        edge=candidate.edge,
        confidence_score=candidate.confidence_score,
        recommendation_reason=candidate.recommendation_reason,
        source="engine",
        status="pending",
        is_active=True,
        was_shown_to_user=was_shown_to_user,
        kickoff_at=kickoff_at,
    )
    db.add(rec)
    db.commit()
    db.refresh(rec)
    return rec, True


def create_manual(
    db: Session,
    *,
    match_id: int,
    league: str,
    home_team: str,
    away_team: str,
    market: str,
    selection: str,
    line: Optional[float],
    odd: float,
    bookmaker: Optional[str],
    confidence_score: Optional[float],
    recommendation_reason: str,
    created_by_id: int,
    created_by_name: str,
) -> FootballRecommendation:
    """Entrada manual de analista (source=analyst)."""
    rec = FootballRecommendation(
        match_id=match_id, league=league, home_team=home_team, away_team=away_team,
        market=market, selection=selection, line=line, odd=odd, bookmaker=bookmaker,
        confidence_score=confidence_score, recommendation_reason=recommendation_reason,
        source="analyst", status="pending", is_active=True, was_shown_to_user=True,
        created_by_id=created_by_id, created_by_name=created_by_name,
    )
    db.add(rec)
    db.commit()
    db.refresh(rec)
    return rec


# Status do front (won/lost/...) → status interno do settlement.
_FRONT_TO_INTERNAL_STATUS = {
    "won": "hit", "lost": "miss", "push": "push", "void": "void",
    "pending": "pending",
}
_CONFIDENCE_TO_SCORE = {"low": 30.0, "medium": 55.0, "high": 80.0}


def list_recommendations(
    db: Session,
    *,
    context: str = "general",
    only_active: bool = True,
    status: Optional[str] = None,
    source: Optional[str] = None,
    league_id: Optional[str] = None,
    market: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[FootballRecommendation]:
    q = select(FootballRecommendation).where(FootballRecommendation.context == context)
    if only_active:
        q = q.where(FootballRecommendation.is_active.is_(True))
    if status:
        q = q.where(FootballRecommendation.status == _FRONT_TO_INTERNAL_STATUS.get(status, status))
    if source:
        q = q.where(FootballRecommendation.source == source)
    if market:
        q = q.where(FootballRecommendation.market == market)
    if league_id:
        # league_id pode vir como nome da liga (o front filtra por id, mas a
        # tabela guarda o nome) — filtra por substring no nome quando não-numérico.
        q = q.where(FootballRecommendation.league.ilike(f"%{league_id}%"))
    q = q.order_by(FootballRecommendation.generated_at.desc()).limit(limit).offset(offset)
    return list(db.scalars(q).all())


# ─── Entradas ao vivo (analista) ─────────────────────────────────────────
def _split_match(label: str) -> tuple[str, str]:
    """'Liverpool x Man City' / 'A vs B' → (home, away)."""
    for sep in (" x ", " vs ", " v ", " - ", " × "):
        if sep in label:
            a, _, b = label.partition(sep)
            return a.strip(), b.strip()
    return label.strip(), ""


def create_live_pick(
    db: Session,
    *,
    match: str,
    match_id: Optional[int],
    league: Optional[str],
    market: str,
    selection: str,
    line: Optional[float],
    odd: Optional[float],
    confidence: Optional[str],
    reason: str,
    created_by_id: int,
    created_by_name: str,
    context: str = "general",
) -> FootballRecommendation:
    home, away = _split_match(match)
    rec = FootballRecommendation(
        context=context,
        match_id=match_id or 0, league=league or "", home_team=home, away_team=away,
        market=market, selection=selection, line=line, odd=odd or 0.0,
        confidence_score=_CONFIDENCE_TO_SCORE.get((confidence or "").lower()),
        recommendation_reason=reason or "", source="analyst", status="pending",
        is_active=True, was_shown_to_user=True,
        created_by_id=created_by_id, created_by_name=created_by_name,
    )
    db.add(rec)
    db.commit()
    db.refresh(rec)
    return rec


def update_live_pick(
    db: Session, rec_id: int, *,
    status: Optional[str] = None, odd: Optional[float] = None,
    confidence: Optional[str] = None, reason: Optional[str] = None,
) -> Optional[FootballRecommendation]:
    rec = db.get(FootballRecommendation, rec_id)
    if rec is None:
        return None
    if status is not None:
        if status == "cancelled":
            rec.is_active = False
        else:
            rec.status = {"active": "pending", "won": "hit", "lost": "miss",
                          "void": "void"}.get(status, rec.status)
    if odd is not None:
        rec.odd = odd
    if confidence is not None:
        rec.confidence_score = _CONFIDENCE_TO_SCORE.get(confidence.lower())
    if reason is not None:
        rec.recommendation_reason = reason
    db.commit()
    db.refresh(rec)
    return rec


def list_live_picks(db: Session, *, context: str = "general", limit: int = 100) -> list[FootballRecommendation]:
    """Entradas de analista ATIVAS, mais recentes primeiro."""
    q = (
        select(FootballRecommendation)
        .where(
            FootballRecommendation.context == context,
            FootballRecommendation.source == "analyst",
            FootballRecommendation.is_active.is_(True),
        )
        .order_by(FootballRecommendation.generated_at.desc())
        .limit(limit)
    )
    return list(db.scalars(q).all())


def list_live(db: Session, *, limit: int = 100) -> list[FootballRecommendation]:
    """Recomendações de jogos AO VIVO ou prestes a começar: ativas, pending,
    com kickoff já passado (ou sem kickoff conhecido). Ordenadas por edge."""
    now = datetime.now(timezone.utc)
    q = (
        select(FootballRecommendation)
        .where(
            FootballRecommendation.is_active.is_(True),
            FootballRecommendation.status == "pending",
        )
        .order_by(FootballRecommendation.edge.desc().nullslast())
        .limit(limit)
    )
    rows = list(db.scalars(q).all())
    return [
        r for r in rows
        if r.kickoff_at is None or r.kickoff_at <= now
    ]


def get_recommendation(db: Session, rec_id: int) -> Optional[FootballRecommendation]:
    return db.get(FootballRecommendation, rec_id)


def deactivate(db: Session, rec_id: int) -> Optional[FootballRecommendation]:
    """Soft delete. Idempotente."""
    rec = db.get(FootballRecommendation, rec_id)
    if rec is None:
        return None
    if rec.is_active:
        rec.is_active = False
        db.commit()
        db.refresh(rec)
    return rec


def pending_by_match(db: Session) -> dict[int, list[FootballRecommendation]]:
    """Agrupa recomendações pending por match_id — usado pelo settlement."""
    rows = db.scalars(
        select(FootballRecommendation).where(
            FootballRecommendation.status == "pending"
        )
    ).all()
    out: dict[int, list[FootballRecommendation]] = {}
    for r in rows:
        out.setdefault(r.match_id, []).append(r)
    return out
