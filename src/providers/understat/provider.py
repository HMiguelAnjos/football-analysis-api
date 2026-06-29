"""
UnderstatProvider — métricas avançadas (xG/xGA/PPDA) das LIGAS EUROPEIAS.

Understat não tem API oficial e fica atrás de Cloudflare (scrape direto é
bloqueado). Os dados vêm como JSON embutido na página da liga
(`teamsData = JSON.parse('...')`). Buscamos via ScraperAPI (config.SCRAPER_API_KEY)
pra furar o Cloudflare; sem a chave, o provider degrada (retorna None).

Valor que ele agrega sobre a api-football: PPDA (pressão) — a api-football não
tem. xG/xGA são redundantes com a agregação de stats, mas servem de fallback.

Cobertura: só as 5 grandes ligas europeias + RFPL (não tem Brasileirão/seleções).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional
from urllib.parse import quote
from urllib.request import Request, urlopen

from src import config

logger = logging.getLogger(__name__)

# api-football league_id → código da liga no Understat.
_UNDERSTAT_LEAGUE = {
    39: "EPL", 140: "La_liga", 135: "Serie_A",
    78: "Bundesliga", 61: "Ligue_1", 235: "RFPL",
}


def _norm(s: str) -> str:
    return " ".join((s or "").strip().lower().split())


class UnderstatProvider:
    name = "understat"

    def __init__(self, base_url: Optional[str] = None) -> None:
        self._base_url = (base_url or config.UNDERSTAT_BASE_URL).rstrip("/")
        # Cache em memória por (liga, temporada) — a página tem todos os times.
        self._tables: dict[tuple[str, int], dict] = {}

    # Protocolo XgProvider — não dá pra mapear só por team_id (Understat é por
    # nome/liga), então a integração usa get_team_advanced.
    def get_team_xg(self, team_id: int) -> Optional[tuple[float, float]]:
        return None

    def get_team_advanced(self, team_name: str, league_id: int,
                          season: int) -> Optional[dict]:
        """xG/xGA por jogo + PPDA do time. None se a liga não é coberta, o
        Understat está indisponível, ou o time não casou pelo nome."""
        league = _UNDERSTAT_LEAGUE.get(int(league_id or 0))
        if not league:
            return None
        table = self._league_table(league, int(season or config.CURRENT_SEASON))
        if not table:
            return None
        n = _norm(team_name)
        if n in table:
            return table[n]
        # Casamento aproximado por contém (nomes diferem entre fontes).
        for name, stats in table.items():
            if n and (n in name or name in n):
                return stats
        return None

    def _league_table(self, league: str, season: int) -> dict:
        key = (league, season)
        if key in self._tables:
            return self._tables[key]
        html = self._fetch(f"{self._base_url}/league/{league}/{season}")
        table = self.parse_teams(html) if html else {}
        self._tables[key] = table
        return table

    def _fetch(self, url: str) -> Optional[str]:
        if config.SCRAPER_API_KEY:
            url = f"https://api.scraperapi.com/?api_key={config.SCRAPER_API_KEY}&url={quote(url, safe='')}"
        try:
            req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
            return urlopen(req, timeout=30).read().decode("utf-8", "ignore")
        except Exception as exc:  # noqa: BLE001
            logger.warning("understat fetch falhou (%s)", exc)
            return None

    @staticmethod
    def parse_teams(html: str) -> dict:
        """HTML da página da liga → {nome_normalizado: {xg, xga, ppda}} (médias
        por jogo). PURO/testável. {} se não achar o bloco."""
        m = re.search(r"teamsData\s*=\s*JSON\.parse\('([^']+)'\)", html or "")
        if not m:
            return {}
        try:
            raw = m.group(1).encode("utf-8").decode("unicode_escape")
            data = json.loads(raw)
        except Exception:  # noqa: BLE001
            return {}
        out: dict[str, dict] = {}
        for t in data.values():
            hist = t.get("history") or []
            if not hist:
                continue
            n = len(hist)
            xg = sum(float(h.get("xG", 0)) for h in hist) / n
            xga = sum(float(h.get("xGA", 0)) for h in hist) / n
            ppda_vals = []
            for h in hist:
                p = h.get("ppda") or {}
                d = float(p.get("def", 0) or 0)
                if d > 0:
                    ppda_vals.append(float(p.get("att", 0)) / d)
            ppda = round(sum(ppda_vals) / len(ppda_vals), 2) if ppda_vals else None
            out[_norm(t.get("title", ""))] = {
                "xg": round(xg, 3), "xga": round(xga, 3), "ppda": ppda,
            }
        return out
