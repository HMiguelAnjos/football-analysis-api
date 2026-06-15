"""Testes do refresh de odds por evento (detecção de gol + reaquecimento)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.providers.base import Match, Team
from src.services.live_odds_service import LiveOddsRefresher, detect_score_changes


def _m(mid: int, hg, ag, status: str = "live", kickoff_min_ago: int = 30) -> Match:
    ko = datetime.now(timezone.utc) - timedelta(minutes=kickoff_min_ago)
    return Match(
        id=mid, league_id=1, league_name="WC", season=2026, utc_kickoff=ko,
        status=status, home_team=Team(id=mid * 10, name="H"),
        away_team=Team(id=mid * 10 + 1, name="A"),
        home_goals=hg, away_goals=ag,
    )


def test_first_sight_is_not_a_change():
    changed, snap = detect_score_changes({}, [_m(1, 0, 0)])
    assert changed == set()
    assert snap == {1: (0, 0)}


def test_same_score_no_change():
    changed, _ = detect_score_changes({1: (1, 0)}, [_m(1, 1, 0)])
    assert changed == set()


def test_goal_is_detected():
    changed, snap = detect_score_changes({1: (0, 0)}, [_m(1, 1, 0)])
    assert changed == {1}
    assert snap[1] == (1, 0)


def test_missing_goals_skipped():
    changed, snap = detect_score_changes({}, [_m(1, None, None)])
    assert changed == set()
    assert snap == {}


class _FakeData:
    """data_service mínimo pro refresher."""

    def __init__(self, live, in_window=True):
        self._live = live
        self._in_window = in_window
        self.invalidated: list[tuple[int, str]] = []
        self.refreshed: list[tuple[int, str]] = []

    def matches_domain_for(self, date, context):
        # Garante que a janela "ao vivo" seja satisfeita (ou não).
        return self._live if self._in_window else []

    def live_matches(self, context):
        return self._live

    def invalidate_odds(self, match_id, context="general"):
        self.invalidated.append((match_id, context))

    def match_odds_domain(self, match, *, context="general", force=False):
        assert force is True
        self.refreshed.append((match.id, context))
        return None


def test_tick_refreshes_only_changed_matches():
    live = [_m(1, 0, 0), _m(2, 0, 0)]
    data = _FakeData(live)
    r = LiveOddsRefresher(data, ["world_cup"])

    # 1º tick: registra placares, nada muda.
    stats = r.tick()
    assert stats["changed"] == 0 and stats["refreshed"] == 0

    # Jogo 1 marca; jogo 2 igual.
    data._live = [_m(1, 1, 0), _m(2, 0, 0)]
    stats = r.tick()
    assert stats["changed"] == 1 and stats["refreshed"] == 1
    assert data.invalidated == [(1, "world_cup")]
    assert data.refreshed == [(1, "world_cup")]


def test_tick_skips_when_no_match_in_window():
    data = _FakeData([_m(1, 0, 0)], in_window=False)
    r = LiveOddsRefresher(data, ["world_cup"])
    stats = r.tick()
    # Fora da janela → nem busca jogos ao vivo (poupa a api-football).
    assert stats["polled"] == 0 and stats["live"] == 0
