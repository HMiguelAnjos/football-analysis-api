"""
Card features worker — recalcula as features de cartão em background (Fase A).
A cada CARD_WORKER_INTERVAL_SECONDS: features rolantes + média do árbitro +
registra/liquida previsões (calibração). Endpoint só LÊ. Best-effort.
"""

from __future__ import annotations

import asyncio
import logging

from src import config

logger = logging.getLogger(__name__)

INITIAL_DELAY_SECONDS = 120


async def start_card_features_worker(
    *,
    interval_seconds: int = config.CARD_WORKER_INTERVAL_SECONDS,
) -> asyncio.Task:
    async def _loop():
        await asyncio.sleep(INITIAL_DELAY_SECONDS)
        while True:
            try:
                await asyncio.to_thread(_tick_once)
            except Exception as exc:  # noqa: BLE001
                logger.warning("card features worker: tick falhou (%s)", exc)
            await asyncio.sleep(interval_seconds)

    task = asyncio.create_task(_loop())
    logger.info("card features worker iniciado (interval=%ds)", interval_seconds)
    return task


def _tick_once() -> None:
    from src.db.database import SessionLocal
    from src.services.football.card_calibration_service import (
        log_pendurados, log_predictions, settle_pendurados, settle_predictions,
    )
    from src.services.football.card_features_service import refresh_upcoming
    from src.services.football.data_service import FootballDataService

    db = SessionLocal()
    data = FootballDataService()
    try:
        stats = refresh_upcoming(db, data)
        logged = log_predictions(db, data)
        settled = settle_predictions(db, data)
        plog = log_pendurados(db, data)
        pset = settle_pendurados(db, data)
        logger.info("card features: %s | prev+%d liq+%d | pendurado log+%d liq+%d",
                    stats, logged, settled, plog, pset)
    finally:
        db.close()
