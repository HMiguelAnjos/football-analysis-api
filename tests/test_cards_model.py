"""Testes do modelo PURO de cartões (probability.cards) — offline."""

from __future__ import annotations

from src.probability import cards as cm


def _feat(favor, *, fouls=None, sample=10):
    return cm.TeamCardFeatures(media_favor_l5=favor, media_faltas_l5=fouls,
                               sample_size=sample)


def test_base_and_away_boost():
    home, away = _feat(2.0), _feat(2.0)
    p = cm.predict(home, away, away_boost=0.10)
    assert p is not None
    # visitante recebe +10% → fora > casa.
    assert p.away_cards > p.home_cards
    assert abs((p.expected_1t + p.expected_2t) - p.expected_total) < 0.01
    # cartão sobe no 2º tempo (proporção default 42/58).
    assert p.expected_2t > p.expected_1t


def test_referee_is_strongest_multiplier():
    home, away = _feat(2.0), _feat(2.0)
    base = cm.predict(home, away)
    # árbitro que dá 30% mais cartão que a média da liga.
    strict = cm.predict(home, away, referee_avg=6.5, league_cards_avg=5.0)
    assert strict.expected_total > base.expected_total
    assert strict.referee_factor == 1.3
    # trava: árbitro absurdo não estoura o clamp (1.5).
    capped = cm.predict(home, away, referee_avg=20.0, league_cards_avg=5.0)
    assert capped.referee_factor == 1.5


def test_context_boost():
    home, away = _feat(2.0), _feat(2.0)
    base = cm.predict(home, away)
    classico = cm.predict(home, away, is_classico=True, context_boost=0.15)
    assert classico.used_context is True
    assert round(classico.expected_total, 2) == round(base.expected_total * 1.15, 2)


def test_foul_factor_scales_base():
    # time que comete mais falta que a média da liga → mais cartão.
    faltoso = cm.predict(_feat(2.0, fouls=18), _feat(2.0, fouls=10),
                         league_fouls_avg=12.0)
    calmo = cm.predict(_feat(2.0, fouls=8), _feat(2.0, fouls=10),
                       league_fouls_avg=12.0)
    assert faltoso.home_cards > calmo.home_cards


def test_pendurado_extra_adds_to_side():
    home, away = _feat(2.0), _feat(2.0)
    base = cm.predict(home, away)
    boosted = cm.predict(home, away, home_extra=0.5)   # efeito líquido do pendurado
    assert boosted.home_cards > base.home_cards


def test_none_without_base():
    assert cm.predict(_feat(0.0), _feat(2.0)) is None


def test_pendurado_effect_dual():
    # COM incentivo (estratégico OU próximo fora) → AUMENTA.
    eff, reason, adj, delta = cm.pendurado_effect(0.30, strategic=True, next_away=False, boost=0.30)
    assert eff == "boost" and adj > 0.30 and delta > 0 and "gancho" in reason
    eff2, reason2, _, _ = cm.pendurado_effect(0.30, strategic=False, next_away=True)
    assert eff2 == "boost" and "fora" in reason2
    # SEM incentivo → REDUZ (dissuasão).
    eff3, _, adj3, delta3 = cm.pendurado_effect(0.30, strategic=False, next_away=False, damp=0.20)
    assert eff3 == "damp" and adj3 < 0.30 and delta3 < 0
