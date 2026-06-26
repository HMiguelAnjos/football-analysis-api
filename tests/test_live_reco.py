"""Testes do serviço de recomendações ao vivo (tracker + persistência)."""

from __future__ import annotations

from datetime import datetime, timezone

from src.providers.base import Match, Team
from src.recommendation.live_engine import CORNERS_OVER, LivePick
from src.services import live_reco_service as lrs


def _match(mid=900):
    return Match(
        id=mid, league_id=1, league_name="WC", season=2026,
        utc_kickoff=datetime.now(timezone.utc), status="live",
        home_team=Team(id=1, name="Casa"), away_team=Team(id=2, name="Fora"),
        home_goals=0, away_goals=1, minute=62,
    )


def test_tracker_deltas_over_window():
    t = lrs.LiveStatsTracker()
    # minuto 50: 3 escanteios; minuto 62: 6 → delta dos últimos ~10 min = 3.
    t.update(900, 50, {"home": {"corner_kicks": 3}, "away": {"corner_kicks": 1}})
    d = t.update(900, 62, {"home": {"corner_kicks": 6}, "away": {"corner_kicks": 2}})
    assert d["home"]["corner_kicks"] == 3
    assert d["away"]["corner_kicks"] == 1


def _pick():
    return LivePick(
        rec_type=CORNERS_OVER, market="Over 8.5 escanteios", line=8.5, odd=1.85,
        confidence=8.0, recommendation="Boa entrada para over escanteios.",
        reason="Pressão alta nos últimos 10 min.", stats_used={"minute": 62, "corners_total": 8},
    )


def test_upsert_list_and_result(db_session):
    m = _match()
    lrs.upsert_live_rec(db_session, m, "world_cup", _pick())
    rows = lrs.list_by_match(db_session, m.id)
    assert len(rows) == 1 and rows[0].rec_type == CORNERS_OVER
    assert rows[0].confidence == 8.0 and rows[0].line == 8.5

    # re-gerar no mesmo tick (mesmo tipo+linha) NÃO duplica — faz update.
    p2 = _pick(); p2.confidence = 9.0
    lrs.upsert_live_rec(db_session, m, "world_cup", p2)
    rows = lrs.list_by_match(db_session, m.id)
    assert len(rows) == 1 and rows[0].confidence == 9.0

    # pendentes inclui; marcar resultado green → sai dos pendentes, vira settled.
    assert len(lrs.list_pending(db_session)) == 1
    updated = lrs.set_result(db_session, rows[0].id, "green")
    assert updated.result == "green" and updated.status == "settled"
    assert lrs.list_pending(db_session) == []
