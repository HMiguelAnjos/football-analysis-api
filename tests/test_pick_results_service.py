"""Testes de settlement + relatório de performance."""

from __future__ import annotations

from src.providers.base import Match, Team
from src.recommendation.engine import RecommendationCandidate
from src.services import recommendation_service as rec_svc
from src.services.pick_results_service import performance_breakdown, settle_finished


class _FakeFootball:
    def get_match_statistics(self, match_id):
        return None


class _FakeDataService:
    """Devolve um jogo já finalizado 2-1."""
    def __init__(self):
        self._fb = _FakeFootball()

    def _football(self):
        return self._fb

    def match_domain(self, match_id):
        return Match(
            id=match_id, league_id=39, league_name="PL", season=2025,
            utc_kickoff=None, status="finished",
            home_team=Team(id=40, name="Liverpool"),
            away_team=Team(id=50, name="Man City"),
            home_goals=2, away_goals=1,
        )


def _cand(market, selection, line, odd, edge):
    return RecommendationCandidate(
        match_id=1001, league="PL", home_team="Liverpool", away_team="Man City",
        market=market, selection=selection, line=line, bookmaker="bet365",
        odd=odd, fair_odd=1.8, implied_probability=0.5, model_probability=0.55,
        edge=edge, confidence_score=60.0, recommendation_reason="x",
    )


def test_settle_marks_hit_and_miss_and_writes_ledger(db_session):
    rec_svc.upsert_from_candidate(db_session, _cand("1x2", "home", None, 2.10, 0.16))
    rec_svc.upsert_from_candidate(db_session, _cand("over_under", "under", 2.5, 2.05, 0.05))

    stats = settle_finished(db_session, _FakeDataService())
    assert stats["settled"] == 2

    recs = rec_svc.list_recommendations(db_session)
    by_market = {r.market: r for r in recs}
    assert by_market["1x2"].status == "hit"          # casa venceu 2-1
    assert by_market["over_under"].status == "miss"  # under 2.5 com 3 gols
    assert by_market["1x2"].actual_result == "2-1"


def test_performance_breakdown_roi(db_session):
    rec_svc.upsert_from_candidate(db_session, _cand("1x2", "home", None, 2.00, 0.16))
    rec_svc.upsert_from_candidate(db_session, _cand("btts", "no", None, 2.00, 0.05))
    settle_finished(db_session, _FakeDataService())

    perf = performance_breakdown(db_session)
    totals = perf["totals"]
    # 1x2 home hit (+1.0), btts no miss (-1.0) → profit 0, accuracy 0.5.
    assert totals["won"] == 1
    assert totals["lost"] == 1
    assert totals["accuracy"] == 0.5
    assert abs(totals["profit_units"]) < 1e-9
