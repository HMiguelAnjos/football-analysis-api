"""
Modelo IN-PLAY (ao vivo) — probabilidades dos mercados a partir do estado atual
do jogo (placar + minuto), não do 0×0.

Ideia: os gols esperados pré-jogo (lambda) valem pro jogo INTEIRO. Ao vivo, só
resta o tempo que falta — então escalamos o lambda pela fração restante, geramos
a distribuição dos gols QUE AINDA VÃO SAIR (Poisson/Dixon-Coles via build_score_
matrix) e somamos ao placar atual pra obter a distribuição do placar FINAL. Os
mercados (1X2, over/under, ambas marcam) saem dessa distribuição final.

Exemplo: Casa perdendo 0×1 aos 80' → resta ~11% do jogo → poucos gols esperados
→ P(Casa vencer) despenca. É o que o modelo pré-jogo não enxergava.

PURO e determinístico — só matemática sobre números. Sem rede, sem estado.
"""

from __future__ import annotations

from src.probability.poisson import build_score_matrix, poisson_pmf

# Minuto "cheio" de um jogo. Acréscimos contam como o finzinho da fração.
_FULL_MATCH_MIN = 90.0
# Piso da fração restante: mesmo nos acréscimos ainda pode sair gol.
_MIN_FRACTION = 0.02
# Lambda mínimo do tempo restante (evita matriz degenerada).
_MIN_REMAINING_LAMBDA = 0.01
# Efeito de CARTÃO VERMELHO no resto do jogo: o time com um a menos marca menos
# (×0.70 por expulsão própria) e o adversário marca mais (×1.30). Valores
# empíricos moderados — compõem se houver mais de uma expulsão.
_RED_SELF = 0.70
_RED_OPP = 1.30

# MOMENTUM (pressão ao vivo): peso de cada estatística no "score de pressão".
_PRESSURE_WEIGHTS = {
    "shots_on_goal": 1.0,
    "total_shots": 0.4,
    "shots_insidebox": 0.5,
    "corner_kicks": 0.10,
    "ball_possession": 0.03,
    "dangerous_attacks": 0.05,
}
# Força do efeito de momentum no lambda restante (0 = ignora). 0.30 → time
# claramente pressionando (70% da pressão) marca ~+12% no tempo que falta.
_MOMENTUM_STRENGTH = 0.30
_MOMENTUM_LO, _MOMENTUM_HI = 0.80, 1.25
# Abaixo de N finalizações somadas, a amostra é fraca demais → sem momentum.
_MOMENTUM_MIN_SHOTS = 4


def team_pressure(stats: dict) -> float:
    """Score de pressão de um time a partir das estatísticas ao vivo do jogo."""
    if not stats:
        return 0.0
    return sum(w * float(stats.get(k, 0) or 0) for k, w in _PRESSURE_WEIGHTS.items())


def momentum_multipliers(
    home_stats: dict, away_stats: dict,
) -> tuple[float, float]:
    """Multiplicadores (casa, fora) do lambda restante pela PRESSÃO atual.
    1.0 = neutro. Time pressionando > 1; pressionado < 1. Cedo no jogo (poucas
    finalizações) devolve (1.0, 1.0) — sinal fraco demais."""
    total_shots = (float((home_stats or {}).get("total_shots", 0) or 0)
                   + float((away_stats or {}).get("total_shots", 0) or 0))
    if total_shots < _MOMENTUM_MIN_SHOTS:
        return (1.0, 1.0)
    ph = team_pressure(home_stats)
    pa = team_pressure(away_stats)
    total = ph + pa
    if total <= 0:
        return (1.0, 1.0)
    share_home = ph / total                       # 0..1 (0.5 = equilíbrio)
    delta = (share_home - 0.5) * 2 * _MOMENTUM_STRENGTH
    mh = min(max(1.0 + delta, _MOMENTUM_LO), _MOMENTUM_HI)
    ma = min(max(1.0 - delta, _MOMENTUM_LO), _MOMENTUM_HI)
    return (mh, ma)


def remaining_fraction(minute: float | None) -> float:
    """Fração do jogo que ainda falta (0..1) a partir do minuto atual."""
    m = minute or 0
    if m < 0:
        m = 0
    if m >= _FULL_MATCH_MIN:
        return _MIN_FRACTION
    return max((_FULL_MATCH_MIN - m) / _FULL_MATCH_MIN, _MIN_FRACTION)


def inplay_market_probs(
    lambda_home: float,
    lambda_away: float,
    minute: float | None,
    score_home: int,
    score_away: int,
    *,
    red_home: int = 0,
    red_away: int = 0,
    mom_home: float = 1.0,
    mom_away: float = 1.0,
    ou_lines: tuple[float, ...] = (2.5,),
) -> dict[str, float]:
    """Probabilidades dos mercados pro PLACAR FINAL, dado o estado atual.

    `red_home`/`red_away` = nº de expulsões de cada lado; `mom_home`/`mom_away` =
    multiplicador de momentum (pressão ao vivo). Ambos ajustam os gols esperados
    do TEMPO RESTANTE. Devolve {home, draw, away, btts_yes, btts_no,
    over_<linha>, under_<linha>}.
    """
    frac = remaining_fraction(minute)
    rem_h = max(lambda_home * frac, _MIN_REMAINING_LAMBDA)
    rem_a = max(lambda_away * frac, _MIN_REMAINING_LAMBDA)
    # Ajuste por expulsão: cada vermelho próprio reduz o ataque; cada vermelho
    # do adversário aumenta o seu (vale só pro tempo que falta).
    if red_home or red_away:
        rh = max(int(red_home), 0)
        ra = max(int(red_away), 0)
        rem_h *= (_RED_SELF ** rh) * (_RED_OPP ** ra)
        rem_a *= (_RED_SELF ** ra) * (_RED_OPP ** rh)
    # Ajuste por momentum (quem está pressionando marca mais no resto do jogo).
    rem_h = max(rem_h * mom_home, _MIN_REMAINING_LAMBDA)
    rem_a = max(rem_a * mom_away, _MIN_REMAINING_LAMBDA)
    sm = build_score_matrix(rem_h, rem_a)  # distribuição dos gols QUE FALTAM

    sh = max(int(score_home), 0)
    sa = max(int(score_away), 0)

    p_home = p_draw = p_away = 0.0
    p_btts_yes = 0.0
    p_over = {line: 0.0 for line in ou_lines}

    n = sm.max_goals
    for i in range(n + 1):
        for j in range(n + 1):
            p = sm.matrix[i][j]
            if p <= 0:
                continue
            fh = sh + i   # placar final do mandante
            fa = sa + j   # placar final do visitante
            if fh > fa:
                p_home += p
            elif fh == fa:
                p_draw += p
            else:
                p_away += p
            if fh >= 1 and fa >= 1:
                p_btts_yes += p
            total_goals = fh + fa
            for line in ou_lines:
                if total_goals > line:
                    p_over[line] += p

    out: dict[str, float] = {
        "home": p_home, "draw": p_draw, "away": p_away,
        "btts_yes": p_btts_yes, "btts_no": 1.0 - p_btts_yes,
    }
    for line in ou_lines:
        out[f"over_{line:g}"] = p_over[line]
        out[f"under_{line:g}"] = 1.0 - p_over[line]
    return out


# Quanto confiamos no RITMO do jogo (vs taxa de temporada). Cresce com os
# minutos jogados, com teto — cedo no jogo, a amostra do jogo é fraca.
_INGAME_TRUST_FULL_MIN = 60.0
_INGAME_TRUST_CAP = 0.70


def live_shots_remaining(season_per90: float, already_on_target: int,
                         minutes_played: float, minutes_left: float,
                         momentum: float = 1.0) -> float:
    """Chutes no gol ADICIONAIS esperados de um jogador no resto do jogo.

    Combina a taxa da TEMPORADA (prior) com o RITMO do jogo atual (quanto o
    jogador já está chutando), pesando o ritmo conforme o tempo já jogado, e
    ajusta pela pressão do time (momentum)."""
    mp = max(minutes_played, 1.0)
    ingame_per90 = (already_on_target / mp) * 90.0
    w = min(mp / _INGAME_TRUST_FULL_MIN, _INGAME_TRUST_CAP)
    rate = (1.0 - w) * max(season_per90, 0.0) + w * ingame_per90
    rate = max(rate, 0.0) * max(momentum, 0.0)
    return rate * (max(minutes_left, 0.0) / 90.0)


def prob_at_least(k: int, lam: float) -> float:
    """P(X >= k) pra X ~ Poisson(lam)."""
    if k <= 0:
        return 1.0
    cdf = sum(poisson_pmf(i, lam) for i in range(k))
    return max(0.0, 1.0 - cdf)
