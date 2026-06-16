"""Engine de probabilidade — puro, determinístico, testável offline."""

from src.probability.edge import (
    confidence_score,
    edge,
    fair_odd,
    implied_probability,
    remove_vig,
)
from src.probability.live import inplay_market_probs, remaining_fraction
from src.probability.poisson import ScoreMatrix, build_score_matrix, poisson_pmf
from src.probability.ratings import TeamRatings, compute_ratings
from src.probability.team_strength import LeagueAverages, expected_goals

__all__ = [
    "build_score_matrix",
    "ScoreMatrix",
    "poisson_pmf",
    "expected_goals",
    "LeagueAverages",
    "TeamRatings",
    "compute_ratings",
    "inplay_market_probs",
    "remaining_fraction",
    "implied_probability",
    "remove_vig",
    "fair_odd",
    "edge",
    "confidence_score",
]
