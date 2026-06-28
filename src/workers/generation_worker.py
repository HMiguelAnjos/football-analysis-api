"""
Generation worker — gera e PERSISTE as recomendações pré-jogo em background.

Sem ele, o banco fica vazio (os feeds da tela são calculados na hora e não
gravam) e não há o que o settlement worker liquidar. Roda a cada
GENERATION_INTERVAL_SECONDS pros contextos geral + Copa. Best-effort: banco/
provider fora do ar = loga e segue.
"""

from __future__ import annotations

import asyncio
import logging

from src import config

logger = logging.getLogger(__name__)

INITIAL_DELAY_SECONDS = 45


async def start_generation_worker(
    *,
    interval_seconds: int = config.GENERATION_INTERVAL_SECONDS,
) -> asyncio.Task:
    async def _loop():
        await asyncio.sleep(INITIAL_DELAY_SECONDS)
        while True:
            try:
                await asyncio.to_thread(_tick_once)
            except Exception as exc:  # noqa: BLE001
                logger.warning("generation worker: tick falhou (%s)", exc)
            await asyncio.sleep(interval_seconds)

    task = asyncio.create_task(_loop())
    logger.info("generation worker iniciado (interval=%ds)", interval_seconds)
    return task


def _tick_once() -> None:
    from src.db.database import SessionLocal
    from src.services.football.generation_service import GenerationService

    db = SessionLocal()
    try:
        gen = GenerationService()
        for ctx in ("general", "world_cup"):
            try:
                res = gen.generate(db, context=ctx)
                logger.info(
                    "generation[%s]: %d persistidas (mercado=%d, props=%d, jogos=%d)",
                    ctx, res.get("persisted", 0), res.get("market_model", 0),
                    res.get("player_props", 0), res.get("matches_analyzed", 0),
                )
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                logger.warning("generation[%s] falhou (%s)", ctx, exc)
    finally:
        db.close()
