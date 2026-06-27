"""Testes das recomendações de player props (projeção do modelo)."""

from __future__ import annotations

from src.providers.base import Match, Team, TeamForm
from src.recommendation.player_props import generate_player_props
from src.schemas.football_schemas import PlayerSchema
from src.services.football.data_service import FootballDataService
from src.services.football.generation_service import GenerationService
from src.services import recommendation_service as rec_svc


def _match():
    return Match(
        id=999, league_id=39, league_name="PL", season=2025, utc_kickoff=None,
        status="scheduled", home_team=Team(id=1, name="Home FC"),
        away_team=Team(id=2, name="Away FC"),
    )


def _player(name, team_id, *, sot, goals, apps=5):
    return PlayerSchema(id=hash(name) % 10000, name=name, team_id=team_id,
                        appearances=apps, goals=goals, shots=sot * 2,
                        shots_on_target=sot)


def test_generator_produces_shooter_and_scorer_picks():
    match = _match()
    # Casa forte atacando (marca 2.2/jogo) contra defesa fraca (sofre 2.0).
    home = TeamForm(team_id=1, matches_played=8, goals_for=2.2, goals_against=0.8)
    away = TeamForm(team_id=2, matches_played=8, goals_for=0.9, goals_against=2.0)
    home_players = [_player("Artilheiro", 1, sot=15, goals=8)]  # 3 sot/jogo, 1.6 gol/jogo
    picks = generate_player_props(
        match=match, home_form=home, away_form=away,
        home_players=home_players, away_players=[],
    )
    markets = {p.market for p in picks}
    assert "player_shots_on_target" in markets
    assert "anytime_scorer" in markets
    p = next(x for x in picks if x.market == "player_shots_on_target")
    assert 0 < p.model_probability <= 1
    assert p.line and p.line < p.projection
    assert "Artilheiro" in p.selection


def test_opponent_strength_scales_projection():
    match = _match()
    pl = [_player("X", 1, sot=10, goals=4)]
    weak_opp = TeamForm(team_id=2, matches_played=8, goals_for=0.7, goals_against=2.4)
    strong_opp = TeamForm(team_id=2, matches_played=8, goals_for=2.4, goals_against=0.4)
    home = TeamForm(team_id=1, matches_played=8, goals_for=1.8, goals_against=1.0)
    vs_weak = generate_player_props(match=match, home_form=home, away_form=weak_opp,
                                    home_players=pl, away_players=[])
    vs_strong = generate_player_props(match=match, home_form=home, away_form=strong_opp,
                                      home_players=pl, away_players=[])
    pw = next(x for x in vs_weak if x.market == "player_shots_on_target")
    ps = next(x for x in vs_strong if x.market == "player_shots_on_target")
    # Contra defesa fraca, a projeção do jogador é maior.
    assert pw.projection > ps.projection


def test_generator_produces_tackles_picks():
    match = _match()
    home = TeamForm(team_id=1, matches_played=8, goals_for=1.5, goals_against=1.2)
    # Visitante ataca bastante → time da casa desarma mais.
    away = TeamForm(team_id=2, matches_played=8, goals_for=2.0, goals_against=1.0)
    cdm = PlayerSchema(id=77, name="Volante", team_id=1, appearances=8, tackles=28)
    picks = generate_player_props(match=match, home_form=home, away_form=away,
                                  home_players=[cdm], away_players=[])
    tk = [p for p in picks if p.market == "player_tackles"]
    assert tk, "deveria gerar prop de desarmes"
    p = tk[0]
    assert "desarmes" in p.selection.lower()
    assert p.line and p.line < p.projection
    assert 0 < p.model_probability <= 1


def test_generation_persists_player_props(db_session):
    # Modo fixtures: jogos gerais (Liverpool=40/City=50) têm jogadores mock
    # (Salah=40, Haaland=50) → gera props.
    gen = GenerationService(FootballDataService())
    result = gen.generate(db_session, context="general", min_edge=2.0, min_confidence=100)
    assert result["player_props"] >= 1
    recs = rec_svc.list_recommendations(db_session, only_active=True, limit=200)
    prop_markets = {r.market for r in recs}
    assert "player_shots_on_target" in prop_markets or "anytime_scorer" in prop_markets
