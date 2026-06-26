"""
Live reco worker — gera e persiste recomendações ao vivo (foco escanteios).

A cada LIVE_RECO_POLL_SECONDS: tira snapshot das stats dos jogos ao vivo,
calcula deltas (últimos ~10 min), classifica via motor puro e faz UPSERT na
football_live_recommendations. Também liquida over escanteios de jogos
encerrados. Best-effort: sem banco/provider, loga e segue.
"""

from __future__ import annotations

import asyncio
import logging

from src import competition, config

logger = logging.getLogger(__name__)

INITIAL_DELAY_SECONDS = 40


async def start_live_reco_worker(
    *, interval_seconds: int = config.LIVE_RECO_POLL_SECONDS,
) -> asyncio.Task:
    from src.services.football.data_service import FootballDataService
    from src.services.live_reco_service import LiveStatsTracker

    data_service = FootballDataService()
    tracker = LiveStatsTracker()
    contexts = [c.key for c in competition.all_contexts()]

    def _tick_once() -> None:
        from src.db.database import SessionLocal
        from src.services import live_reco_service as lrs

        db = SessionLocal()
        try:
            stats = lrs.generate_for_live_matches(db, data_service, tracker, contexts)
            settled = lrs.settle_finished(db, data_service)
            if stats.get("saved") or settled.get("settled"):
                logger.info("live reco: %s | settle %s", stats, settled)
        finally:
            db.close()

    async def _loop():
        await asyncio.sleep(INITIAL_DELAY_SECONDS)
        while True:
            try:
                await asyncio.to_thread(_tick_once)
            except Exception as exc:  # noqa: BLE001
                logger.warning("live reco worker: tick falhou (%s)", exc)
            await asyncio.sleep(interval_seconds)

    task = asyncio.create_task(_loop())
    logger.info("live reco worker iniciado (interval=%ds)", interval_seconds)
    return task
