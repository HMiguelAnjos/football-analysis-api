"""
UnderstatProvider — métricas avançadas (xG/xA). OPCIONAL e best-effort.

Understat não tem API oficial: os dados vêm como JSON embutido no HTML das
páginas. Este provider é um GANCHO — a estrutura está pronta (implementa
XgProvider), mas a coleta exige mapear team_id (api-football) → slug do
understat, o que depende da liga. Por padrão XG_PROVIDER="none" e o sistema
usa os gols reais como proxy de xG (o blend em team_strength já tolera xG=None).

Quando for implementar: buscar a página da liga/time, extrair o bloco
`JSON.parse('...')` com os dados de xG e mapear pro team_id interno.
"""

from __future__ import annotations

import logging
from typing import Optional

from src import config

logger = logging.getLogger(__name__)


class UnderstatProvider:
    name = "understat"

    def __init__(self, base_url: Optional[str] = None) -> None:
        self._base_url = (base_url or config.UNDERSTAT_BASE_URL).rstrip("/")

    def get_team_xg(self, team_id: int) -> Optional[tuple[float, float]]:
        # Gancho não implementado por padrão — exige mapeamento de slug.
        # Retorna None (degradação graciosa: usa gols reais).
        logger.debug("UnderstatProvider.get_team_xg(%s): não implementado", team_id)
        return None
