"""Testes de persistência das recomendações + permissões."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.recommendation.engine import RecommendationCandidate
from src.services import recommendation_service as svc
from src.services.permissions import MANAGE_RECOMMENDATIONS, has_permission


def _candidate(**over):
    base = dict(
        match_id=1001, league="Premier League", home_team="Liverpool",
        away_team="Man City", market="1x2", selection="home", line=None,
        bookmaker="bet365", odd=2.10, fair_odd=1.80,
        implied_probability=0.476, model_probability=0.555, edge=0.16,
        confidence_score=62.0, recommendation_reason="teste",
    )
    base.update(over)
    return RecommendationCandidate(**base)


# ── Permissões ─────────────────────────────────────────────────────────────
def test_admin_and_analyst_can_manage():
    assert has_permission(SimpleNamespace(role="admin"), MANAGE_RECOMMENDATIONS)
    assert has_permission(SimpleNamespace(role="analyst"), MANAGE_RECOMMENDATIONS)


def test_user_cannot_manage():
    assert not has_permission(SimpleNamespace(role="user"), MANAGE_RECOMMENDATIONS)


# ── UPSERT skip ────────────────────────────────────────────────────────────
def test_upsert_creates_then_skips(db_session):
    rec, created = svc.upsert_from_candidate(db_session, _candidate())
    assert created is True
    assert rec.id is not None
    assert rec.source == "engine"
    assert rec.status == "pending"

    # Mesma chave → skip (não duplica).
    rec2, created2 = svc.upsert_from_candidate(db_session, _candidate(odd=2.5))
    assert created2 is False
    assert rec2.id == rec.id
    assert rec2.odd == 2.10  # preserva o primeiro snapshot


def test_upsert_distinct_selection_creates_new(db_session):
    svc.upsert_from_candidate(db_session, _candidate(selection="home"))
    _, created = svc.upsert_from_candidate(db_session, _candidate(selection="away", odd=3.4))
    assert created is True
    assert len(svc.list_recommendations(db_session)) == 2


# ── Manual ─────────────────────────────────────────────────────────────────
def test_create_manual(db_session):
    rec = svc.create_manual(
        db_session, match_id=1002, league="PL", home_team="Arsenal",
        away_team="Chelsea", market="btts", selection="yes", line=None,
        odd=1.70, bookmaker="bet365", confidence_score=None,
        recommendation_reason="editorial", created_by_id=1, created_by_name="Ana",
    )
    assert rec.source == "analyst"
    assert rec.created_by_name == "Ana"


# ── Soft delete ────────────────────────────────────────────────────────────
def test_deactivate_soft_delete_idempotent(db_session):
    rec, _ = svc.upsert_from_candidate(db_session, _candidate())
    out = svc.deactivate(db_session, rec.id)
    assert out.is_active is False
    # Some do feed ativo, mas continua no banco.
    assert svc.list_recommendations(db_session, only_active=True) == []
    assert len(svc.list_recommendations(db_session, only_active=False)) == 1
    # Idempotente.
    assert svc.deactivate(db_session, rec.id).is_active is False


def test_deactivate_missing_returns_none(db_session):
    assert svc.deactivate(db_session, 99999) is None
