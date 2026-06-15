"""
Cliente HTTP fino pra api-football (v3).

Suporta hospedagem direta (api-sports) e via RapidAPI — só muda base URL +
headers (config). Falha silenciosa: qualquer erro devolve {} e o provider
decide o fallback. A api-football devolve sempre {"response": [...], ...}.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import requests

from src import config

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 12


class ApiFootballClient:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: Optional[str] = None,
        rapidapi: Optional[bool] = None,
        host: Optional[str] = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = (base_url or config.API_FOOTBALL_BASE_URL).rstrip("/")
        self._rapidapi = config.API_FOOTBALL_RAPIDAPI if rapidapi is None else rapidapi
        self._host = host or config.API_FOOTBALL_HOST

    def _headers(self) -> dict[str, str]:
        if self._rapidapi:
            return {"x-rapidapi-key": self._api_key, "x-rapidapi-host": self._host}
        return {"x-apisports-key": self._api_key}

    def get(self, path: str, params: Optional[dict[str, Any]] = None) -> dict:
        """GET genérico. Devolve o JSON completo ou {} em falha."""
        url = f"{self._base_url}/{path.lstrip('/')}"
        try:
            resp = requests.get(
                url, params=params or {}, headers=self._headers(),
                timeout=DEFAULT_TIMEOUT,
            )
            self._log_quota(resp)
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, dict) else {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("api-football GET %s falhou: %s", path, exc)
            return {}

    def response(self, path: str, params: Optional[dict[str, Any]] = None) -> list:
        """Atalho: devolve direto a lista em data['response'] (ou [])."""
        data = self.get(path, params)
        resp = data.get("response")
        return resp if isinstance(resp, list) else []

    @staticmethod
    def _log_quota(resp: requests.Response) -> None:
        remaining = resp.headers.get("x-ratelimit-requests-remaining")
        if remaining is not None:
            logger.info("api-football quota: %s remaining", remaining)
