"""Testes das probabilidades de mercado derivadas da matriz de placar."""

from __future__ import annotations

from src.probability.markets import (
    anytime_scorer,
    asian_handicap,
    btts,
    double_chance,
    draw_no_bet,
    match_winner,
    over_under,
    player_over_line,
    poisson_over_under,
    team_totals,
)
from src.probability.poisson import build_score_matrix


def test_match_winner_sums_to_one():
    sm = build_score_matrix(1.6, 1.1)
    w = match_winner(sm)
    assert abs(sum(w.values()) - 1.0) < 1e-9
    # Mandante mais forte → favorito.
    assert w["home"] > w["away"]


def test_double_chance_consistent_with_1x2():
    sm = build_score_matrix(1.5, 1.2)
    w = match_winner(sm)
    dc = double_chance(sm)
    assert abs(dc["home_draw"] - (w["home"] + w["draw"])) < 1e-9
    assert abs(dc["home_away"] - (w["home"] + w["away"])) < 1e-9


def test_draw_no_bet_sums_to_one():
    sm = build_score_matrix(1.5, 1.5)
    dnb = draw_no_bet(sm)
    assert abs(sum(dnb.values()) - 1.0) < 1e-9
    # Times iguais → ~50/50.
    assert abs(dnb["home"] - dnb["away"]) < 0.05


def test_over_under_complementary():
    sm = build_score_matrix(1.5, 1.5)
    ou = over_under(sm, 2.5)
    assert abs(ou["over"] + ou["under"] - 1.0) < 1e-9
    # Lambda total 3.0 > 2.5 → over favorito.
    assert ou["over"] > ou["under"]


def test_btts_complementary():
    sm = build_score_matrix(1.8, 1.4)
    b = btts(sm)
    assert abs(b["yes"] + b["no"] - 1.0) < 1e-9


def test_asian_handicap_quarter_line_average():
    sm = build_score_matrix(2.0, 1.0)
    # Quarter line -0.25 = média de 0.0 e -0.5.
    q = asian_handicap(sm, -0.25)
    a = asian_handicap(sm, 0.0)
    b = asian_handicap(sm, -0.5)
    assert abs(q["home"] - (a["home"] + b["home"]) / 2) < 1e-9


def test_team_totals():
    sm = build_score_matrix(2.5, 0.5)
    home = team_totals(sm, 1.5, home=True)
    assert home["over"] > home["under"]   # mandante marca muito


def test_poisson_over_under():
    ou = poisson_over_under(9.5, 9.5)  # escanteios esperados 9.5
    assert abs(ou["over"] + ou["under"] - 1.0) < 1e-9


def test_anytime_scorer_increases_with_rate():
    low = anytime_scorer(0.3)
    high = anytime_scorer(1.0)
    assert 0 < low < high < 1


def test_player_over_line():
    ou = player_over_line(3.0, 2.5)  # 3 chutes/90 vs linha 2.5
    assert ou["over"] > ou["under"]
