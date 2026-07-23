"""Testes do modelo PURO de escanteios (probability.corners) — offline."""

from __future__ import annotations

from src.probability import corners as cm


def _feat(favor, contra, *, style=None, sample=10):
    return cm.TeamCornerFeatures(
        media_favor_l5=favor, media_contra_l5=contra,
        indice_estilo_ofensivo=style, sample_size=sample)


def test_decayed_mean_weights_recent_more():
    # recente-primeiro: [10, 0] com meia-vida curta puxa pra cima do 5 simples.
    m = cm.decayed_mean([10, 0], halflife=1.0)
    assert m is not None and m > 5.0
    # lista homogênea → a própria média.
    assert cm.decayed_mean([4, 4, 4]) == 4.0
    # vazio / só None → None.
    assert cm.decayed_mean([]) is None
    assert cm.decayed_mean([None, None]) is None


def test_expected_corners_is_for_against_blend():
    # casa bate 6 e concede 4; fora bate 5 e concede 3.
    home, away = _feat(6, 4), _feat(5, 3)
    total, hc, ac, used_h2h, used_style = cm.expected_corners(home, away)
    # casa = (6 + 3)/2 = 4.5 ; fora = (5 + 4)/2 = 4.5 ; total = 9.
    assert hc == 4.5 and ac == 4.5 and total == 9.0
    assert used_h2h is False and used_style is False


def test_style_boost_only_above_league_avg():
    home = _feat(6, 4, style=2.0)      # acima da média
    away = _feat(5, 3, style=0.5)      # abaixo
    total, hc, ac, _, used_style = cm.expected_corners(
        home, away, league_style_avg=1.0, style_boost=0.10)
    assert used_style is True
    assert hc > 4.5                    # casa recebeu +10%
    assert ac == 4.5                   # fora não


def test_h2h_only_with_min_games():
    home, away = _feat(6, 4), _feat(5, 3)   # total base = 9
    # retorno: (total, casa, fora, used_h2h, used_style)
    # 2 confrontos → ignora H2H.
    t2, _hc, _ac, used_h2h2, _ = cm.expected_corners(home, away, h2h_media=13, h2h_games=2)
    assert t2 == 9.0 and used_h2h2 is False
    # 3 confrontos → mistura (puxa pra cima).
    t3, _hc, _ac, used_h2h3, _ = cm.expected_corners(home, away, h2h_media=13,
                                                     h2h_games=3, h2h_weight=0.2)
    assert used_h2h3 is True and 9.0 < t3 < 13.0


def test_predict_full_output():
    home, away = _feat(6, 4), _feat(5, 3)   # esperado 9
    pred = cm.predict(home, away)
    assert pred is not None
    # split por tempo soma o total; 2T > 1T (proporção 45/55).
    assert abs((pred.expected_1t + pred.expected_2t) - pred.expected_total) < 0.01
    assert pred.expected_2t > pred.expected_1t
    # linha ~1.5 abaixo do esperado, over provável.
    assert pred.line == max(6.5, round(9.0) - 1.5)
    assert 0.0 < pred.prob_over <= 1.0
    assert pred.sample == 10


def test_predict_none_without_data():
    assert cm.predict(_feat(0, 0), _feat(5, 3)) is None


def test_offensive_style_index_adapted():
    # (finalizações + na área)/posse ; sem cruzamentos (adaptado).
    assert cm.offensive_style_index(10, 6, 50) == round(16 / 50, 4)
    assert cm.offensive_style_index(10, 6, 0) is None
