"""
Live odds worker — reaquece odds POR EVENTO em background.

Roda a cada LIVE_ODDS_POLL_SECONDS: detecta gols via 1 chamada agregada barata
(fixtures?live=all) e reaquece só as odds dos jogos que mudaram. Fora da janela
de jogo ao vivo, nem chama a api-football. Best-effort: falha = loga e segue.
"""

from __future__ import annotations

import asyncio
import logging

from src import competition, config

logger = logging.getLogger(__name__)

INITIAL_DELAY_SECONDS = 30


async def start_live_odds_worker(
    *,
    interval_seconds: int = config.LIVE_ODDS_POLL_SECONDS,
) -> asyncio.Task:
    from src.services.football.data_service import FootballDataService
    from src.services.live_odds_service import LiveOddsRefresher

    contexts = [c.key for c in competition.all_contexts()]
    refresher = LiveOddsRefresher(FootballDataService(), contexts)

    async def _loop():
        await asyncio.sleep(INITIAL_DELAY_SECONDS)
        while True:
            try:
                stats = await asyncio.to_thread(refresher.tick)
                if stats.get("changed") or stats.get("refreshed"):
                    logger.info("live odds: %s", stats)
            except Exception as exc:  # noqa: BLE001
                logger.warning("live odds worker: tick falhou (%s)", exc)
            await asyncio.sleep(interval_seconds)

    task = asyncio.create_task(_loop())
    logger.info(
        "live odds worker iniciado (interval=%ds, debounce=%ds)",
        interval_seconds, INITIAL_DELAY_SECONDS,
    )
    return task
