"""
Cliente HTTP pra The Odds API (https://the-odds-api.com), v4.

Genérico por `sport` (sport key). Camada fina sobre requests: payload cru,
falha silenciosa (None/[]). Headers de quota logados pra visibilidade de
consumo. Reaproveitado do backend anterior — já era agnóstico de esporte.
"""

from __future__ import annotations

import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://api.the-odds-api.com/v4"
DEFAULT_TIMEOUT = 10


class OddsApiClient:
    def __init__(self, api_key: str, regions: str = "eu") -> None:
        self._api_key = api_key
        self._regions = regions

    def list_events(self, sport: str) -> list[dict]:
        """Eventos (jogos) ativos pro sport. 1 crédito por chamada."""
        url = f"{BASE_URL}/sports/{sport}/events"
        try:
            resp = requests.get(url, params={"apiKey": self._api_key},
                                timeout=DEFAULT_TIMEOUT)
            self._log_quota(resp)
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, list) else []
        except Exception as exc:  # noqa: BLE001
            logger.warning("OddsApi list_events(%s) falhou: %s", sport, exc)
            return []

    def event_odds(
        self,
        *,
        sport: str,
        event_id: str,
        markets: list[str],
        bookmakers: Optional[str] = None,
    ) -> Optional[dict]:
        """Odds de UM evento. Cobra 10 × len(markets) × len(regions)."""
        url = f"{BASE_URL}/sports/{sport}/events/{event_id}/odds"
        params = {
            "apiKey": self._api_key,
            "regions": self._regions,
            "markets": ",".join(markets),
            "oddsFormat": "decimal",
        }
        if bookmakers:
            params["bookmakers"] = bookmakers
        try:
            resp = requests.get(url, params=params, timeout=DEFAULT_TIMEOUT)
            self._log_quota(resp)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning("OddsApi event_odds falhou (%s): %s", event_id, exc)
            return None

    def sport_odds(
        self, *, sport: str, markets: list[str], bookmakers: Optional[str] = None,
    ) -> list[dict]:
        """Odds de TODOS os eventos do sport numa chamada (barato — usado pra
        1x2 inline no card). Cobra 10 × len(markets) × len(regions) no total,
        não por evento."""
        url = f"{BASE_URL}/sports/{sport}/odds"
        params = {
            "apiKey": self._api_key,
            "regions": self._regions,
            "markets": ",".join(markets),
            "oddsFormat": "decimal",
        }
        if bookmakers:
            params["bookmakers"] = bookmakers
        try:
            resp = requests.get(url, params=params, timeout=DEFAULT_TIMEOUT)
            self._log_quota(resp)
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, list) else []
        except Exception as exc:  # noqa: BLE001
            logger.warning("OddsApi sport_odds(%s) falhou: %s", sport, exc)
            return []

    @staticmethod
    def _log_quota(resp: requests.Response) -> None:
        remaining = resp.headers.get("x-requests-remaining")
        used = resp.headers.get("x-requests-used")
        if remaining is not None:
            logger.info("OddsApi quota: %s used, %s remaining", used, remaining)
