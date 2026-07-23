"""
Corner features worker — recalcula as features rolantes de escanteio em
background (Fase 1). Roda a cada CORNER_WORKER_INTERVAL_SECONDS: para os times
com jogo próximo, coleta stats, aplica decaimento e faz UPSERT das features.

Best-effort: banco/provider fora do ar = loga e segue. O endpoint só LÊ features
já prontas, então uma falha aqui nunca derruba a requisição do usuário.
"""

from __future__ import annotations

import asyncio
import logging

from src import config

logger = logging.getLogger(__name__)

INITIAL_DELAY_SECONDS = 90


async def start_corner_features_worker(
    *,
    interval_seconds: int = config.CORNER_WORKER_INTERVAL_SECONDS,
) -> asyncio.Task:
    async def _loop():
        await asyncio.sleep(INITIAL_DELAY_SECONDS)
        while True:
            try:
                await asyncio.to_thread(_tick_once)
            except Exception as exc:  # noqa: BLE001
                logger.warning("corner features worker: tick falhou (%s)", exc)
            await asyncio.sleep(interval_seconds)

    task = asyncio.create_task(_loop())
    logger.info("corner features worker iniciado (interval=%ds)", interval_seconds)
    return task


def _tick_once() -> None:
    from src.db.database import SessionLocal
    from src.services.football.corner_calibration_service import (
        log_predictions, settle_predictions,
    )
    from src.services.football.corner_features_service import refresh_upcoming
    from src.services.football.data_service import FootballDataService

    db = SessionLocal()
    data = FootballDataService()
    try:
        stats = refresh_upcoming(db, data)                 # features rolantes
        logged = log_predictions(db, data)                 # calibração: registra previsão
        settled = settle_predictions(db, data)             # calibração: fecha com o real
        logger.info("corner features: %s | previsoes+%d | liquidadas+%d",
                    stats, logged, settled)
    finally:
        db.close()
