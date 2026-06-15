"""Testes do modo Copa do Mundo (competition context = world_cup)."""

from __future__ import annotations

from src import competition
from src.services import recommendation_service as rec_svc
from src.services.football.data_service import FootballDataService
from src.services.football.generation_service import GenerationService


# ─── Registry de contexto ────────────────────────────────────────────────
def test_resolve_contexts():
    assert competition.normalize(None) == "general"
    assert competition.normalize("WORLD_CUP") == "world_cup"
    assert competition.normalize("xpto") == "general"
    wc = competition.resolve("world_cup")
    assert wc.has("groups") and wc.has("bracket")
    assert not competition.resolve("general").has("groups")


# ─── Isolamento de jogos por contexto ────────────────────────────────────
def test_matches_filtered_by_context():
    svc = FootballDataService()
    # Sem data: geral = jogos do dia; Copa (torneio) = temporada inteira.
    _, general = svc.matches(context="general")
    _, wc = svc.matches(context="world_cup")

    assert general and wc
    # Copa só traz jogos da liga da Copa (com fase/contexto preenchidos).
    assert all(m.context == "world_cup" for m in wc)
    assert all(m.stage for m in wc)
    # Nenhum jogo da Copa aparece no geral e vice-versa.
    wc_ids = {m.id for m in wc}
    gen_ids = {m.id for m in general}
    assert wc_ids.isdisjoint(gen_ids)


def test_groups_and_bracket_only_in_world_cup():
    svc = FootballDataService()
    assert svc.groups(context="general") == []
    assert svc.bracket(context="general") == []

    groups = svc.groups(context="world_cup")
    assert groups and groups[0].standings
    assert groups[0].standings[0].team.name

    bracket = svc.bracket(context="world_cup")
    # Há ao menos uma fase de mata-mata (R16/quartas) nos fixtures.
    assert bracket
    stages = {s.stage for s in bracket}
    assert stages & {"round_of_16", "quarter"}


def test_world_cup_match_has_penalties():
    svc = FootballDataService()
    m = svc.get_match(2003, context="world_cup")  # quartas decidida nos pênaltis
    assert m is not None
    assert m.penalty_home == 4 and m.penalty_away == 3
    assert m.winner == "home"


# ─── Isolamento de recomendações no banco ────────────────────────────────
def test_recommendations_isolated_by_context(db_session):
    gen = GenerationService(FootballDataService())
    gen.generate(db_session, context="general", min_edge=-1.0, min_confidence=0.0)
    gen.generate(db_session, context="world_cup", min_edge=-1.0, min_confidence=0.0)

    general = rec_svc.list_recommendations(db_session, context="general")
    wc = rec_svc.list_recommendations(db_session, context="world_cup")

    assert general and wc
    assert all(r.context == "general" for r in general)
    assert all(r.context == "world_cup" for r in wc)
    # Picks da Copa carregam a fase do torneio.
    assert any(r.stage for r in wc)


def test_live_pick_context_isolation(db_session):
    rec_svc.create_live_pick(
        db_session, context="world_cup", match="Brazil x France", match_id=2001,
        league="FIFA World Cup", market="1x2", selection="home", line=None,
        odd=2.1, confidence="high", reason="forte", created_by_id=1,
        created_by_name="Analista",
    )
    assert len(rec_svc.list_live_picks(db_session, context="world_cup")) == 1
    assert rec_svc.list_live_picks(db_session, context="general") == []
