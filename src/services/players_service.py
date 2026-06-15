"""
PlayersService — ingestão de jogadores no banco (tabela football_players).

"Popular o banco": busca o elenco da competição (via data_service, cacheado em
disco) e faz UPSERT em football_players com stats + índices compostos no payload
JSON. Assim os dados ficam num banco real (queryável, durável) além do cache.

Best-effort: sem banco, loga e segue.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from src.db.models import FootballPlayer
from src.metrics.player_index import compute_indices

logger = logging.getLogger(__name__)


def populate_players(db: Session, data_service, *, context: str = "general") -> dict:
    """Busca os jogadores da competição e grava/atualiza em football_players
    (stats + índices IPO/ICJ/ID/IIP no payload). Retorna {upserted, total}."""
    players = data_service.competition_players(context)
    if not players:
        return {"upserted": 0, "total": 0}

    indexed = {pi.player.id: pi for pi in compute_indices(players)}
    upserted = 0
    for p in players:
        pi = indexed.get(p.id)
        payload = p.model_dump(mode="json")
        payload["context"] = context
        if pi is not None:
            payload["indices"] = {"ipo": pi.ipo, "icj": pi.icj, "id": pi.id, "iip": pi.iip}
        row = db.get(FootballPlayer, p.id)
        if row is None:
            row = FootballPlayer(id=p.id, name=p.name, team_id=p.team_id or None,
                                 payload=payload)
            db.add(row)
        else:
            row.name = p.name
            row.team_id = p.team_id or None
            row.payload = payload
        upserted += 1
    db.commit()
    logger.info("players populate (%s): %d jogadores", context, upserted)
    return {"upserted": upserted, "total": len(players)}
