"""Testes do motor de recomendações ao vivo (foco escanteios)."""

from __future__ import annotations

from src.recommendation.live_engine import (
    AVOID_ENTRY,
    CORNERS_OVER,
    TEAM_CORNERS_OVER,
    LiveStats,
    TeamLive,
    classify_live,
)


def _pressing_home(minute=62):
    # Mandante pressionando muito nos últimos 10 min, escanteios ainda baixos.
    return LiveStats(
        minute=minute, home_score=0, away_score=1,   # perdendo → precisa atacar
        home=TeamLive(
            name="Casa", corners=5, total_shots=14, shots_on=5, shots_insidebox=9,
            blocked_shots=4, possession=63, xg=1.4,
            d_corners=3, d_shots=6, d_shots_insidebox=4, d_blocked=3, d_xg=0.6,
        ),
        away=TeamLive(name="Fora", corners=2, total_shots=4, shots_on=1,
                      shots_insidebox=2, blocked_shots=1, possession=37, xg=0.4),
    )


def test_pressing_team_gets_corner_entry():
    pick = classify_live(_pressing_home())
    assert pick.rec_type in (CORNERS_OVER, TEAM_CORNERS_OVER)
    assert "escanteios" in pick.market.lower()
    assert pick.confidence >= 5.0
    # justificativa cita estatística (não genérica)
    assert any(k in pick.reason.lower() for k in ("chutes na área", "escanteios", "bloqueios"))
    assert pick.stats_used["minute"] == 62


def test_dead_game_is_avoid_entry():
    # Jogo parado: nada acontecendo nos últimos 10 min, fim de jogo.
    s = LiveStats(
        minute=86, home_score=1, away_score=0,
        home=TeamLive(name="Casa", corners=4, total_shots=6, possession=55),
        away=TeamLive(name="Fora", corners=3, total_shots=5, possession=45),
    )
    pick = classify_live(s)
    assert pick.rec_type == AVOID_ENTRY


def test_single_shot_does_not_trigger_entry():
    # Só um chute recente, sem volume → NÃO vira entrada.
    s = LiveStats(
        minute=30, home_score=0, away_score=0,
        home=TeamLive(name="Casa", corners=1, total_shots=2, d_shots=1,
                      d_shots_insidebox=1, possession=50),
        away=TeamLive(name="Fora", corners=1, total_shots=1, possession=50),
    )
    pick = classify_live(s)
    assert pick.rec_type == AVOID_ENTRY


def test_corners_priority_over_shots():
    # Mesmo com chutes, o motor prioriza o mercado de escanteios.
    pick = classify_live(_pressing_home())
    assert pick.rec_type != "shots_on_target"


def test_confidence_capped_0_10():
    pick = classify_live(_pressing_home())
    assert 0.0 <= pick.confidence <= 10.0
