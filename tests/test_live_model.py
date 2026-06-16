"""Testes do modelo in-play (ao vivo)."""

from __future__ import annotations

from src.probability import build_score_matrix
from src.probability.live import inplay_market_probs, remaining_fraction
from src.probability.markets import match_winner


def test_remaining_fraction_monotonic():
    assert remaining_fraction(0) == 1.0
    assert remaining_fraction(45) == 0.5
    assert remaining_fraction(90) < 0.05
    assert remaining_fraction(120) < 0.05  # acréscimos: piso
    assert remaining_fraction(None) == 1.0


def test_kickoff_state_matches_prematch():
    # No minuto 0 e 0x0, o in-play deve bater com o pré-jogo.
    lh, la = 1.6, 1.1
    pre = match_winner(build_score_matrix(lh, la))
    live = inplay_market_probs(lh, la, 0, 0, 0)
    assert abs(live["home"] - pre["home"]) < 1e-9
    assert abs(live["draw"] - pre["draw"]) < 1e-9
    assert abs(live["away"] - pre["away"]) < 1e-9


def test_late_lead_dominates():
    # Mandante vencendo 1x0 aos 85' → P(vitória) deve ser alta e bem maior
    # que a estimativa de início de jogo.
    lh, la = 1.4, 1.2
    start = inplay_market_probs(lh, la, 0, 0, 0)["home"]
    leading = inplay_market_probs(lh, la, 85, 1, 0)["home"]
    assert leading > 0.8
    assert leading > start


def test_probabilities_sum_to_one():
    p = inplay_market_probs(1.5, 1.3, 60, 1, 1)
    assert abs((p["home"] + p["draw"] + p["away"]) - 1.0) < 1e-6
    assert abs((p["over_2.5"] + p["under_2.5"]) - 1.0) < 1e-6
    assert abs((p["btts_yes"] + p["btts_no"]) - 1.0) < 1e-6


def test_red_card_helps_opponent():
    # Jogo 0x0 no minuto 50. Se o VISITANTE leva vermelho, a chance do mandante
    # vencer deve SUBIR vs o mesmo cenário sem expulsão.
    base = inplay_market_probs(1.4, 1.2, 50, 0, 0)["home"]
    away_sent_off = inplay_market_probs(1.4, 1.2, 50, 0, 0, red_away=1)["home"]
    home_sent_off = inplay_market_probs(1.4, 1.2, 50, 0, 0, red_home=1)["home"]
    assert away_sent_off > base   # adversário com 10 → mandante favorecido
    assert home_sent_off < base   # mandante com 10 → mandante prejudicado


def test_already_btts_locks_yes():
    # Já está 1x1 → ambas marcam é CERTO (1.0), independente do que falta.
    p = inplay_market_probs(1.5, 1.5, 70, 1, 1)
    assert p["btts_yes"] == 1.0
    # E over 2.5: já tem 2 gols, então over depende só de +1 gol sair.
    assert 0.0 < p["over_2.5"] < 1.0
