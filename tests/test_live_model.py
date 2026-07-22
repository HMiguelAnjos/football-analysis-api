"""Testes do modelo in-play (ao vivo)."""

from __future__ import annotations

from src.probability import build_score_matrix
from src.probability.live import (
    inplay_market_probs,
    live_shots_remaining,
    momentum_multipliers,
    prob_at_least,
    remaining_fraction,
)
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


def test_momentum_neutral_when_low_sample():
    # Poucas finalizações no total → sem sinal de momentum (neutro).
    mh, ma = momentum_multipliers({"total_shots": 1}, {"total_shots": 1})
    assert mh == 1.0 and ma == 1.0


def test_momentum_favors_pressing_team():
    # Casa pressionando muito (chutes/posse) → multiplicador > 1; fora < 1.
    home = {"total_shots": 12, "shots_on_goal": 6, "ball_possession": 65, "corner_kicks": 7}
    away = {"total_shots": 3, "shots_on_goal": 1, "ball_possession": 35, "corner_kicks": 1}
    mh, ma = momentum_multipliers(home, away)
    assert mh > 1.0 and ma < 1.0


def test_momentum_lifts_pressing_team_scoring():
    # Com momentum a favor, a chance da casa marcar (over no resto) sobe.
    base = inplay_market_probs(1.3, 1.3, 60, 0, 0)["home"]
    pressing = inplay_market_probs(1.3, 1.3, 60, 0, 0, mom_home=1.2, mom_away=0.85)["home"]
    assert pressing > base


def test_prob_at_least():
    # Poisson(lam=2): P(>=1) = 1 - e^-2 ≈ 0.865
    assert abs(prob_at_least(1, 2.0) - (1 - 2.718281828 ** -2)) < 1e-3
    assert prob_at_least(0, 0.5) == 1.0


def test_live_shots_more_when_hot_and_pressing():
    # Jogador chutando muito (3 no gol em 45') + time pressionando → projeção de
    # chutes restantes MAIOR que um jogador frio.
    hot = live_shots_remaining(0.8, 3, 45, 45, momentum=1.2)
    cold = live_shots_remaining(0.8, 0, 45, 45, momentum=0.9)
    assert hot > cold > 0


def test_live_shots_decay_near_end():
    # Pouco tempo restante → poucos chutes adicionais esperados.
    end = live_shots_remaining(1.0, 1, 88, 2, momentum=1.0)
    assert end < 0.2


def test_already_btts_locks_yes():
    # Já está 1x1 → ambas marcam é CERTO (1.0), independente do que falta.
    p = inplay_market_probs(1.5, 1.5, 70, 1, 1)
    assert p["btts_yes"] == 1.0
    # E over 2.5: já tem 2 gols, então over depende só de +1 gol sair.
    assert 0.0 < p["over_2.5"] < 1.0


def test_live_settled_skips_already_decided_markets():
    """O que já aconteceu não é aposta: com 1-1 aos 73', 'ambas marcam' saía
    a 100%. _live_settled corta mercado já resolvido pelo placar."""
    from src.services.football.data_service import _live_settled

    # BTTS: os dois já marcaram → resolvido (sim feito, não impossível).
    assert _live_settled("btts", None, 1, 1) is True
    assert _live_settled("btts", None, 2, 3) is True
    # Só um marcou (ou 0-0) → ainda em aberto.
    assert _live_settled("btts", None, 1, 0) is False
    assert _live_settled("btts", None, 0, 0) is False

    # Over/under: total já passou da linha → resolvido.
    assert _live_settled("over_under", 2.5, 2, 1) is True    # 3 gols
    assert _live_settled("over_under", 2.5, 1, 1) is False   # 2 gols, em aberto

    # 1X2 nunca é "já decidido" — só resolve no apito final.
    assert _live_settled("1x2", None, 3, 0) is False
    # Sem placar → não dá pra afirmar nada.
    assert _live_settled("btts", None, None, None) is False
