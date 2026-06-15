"""
TheOddsApiProvider — odds de futebol normalizadas pros mercados canônicos.

Implementa OddsProvider. Resolve o evento do The Odds API a partir dos nomes
dos times do jogo (match por similaridade de nome, tolerante a divergência
entre fontes) e traduz os outcomes crus dos books pras SELEÇÕES CANÔNICAS que
o engine entende (home/draw/away, over/under, yes/no, …).

A normalização (`normalize_event`) é estática e pura — testável com um payload
de exemplo, sem rede.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from src import config
from src.providers.base import (
    Match,
    MarketOdds,
    MatchOdds,
    Selection,
)
from src.providers.odds.client import OddsApiClient

logger = logging.getLogger(__name__)

# Mercados do The Odds API que pedimos (depende do plano da conta).
SOCCER_MARKETS = ["h2h", "totals", "btts", "double_chance", "draw_no_bet", "spreads"]


def _norm(s: str) -> str:
    return " ".join((s or "").strip().lower().replace(".", "").split())


def _name_matches(a: str, b: str) -> bool:
    """Match tolerante de nomes de time entre fontes diferentes."""
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    # Substring ou token compartilhado significativo (ex: "Man City" vs
    # "Manchester City"; "Liverpool FC" vs "Liverpool").
    if na in nb or nb in na:
        return True
    ta, tb = set(na.split()), set(nb.split())
    shared = {t for t in (ta & tb) if len(t) > 3}
    return bool(shared)


class TheOddsApiProvider:
    name = "the_odds_api"

    def __init__(
        self,
        client: Optional[OddsApiClient] = None,
        *,
        sport_keys: Optional[list[str]] = None,
        bookmakers: str = "",
    ) -> None:
        if client is None:
            if not config.ODDS_API_KEY:
                raise ValueError("TheOddsApiProvider precisa de ODDS_API_KEY")
            client = OddsApiClient(config.ODDS_API_KEY, regions=config.ODDS_REGIONS)
        self._client = client
        self._sport_keys = sport_keys or config.ODDS_SPORT_KEYS
        self._bookmakers = bookmakers or config.ODDS_BOOKMAKERS
        # Cache em memória por sport key (corta list_events/sport_odds repetidos).
        self._ev_cache: dict[str, tuple[list, float]] = {}
        self._h2h_cache: dict[str, tuple[list, float]] = {}

    def _events_for(self, sport: str, ttl: float = 6 * 3600) -> list:
        import time as _t
        hit = self._ev_cache.get(sport)
        if hit and (_t.monotonic() - hit[1]) < ttl:
            return hit[0]
        evs = self._client.list_events(sport)
        self._ev_cache[sport] = (evs, _t.monotonic())
        return evs

    def _h2h_for_sport(self, sport: str, ttl: float = 180.0) -> list:
        import time as _t
        hit = self._h2h_cache.get(sport)
        if hit and (_t.monotonic() - hit[1]) < ttl:
            return hit[0]
        evs = self._client.sport_odds(
            sport=sport, markets=["h2h"], bookmakers=self._bookmakers or None,
        )
        self._h2h_cache[sport] = (evs, _t.monotonic())
        return evs

    def get_match_odds(self, match: Match) -> Optional[MatchOdds]:
        event = self._resolve_event(match)
        if event is None:
            logger.info("Odds: evento não resolvido pro jogo %s", match.id)
            return None
        sport, event_id = event
        payload = self._client.event_odds(
            sport=sport, event_id=event_id, markets=SOCCER_MARKETS,
            bookmakers=self._bookmakers or None,
        )
        if not payload:
            return None
        return self.normalize_event(payload, match)

    def get_h2h_odds(self, matches: list[Match]) -> dict[int, dict[str, float]]:
        """1x2 em LOTE: 1 chamada por sport key (barato), casa por nome de time.
        Devolve {match_id: {home, draw, away}}."""
        if not matches:
            return {}
        # Junta todos os eventos h2h dos sport keys num índice.
        events: list[dict] = []
        for sport in self._sport_keys:
            events.extend(self._h2h_for_sport(sport))
        out: dict[int, dict[str, float]] = {}
        for m in matches:
            ev = next(
                (e for e in events
                 if _name_matches(e.get("home_team", ""), m.home_team.name)
                 and _name_matches(e.get("away_team", ""), m.away_team.name)),
                None,
            )
            if ev is None:
                continue
            main = self._extract_h2h(ev, m.home_team.name, m.away_team.name)
            if main:
                out[m.id] = main
        return out

    @staticmethod
    def _extract_h2h(ev: dict, home: str, away: str) -> dict[str, float]:
        """Média de home/draw/away entre books do evento."""
        acc: dict[str, list[float]] = {"home": [], "draw": [], "away": []}
        for book in ev.get("bookmakers", []):
            for mkt in book.get("markets", []):
                if mkt.get("key") != "h2h":
                    continue
                for o in mkt.get("outcomes", []):
                    name = o.get("name", "")
                    price = o.get("price")
                    if price is None:
                        continue
                    if name.lower() == "draw":
                        acc["draw"].append(float(price))
                    elif _name_matches(name, home):
                        acc["home"].append(float(price))
                    elif _name_matches(name, away):
                        acc["away"].append(float(price))
        import statistics as _st
        return {k: round(_st.mean(v), 2) for k, v in acc.items() if v}

    def _resolve_event(self, match: Match) -> Optional[tuple[str, str]]:
        """Varre os sport keys e acha o evento cujos times batem com o jogo."""
        for sport in self._sport_keys:
            for ev in self._events_for(sport):
                if (
                    _name_matches(ev.get("home_team", ""), match.home_team.name)
                    and _name_matches(ev.get("away_team", ""), match.away_team.name)
                ):
                    return sport, ev.get("id", "")
        return None

    # --- normalização (estática, pura) -------------------------------------

    @staticmethod
    def normalize_event(payload: dict, match: Match) -> MatchOdds:
        """Traduz o payload cru do The Odds API em MatchOdds canônico."""
        home_name = match.home_team.name
        away_name = match.away_team.name
        markets: dict[str, list[Selection]] = {}

        def add(market: str, sel: Selection) -> None:
            markets.setdefault(market, []).append(sel)

        for book in payload.get("bookmakers", []):
            book_name = book.get("title") or book.get("key", "")
            for m in book.get("markets", []):
                key = m.get("key", "")
                for o in m.get("outcomes", []):
                    oname = o.get("name", "")
                    price = o.get("price")
                    point = o.get("point")
                    if price is None:
                        continue
                    try:
                        price = float(price)
                    except (TypeError, ValueError):
                        continue
                    line = None
                    if point is not None:
                        try:
                            line = float(point)
                        except (TypeError, ValueError):
                            line = None

                    canon = _canon_selection(
                        key, oname, home_name, away_name,
                    )
                    if canon is None:
                        continue
                    market_key, sel_name, sel_line = canon
                    if sel_line is None:
                        sel_line = line
                    # Handicap: a linha canônica é SEMPRE do ponto de vista do
                    # mandante. O point do visitante vem simétrico, então
                    # invertemos o sinal pra deixar tudo no eixo do mandante.
                    if market_key == "asian_handicap" and sel_name == "away" and sel_line is not None:
                        sel_line = -sel_line
                    add(market_key, Selection(
                        bookmaker=book_name, name=sel_name, price=price,
                        line=sel_line,
                    ))

        return MatchOdds(
            match_id=match.id,
            fetched_at=time.time(),
            markets={k: MarketOdds(k, v) for k, v in markets.items()},
        )


def _canon_selection(
    market_key: str, outcome_name: str, home: str, away: str,
) -> Optional[tuple[str, str, Optional[float]]]:
    """Mapeia (market, outcome) do The Odds API → (market_canon, seleção, linha).

    Retorna None quando não modelamos o mercado/seleção.
    """
    name = outcome_name.strip()
    low = name.lower()

    if market_key == "h2h":
        if low == "draw":
            return "1x2", "draw", None
        if _name_matches(name, home):
            return "1x2", "home", None
        if _name_matches(name, away):
            return "1x2", "away", None
        return None

    if market_key == "totals":
        if low.startswith("over"):
            return "over_under", "over", None
        if low.startswith("under"):
            return "over_under", "under", None
        return None

    if market_key == "btts":
        if low in ("yes", "y"):
            return "btts", "yes", None
        if low in ("no", "n"):
            return "btts", "no", None
        return None

    if market_key == "draw_no_bet":
        if _name_matches(name, home):
            return "dnb", "home", None
        if _name_matches(name, away):
            return "dnb", "away", None
        return None

    if market_key == "double_chance":
        # Outcomes podem vir como "Home/Draw" ou nomes de time. Tratamos os
        # rótulos textuais comuns.
        canon = {
            "home/draw": "home_draw", "draw/home": "home_draw",
            "home/away": "home_away", "away/home": "home_away",
            "draw/away": "draw_away", "away/draw": "draw_away",
        }.get(low)
        if canon:
            return "double_chance", canon, None
        return None

    if market_key == "spreads":
        # Handicap asiático/europeu: outcome = nome do time, point = handicap.
        if _name_matches(name, home):
            return "asian_handicap", "home", None  # linha vem do point (home pov)
        if _name_matches(name, away):
            # point do visitante é o simétrico; convertemos pro pov do mandante
            return "asian_handicap", "away", None
        return None

    return None
