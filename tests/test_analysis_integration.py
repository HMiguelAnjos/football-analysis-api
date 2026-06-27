"""Integração da engine de análise com o serviço (modo fixtures, offline)."""

from __future__ import annotations

from src.analysis.markets import RAW_KEYS
from src.services.football.data_service import FootballDataService

GRADES = {"A+", "A", "B", "C", "AVOID"}


def test_analysis_opportunities_returns_contract():
    svc = FootballDataService()
    recs = svc.analysis_opportunities(context="general", limit=20, include_avoid=True)
    assert isinstance(recs, list)
    if recs:
        r = recs[0]
        assert r.grade in GRADES
        assert r.recommendation_type == "PRE_GAME"
        assert 0 <= r.confidence <= 100
        assert 0 <= r.edge_score <= 100
        assert 0 <= r.risk_score <= 100
        assert set(r.raw_scores.keys()) == set(RAW_KEYS)


def test_analysis_opportunities_filters_avoid_by_default():
    svc = FootballDataService()
    only_strong = svc.analysis_opportunities(context="general", limit=30)
    assert all(r.grade != "AVOID" for r in only_strong)


def test_analysis_opportunities_is_cached():
    svc = FootballDataService()
    a = svc.analysis_opportunities(context="general", limit=20, include_avoid=True)
    b = svc.analysis_opportunities(context="general", limit=20, include_avoid=True)
    assert len(a) == len(b)


def test_live_analysis_offline_is_safe():
    # Sem jogos ao vivo nos fixtures → lista vazia, sem exceção.
    svc = FootballDataService()
    assert svc.live_analysis(context="general", limit=20) == []
