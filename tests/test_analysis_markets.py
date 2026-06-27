"""Testes da MarketRecommendationEngine (pré-jogo) + grade/confiança."""

from src.analysis.features import MatchFeatures, TeamFeatures
from src.analysis.grade import confidence, grade
from src.analysis.markets import RAW_KEYS, MarketRecommendationEngine

MKT = MarketRecommendationEngine()


def _strong() -> TeamFeatures:
    return TeamFeatures(
        xg=2.4, xg_per_shot=0.17, big_chances=3.6, box_touches=42,
        shots_on_target=6.5, goals_for=2.3, off_form=2.3, ppg=2.4,
        corners_for=6.5, cards_for=2.0, matches_played=10)


def _frail() -> TeamFeatures:
    return TeamFeatures(
        xga=2.4, big_chances_conceded=3.6, shots_on_target_conceded=6.5,
        goals_conceded_recent=2.3, ppda=15.0, xg=0.8, goals_for=0.8,
        corners_for=4.0, cards_for=2.0, ppg=0.8, matches_played=10)


# ── grade / confiança ────────────────────────────────────────────────────────
def test_grade_tiers():
    assert grade(86, 20) == "A+"
    assert grade(80, 30) == "A"
    assert grade(72, 40) == "B"
    assert grade(64, 50) == "C"
    assert grade(50, 30) == "AVOID"          # edge baixo


def test_grade_hard_cap_on_risk():
    assert grade(95, 80) == "AVOID"          # risco acima do teto duro


def test_confidence_eroded_by_risk():
    assert confidence(80, 20) == 71.0        # 80 - 20*0.45
    assert confidence(50, 100) == 5.0        # 50 - 45


# ── Recomendações pré-jogo ───────────────────────────────────────────────────
def test_pre_game_generates_market_recs():
    mf = MatchFeatures(home=_strong(), away=_strong())
    recs = MKT.recommend_pre_game(mf, match_id=1, home_name="A", away_name="B")
    assert recs                                # cenário forte gera recs
    markets = {r.market for r in recs}
    assert "over_under" in markets
    over = next(r for r in recs if r.market == "over_under")
    assert over.edge_score >= 70
    assert over.grade in ("A+", "A", "B")
    assert over.recommendation_type == "PRE_GAME"


def test_raw_scores_shape():
    mf = MatchFeatures(home=_strong(), away=_strong())
    rec = MKT.recommend_pre_game(mf, match_id=1)[0]
    assert set(rec.raw_scores.keys()) == set(RAW_KEYS)
    assert rec.raw_scores["liveGameState"] is None      # pré-jogo


def test_1x2_picks_stronger_side():
    mf = MatchFeatures(home=_strong(), away=_frail())
    recs = MKT.recommend_pre_game(mf, match_id=1, home_name="Casa", away_name="Fora",
                                  include_avoid=True)
    r1x2 = next(r for r in recs if r.market == "1x2")
    assert r1x2.selection == "Casa"           # mandante muito superior


def test_neutral_data_yields_no_strong_recs():
    mf = MatchFeatures(home=TeamFeatures(), away=TeamFeatures())
    recs = MKT.recommend_pre_game(mf, match_id=1)
    assert recs == []                          # tudo AVOID → lista vazia
    all_recs = MKT.recommend_pre_game(mf, match_id=1, include_avoid=True)
    assert all_recs and all(r.grade == "AVOID" for r in all_recs)


def test_corners_line_projected_from_data():
    mf = MatchFeatures(home=_strong(), away=_strong())   # corners_for 6.5 + 6.5 = 13
    recs = MKT.recommend_pre_game(mf, match_id=1, include_avoid=True)
    corners = next(r for r in recs if r.market == "corners")
    assert corners.line == 12.5                # round(13)-0.5


def test_warnings_surface_missing_data():
    mf = MatchFeatures(home=_strong(), away=_strong())
    recs = MKT.recommend_pre_game(mf, match_id=1, include_avoid=True)
    btts = next(r for r in recs if r.market == "btts")
    assert any("ausente" in w.lower() for w in btts.warnings)
