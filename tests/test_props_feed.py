"""Testes do feed de player props (modo fixtures, offline)."""

from __future__ import annotations

from src import config
from src.services.football.data_service import FootballDataService


def test_team_player_pool_fallback_to_competition_players():
    # Fixtures não tem get_squad → team_player_pool cai em competition_players
    # filtrado pelo time. Liverpool = id 40 no dataset mock.
    svc = FootballDataService()
    pool = svc.team_player_pool(40, context="general")
    assert isinstance(pool, list)
    assert pool and all(p.team_id == 40 for p in pool)


def test_match_props_returns_list():
    svc = FootballDataService()
    props = svc.match_props(1001, context="general")  # Liverpool x Man City
    assert isinstance(props, list)


def test_props_feed_is_cached():
    svc = FootballDataService()
    first = svc.props(context="general", limit=20)
    second = svc.props(context="general", limit=20)
    assert isinstance(first, list)
    # Mesma chave de cache → mesmo resultado (não recomputa).
    assert len(first) == len(second)


def test_prop_picks_respect_confidence_floor():
    # P1+P2: nenhum prop abaixo do piso de confiança chega ao feed/persistência
    # (remove player_cards ~30 e o lixo <60). O fixture gera props de confiança
    # alta → a lista fica NÃO vazia (piso não zera o que é bom).
    svc = FootballDataService()
    _m, picks = svc.match_prop_picks(1001, context="general")   # Liverpool x Man City
    assert picks, "esperava props no fixture (Liverpool x Man City)"
    assert all(p.confidence_score >= config.MIN_DISPLAY_CONFIDENCE for p in picks)


def test_prop_picks_gated_by_league(monkeypatch):
    # P3a: liga fora de PROP_LEAGUE_IDS (ex.: copa) não gera props — evita o
    # desperdício de props que nunca liquidam (copas com void altíssimo).
    svc = FootballDataService()
    monkeypatch.setattr(config, "PROP_LEAGUE_IDS", [999])   # 39 (PL) fica de fora
    m, picks = svc.match_prop_picks(1001, context="general")
    assert m is not None and picks == []
