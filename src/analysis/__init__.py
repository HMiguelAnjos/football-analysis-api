"""
Camada de ANÁLISE com scores (0–100) para futebol.

Pura, determinística, sem I/O / rede / cache — espelha o padrão de
``probability/`` e ``recommendation/``. Tudo aqui é unit-testável offline.

Fluxo:
    domínio (TeamForm/MatchStatistics/...) → features.py (extração + nulos)
    → scores.py (FootballAnalysisEngine, 11 scores) → markets.py / live.py
    (recomendações por mercado + grade + explicabilidade).

Os PESOS e FAIXAS de normalização ficam todos em ``weights.py`` (1 arquivo
central, ajustável) — nada de número mágico espalhado pelo código.
"""

from src.analysis.grade import confidence, grade
from src.analysis.helpers import (
    clamp, invert_score, normalize, safe_divide, weighted_average,
)
from src.analysis.markets import AnalysisRecommendation, MarketRecommendationEngine
from src.analysis.scores import FootballAnalysisEngine, ScoreResult

__all__ = [
    "clamp", "invert_score", "normalize", "safe_divide", "weighted_average",
    "FootballAnalysisEngine", "ScoreResult",
    "MarketRecommendationEngine", "AnalysisRecommendation", "grade", "confidence",
]
