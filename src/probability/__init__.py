"""Engine de probabilidade — puro, determinístico, testável offline."""

from src.probability.edge import (
    confidence_score,
    edge,
    fair_odd,
    implied_probability,
    remove_vig,
)
from src.probability.live import (
    inplay_market_probs,
    live_shots_remaining,
    momentum_multipliers,
    prob_at_least,
    remaining_fraction,
    team_pressure,
)
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
    "momentum_multipliers",
    "team_pressure",
    "live_shots_remaining",
    "prob_at_least",
    "implied_probability",
    "remove_vig",
    "fair_odd",
    "edge",
    "confidence_score",
]
