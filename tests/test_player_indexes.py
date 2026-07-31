"""Índices de jogador (IPO/ICJ/ID/IIP) — fórmulas exatas, dado real, cobertura/
confiança e regra de ausente≠zero. Fixtures determinísticas (minutos=900 →
per-90 = total/10)."""

from __future__ import annotations

import pytest

from src.metrics.player_index import (
    PlayerIndexInput,
    compute_indices,
    compute_player_indexes,
)
from src.schemas.football_schemas import PlayerSchema
from src.services.football.data_service import FootballDataService

M = 900   # minutos → per-90 = total / 10


# Entrada IPO 100% disponível: xg→100, shots→50, sot→100, toques→50, forma→0.
def _ipo_full():
    return PlayerIndexInput(minutes=M, xg=8.0, shots=25.0, shots_on_target=20.0,
                            touches_in_box=50.0, recent_form=0.0)


def _icj_full():
    # xa→50, key_passes→100, big_chances→50, crosses→50.
    return PlayerIndexInput(minutes=M, xa=2.5, key_passes=30.0,
                            big_chances_created=5.0, accurate_crosses=15.0)


def _id_full():
    # tackles→100, interceptions→50, recoveries→25, duels_won→100.
    return PlayerIndexInput(minutes=M, tackles=50.0, interceptions=20.0,
                            recoveries=25.0, duels_won=80.0)


def _all_full():
    return PlayerIndexInput(
        minutes=M, xg=8.0, shots=25.0, shots_on_target=20.0, touches_in_box=50.0,
        recent_form=0.0, xa=2.5, key_passes=30.0, big_chances_created=5.0,
        accurate_crosses=15.0, tackles=50.0, interceptions=20.0, recoveries=25.0,
        duels_won=80.0)


# 1 — IPO exato, tudo disponível.
def test_ipo_exact():
    r = compute_player_indexes(_ipo_full())
    # 100*0.40 + 50*0.15 + 100*0.20 + 50*0.15 + 0*0.10 = 75.0
    assert r.ipo.score == 75.0
    assert r.ipo.coverage == 1.0
    assert r.ipo.confidence == "high"
    assert r.ipo.missing_metrics == []


# 2 — ICJ exato.
def test_icj_exact():
    r = compute_player_indexes(_icj_full())
    # 50*0.40 + 100*0.30 + 50*0.20 + 50*0.10 = 65.0
    assert r.icj.score == 65.0
    assert r.icj.coverage == 1.0


# 3 — ID exato.
def test_id_exact():
    r = compute_player_indexes(_id_full())
    # 100*0.35 + 50*0.35 + 25*0.20 + 100*0.10 = 67.5
    assert r.id.score == 67.5
    assert r.id.coverage == 1.0


# 4 — IIP neutro (context_factor = 1.0).
def test_iip_neutral():
    r = compute_player_indexes(_all_full())
    # (75 + 65 + 67.5) / 3 = 69.166... → 69.2
    assert r.iip.base_score == 69.2
    assert r.iip.context_factor == 1.0
    assert r.iip.score == 69.2
    assert r.iip.missing_components == []


# 5 — IIP com context_factor válido.
def test_iip_with_context_factor():
    r = compute_player_indexes(_all_full(), context_factor=1.1)
    assert r.iip.context_factor == 1.1
    assert r.iip.score == round(r.iip.base_score * 1.1, 1)   # 69.2*1.1 = 76.1


# 6 — Métrica faltando → reponderação (não vira zero).
def test_missing_metric_reweight():
    inp = _ipo_full()
    inp.xg = None                                   # tira o peso 0.40
    r = compute_player_indexes(inp)
    assert "xg" in r.ipo.missing_metrics
    assert r.ipo.coverage == 0.6                     # 1.0 - 0.40
    # (50*0.15 + 100*0.20 + 50*0.15 + 0*0.10) / 0.60 = 35/0.6 = 58.3
    assert r.ipo.score == 58.3
    assert r.ipo.confidence == "medium"


# 7 — Componente faltando → IIP repondera sobre os presentes.
def test_missing_component_reweight():
    inp = _all_full()
    inp.xa = inp.key_passes = inp.big_chances_created = inp.accurate_crosses = None
    r = compute_player_indexes(inp)
    assert r.icj.score is None                        # ICJ indisponível
    assert r.iip.missing_components == ["icj"]
    assert r.iip.base_score == round((r.ipo.score + r.id.score) / 2, 1)


# 8 — Zero real ≠ ausente.
def test_zero_is_not_missing():
    zero = compute_player_indexes(PlayerIndexInput(minutes=M, shots=0.0,
                                                   shots_on_target=10.0))
    missing = compute_player_indexes(PlayerIndexInput(minutes=M, shots=None,
                                                      shots_on_target=10.0))
    assert "shots" not in zero.ipo.missing_metrics    # 0 é valor real
    assert zero.ipo.breakdown["shots"] == 0.0
    assert "shots" in missing.ipo.missing_metrics     # None é ausente
    assert missing.ipo.breakdown["shots"] is None
    assert zero.ipo.coverage > missing.ipo.coverage   # o zero conta na cobertura


# 9 — Fronteiras da normalização (clamp 0..100).
def test_normalization_boundaries():
    hi = compute_player_indexes(PlayerIndexInput(minutes=M, tackles=9999.0))
    lo = compute_player_indexes(PlayerIndexInput(minutes=M, tackles=0.0))
    assert hi.id.breakdown["tackles"] == 100.0        # acima do teto → 100
    assert lo.id.breakdown["tackles"] == 0.0


# 10 — Entrada negativa / inválida não quebra.
def test_negative_and_invalid_inputs():
    neg = compute_player_indexes(PlayerIndexInput(minutes=M, tackles=-5.0),
                                 context_factor=-2.0)
    assert neg.id.breakdown["tackles"] == 0.0         # negativo → clamp 0
    assert neg.iip.context_factor == 1.0              # contexto inválido → 1.0
    # Sem minutos = sem amostra → tudo ausente (não zero).
    no_min = compute_player_indexes(PlayerIndexInput(minutes=0, shots=10.0))
    assert no_min.ipo.score is None
    assert no_min.ipo.confidence == "unavailable"


# 11 — Clamp final do score.
def test_final_clamp():
    # Todos os componentes no teto (100) e contexto 2.0 → IIP preso em 100.
    inp = PlayerIndexInput(
        minutes=M, xg=999, shots=999, shots_on_target=999, touches_in_box=999,
        recent_form=100, xa=999, key_passes=999, big_chances_created=999,
        accurate_crosses=999, tackles=999, interceptions=999, recoveries=999,
        duels_won=999)
    r = compute_player_indexes(inp, context_factor=2.0)
    assert r.ipo.score == 100.0
    assert r.iip.base_score == 100.0
    assert r.iip.score == 100.0                       # 100*2.0 preso em 100


# 12 — Cobertura e confiança.
def test_coverage_and_confidence():
    # Só recent_form (peso 0.10) → cobertura 0.10 → low e indisponível.
    only_form = compute_player_indexes(PlayerIndexInput(minutes=M, recent_form=80))
    assert only_form.ipo.coverage == pytest.approx(0.1)
    assert only_form.ipo.confidence == "low"
    assert only_form.ipo.available is False
    # xg + sot (0.40 + 0.20 = 0.60) → medium e disponível.
    partial = compute_player_indexes(PlayerIndexInput(minutes=M, xg=4.0,
                                                      shots_on_target=10.0))
    assert partial.ipo.coverage == pytest.approx(0.6)
    assert partial.ipo.confidence == "medium"
    assert partial.ipo.available is True


# 13 — Mapeamento do provider com xG/xA indisponíveis (dado real → None).
def test_provider_mapping_unavailable_xg_xa():
    p = PlayerSchema(id=1, name="Casemiro", minutes=M, shots=25.0,
                     shots_on_target=10.0, key_passes=30, tackles=25,
                     interceptions=20, duels_won=40, xg=None, xa=None)
    inp = FootballDataService._player_index_input(p)
    # Não fornecidos por jogador → None (nunca inventados/zerados).
    assert inp.xg is None and inp.xa is None
    assert inp.touches_in_box is None
    assert inp.big_chances_created is None
    assert inp.accurate_crosses is None
    assert inp.recoveries is None
    assert inp.recent_form is None
    # Dados reais mapeados.
    assert inp.shots == 25.0 and inp.shots_on_target == 10.0
    assert inp.key_passes == 30.0
    assert inp.tackles == 25.0 and inp.interceptions == 20.0 and inp.duels_won == 40.0


# 14 — Compatibilidade: o leaderboard antigo (compute_indices) segue intacto.
def test_backward_compat_leaderboard_unchanged():
    p = PlayerSchema(id=1, name="X", minutes=M, shots=25.0, shots_on_target=10.0,
                     goals=5, assists=3, key_passes=30, tackles=25,
                     interceptions=20, duels_won=40, rating=7.5)
    res = compute_indices([p])
    assert len(res) == 1
    # Contrato antigo: ipo/icj/id/iip são FLOAT direto (não o novo IndexResult).
    assert isinstance(res[0].ipo, float)
    assert isinstance(res[0].iip, float)
    # E o PlayerSchema público não perdeu seus campos.
    assert hasattr(p, "shots") and hasattr(p, "iip")
