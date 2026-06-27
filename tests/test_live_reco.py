"""Testes do serviço de recomendações ao vivo (tracker + persistência)."""

from __future__ import annotations

from datetime import datetime, timezone

from src.providers.base import Match, MatchStatistics, Team
from src.recommendation.live_engine import (
    CORNERS_OVER, GOAL_PRESSURE, NEXT_CORNER, SHOTS_ON_TARGET, TEAM_CORNERS_OVER,
    LivePick,
)
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


class _FakeDS:
    def __init__(self, m, stats):
        self._m, self._stats = m, stats

    def match_domain(self, match_id, context="general"):
        return self._m

    def _live_stats(self, m, context):
        return self._stats


def _lp(rec_type, market, line, stats_used):
    return LivePick(rec_type=rec_type, market=market, line=line, odd=None,
                    confidence=7.0, recommendation="x", reason="y", stats_used=stats_used)


def test_settle_all_types(db_session):
    m = _match(950)
    m.status = "finished"
    m.home_goals, m.away_goals = 2, 1            # Casa marcou (era 0x1 na hora)
    # Final: Casa 7 escanteios / 6 no gol; Fora 3 / 2.
    stats = MatchStatistics(
        match_id=950,
        home={"corner_kicks": 7, "shots_on_goal": 6},
        away={"corner_kicks": 3, "shots_on_goal": 2},
    )
    lrs.upsert_live_rec(db_session, m, "world_cup",
                        _lp(CORNERS_OVER, "Over 8.5 escanteios", 8.5, {"score": "0-1"}))
    lrs.upsert_live_rec(db_session, m, "world_cup",
                        _lp(TEAM_CORNERS_OVER, "Casa over 5.5 escanteios", 5.5, {"score": "0-1"}))
    lrs.upsert_live_rec(db_session, m, "world_cup",
                        _lp(SHOTS_ON_TARGET, "Casa over 4.5 chutes no gol", 4.5, {"score": "0-1"}))
    lrs.upsert_live_rec(db_session, m, "world_cup",
                        _lp(NEXT_CORNER, "Próximo escanteio: Casa", None,
                            {"corners_home": 4, "corners_away": 2}))
    lrs.upsert_live_rec(db_session, m, "world_cup",
                        _lp(GOAL_PRESSURE, "Casa pressão de gol", None, {"score": "0-1"}))

    res = lrs.settle_finished(db_session, _FakeDS(m, stats))
    assert res["settled"] == 5
    rows = {r.rec_type: r for r in lrs.list_by_match(db_session, 950)}
    assert rows[CORNERS_OVER].result == "green"        # 10 > 8.5
    assert rows[TEAM_CORNERS_OVER].result == "green"   # Casa 7 > 5.5
    assert rows[SHOTS_ON_TARGET].result == "green"     # Casa 6 > 4.5
    assert rows[NEXT_CORNER].result == "green"         # Casa 7 > 4 (teve mais cantos)
    assert rows[GOAL_PRESSURE].result == "green"       # Casa 2 > 1 gol (marcou)
    assert all(r.status == "settled" for r in rows.values())
