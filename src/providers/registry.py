"""
Registry de providers — escolhe a implementação por config.

Regras:
  • FOOTBALL (api-football): cai pra fixtures quando falta chave (a API precisa
    SEMPRE ter dados pra subir/demonstrar).
  • ODDS (The Odds API): OPCIONAL. Sem chave (e fora do modo fixtures), fica
    DESATIVADO (None) — o sistema funciona só com api-football; odds, painel de
    odds e recomendações de valor degradam pra vazio em vez de mostrar dados
    falsos. Em modo fixtures (dev offline), usa odds mock pra demonstrar.
  • XG: opcional.

Singletons preguiçosos. Sentinela _UNSET permite cachear o valor None (odds
desativadas) sem re-resolver a cada chamada.
"""

from __future__ import annotations

import logging
from typing import Optional

from src import config
from src.providers import fixtures
from src.providers.base import FootballDataProvider, OddsProvider, XgProvider

logger = logging.getLogger(__name__)

_UNSET = object()
_football = _UNSET       # provider do contexto geral
_football_wc = _UNSET    # provider do contexto Copa do Mundo
_odds = _UNSET
_xg = _UNSET


def _build_general() -> FootballDataProvider:
    choice = "fixtures" if config.USE_FIXTURES else config.FOOTBALL_PROVIDER
    if choice == "api_football" and config.API_FOOTBALL_KEY:
        try:
            from src.providers.api_football.provider import ApiFootballProvider
            logger.info("FootballDataProvider[general]: api_football")
            return ApiFootballProvider()
        except Exception as exc:  # noqa: BLE001
            logger.warning("api_football indisponível (%s) — usando fixtures", exc)
    elif choice == "api_football":
        logger.warning("FOOTBALL_PROVIDER=api_football sem API_FOOTBALL_KEY — usando fixtures")
    logger.info("FootballDataProvider[general]: fixtures (offline)")
    return fixtures


def _build_world_cup() -> FootballDataProvider:
    choice = "fixtures" if config.USE_FIXTURES else config.WORLD_CUP_PROVIDER
    if choice == "openfootball":
        try:
            from src.providers.openfootball.provider import OpenFootballProvider
            logger.info("FootballDataProvider[world_cup]: openfootball (grátis)")
            return OpenFootballProvider()
        except Exception as exc:  # noqa: BLE001
            logger.warning("openfootball indisponível (%s) — usando fixtures", exc)
    elif choice == "api_football" and config.API_FOOTBALL_KEY:
        try:
            from src.providers.api_football.provider import ApiFootballProvider
            logger.info("FootballDataProvider[world_cup]: api_football")
            return ApiFootballProvider()
        except Exception as exc:  # noqa: BLE001
            logger.warning("api_football indisponível (%s) — usando fixtures", exc)
    logger.info("FootballDataProvider[world_cup]: fixtures (offline)")
    return fixtures


def get_football_provider(context: str = "general") -> FootballDataProvider:
    """Provider de dados conforme o CONTEXTO. Copa do Mundo usa o provider
    dedicado (openfootball por padrão), independente do provider geral."""
    global _football, _football_wc
    if context == "world_cup":
        if _football_wc is _UNSET:
            _football_wc = _build_world_cup()
        return _football_wc
    if _football is _UNSET:
        _football = _build_general()
    return _football


def get_odds_provider() -> Optional[OddsProvider]:
    global _odds
    if _odds is not _UNSET:
        return _odds
    choice = "fixtures" if config.USE_FIXTURES else config.ODDS_PROVIDER
    if choice == "fixtures":
        _odds = fixtures
        logger.info("OddsProvider: fixtures (offline)")
        return _odds
    if choice == "the_odds_api" and config.ODDS_API_KEY:
        try:
            from src.providers.odds.provider import TheOddsApiProvider
            _odds = TheOddsApiProvider()
            logger.info("OddsProvider: the_odds_api")
            return _odds
        except Exception as exc:  # noqa: BLE001
            logger.warning("the_odds_api indisponível (%s) — odds DESATIVADAS", exc)
            _odds = None
            return _odds
    # "none", ou the_odds_api sem chave → odds desativadas (só api-football).
    if choice == "the_odds_api":
        logger.info("OddsProvider: DESATIVADO (sem ODDS_API_KEY) — rodando só com api-football")
    else:
        logger.info("OddsProvider: DESATIVADO (ODDS_PROVIDER=%s)", choice)
    _odds = None
    return _odds


def get_xg_provider() -> Optional[XgProvider]:
    global _xg
    if _xg is not _UNSET:
        return _xg
    choice = "fixtures" if config.USE_FIXTURES else config.XG_PROVIDER
    if choice == "understat":
        try:
            from src.providers.understat.provider import UnderstatProvider
            _xg = UnderstatProvider()
            logger.info("XgProvider: understat")
            return _xg
        except Exception as exc:  # noqa: BLE001
            logger.warning("understat indisponível (%s) — xG desligado", exc)
            _xg = None
            return _xg
    if choice == "fixtures":
        _xg = fixtures
        return _xg
    _xg = None
    return _xg


def reset() -> None:
    """Zera os singletons — usado em testes pra trocar config/providers."""
    global _football, _football_wc, _odds, _xg
    _football = _football_wc = _odds = _xg = _UNSET
