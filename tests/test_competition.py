"""Contexto de competição — hoje há só ligas regulares ('general').

A Copa do Mundo foi removida (jul/2026). A abstração de contexto continua, mas
com um único item; qualquer contexto desconhecido cai em 'general'.
"""

from __future__ import annotations

from src import competition


def test_single_context_is_general():
    ctxs = competition.all_contexts()
    assert [c.key for c in ctxs] == ["general"]


def test_unknown_context_falls_back_to_general():
    assert competition.normalize(None) == "general"
    assert competition.normalize("world_cup") == "general"   # não existe mais
    assert competition.normalize("xpto") == "general"


def test_general_uses_league_config():
    cfg = competition.resolve("general")
    assert cfg.league_ids            # ligas configuradas (DEFAULT_LEAGUE_IDS)
    assert cfg.tournament is False    # liga regular, não torneio
    assert not cfg.has("groups")      # sem features de torneio
