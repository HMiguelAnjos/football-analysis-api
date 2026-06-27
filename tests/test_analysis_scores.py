"""Testes dos 11 scores da FootballAnalysisEngine — inclui o fallback-50."""

from src.analysis.features import LiveFeatures, MatchFeatures, TeamFeatures
from src.analysis.scores import FootballAnalysisEngine

ENG = FootballAnalysisEngine()


def _strong_attack() -> TeamFeatures:
    return TeamFeatures(
        xg=2.4, xg_per_shot=0.17, big_chances=3.6, box_touches=42,
        shots_on_target=6.5, goals_for=2.4, off_form=2.4, matches_played=10)


def _frail_defense() -> TeamFeatures:
    return TeamFeatures(
        xga=2.4, big_chances_conceded=3.6, shots_on_target_conceded=6.5,
        goals_conceded_recent=2.4, ppda=15.0, matches_played=10)


def _solid_defense() -> TeamFeatures:
    return TeamFeatures(
        xga=0.5, big_chances_conceded=0.6, shots_on_target_conceded=1.6,
        goals_conceded_recent=0.5, ppda=6.5, matches_played=10)


# ── Fallback neutro 50 ───────────────────────────────────────────────────────
def test_empty_features_fall_back_to_neutral_with_warnings():
    r = ENG.offensive_threat(TeamFeatures())
    assert 49.0 <= r.value <= 51.0
    assert r.warnings  # cada componente ausente vira warning


# ── OffensiveThreat ──────────────────────────────────────────────────────────
def test_offensive_threat_high_for_strong_attack():
    r = ENG.offensive_threat(_strong_attack())
    assert r.value >= 75
    assert any("perigo" in x.lower() for x in r.reasons)


# ── DefensiveFragility ───────────────────────────────────────────────────────
def test_defensive_fragility_higher_when_frail():
    frail = ENG.defensive_fragility(_frail_defense()).value
    solid = ENG.defensive_fragility(_solid_defense()).value
    assert frail > solid
    assert frail >= 65


# ── Matchup ──────────────────────────────────────────────────────────────────
def test_matchup_high_attack_vs_frail_defense():
    r = ENG.matchup(off_threat=80, opp_def_fragility=78, creation=60)
    assert r.value >= 70
    assert r.reasons


# ── Momentum ─────────────────────────────────────────────────────────────────
def test_momentum_streak_reason():
    tf = TeamFeatures(ppg=2.4, last10_ppg=2.2, streak=4.0, matches_played=10)
    r = ENG.momentum(tf)
    assert any("sequência" in x.lower() for x in r.reasons)


# ── Efficiency / regressão ───────────────────────────────────────────────────
def test_efficiency_flags_regression_when_overperforming():
    tf = TeamFeatures(goals_for=2.2, xg=1.2, matches_played=10)
    r = ENG.efficiency(tf)
    assert any("regress" in x.lower() for x in r.reasons)
    assert r.warnings


def test_efficiency_neutral_without_xg():
    r = ENG.efficiency(TeamFeatures(goals_for=2.0, matches_played=10))
    assert r.value == 50.0
    assert r.warnings


# ── Risk ─────────────────────────────────────────────────────────────────────
def test_risk_flags_small_sample():
    tf = TeamFeatures(matches_played=3)
    mf = MatchFeatures(home=tf, away=TeamFeatures())
    r = ENG.risk(tf, mf)
    assert any("amostra" in x.lower() for x in r.reasons)
    assert 0 <= r.value <= 100


# ── CardsTension ─────────────────────────────────────────────────────────────
def test_cards_tension_higher_in_derby_knockout():
    tf = TeamFeatures(cards_for=3.0, fouls=15, matches_played=10)
    hot = ENG.cards_tension(tf, MatchFeatures(home=tf, away=tf, derby=True, knockout=True))
    calm = ENG.cards_tension(tf, MatchFeatures(home=tf, away=tf))
    assert hot.value > calm.value


# ── CornersPressure ──────────────────────────────────────────────────────────
def test_corners_pressure_handles_missing_data():
    r = ENG.corners_pressure(TeamFeatures(), TeamFeatures(), off_pressure=50)
    assert 0 <= r.value <= 100
    assert r.warnings


# ── LiveGameState ────────────────────────────────────────────────────────────
def test_live_game_state_urgency_when_losing():
    lf = LiveFeatures(minute=70, home_score=0, away_score=1,
                      recent_pressure_home=80, momentum_home=1.2)
    r = ENG.live_game_state(lf, "home")
    assert any("precisa" in x.lower() for x in r.reasons)
    assert r.value >= 55


# ── analyze_match (bundle) ───────────────────────────────────────────────────
def test_analyze_match_matchup_uses_opponent_defense():
    mf = MatchFeatures(home=_strong_attack(), away=_frail_defense())
    out = ENG.analyze_match(mf)
    assert "matchup" in out["home"] and "cornersPressure" in out["home"]
    # Ataque forte (casa) vs defesa frágil (fora) → matchup casa > matchup fora
    assert out["home"]["matchup"].value > out["away"]["matchup"].value
