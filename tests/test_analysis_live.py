"""Testes da LiveRecommendationEngine — regras ao vivo (seção 5)."""

from src.analysis.features import LiveFeatures
from src.analysis.live import LiveRecommendationEngine, _live_pressure

LIVE = LiveRecommendationEngine()


def _pressing_home_losing(minute: int = 60) -> LiveFeatures:
    """Mandante perdendo e pressionando muito; visitante recuado."""
    return LiveFeatures(
        minute=minute, home_score=0, away_score=1,
        shots_home=14, shots_away=3, shots_on_home=5, shots_on_away=1,
        xg_home=1.8, xg_away=0.4, corners_home=7, corners_away=1,
        insidebox_home=18, insidebox_away=3, blocked_home=4, blocked_away=0,
        possession_home=64, possession_away=36,
        fouls_home=8, fouls_away=14, cards_home=1, cards_away=3,
        momentum_home=1.25, momentum_away=0.85,
        recent_pressure_home=85, recent_pressure_away=20)


# ── pressão ao vivo ──────────────────────────────────────────────────────────
def test_live_pressure_high_for_pressing_side():
    lf = _pressing_home_losing()
    assert _live_pressure(lf, "home") > _live_pressure(lf, "away")
    assert _live_pressure(lf, "home") >= 65


# ── Escanteios ao vivo ───────────────────────────────────────────────────────
def test_live_corners_recommended_when_pressing_and_losing():
    recs = LIVE.recommend_live(_pressing_home_losing(), match_id=1,
                               home_name="Casa", away_name="Fora", include_avoid=True)
    corners = next(r for r in recs if r.market == "corners")
    assert corners.recommendation_type == "LIVE"
    assert corners.selection == "Over"
    assert corners.line > 8  # acima dos 8 cantos atuais


def test_live_corners_line_is_current_plus_buffer():
    lf = _pressing_home_losing()
    recs = LIVE.recommend_live(lf, match_id=1, include_avoid=True)
    corners = next(r for r in recs if r.market == "corners")
    # 7 + 1 = 8 cantos atuais; linha = 8 + 3.5
    assert corners.line == 11.5


def test_live_corners_skipped_outside_minute_window():
    lf = _pressing_home_losing(minute=10)        # antes da janela 22–83
    recs = LIVE.recommend_live(lf, match_id=1, include_avoid=True)
    assert all(r.market != "corners" for r in recs)


def test_live_corners_reason_mentions_pressure():
    recs = LIVE.recommend_live(_pressing_home_losing(), match_id=1,
                               home_name="Casa", include_avoid=True)
    corners = next(r for r in recs if r.market == "corners")
    assert any("pression" in x.lower() for x in corners.reasons)


# ── Gols ao vivo ─────────────────────────────────────────────────────────────
def test_live_goals_edge_high_in_open_game():
    lf = LiveFeatures(
        minute=55, home_score=1, away_score=1,
        shots_on_home=5, shots_on_away=4, xg_home=1.6, xg_away=1.4,
        insidebox_home=15, insidebox_away=14, corners_home=5, corners_away=5,
        possession_home=52, possession_away=48)
    recs = LIVE.recommend_live(lf, match_id=1, include_avoid=True)
    goals = next(r for r in recs if r.market == "over_under")
    assert goals.line == 3.5                       # 2 gols + 1.5
    assert goals.edge_score >= 60


def test_live_goals_warns_without_live_xg():
    lf = LiveFeatures(minute=55, home_score=0, away_score=0,
                      shots_on_home=2, shots_on_away=2)
    recs = LIVE.recommend_live(lf, match_id=1, include_avoid=True)
    goals = next(r for r in recs if r.market == "over_under")
    assert any("xg" in w.lower() for w in goals.warnings)


# ── Cartões ao vivo ──────────────────────────────────────────────────────────
def test_live_cards_higher_in_tight_2h_knockout():
    lf = LiveFeatures(minute=70, home_score=1, away_score=1,
                      fouls_home=12, fouls_away=13, cards_home=3, cards_away=2)
    hot = LIVE.recommend_live(lf, match_id=1, knockout=True, include_avoid=True)
    calm_lf = LiveFeatures(minute=20, home_score=3, away_score=0,
                           fouls_home=4, fouls_away=3, cards_home=0, cards_away=0)
    calm = LIVE.recommend_live(calm_lf, match_id=1, include_avoid=True)
    hot_cards = next(r for r in hot if r.market == "cards")
    calm_cards = next(r for r in calm if r.market == "cards")
    assert hot_cards.edge_score > calm_cards.edge_score


# ── raw_scores ao vivo ───────────────────────────────────────────────────────
def test_live_raw_scores_have_live_game_state():
    recs = LIVE.recommend_live(_pressing_home_losing(), match_id=1, include_avoid=True)
    assert all(r.raw_scores.get("liveGameState") is not None for r in recs)
