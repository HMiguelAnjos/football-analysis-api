"""
Contexto de competição — fonte ÚNICA da verdade pra alternar "futebol geral" ↔
"Copa do Mundo".

Toda regra que depende do contexto (quais ligas, qual season, quais sport keys
de odds, quais features de torneio) mora AQUI. Services e rotas só perguntam
`resolve(context)` — sem `if context == ...` espalhado pelo código.

Adicionar um novo contexto no futuro (ex.: "champions_league") = um item em
`_REGISTRY`, sem tocar em service/rota.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src import config

GENERAL = "general"
WORLD_CUP = "world_cup"


@dataclass(frozen=True)
class CompetitionConfig:
    key: str
    label: str
    league_ids: list[int]
    season: int
    odds_sport_keys: list[str]
    # Features de torneio habilitadas (grupos, chaveamento, outrights...).
    features: list[str] = field(default_factory=list)
    # Torneio = conjunto fechado de jogos por temporada (busca por season, não
    # por "hoje"). Liga regular = False (jogos do dia).
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
        WORLD_CUP: CompetitionConfig(
            key=WORLD_CUP,
            label="Copa do Mundo",
            league_ids=[config.WORLD_CUP_LEAGUE_ID],
            season=config.WORLD_CUP_SEASON,
            odds_sport_keys=[config.WORLD_CUP_ODDS_SPORT_KEY],
            features=["groups", "bracket", "outrights"],
            tournament=True,
        ),
    }


def normalize(context: str | None) -> str:
    """Devolve um contexto válido; desconhecido/None → geral."""
    if context and context.strip().lower() in (GENERAL, WORLD_CUP):
        return context.strip().lower()
    return GENERAL


def resolve(context: str | None) -> CompetitionConfig:
    return _registry()[normalize(context)]


def all_contexts() -> list[CompetitionConfig]:
    return list(_registry().values())
