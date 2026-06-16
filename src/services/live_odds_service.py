"""
Refresh de odds POR EVENTO (+ fallback).

Em vez de re-buscar odds de todos os jogos a cada N minutos (caro na The Odds
API: event_odds custa markets×regions POR jogo), reaquecemos a odd de um jogo
ao vivo quando:
  • sai GOL (muda o placar), ou
  • sai EXPULSÃO (muda o nº de vermelhos), ou
  • passaram 10 min do último refresh (fallback — odd nunca fica mais velha).

A detecção de gol/expulsão usa chamadas baratas da api-football; a odd cara
(The Odds API) só é tocada no refresh. Há um TETO de refreshes por tick
(backstop de gasto). `detect_state_changes` é PURA e testável.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

from src import config
from src.providers.base import Match

logger = logging.getLogger(__name__)

# Estado relevante de um jogo: (placar_casa, placar_fora, verm_casa, verm_fora).
State = tuple[int, int, int, int]


def detect_state_changes(
    prev: dict[int, State], states: dict[int, State],
) -> set[int]:
    """IDs cujo estado (placar OU expulsões) mudou vs o snapshot anterior.

    Jogo que ACABOU de entrar no ar (sem snapshot) NÃO conta como mudança — só
    registra o estado inicial, pra não disparar refresh à toa no primeiro tick.
    """
    changed: set[int] = set()
    for mid, st in states.items():
        before = prev.get(mid)
        if before is not None and before != st:
            changed.add(mid)
    return changed


class LiveOddsRefresher:
    """Mantém o estado (placar+expulsões) e o último refresh por jogo; a cada
    tick reaquece odds de quem teve gol/expulsão ou estourou o fallback de 10
    min. Stateful por instância (vive no worker)."""

    def __init__(self, data_service, contexts: list[str]) -> None:
        self._data = data_service
        self._contexts = contexts
        self._snapshots: dict[str, dict[int, State]] = {c: {} for c in contexts}
        # Último refresh (monotonic) por (contexto, match_id) — base do fallback.
        self._last_refresh: dict[tuple[str, int], float] = {}

    def _any_match_in_live_window(self, context: str) -> bool:
        """True se algum jogo do contexto está (ou deveria estar) ao vivo agora.
        Usa dados já cacheados (grátis) + hora do kickoff — robusto a status
        desatualizado. Fora da janela, nem chamamos a api-football."""
        now = datetime.now(timezone.utc)
        window = timedelta(hours=config.LIVE_WINDOW_HOURS)
        try:
            matches = self._data.matches_domain_for(None, context)
        except Exception:  # noqa: BLE001
            return False
        for m in matches:
            if m.status == "live":
                return True
            if (m.status != "finished" and m.utc_kickoff is not None
                    and m.utc_kickoff <= now <= m.utc_kickoff + window):
                return True
        return False

    def _state_of(self, m: Match, context: str) -> State:
        """(placar_casa, placar_fora, verm_casa, verm_fora) do jogo agora."""
        sh = int(m.home_goals or 0)
        sa = int(m.away_goals or 0)
        rh, ra = self._data._red_cards(m, context)
        return (sh, sa, rh, ra)

    def _refresh(self, m: Match, context: str, stats: dict) -> None:
        self._data.invalidate_odds(m.id, context)
        try:
            self._data.match_odds_domain(m, context=context, force=True)
            self._last_refresh[(context, m.id)] = time.monotonic()
            stats["refreshed"] += 1
        except Exception:  # noqa: BLE001 — provider instável não derruba o loop
            logger.warning("live odds: falha reaquecendo jogo %s", m.id)

    def tick(self) -> dict:
        """Um ciclo: reaquece odds de jogos com gol/expulsão ou fallback 10min,
        respeitando o teto de refreshes por tick."""
        stats = {"polled": 0, "live": 0, "changed": 0, "refreshed": 0, "fallback": 0}
        now = time.monotonic()
        fallback = config.LIVE_ODDS_FALLBACK_SECONDS
        budget = config.LIVE_ODDS_MAX_REFRESH_PER_TICK

        for ctx in self._contexts:
            if not self._any_match_in_live_window(ctx):
                continue
            stats["polled"] += 1
            live = [m for m in self._data.live_matches(ctx)
                    if m.home_goals is not None and m.away_goals is not None]
            stats["live"] += len(live)

            states = {m.id: self._state_of(m, ctx) for m in live}
            changed = detect_state_changes(self._snapshots.get(ctx, {}), states)
            self._snapshots[ctx] = states
            stats["changed"] += len(changed)

            for m in live:
                if stats["refreshed"] >= budget:
                    logger.info("live odds: teto de %d refreshes/tick atingido", budget)
                    break
                last = self._last_refresh.get((ctx, m.id))
                due_fallback = last is None or (now - last) >= fallback
                if m.id in changed:
                    self._refresh(m, ctx, stats)
                elif due_fallback:
                    self._refresh(m, ctx, stats)
                    stats["fallback"] += 1
        return stats
