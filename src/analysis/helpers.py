"""
Helpers de normalização — base de TODOS os scores.

Regras:
- Tudo entra na escala 0–100 antes de ser combinado.
- Nada quebra com ``None``: ``normalize(None,...)`` devolve ``None`` (o
  combinador de score substitui por 50 neutro e registra warning).
"""

from __future__ import annotations

from typing import Iterable, Optional


def clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    """Prende ``value`` ao intervalo [lo, hi]."""
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value


def normalize(value: Optional[float], lo: float, hi: float) -> Optional[float]:
    """Mapeia ``value`` de [lo, hi] para [0, 100], preso nas bordas.

    ``None`` (dado ausente) → ``None`` (o score decide o fallback). Faixa
    degenerada (hi == lo) → 50 neutro.
    """
    if value is None:
        return None
    if hi == lo:
        return 50.0
    pct = (value - lo) / (hi - lo) * 100.0
    return clamp(pct, 0.0, 100.0)


def invert_score(score: Optional[float]) -> Optional[float]:
    """Inverte um score 0–100 (100 vira 0). Útil quando 'menor é melhor'
    (ex.: PPDA baixo = mais pressão). Propaga ``None``."""
    if score is None:
        return None
    return clamp(100.0 - score, 0.0, 100.0)


def safe_divide(a: Optional[float], b: Optional[float],
                default: float = 0.0) -> float:
    """Divisão que nunca estoura: b == 0 / None / a None → ``default``."""
    if a is None or b is None or b == 0:
        return default
    return a / b


def weighted_average(items: Iterable[tuple[Optional[float], float]]) -> Optional[float]:
    """Média ponderada de ``(valor, peso)``, IGNORANDO valores ``None``
    (renormaliza sobre os pesos presentes). Sem nenhum valor válido → ``None``.

    Para a regra de fallback-neutro-50 da engine, use o combinador do
    ``scores.py`` (este helper é o cru, sem efeitos colaterais)."""
    total_w = 0.0
    acc = 0.0
    for value, weight in items:
        if value is None or weight <= 0:
            continue
        acc += value * weight
        total_w += weight
    if total_w <= 0:
        return None
    return acc / total_w
