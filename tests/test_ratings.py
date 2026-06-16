"""Testes dos ratings de força (ataque/defesa) ajustados por adversário."""

from __future__ import annotations

from src.providers.base import Match, Team
from src.probability.ratings import compute_ratings


def _match(mid: int, home: int, away: int, hg: int, ag: int,
           status: str = "finished") -> Match:
    return Match(
        id=mid, league_id=1, league_name="Test", season=2026,
        utc_kickoff=None, status=status,
        home_team=Team(id=home, name=f"T{home}"),
        away_team=Team(id=away, name=f"T{away}"),
        home_goals=hg, away_goals=ag,
    )


def test_skips_unfinished_and_missing_goals():
    games = [
        _match(1, 10, 20, 3, 0, status="scheduled"),
        _match(2, 10, 20, None, None),  # type: ignore[arg-type]
    ]
    r = compute_ratings(games)
    # Nenhum jogo válido → ratings vazios, média default.
    assert r.attack == {}
    assert r.defense == {}


def test_dedupes_by_match_id():
    # O mesmo jogo vindo do histórico dos dois times não deve contar em dobro.
    games = [_match(1, 10, 20, 2, 1), _match(1, 10, 20, 2, 1)]
    r = compute_ratings(games)
    # 1 jogo, 3 gols → média = 3 / (2*1) = 1.5.
    assert abs(r.avg - 1.5) < 1e-9


def test_opponent_adjustment_discounts_goals_vs_weak_defenses():
    """Dois times marcam a MESMA média de gols (2/jogo), mas um contra defesas
    fortes e outro contra defesas fracas — o que enfrentou defesas fortes deve
    ter ataque MAIOR. É o conserto do 'Cabo Verde marca muito vs fracos'."""
    z = 9  # time de referência que calibra a defesa dos adversários
    games = [
        # Adversários 3,4 = defesa FORTE (sofrem só 1 do referência).
        _match(1, z, 3, 1, 0), _match(2, z, 4, 1, 0),
        # Adversários 5,6 = defesa FRACA (sofrem 4 do referência).
        _match(3, z, 5, 4, 0), _match(4, z, 6, 4, 0),
        # Time 1 marca 2 contra defesas fortes; time 2 marca 2 contra fracas.
        _match(5, 1, 3, 2, 0), _match(6, 1, 4, 2, 0),
        _match(7, 2, 5, 2, 0), _match(8, 2, 6, 2, 0),
        # Dá algum ataque aos adversários pra não ficarem no piso.
        _match(9, 3, z, 2, 1), _match(10, 4, z, 2, 1),
        _match(11, 5, z, 2, 1), _match(12, 6, z, 2, 1),
    ]
    r = compute_ratings(games, iterations=30)
    assert r.has(1) and r.has(2)
    # Marcar 2 contra defesa boa vale mais que 2 contra defesa ruim.
    assert r.attack[1] > r.attack[2]
    # E os adversários fracos têm defesa pior (rating maior) que os fortes.
    assert r.defense[5] > r.defense[3]


def test_shrink_compresses_extremes_toward_mean():
    # Time que goleia (ataque alto) + segura (defesa baixa) vs fracos.
    games = [
        _match(1, 1, 2, 5, 0), _match(2, 1, 3, 4, 0), _match(3, 1, 2, 6, 0),
        _match(4, 2, 3, 1, 1), _match(5, 3, 2, 0, 0),
    ]
    raw = compute_ratings(games)
    shrunk = compute_ratings(games, shrink=0.55)
    # O extremo do time 1 fica mais perto de 1.0 com shrink.
    assert abs(shrunk.attack[1] - 1.0) < abs(raw.attack[1] - 1.0)
    assert abs(shrunk.defense[1] - 1.0) < abs(raw.defense[1] - 1.0)


def test_lambdas_favor_stronger_attack():
    # Time forte (muitos gols, poucos sofridos) vs time fraco.
    games = [
        _match(1, 1, 2, 4, 0), _match(2, 1, 3, 3, 0), _match(3, 1, 2, 5, 1),
        _match(4, 2, 3, 1, 1), _match(5, 3, 2, 0, 0), _match(6, 2, 1, 0, 3),
    ]
    r = compute_ratings(games)
    lh, la = r.lambdas(1, 2)
    assert lh > la  # o time forte (1) espera marcar mais que o fraco (2)
    assert 0.1 <= lh <= 5.0 and 0.1 <= la <= 5.0
