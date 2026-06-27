"""
PESOS e FAIXAS de normalização — arquivo central de calibração.

NADA de número mágico espalhado pelo código: todo peso de score, toda faixa
(min/max) de normalização e todo limiar de grade vivem aqui. Ajustar o modelo
depois = mexer só neste arquivo (de preferência validando contra o histórico
em ``football_pick_results``).

Convenção: cada dicionário de pesos soma ~1.0. As chaves batem com os nomes
das features em ``features.py``.
"""

from __future__ import annotations

# ─── Faixas de normalização (valor bruto → 0–100) ────────────────────────────
# (lo, hi): lo → 0, hi → 100. Calibráveis. Por jogo, salvo indicação.
NORM: dict[str, tuple[float, float]] = {
    # Ofensivo
    "xg": (0.4, 2.6),
    "xg_per_shot": (0.05, 0.18),
    "big_chances": (0.5, 4.0),
    "box_touches": (15.0, 45.0),
    "shots_on_target": (1.5, 7.0),
    "goals_for": (0.4, 2.6),
    "off_form": (0.4, 2.6),            # gols marcados recentes (proxy)
    # Criação
    "xa": (0.4, 2.2),
    "key_passes": (5.0, 16.0),
    "progressive_passes": (20.0, 60.0),
    "final_third_passes": (25.0, 75.0),
    "accurate_crosses": (2.0, 10.0),
    # Defesa (maior = mais frágil)
    "xga": (0.4, 2.6),
    "big_chances_conceded": (0.5, 4.0),
    "shots_on_target_conceded": (1.5, 7.0),
    "goals_conceded_recent": (0.4, 2.6),
    "def_errors": (0.0, 2.0),
    "ppda": (6.0, 16.0),               # alto = pressiona pouco (defesa pior)
    # Momento
    "ppg": (0.0, 3.0),                 # pontos por jogo
    "xg_trend": (-1.0, 1.0),           # variação de saldo de xG
    "streak": (-5.0, 5.0),             # sequência (+vitórias / -derrotas)
    # Pressão
    "high_recoveries": (3.0, 12.0),
    "final_third_entries": (15.0, 45.0),
    "off_possession": (35.0, 65.0),    # posse no campo ofensivo (proxy: posse)
    "shots_after_recovery": (0.5, 4.0),
    # Escanteios
    "corners_for": (3.0, 8.0),
    "corners_against": (3.0, 8.0),
    "crosses": (8.0, 24.0),
    "lateral_attacks": (15.0, 45.0),
    "blocked_shots": (1.0, 5.0),
    # Cartões
    "cards_for": (1.0, 4.0),
    "fouls": (8.0, 18.0),
    # Risco
    "sample": (3.0, 12.0),             # jogos de amostra (poucos = arriscado)
    "injuries": (0.0, 5.0),
    "volatility": (0.3, 1.5),          # desvio de resultados/xG
    # Eficiência (razões gols/xG)
    "att_efficiency": (0.6, 1.4),
    "def_efficiency": (0.6, 1.4),
}

# ─── Pesos por score (somam ~1.0) ────────────────────────────────────────────
OFFENSIVE_THREAT = {
    "xg": 0.30, "big_chances": 0.20, "box_touches": 0.15,
    "shots_on_target": 0.15, "xg_per_shot": 0.10, "off_form": 0.10,
}
CREATION = {
    "xa": 0.30, "key_passes": 0.25, "progressive_passes": 0.15,
    "final_third_passes": 0.15, "big_chances": 0.10, "accurate_crosses": 0.05,
}
DEFENSIVE_FRAGILITY = {
    "xga": 0.30, "big_chances_conceded": 0.25, "shots_on_target_conceded": 0.15,
    "goals_conceded_recent": 0.15, "def_errors": 0.10, "ppda": 0.05,
}
MATCHUP = {
    "off_threat": 0.45, "opp_def_fragility": 0.35, "creation": 0.20,
}
MOMENTUM = {
    "last5": 0.35, "last10": 0.20, "home_away": 0.20, "xg_trend": 0.15, "streak": 0.10,
}
PRESSURE = {
    "ppda": 0.30, "high_recoveries": 0.20, "final_third_entries": 0.20,
    "off_possession": 0.20, "shots_after_recovery": 0.10,
}
RISK = {
    "sample": 0.20, "volatility": 0.20, "injuries": 0.15, "rotation": 0.15,
    "odd_crushed": 0.10, "model_vs_market": 0.10, "abnormal_context": 0.10,
}
CORNERS_PRESSURE = {
    "crosses": 0.20, "lateral_attacks": 0.15, "blocked_shots": 0.10,
    "final_third_entries": 0.15, "off_pressure": 0.15,
    "corners_for": 0.15, "opp_corners_against": 0.10,
}
CARDS_TENSION = {
    "cards_for": 0.25, "fouls": 0.20, "derby": 0.15, "knockout": 0.15,
    "importance": 0.10, "referee": 0.10, "tight_score": 0.05,
}
LIVE_GAME_STATE = {
    "game_minute": 0.15, "score_urgency": 0.25, "recent_pressure": 0.25,
    "live_momentum": 0.15, "odds_value": 0.10, "fatigue": 0.05, "card_risk": 0.05,
}

# ─── Pesos de combinação por mercado (markets.py / live.py) ───────────────────
# edgeScore de cada mercado = combinação dos scores de jogo já calculados.
MARKET_OVER = {
    "home_off": 0.25, "away_off": 0.25, "home_def_frag": 0.15,
    "away_def_frag": 0.15, "matchup": 0.10, "off_momentum": 0.10,
}
MARKET_BTTS = {
    "home_off": 0.25, "away_off": 0.25, "home_def_frag": 0.20,
    "away_def_frag": 0.20, "recent_freq": 0.10,
}
MARKET_1X2 = {
    "matchup": 0.45, "opp_matchup_inv": 0.20, "momentum": 0.15,
    "home_field": 0.10, "defense": 0.10,
}
MARKET_CORNERS = {"corners_pressure": 0.6, "off_pressure": 0.25, "matchup": 0.15}
MARKET_CARDS = {"cards_tension": 0.7, "tight_match": 0.30}
MARKET_SHOTS = {
    "off_threat": 0.35, "opp_def_frag": 0.25, "shots_on_target": 0.20,
    "pressure": 0.20,
}

# ─── Grade (edge + risco → A+/A/B/C/AVOID) ───────────────────────────────────
# Cada tier: (edge_min, risk_max). Avaliado de cima pra baixo.
GRADE_TIERS: list[tuple[str, float, float]] = [
    ("A+", 85.0, 25.0),
    ("A", 78.0, 35.0),
    ("B", 70.0, 45.0),
    ("C", 62.0, 55.0),
]
GRADE_FALLBACK = "AVOID"
# Risco acima disso → AVOID direto, independente do edge.
RISK_HARD_CAP = 70.0

# Confiança = edge ajustado pelo risco (0–100).
CONFIDENCE_RISK_PENALTY = 0.45        # quanto o risco corrói a confiança
