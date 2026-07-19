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
_football = _UNSET       # provider de dados das ligas
_xg = _UNSET


def _build_general() -> FootballDataProvider:
    choice = "fixtures" if config.USE_FIXTURES else config.FOOTBALL_PROVIDER
    if choice == "api_football" and config.API_FOOTBALL_KEY:
        try:
            from src.providers.api_football.provider import ApiFootballProvider
            logger.info("FootballDataProvider: api_football")
            return ApiFootballProvider()
        except Exception as exc:  # noqa: BLE001
            logger.warning("api_football indisponível (%s) — usando fixtures", exc)
    elif choice == "api_football":
        logger.warning("FOOTBALL_PROVIDER=api_football sem API_FOOTBALL_KEY — usando fixtures")
    logger.info("FootballDataProvider: fixtures (offline)")
    return fixtures


def get_football_provider(context: str = "general") -> FootballDataProvider:
    """Provider de dados das ligas. `context` é mantido por compatibilidade da
    assinatura (há um único contexto hoje: 'general')."""
    global _football
    if _football is _UNSET:
        _football = _build_general()
    return _football


_odds_by_ctx: dict[str, Optional[OddsProvider]] = {}


def _build_odds(context: str) -> Optional[OddsProvider]:
    from src import competition
    choice = "fixtures" if config.USE_FIXTURES else config.ODDS_PROVIDER
    if choice == "fixtures":
        logger.info("OddsProvider[%s]: fixtures (offline)", context)
        return fixtures
    if choice == "the_odds_api" and config.ODDS_API_KEY:
        try:
            from src.providers.odds.provider import TheOddsApiProvider
            cfg = competition.resolve(context)
            logger.info("OddsProvider[%s]: the_odds_api (%s)", context, cfg.odds_sport_keys)
            return TheOddsApiProvider(
                sport_keys=cfg.odds_sport_keys, bookmakers=config.ODDS_BOOKMAKERS,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("the_odds_api indisponível (%s) — odds DESATIVADAS", exc)
            return None
    logger.info("OddsProvider[%s]: DESATIVADO (ODDS_PROVIDER=%s)", context, choice)
    return None


def get_odds_provider(context: str = "general") -> Optional[OddsProvider]:
    if context not in _odds_by_ctx:
        _odds_by_ctx[context] = _build_odds(context)
    return _odds_by_ctx[context]


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
    global _football, _xg
    _football = _xg = _UNSET
    _odds_by_ctx.clear()
