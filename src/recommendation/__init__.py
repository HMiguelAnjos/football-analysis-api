"""Engine de recomendação de apostas de valor."""

from src.recommendation.engine import (
    RecommendationCandidate,
    generate_recommendations,
)

__all__ = ["RecommendationCandidate", "generate_recommendations"]
