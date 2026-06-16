"""Testes do refresh de odds por evento (gol/expulsão) + fallback."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.providers.base import Match, Team
from src.services.live_odds_service import LiveOddsRefresher, detect_state_changes


def _m(mid: int, hg, ag, status: str = "live", kickoff_min_ago: int = 30) -> Match:
    ko = datetime.now(timezone.utc) - timedelta(minutes=kickoff_min_ago)
    return Match(
        id=mid, league_id=1, league_name="WC", season=2026, utc_kickoff=ko,
        status=status, home_team=Team(id=mid * 10, name="H"),
        away_team=Team(id=mid * 10 + 1, name="A"),
        home_goals=hg, away_goals=ag,
    )


# ── detecção pura (estado = placar + expulsões) ──────────────────────────────
def test_first_sight_is_not_a_change():
    assert detect_state_changes({}, {1: (0, 0, 0, 0)}) == set()


def test_same_state_no_change():
    assert detect_state_changes({1: (1, 0, 0, 0)}, {1: (1, 0, 0, 0)}) == set()


def test_goal_is_detected():
    assert detect_state_changes({1: (0, 0, 0, 0)}, {1: (1, 0, 0, 0)}) == {1}


def test_red_card_is_detected():
    # mesmo placar, mas uma expulsão nova no visitante → mudança.
    assert detect_state_changes({1: (1, 0, 0, 0)}, {1: (1, 0, 0, 1)}) == {1}


# ── refresher (efeito colateral) ─────────────────────────────────────────────
class _FakeData:
    def __init__(self, live, in_window=True, reds=(0, 0)):
        self._live = live
        self._in = in_window
        self._reds = reds
        self.refreshed: list[int] = []
        self.invalidated: list[tuple[int, str]] = []

    def matches_domain_for(self, date, context):
        return self._live if self._in else []

    def live_matches(self, context):
        return self._live

    def _red_cards(self, m, context):
        return self._reds

    def invalidate_odds(self, match_id, context="general"):
        self.invalidated.append((match_id, context))

    def match_odds_domain(self, match, *, context="general", force=False):
        assert force is True
        self.refreshed.append(match.id)
        return None


def test_first_tick_warms_via_fallback_then_quiet():
    data = _FakeData([_m(1, 0, 0)])
    r = LiveOddsRefresher(data, ["world_cup"])
    s1 = r.tick()                       # 1ª vez → fallback aquece a odd
    assert s1["refreshed"] == 1 and s1["fallback"] == 1
    data.refreshed.clear()
    s2 = r.tick()                       # nada mudou e fallback não venceu → 0
    assert s2["refreshed"] == 0


def test_goal_triggers_refresh():
    data = _FakeData([_m(1, 0, 0)])
    r = LiveOddsRefresher(data, ["world_cup"])
    r.tick()                            # baseline
    data.refreshed.clear()
    data._live = [_m(1, 1, 0)]          # gol
    s = r.tick()
    assert s["changed"] == 1 and data.refreshed == [1]


def test_red_card_triggers_refresh():
    data = _FakeData([_m(1, 1, 0)], reds=(0, 0))
    r = LiveOddsRefresher(data, ["world_cup"])
    r.tick()                            # baseline
    data.refreshed.clear()
    data._reds = (0, 1)                 # expulsão no visitante (mesmo placar)
    s = r.tick()
    assert s["changed"] == 1 and data.refreshed == [1]


def test_tick_skips_when_no_match_in_window():
    data = _FakeData([_m(1, 0, 0)], in_window=False)
    r = LiveOddsRefresher(data, ["world_cup"])
    s = r.tick()
    assert s["polled"] == 0 and s["refreshed"] == 0
