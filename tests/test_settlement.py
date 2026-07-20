"""Testes da liquidação (settlement) de seleções."""

from __future__ import annotations

import pytest

from src.recommendation.settlement import MatchResult, profit_units, settle


@pytest.fixture
def res_2_1():
    return MatchResult(home_goals=2, away_goals=1)


def test_1x2(res_2_1):
    assert settle("1x2", "home", None, res_2_1) == "hit"
    assert settle("1x2", "away", None, res_2_1) == "miss"
    assert settle("1x2", "draw", None, res_2_1) == "miss"


def test_double_chance(res_2_1):
    assert settle("double_chance", "home_draw", None, res_2_1) == "hit"
    assert settle("double_chance", "draw_away", None, res_2_1) == "miss"


def test_dnb_push_on_draw():
    draw = MatchResult(home_goals=1, away_goals=1)
    assert settle("dnb", "home", None, draw) == "push"
    assert settle("dnb", "home", None, MatchResult(2, 0)) == "hit"


def test_over_under(res_2_1):
    assert settle("over_under", "over", 2.5, res_2_1) == "hit"   # 3 > 2.5
    assert settle("over_under", "under", 2.5, res_2_1) == "miss"
    assert settle("over_under", "over", 3.5, res_2_1) == "miss"


def test_btts(res_2_1):
    assert settle("btts", "yes", None, res_2_1) == "hit"
    assert settle("btts", "no", None, res_2_1) == "miss"
    assert settle("btts", "no", None, MatchResult(3, 0)) == "hit"


def test_asian_handicap(res_2_1):
    # Mandante -1: 2-1+(-1)=0 → push.
    assert settle("asian_handicap", "home", -1.0, res_2_1) == "push"
    # Mandante -0.5 (linha inteira não, mas testa <): margin 0.5>0 → hit.
    assert settle("asian_handicap", "home", 0.0, res_2_1) == "hit"


def test_team_totals(res_2_1):
    assert settle("team_total_home", "over", 1.5, res_2_1) == "hit"   # casa 2
    assert settle("team_total_away", "over", 1.5, res_2_1) == "miss"  # fora 1


def test_corners_void_without_data(res_2_1):
    assert settle("corners", "over", 9.5, res_2_1) == "void"
    with_corners = MatchResult(2, 1, corners=11)
    assert settle("corners", "over", 9.5, with_corners) == "hit"


def test_anytime_scorer():
    res = MatchResult(2, 1, scorers=["Mohamed Salah", "Erling Haaland"])
    assert settle("anytime_scorer", "Mohamed Salah", None, res) == "hit"
    assert settle("anytime_scorer", "Bukayo Saka", None, res) == "miss"
    # Sem lista de scorers → void.
    assert settle("anytime_scorer", "Salah", None, MatchResult(2, 1)) == "void"


def test_profit_units():
    assert profit_units("hit", 2.5) == 1.5
    assert profit_units("miss", 2.5) == -1.0
    assert profit_units("push", 2.5) == 0.0


def test_scorer_settle_ignores_accent_mismatch():
    # Fontes diferentes da api-football grafam o mesmo jogador com/sem acento —
    # sem normalizar, um artilheiro real virava MISS falso.
    res = MatchResult(2, 0, scorers=["Vinicius Junior"])   # fonte sem acento
    assert settle("anytime_scorer", "Vinícius Júnior — Marcar a qualquer momento",
                  None, res) == "hit"


def test_player_props_settle_with_player_stats():
    # Formato real da seleção ("Nome — Mais de N ...") + stats por jogador.
    res = MatchResult(1, 1, scorers=["Casemiro"], player_stats={
        "casemiro": {"tackles": 3.0, "shots_on_target": 1.0, "shots": 2.0, "assists": 0.0},
    })
    # "Mais de 2 desarmes" → line 1.5 (≥2); fez 3 → hit.
    assert settle("player_tackles", "Casemiro — Mais de 2 desarmes", 1.5, res) == "hit"
    # "Mais de 2 chutes no gol" → line 1.5 (≥2); fez 1 no alvo → miss.
    assert settle("player_shots_on_target", "Casemiro — Mais de 2 chutes no gol", 1.5, res) == "miss"
    # artilheiro pelo nome extraído da seleção.
    assert settle("anytime_scorer", "Casemiro — Marcar a qualquer momento", None, res) == "hit"
    # jogador sem stats → void (não erro).
    assert settle("player_tackles", "Fulano — Mais de 1 desarmes", 0.5, res) == "void"


def test_player_cards_settle():
    # "Levar cartão" é booleano (sem linha): HIT se levou ≥1 (amarelo ou vermelho).
    res = MatchResult(1, 1, player_stats={
        "kanu": {"cards": 1.0}, "lyanco": {"cards": 0.0},
    })
    assert settle("player_cards", "Kanu — Levar cartão", None, res) == "hit"
    assert settle("player_cards", "Lyanco — Levar cartão", None, res) == "miss"
    # jogador sem stats (ou fonte sem cartões) → void, não erro.
    assert settle("player_cards", "Fulano — Levar cartão", None, res) == "void"
