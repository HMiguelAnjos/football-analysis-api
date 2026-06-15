"""Cache: TTL em memória (odds) e persistência em disco (dado lento)."""

from __future__ import annotations

from src.providers import registry
from src.services.football.data_service import FootballDataService


class _SpyProvider:
    """Conta quantas vezes o provider foi realmente chamado."""
    name = "spy"

    def __init__(self):
        self.match_calls = 0
        self.form_calls = 0

    def get_matches_by_date(self, date):
        self.match_calls += 1
        from src.providers import fixtures
        return fixtures.get_matches_by_date(date)

    def get_team_form(self, team_id, last_n=10):
        self.form_calls += 1
        from src.providers import fixtures
        return fixtures.get_team_form(team_id, last_n)


def test_disk_cache_survives_restart(monkeypatch):
    spy = _SpyProvider()
    monkeypatch.setattr(registry, "_football", spy)

    # 1ª "vida" do processo: busca → grava no disco.
    svc1 = FootballDataService()
    svc1.matches_by_date_domain("2026-06-14")
    assert spy.match_calls == 1

    # Mesma instância, 2ª leitura → cache (memória).
    svc1.matches_by_date_domain("2026-06-14")
    assert spy.match_calls == 1

    # "Restart": nova instância (cache de memória zerado) lê do MESMO disco.
    svc2 = FootballDataService()
    matches = svc2.matches_by_date_domain("2026-06-14")
    assert spy.match_calls == 1, "deveria ler do disco, não chamar o provider"
    assert matches and matches[0].home_team.name


def test_team_form_cached_on_disk(monkeypatch):
    spy = _SpyProvider()
    monkeypatch.setattr(registry, "_football", spy)

    svc = FootballDataService()
    svc.team_form(40)
    svc.team_form(40)
    assert spy.form_calls == 1  # 2ª leitura veio do cache
