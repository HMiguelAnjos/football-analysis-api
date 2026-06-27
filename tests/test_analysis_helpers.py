"""Testes dos helpers de normalização — base de todos os scores."""

from src.analysis.helpers import (
    clamp, invert_score, normalize, safe_divide, weighted_average,
)


def test_normalize_maps_range_to_0_100():
    assert normalize(0, 0, 10) == 0.0
    assert normalize(10, 0, 10) == 100.0
    assert normalize(5, 0, 10) == 50.0


def test_normalize_clamps_outside_range():
    assert normalize(-5, 0, 10) == 0.0
    assert normalize(50, 0, 10) == 100.0


def test_normalize_none_propagates():
    assert normalize(None, 0, 10) is None


def test_normalize_degenerate_range_is_neutral():
    assert normalize(7, 5, 5) == 50.0


def test_clamp():
    assert clamp(-1) == 0.0
    assert clamp(150) == 100.0
    assert clamp(42) == 42.0


def test_invert_score():
    assert invert_score(70) == 30.0
    assert invert_score(0) == 100.0
    assert invert_score(None) is None


def test_safe_divide():
    assert safe_divide(10, 2) == 5.0
    assert safe_divide(10, 0, default=1.0) == 1.0
    assert safe_divide(None, 2, default=9.0) == 9.0
    assert safe_divide(10, None, default=9.0) == 9.0


def test_weighted_average_ignores_none_and_renormalizes():
    # 80 com peso 0.5 + None (ignorado) + 40 com peso 0.5 = 60
    assert weighted_average([(80, 0.5), (None, 0.3), (40, 0.5)]) == 60.0


def test_weighted_average_all_none_returns_none():
    assert weighted_average([(None, 0.5), (None, 0.5)]) is None
