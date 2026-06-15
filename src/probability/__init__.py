"""Engine de probabilidade — puro, determinístico, testável offline."""

from src.probability.edge import (
    confidence_score,
    edge,
    fair_odd,
    implied_probability,
    remove_vig,
)
from src.probability.poisson import ScoreMatrix, build_score_matrix, poisson_pmf
from src.probability.team_strength import LeagueAverages, expected_goals

__all__ = [
    "build_score_matrix",
    "ScoreMatrix",
    "poisson_pmf",
    "expected_goals",
    "LeagueAverages",
    "implied_probability",
    "remove_vig",
    "fair_odd",
    "edge",
    "confidence_score",
]
