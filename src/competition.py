"""
Contexto de competição — fonte ÚNICA da verdade das ligas regulares.

Toda regra que depende do contexto (quais ligas, qual season, quais sport keys
de odds) mora AQUI. Services e rotas só perguntam `resolve(context)` — sem
`if context == ...` espalhado pelo código.

Hoje há um único contexto: `general` (ligas regulares). A abstração continua
genérica — adicionar um novo contexto no futuro (ex.: "champions_league") = um
item em `_registry`, sem tocar em service/rota. O contexto exclusivo da Copa do
Mundo foi REMOVIDO (jul/2026, evento encerrado) — junto com suas features de
torneio (grupos/chaveamento) e o provider openfootball.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src import config

GENERAL = "general"


@dataclass(frozen=True)
class CompetitionConfig:
    key: str
    label: str
    league_ids: list[int]
    season: int
    odds_sport_keys: list[str]
    # Features opcionais por contexto (reservado p/ competições futuras).
    features: list[str] = field(default_factory=list)
    # Torneio = conjunto fechado de jogos por temporada (busca por season). Liga
    # regular = False (jogos do dia). Mantido genérico p/ copas futuras.
    tournament: bool = False

    def has(self, feature: str) -> bool:
        return feature in self.features


def _registry() -> dict[str, CompetitionConfig]:
    # Construído sob demanda pra refletir overrides de config em testes.
    return {
        GENERAL: CompetitionConfig(
            key=GENERAL,
            label="Futebol",
            league_ids=config.DEFAULT_LEAGUE_IDS,
            season=config.CURRENT_SEASON,
            odds_sport_keys=config.ODDS_SPORT_KEYS,
            features=[],
        ),
    }


def normalize(context: str | None) -> str:
    """Devolve um contexto válido; qualquer coisa desconhecida → geral."""
    if context and context.strip().lower() in _registry():
        return context.strip().lower()
    return GENERAL


def resolve(context: str | None) -> CompetitionConfig:
    return _registry()[normalize(context)]


def all_contexts() -> list[CompetitionConfig]:
    return list(_registry().values())
