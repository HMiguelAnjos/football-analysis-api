"""
Conversões odd ↔ probabilidade e cálculo de edge / value.

- implied_probability: prob. implícita na odd da casa (com a margem embutida).
- remove_vig: tira a margem (overround) de um mercado pra estimar a prob.
  "justa" do mercado — útil pra comparar com o modelo.
- fair_odd: odd justa a partir da prob. do modelo.
- edge (EV): valor esperado de apostar 1 unidade naquela odd, dado o modelo.
- confidence_score: 0-100 combinando magnitude do edge, completude dos dados
  e tamanho de amostra.

PURO.
"""

from __future__ import annotations


def implied_probability(decimal_odd: float) -> float:
    """Probabilidade implícita (inclui a margem da casa)."""
    if decimal_odd <= 1.0:
        return 1.0
    return 1.0 / decimal_odd


def remove_vig(decimal_odds: list[float]) -> list[float]:
    """Normaliza as probs implícitas de um mercado exaustivo pra somarem 1
    (remove o overround). Devolve as probabilidades 'justas' do mercado."""
    implied = [implied_probability(o) for o in decimal_odds]
    total = sum(implied)
    if total <= 0:
        n = len(decimal_odds)
        return [1.0 / n] * n if n else []
    return [p / total for p in implied]


def fair_odd(probability: float) -> float:
    """Odd justa (sem margem) pra uma probabilidade do modelo."""
    if probability <= 0:
        return float("inf")
    return 1.0 / probability


def edge(model_probability: float, decimal_odd: float) -> float:
    """Valor esperado (EV) de apostar 1 unidade.

    EV = p*(odd-1) - (1-p) = p*odd - 1.
    Positivo = aposta de valor segundo o modelo.
    """
    return model_probability * decimal_odd - 1.0


def confidence_score(
    *,
    edge_value: float,
    model_probability: float,
    matches_sample: int,
    has_xg: bool,
) -> float:
    """Heurística 0-100 de confiança na recomendação.

    Combina:
      • magnitude do edge (mais valor → mais confiança), saturando em ~+15%;
      • probabilidade do modelo (jogadas muito improváveis pesam menos);
      • completude dos dados (amostra de jogos + presença de xG).
    Determinística e fácil de re-tunar.
    """
    # Componente de edge: 0 em edge<=0, satura perto de 1 em edge=0.15.
    edge_comp = max(0.0, min(edge_value / 0.15, 1.0))

    # Componente de probabilidade: penaliza caudas (<10% ou >90%).
    prob_comp = 1.0 - abs(model_probability - 0.5) / 0.5  # 1 no 0.5, 0 nas pontas
    prob_comp = max(0.2, prob_comp)

    # Completude de dados.
    sample_comp = min(matches_sample / 10.0, 1.0)
    data_comp = 0.6 * sample_comp + 0.4 * (1.0 if has_xg else 0.0)
    data_comp = max(0.3, data_comp)

    score = 100.0 * (0.55 * edge_comp + 0.20 * prob_comp + 0.25 * data_comp)
    return round(min(max(score, 0.0), 100.0), 1)
