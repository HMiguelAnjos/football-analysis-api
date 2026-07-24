"""
Modelo PRÉ-JOGO de cartões (Futebol Analyst — Fase A: nível de time).

PURO: entra as features rolantes dos dois times + fator do árbitro + flags de
contexto, sai a expectativa de cartões do jogo (total + por tempo) e a prob de
over via Poisson. Sem rede, sem I/O.

Regras (nível de time):
  1. Base do time = media_cartoes_favor_L5, ajustada pelas faltas (correlação).
  2. Árbitro = multiplicador media_arbitro / media_liga (AJUSTE MAIS FORTE).
  3. Local = visitante recebe acréscimo (viés de arbitragem pró-mandante).
  4. Contexto = clássico OU jogo decisivo → boost no total.
  6. Split 1T/2T = proporção histórica REAL de cada time (cartão tem minuto).
  Modelo: Poisson (λ = total esperado).

Regra 5 (pendurado, efeito duplo por jogador) entra na Fase B via
`apply_pendurado_effects` sobre o total do time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.probability.corners import decayed_mean  # reuso do decaimento temporal
from src.probability.markets import poisson_over_under

__all__ = ["TeamCardFeatures", "CardPrediction", "predict", "decayed_mean"]

# ─── Constantes calibráveis (o service injeta os valores do config) ─────────
DEFAULT_PROP_1T = 0.42          # cartão sobe no 2º tempo (jogo mais truncado)
DEFAULT_PROP_2T = 0.58
FOUL_WEIGHT = 0.30              # quanto as faltas (vs média da liga) mexem na base
FOUL_CLAMP = (0.8, 1.3)        # trava o fator de faltas
AWAY_BOOST = 0.08              # +8% pro visitante (regra 3)
CONTEXT_BOOST = 0.15          # +15% em clássico/decisivo (regra 4)
REF_CLAMP = (0.7, 1.5)        # trava o multiplicador do árbitro (evita extremo)
LINE_OFFSET = 1.5
MIN_LINE = 1.5


@dataclass
class TeamCardFeatures:
    media_favor_l5: float               # cartões recebidos/jogo (média decaída L5)
    media_faltas_l5: Optional[float] = None
    media_favor_l10: Optional[float] = None
    prop_1t: float = DEFAULT_PROP_1T
    prop_2t: float = DEFAULT_PROP_2T
    sample_size: int = 0


@dataclass
class CardPrediction:
    expected_total: float
    expected_1t: float
    expected_2t: float
    home_cards: float
    away_cards: float
    line: float
    prob_over: float
    sample: int
    confidence: float
    referee_factor: float
    used_context: bool


def _foul_factor(fouls: Optional[float], league_fouls_avg: Optional[float]) -> float:
    """Faltas acima da média da liga → mais cartão (regra 1). 1.0 sem referência."""
    if not fouls or not league_fouls_avg:
        return 1.0
    raw = 1.0 + FOUL_WEIGHT * (fouls / league_fouls_avg - 1.0)
    return min(max(raw, FOUL_CLAMP[0]), FOUL_CLAMP[1])


def _ref_factor(referee_avg: Optional[float], league_cards_avg: Optional[float]) -> float:
    """Multiplicador do árbitro (regra 2) — o ajuste mais forte. 1.0 sem dado."""
    if not referee_avg or not league_cards_avg:
        return 1.0
    return min(max(referee_avg / league_cards_avg, REF_CLAMP[0]), REF_CLAMP[1])


def predict(
    home: TeamCardFeatures,
    away: TeamCardFeatures,
    *,
    referee_avg: Optional[float] = None,
    league_cards_avg: Optional[float] = None,
    league_fouls_avg: Optional[float] = None,
    is_classico: bool = False,
    is_decisivo: bool = False,
    context_boost: float = CONTEXT_BOOST,
    away_boost: float = AWAY_BOOST,
    line: Optional[float] = None,
    home_extra: float = 0.0,
    away_extra: float = 0.0,
) -> Optional[CardPrediction]:
    """Previsão de cartões do jogo. `home_extra`/`away_extra` são o efeito líquido
    da regra do pendurado (Fase B) — somados ao total de cada lado. None se sem
    base."""
    if home.media_favor_l5 <= 0 or away.media_favor_l5 <= 0:
        return None

    home_c = home.media_favor_l5 * _foul_factor(home.media_faltas_l5, league_fouls_avg)
    away_c = away.media_favor_l5 * _foul_factor(away.media_faltas_l5, league_fouls_avg)
    away_c *= (1.0 + away_boost)                       # regra 3: visitante recebe mais

    # Regra 5 (Fase B): efeito líquido dos pendurados entra por lado.
    home_c = max(home_c + home_extra, 0.0)
    away_c = max(away_c + away_extra, 0.0)

    ref_factor = _ref_factor(referee_avg, league_cards_avg)
    total = (home_c + away_c) * ref_factor             # regra 2: árbitro

    used_context = bool(is_classico or is_decisivo)
    if used_context:                                   # regra 4
        total *= (1.0 + context_boost)

    if total <= 0:
        return None

    # Regra 6: split por tempo real (proporção ponderada pela contribuição).
    p1 = (home.prop_1t * home_c + away.prop_1t * away_c) / (home_c + away_c) \
        if (home_c + away_c) else DEFAULT_PROP_1T
    exp_1t = total * p1
    exp_2t = total - exp_1t

    if line is None:
        line = max(MIN_LINE, round(total) - LINE_OFFSET)
    prob = poisson_over_under(total, line)["over"]

    return CardPrediction(
        expected_total=round(total, 2), expected_1t=round(exp_1t, 2),
        expected_2t=round(exp_2t, 2), home_cards=round(home_c, 2),
        away_cards=round(away_c, 2), line=line, prob_over=round(prob, 4),
        sample=min(home.sample_size, away.sample_size),
        confidence=round(min(prob, 0.97) * 100, 1),
        referee_factor=round(ref_factor, 3), used_context=used_context,
    )
