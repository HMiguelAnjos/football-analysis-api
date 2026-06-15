"""Testes dos índices compostos (IPO/ICJ/ID/IIP)."""

from __future__ import annotations

from src.metrics.player_index import compute_indices
from src.schemas.football_schemas import PlayerSchema


def _p(name, **kw):
    base = dict(id=abs(hash(name)) % 100000, name=name, minutes=900)
    base.update(kw)
    return PlayerSchema(**base)


def test_indices_in_0_100_and_attacker_leads_ipo():
    players = [
        _p("Atacante", shots_on_target=30, shots=60, goals=12, rating=8.0, assists=2,
           key_passes=10, dribbles=20, tackles=2, interceptions=1, duels_won=30),
        _p("Volante", shots_on_target=2, shots=5, goals=0, rating=7.0, assists=1,
           key_passes=4, dribbles=5, tackles=40, interceptions=35, duels_won=60),
        _p("Meia", shots_on_target=8, shots=20, goals=3, rating=7.5, assists=12,
           key_passes=40, dribbles=30, tackles=10, interceptions=8, duels_won=40),
    ]
    idx = {pi.player.name: pi for pi in compute_indices(players)}
    for pi in idx.values():
        for v in (pi.ipo, pi.icj, pi.id, pi.iip):
            assert 0 <= v <= 100
    # Atacante lidera periculosidade ofensiva; volante o defensivo; meia a criação.
    assert idx["Atacante"].ipo == max(p.ipo for p in idx.values())
    assert idx["Volante"].id == max(p.id for p in idx.values())
    assert idx["Meia"].icj == max(p.icj for p in idx.values())


def test_context_factor_scales_iip():
    players = [_p("X", shots_on_target=10, shots=20, goals=4, rating=7.5,
                  assists=5, key_passes=15, dribbles=10, tackles=10,
                  interceptions=8, duels_won=30)]
    base = compute_indices(players)[0].iip
    boosted = compute_indices(players, context_factor=1.2)[0].iip
    assert boosted >= base


def test_low_minutes_filtered():
    players = [
        _p("Titular", minutes=800, shots_on_target=10, shots=20, goals=4, rating=7.5),
        _p("Reserva", minutes=10, shots_on_target=5, shots=8, goals=2, rating=9.0),
    ]
    names = {pi.player.name for pi in compute_indices(players)}
    assert "Titular" in names
    assert "Reserva" not in names   # < 30 min → fora do ranking
