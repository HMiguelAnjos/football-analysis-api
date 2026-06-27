"""Testes da agregação de stats por jogo + merge nas features."""

from src.analysis.features import (
    TeamAdvancedStats, TeamFeatures, aggregate_advanced,
)
from src.providers.base import TeamForm


def _sample(own, opp):
    return {"own": own, "opp": opp}


def test_aggregate_averages_present_keys():
    samples = [
        _sample({"expected_goals": 2.0, "shots_on_goal": 6, "corner_kicks": 7,
                 "total_shots": 14, "ball_possession": 60, "fouls": 10,
                 "yellow_cards": 2, "red_cards": 0},
                {"expected_goals": 0.8, "shots_on_goal": 2, "corner_kicks": 3}),
        _sample({"expected_goals": 1.0, "shots_on_goal": 4, "corner_kicks": 5,
                 "total_shots": 10, "ball_possession": 50, "fouls": 12,
                 "yellow_cards": 1, "red_cards": 1},
                {"expected_goals": 1.2, "shots_on_goal": 4, "corner_kicks": 5}),
    ]
    adv = aggregate_advanced(samples)
    assert adv.sample == 2
    assert adv.xg == 1.5                      # (2.0+1.0)/2
    assert adv.xga == 1.0                     # (0.8+1.2)/2
    assert adv.shots_on_for == 5.0           # (6+4)/2
    assert adv.corners_for == 6.0            # (7+5)/2
    assert adv.cards_for == 2.0              # (2+0)+(1+1) = 2 e 2 → média 2
    assert adv.possession == 55.0


def test_aggregate_ignores_missing_per_key():
    samples = [
        _sample({"expected_goals": 2.0}, {}),
        _sample({"shots_on_goal": 4}, {}),    # sem xG aqui
    ]
    adv = aggregate_advanced(samples)
    assert adv.xg == 2.0                       # média só do jogo que tinha xG
    assert adv.shots_on_for == 4.0
    assert adv.xga is None                     # nunca veio


def test_aggregate_prefers_event_cards():
    # Cartões REAIS via eventos (chave "cards") têm prioridade sobre a estatística.
    samples = [
        {"own": {"expected_goals": 1.0}, "opp": {}, "cards": 3},
        {"own": {"expected_goals": 1.0}, "opp": {}, "cards": 5},
    ]
    adv = aggregate_advanced(samples)
    assert adv.cards_for == 4.0                # (3+5)/2 dos eventos


def test_aggregate_cards_none_without_yellow_or_events():
    # Sem eventos e sem amarelo (só vermelho na estatística) → não agrega cartões.
    samples = [{"own": {"red_cards": 0}, "opp": {}}]
    adv = aggregate_advanced(samples)
    assert adv.cards_for is None


def test_aggregate_empty_is_neutral():
    adv = aggregate_advanced([])
    assert adv.sample == 0 and adv.xg is None


def test_merge_advanced_fills_missing_features():
    form = TeamForm(team_id=1, goals_for=1.8, goals_against=1.0, matches_played=12)
    tf = TeamFeatures.from_form(form)
    assert tf.xg is None and tf.shots_on_target is None   # TeamForm não traz
    adv = TeamAdvancedStats(xg=1.9, xga=0.9, shots_on_for=5.5, shots_total_for=15,
                            corners_for=6.0, cards_for=2.2, possession=58, sample=10)
    tf.merge_advanced(adv)
    assert tf.xg == 1.9 and tf.xga == 0.9
    assert tf.shots_on_target == 5.5
    assert tf.corners_for == 6.0 and tf.cards_for == 2.2
    assert tf.off_possession == 58
    assert abs(tf.xg_per_shot - 1.9 / 15) < 1e-9          # derivado


def test_merge_advanced_none_keeps_features():
    form = TeamForm(team_id=1, goals_for=1.5, matches_played=10)
    tf = TeamFeatures.from_form(form)
    before = tf.xg
    tf.merge_advanced(None)
    assert tf.xg == before                     # nada muda
