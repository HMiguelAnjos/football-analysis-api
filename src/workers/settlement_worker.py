"""
Settlement worker — liquida recomendações pending em background.

Roda a cada SETTLEMENT_INTERVAL_SECONDS: lê pending do Postgres, busca o
resultado final dos jogos terminados (via FootballDataService) e marca
hit/miss/push/void. Best-effort: banco/provider fora do ar = loga e segue.
"""

from __future__ import annotations

import asyncio
import logging

from src import config

logger = logging.getLogger(__name__)

INITIAL_DELAY_SECONDS = 60


async def start_settlement_worker(
    *,
    interval_seconds: int = config.SETTLEMENT_INTERVAL_SECONDS,
) -> asyncio.Task:
    async def _loop():
        await asyncio.sleep(INITIAL_DELAY_SECONDS)
        while True:
            try:
                await asyncio.to_thread(_tick_once)
            except Exception as exc:  # noqa: BLE001
                logger.warning("settlement worker: tick falhou (%s)", exc)
            await asyncio.sleep(interval_seconds)

    task = asyncio.create_task(_loop())
    logger.info(
        "settlement worker iniciado (interval=%ds, debounce=%ds)",
        interval_seconds, INITIAL_DELAY_SECONDS,
    )
    return task


def _tick_once() -> None:
    from src.db.database import SessionLocal
    from src.services.football.data_service import FootballDataService
    from src.services.pick_results_service import settle_finished

    db = SessionLocal()
    try:
        stats = settle_finished(db, FootballDataService())
        if stats.get("settled") or stats.get("errors"):
            logger.info("settlement: %s", stats)
    finally:
        db.close()
