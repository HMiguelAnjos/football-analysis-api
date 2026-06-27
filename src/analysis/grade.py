"""
Grade e confiança de uma recomendação.

grade = função (edgeScore, riskScore) → A+ / A / B / C / AVOID, com os limiares
centralizados em ``weights.py``. Risco acima do teto duro → AVOID direto.
"""

from __future__ import annotations

from src.analysis import weights as W
from src.analysis.helpers import clamp


def grade(edge_score: float, risk_score: float) -> str:
    """A+/A/B/C conforme (edge ≥ min) e (risco ≤ max); senão AVOID."""
    if risk_score > W.RISK_HARD_CAP:
        return W.GRADE_FALLBACK
    for name, edge_min, risk_max in W.GRADE_TIERS:
        if edge_score >= edge_min and risk_score <= risk_max:
            return name
    return W.GRADE_FALLBACK


def confidence(edge_score: float, risk_score: float) -> float:
    """Confiança 0–100 = edge corroído pelo risco."""
    return clamp(edge_score - risk_score * W.CONFIDENCE_RISK_PENALTY, 0.0, 100.0)
