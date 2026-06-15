"""
Refresh de odds POR EVENTO.

Em vez de re-buscar odds de todos os jogos a cada N minutos (caro na The Odds
API: event_odds custa markets×regions POR jogo), detectamos GOLS via 1 chamada
agregada barata (fixtures?live=all na api-football) e só então invalidamos +
reaquecemos as odds do jogo que mudou. Economia enorme: nos ~88 minutos sem gol
de um jogo, zero chamada de odds.

`detect_score_changes` é PURA e testável. O efeito colateral (invalidar/reaquecer)
fica isolado em `LiveOddsRefresher.tick`.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from src import config
from src.providers.base import Match

logger = logging.getLogger(__name__)

Score = tuple[int, int]


def detect_score_changes(
    prev: dict[int, Score], live: list[Match],
) -> tuple[set[int], dict[int, Score]]:
    """Compara o placar atual dos jogos ao vivo com o snapshot anterior.

    Devolve (ids_que_mudaram, novo_snapshot). Um jogo que ACABOU de entrar no ar
    (sem snapshot anterior) NÃO conta como mudança — só registra o placar inicial,
    pra não disparar um refresh à toa no primeiro tick.
    """
    changed: set[int] = set()
    snapshot: dict[int, Score] = {}
    for m in live:
        if m.home_goals is None or m.away_goals is None:
            continue
        score = (int(m.home_goals), int(m.away_goals))
        snapshot[m.id] = score
        before = prev.get(m.id)
        if before is not None and before != score:
            changed.add(m.id)
    return changed, snapshot


class LiveOddsRefresher:
    """Mantém o snapshot de placar por contexto e, a cada tick, reaquece as odds
    dos jogos que sofreram gol. Stateful por instância (vive no worker)."""

    def __init__(self, data_service, contexts: list[str]) -> None:
        self._data = data_service
        self._contexts = contexts
        self._snapshots: dict[str, dict[int, Score]] = {c: {} for c in contexts}

    def _any_match_in_live_window(self, context: str) -> bool:
        """True se algum jogo do contexto está (ou deveria estar) ao vivo agora.
        Usa dados já cacheados (grátis) e a hora do kickoff — robusto a status
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

    def tick(self) -> dict:
        """Um ciclo: pra cada contexto com jogo na janela, busca os ao-vivo
        (1 chamada) e reaquece odds só dos jogos com gol novo."""
        stats = {"polled": 0, "live": 0, "changed": 0, "refreshed": 0}
        for ctx in self._contexts:
            if not self._any_match_in_live_window(ctx):
                continue
            stats["polled"] += 1
            live = self._data.live_matches(ctx)
            stats["live"] += len(live)
            changed, snapshot = detect_score_changes(self._snapshots.get(ctx, {}), live)
            self._snapshots[ctx] = snapshot
            by_id = {m.id: m for m in live}
            for mid in changed:
                self._data.invalidate_odds(mid, ctx)
                m = by_id.get(mid)
                if m is None:
                    continue
                try:
                    self._data.match_odds_domain(m, context=ctx, force=True)
                    stats["refreshed"] += 1
                except Exception:  # noqa: BLE001 — provider instável não derruba o loop
                    logger.warning("live odds: falha reaquecendo jogo %s", mid)
            stats["changed"] += len(changed)
        return stats
