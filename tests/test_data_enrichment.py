"""Testes do enriquecimento de dados: passes-chave por time (ponto 1) +
parser do Understat (ponto 2)."""

from __future__ import annotations

from src.analysis.features import TeamFeatures
from src.analysis.scores import FootballAnalysisEngine
from src.providers.understat.provider import UnderstatProvider

ENG = FootballAnalysisEngine()


# ── Ponto 1: passes-chave elevam a Criação ───────────────────────────────────
def test_key_passes_lift_creation_score():
    empty = ENG.creation(TeamFeatures()).value          # tudo ausente → ~50
    rich = ENG.creation(TeamFeatures(key_passes=14)).value  # criação real
    assert rich > empty


# ── Ponto 2: parser do Understat (sem rede) ──────────────────────────────────
def test_understat_parse_teams_aggregates():
    html = (
        "var teamsData = JSON.parse('"
        '{"1":{"id":"1","title":"Arsenal","history":['
        '{"xG":1.5,"xGA":0.8,"ppda":{"att":120,"def":12}},'
        '{"xG":2.5,"xGA":1.2,"ppda":{"att":80,"def":10}}]}}'
        "');"
    )
    table = UnderstatProvider.parse_teams(html)
    a = table["arsenal"]
    assert a["xg"] == 2.0          # (1.5+2.5)/2
    assert a["xga"] == 1.0         # (0.8+1.2)/2
    assert a["ppda"] == 9.0        # (120/12 + 80/10)/2 = (10+8)/2


def test_understat_parse_empty_when_no_block():
    assert UnderstatProvider.parse_teams("<html>nada aqui</html>") == {}


def test_understat_unsupported_league_returns_none():
    # Brasileirão (71) não é coberto pelo Understat.
    prov = UnderstatProvider()
    assert prov.get_team_advanced("Flamengo", 71, 2025) is None
    # get_team_xg (protocolo) sempre None (Understat é por nome/liga).
    assert prov.get_team_xg(123) is None
