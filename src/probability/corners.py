"""
Modelo PRÉ-JOGO de escanteios (Futebol Analyst — Fase 1, regras 1-3 + 5).

PURO: entra as features rolantes dos dois times (produzidas pelo worker), sai a
expectativa de escanteios do jogo (total + por tempo) e a probabilidade de over
via Poisson. Sem rede, sem I/O, sem cache — 100% testável offline.

Regras implementadas:
  1. Esperado = blend cruzado (o que o time BATE × o que o adversário CONCEDE)
     dos dois lados, ajustado pelo H2H quando há >= H2H_MIN_GAMES confrontos.
  2. Split por tempo = proporção 1T/2T aplicada sobre o total (proporção fixa da
     liga por ora — a api-football não separa escanteio por tempo).
  3. Ajuste de estilo = time com índice ofensivo acima da média da liga ganha um
     boost (configurável) nos escanteios a favor.
  5. Poisson pra a contagem discreta de escanteios.

Regra 4 (pressão do mandante perdendo, ao vivo) fica pra a Fase 2.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.probability.markets import poisson_over_under

# ─── Constantes calibráveis (defaults; o service pode injetar do config) ────
# Proporção fixa da liga entre 1º e 2º tempo. A api não separa escanteio por
# tempo; o 2º tempo tem mais escanteios (padrão do futebol).
DEFAULT_PROP_1T = 0.45
DEFAULT_PROP_2T = 0.55
# H2H entra no blend só com amostra mínima, com peso moderado.
H2H_MIN_GAMES = 3
H2H_WEIGHT = 0.20
# Regra 3: boost nos escanteios a favor de quem ataca mais que a média da liga.
STYLE_BOOST = 0.08                      # +8% (entre 5% e 10%)
# Linha recomendada ~1.5 abaixo do esperado: over provável que ainda paga.
LINE_OFFSET = 1.5
MIN_LINE = 6.5


@dataclass
class TeamCornerFeatures:
    """Features rolantes de escanteio de um time (o worker produz, já com
    decaimento temporal aplicado nas médias)."""
    media_favor_l5: float               # escanteios A FAVOR (média decaída L5)
    media_contra_l5: float              # escanteios SOFRIDOS (média decaída L5)
    media_favor_l10: Optional[float] = None
    media_contra_l10: Optional[float] = None
    indice_estilo_ofensivo: Optional[float] = None
    prop_1t: float = DEFAULT_PROP_1T
    prop_2t: float = DEFAULT_PROP_2T
    sample_size: int = 0


@dataclass
class CornerPrediction:
    expected_total: float
    expected_1t: float
    expected_2t: float
    home_corners: float
    away_corners: float
    line: float
    prob_over: float
    sample: int
    confidence: float                   # 0-100
    used_h2h: bool
    used_style: bool


def expected_corners(
    home: TeamCornerFeatures,
    away: TeamCornerFeatures,
    *,
    h2h_media: Optional[float] = None,
    h2h_games: int = 0,
    league_style_avg: Optional[float] = None,
    style_boost: float = STYLE_BOOST,
    h2h_weight: float = H2H_WEIGHT,
) -> tuple[float, float, float, bool, bool]:
    """Regra 1 + 3. Devolve (total, escanteios_casa, escanteios_fora, usou_h2h,
    usou_estilo).

    Cada lado é o BLEND entre quanto o time bate e quanto o adversário concede —
    somar só o 'a favor' dos dois superestima (foi o que vimos no ledger)."""
    home_c = (home.media_favor_l5 + away.media_contra_l5) / 2.0
    away_c = (away.media_favor_l5 + home.media_contra_l5) / 2.0

    used_style = False
    if league_style_avg:
        if home.indice_estilo_ofensivo and home.indice_estilo_ofensivo > league_style_avg:
            home_c *= (1.0 + style_boost)
            used_style = True
        if away.indice_estilo_ofensivo and away.indice_estilo_ofensivo > league_style_avg:
            away_c *= (1.0 + style_boost)
            used_style = True

    total = home_c + away_c

    used_h2h = False
    if h2h_media and h2h_games >= H2H_MIN_GAMES:
        total = (1.0 - h2h_weight) * total + h2h_weight * h2h_media
        used_h2h = True

    return total, home_c, away_c, used_h2h, used_style


def predict(
    home: TeamCornerFeatures,
    away: TeamCornerFeatures,
    *,
    h2h_media: Optional[float] = None,
    h2h_games: int = 0,
    league_style_avg: Optional[float] = None,
    line: Optional[float] = None,
    style_boost: float = STYLE_BOOST,
    h2h_weight: float = H2H_WEIGHT,
) -> Optional[CornerPrediction]:
    """Previsão completa de escanteios do jogo. None se faltar dado essencial."""
    if home.media_favor_l5 <= 0 or away.media_favor_l5 <= 0:
        return None

    total, home_c, away_c, used_h2h, used_style = expected_corners(
        home, away, h2h_media=h2h_media, h2h_games=h2h_games,
        league_style_avg=league_style_avg, style_boost=style_boost,
        h2h_weight=h2h_weight,
    )
    if total <= 0:
        return None

    # Regra 2: split por tempo. Proporção ponderada pela contribuição de cada
    # time (com props fixas da liga, degenera na constante — mas fica pronto pra
    # quando houver proporção por-time).
    p1 = (home.prop_1t * home_c + away.prop_1t * away_c) / total
    exp_1t = total * p1
    exp_2t = total - exp_1t

    if line is None:
        line = max(MIN_LINE, round(total) - LINE_OFFSET)
    prob = poisson_over_under(total, line)["over"]

    sample = min(home.sample_size, away.sample_size)
    return CornerPrediction(
        expected_total=round(total, 2),
        expected_1t=round(exp_1t, 2),
        expected_2t=round(exp_2t, 2),
        home_corners=round(home_c, 2),
        away_corners=round(away_c, 2),
        line=line,
        prob_over=round(prob, 4),
        sample=sample,
        confidence=round(min(prob, 0.97) * 100, 1),
        used_h2h=used_h2h,
        used_style=used_style,
    )


def decayed_mean(values: list[float], halflife: float = 5.0) -> Optional[float]:
    """Média com DECAIMENTO temporal: `values` em ordem RECENTE-primeiro (índice
    0 = jogo mais recente). peso_i = 0.5 ** (i / halflife) → jogos recentes pesam
    mais que os antigos (regra geral do spec: não é média simples). None se vazio."""
    vals = [float(v) for v in values if v is not None]
    if not vals:
        return None
    weights = [0.5 ** (i / halflife) for i in range(len(vals))]
    wsum = sum(weights)
    return round(sum(v * w for v, w in zip(vals, weights)) / wsum, 4) if wsum else None


def offensive_style_index(shots: float, shots_insidebox: float,
                          possession: float) -> Optional[float]:
    """Índice de estilo ofensivo = (finalizações + finalizações na área) / posse.
    Adaptado: o spec pedia cruzamentos, mas a api-football não fornece — troca
    por finalizações na área (proxy de intenção ofensiva). None sem posse."""
    if not possession or possession <= 0:
        return None
    return round((shots + shots_insidebox) / possession, 4)
