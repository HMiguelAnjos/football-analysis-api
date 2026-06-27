"""
FootballAnalysisEngine — os 11 scores (0–100) de futebol.

Cada score devolve um ``ScoreResult`` com:
- ``value``: 0–100 (50 = neutro),
- ``reasons``: leitura humana do que puxou o score (explicabilidade),
- ``warnings``: dados ausentes / alertas.

Regra de ouro: dado faltando NUNCA quebra — vira 50 neutro + warning. Todos os
pesos e faixas vêm de ``weights.py``; aqui não há número mágico.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.analysis import weights as W
from src.analysis.features import LiveFeatures, MatchFeatures, TeamFeatures
from src.analysis.helpers import clamp, invert_score, normalize, weighted_average


@dataclass
class ScoreResult:
    value: float
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _norm(value, key: str):
    """Normaliza ``value`` pela faixa central de ``key`` (None → None)."""
    lo, hi = W.NORM[key]
    return normalize(value, lo, hi)


def _combine(components: list[tuple[str, float | None, float]]) -> tuple[float, list[str]]:
    """Média ponderada com FALLBACK NEUTRO 50: cada componente é
    ``(label, valor|None, peso)``. Valor None → 50 + warning 'label ausente'.
    Devolve (score 0–100, warnings)."""
    warnings: list[str] = []
    acc = 0.0
    total_w = 0.0
    for label, value, weight in components:
        if weight <= 0:
            continue
        if value is None:
            value = 50.0
            warnings.append(f"{label}: dado ausente")
        acc += value * weight
        total_w += weight
    score = acc / total_w if total_w else 50.0
    return clamp(score, 0.0, 100.0), warnings


class FootballAnalysisEngine:
    """Calcula os scores de jogo a partir das features. Sem estado/IO."""

    # ── 3.1 OffensiveThreatScore ─────────────────────────────────────────────
    def offensive_threat(self, tf: TeamFeatures) -> ScoreResult:
        w = W.OFFENSIVE_THREAT
        score, warns = _combine([
            ("xG", _norm(tf.xg, "xg"), w["xg"]),
            ("grandes chances", _norm(tf.big_chances, "big_chances"), w["big_chances"]),
            ("toques na área", _norm(tf.box_touches, "box_touches"), w["box_touches"]),
            ("finalizações no alvo", _norm(tf.shots_on_target, "shots_on_target"), w["shots_on_target"]),
            ("xG por finalização", _norm(tf.xg_per_shot, "xg_per_shot"), w["xg_per_shot"]),
            ("forma ofensiva", _norm(tf.off_form, "off_form"), w["off_form"]),
        ])
        reasons = []
        if score >= 70:
            reasons.append("Ataque oferece muito perigo")
        elif score <= 35:
            reasons.append("Ataque pouco perigoso")
        return ScoreResult(score, reasons, warns)

    # ── 3.2 CreationScore ────────────────────────────────────────────────────
    def creation(self, tf: TeamFeatures) -> ScoreResult:
        w = W.CREATION
        score, warns = _combine([
            ("xA", _norm(tf.xa, "xa"), w["xa"]),
            ("passes-chave", _norm(tf.key_passes, "key_passes"), w["key_passes"]),
            ("passes progressivos", _norm(tf.progressive_passes, "progressive_passes"), w["progressive_passes"]),
            ("passes ao último terço", _norm(tf.final_third_passes, "final_third_passes"), w["final_third_passes"]),
            ("grandes chances", _norm(tf.big_chances, "big_chances"), w["big_chances"]),
            ("cruzamentos certos", _norm(tf.accurate_crosses, "accurate_crosses"), w["accurate_crosses"]),
        ])
        reasons = []
        if score >= 70:
            reasons.append("Cria muitas chances")
        return ScoreResult(score, reasons, warns)

    # ── 3.3 DefensiveFragilityScore (maior = mais frágil) ────────────────────
    def defensive_fragility(self, tf: TeamFeatures) -> ScoreResult:
        w = W.DEFENSIVE_FRAGILITY
        score, warns = _combine([
            ("xGA", _norm(tf.xga, "xga"), w["xga"]),
            ("grandes chances cedidas", _norm(tf.big_chances_conceded, "big_chances_conceded"), w["big_chances_conceded"]),
            ("finalizações no alvo cedidas", _norm(tf.shots_on_target_conceded, "shots_on_target_conceded"), w["shots_on_target_conceded"]),
            ("gols sofridos recentes", _norm(tf.goals_conceded_recent, "goals_conceded_recent"), w["goals_conceded_recent"]),
            ("erros defensivos", _norm(tf.def_errors, "def_errors"), w["def_errors"]),
            ("PPDA", _norm(tf.ppda, "ppda"), w["ppda"]),
        ])
        reasons = []
        if score >= 70:
            reasons.append("Defesa vulnerável")
        elif score <= 35:
            reasons.append("Defesa sólida")
        return ScoreResult(score, reasons, warns)

    # ── 3.4 MatchupScore (ataque do time × defesa do adversário) ─────────────
    def matchup(self, off_threat: float, opp_def_fragility: float,
                creation: float) -> ScoreResult:
        w = W.MATCHUP
        score = weighted_average([
            (off_threat, w["off_threat"]),
            (opp_def_fragility, w["opp_def_fragility"]),
            (creation, w["creation"]),
        ]) or 50.0
        reasons = []
        if off_threat >= 65 and opp_def_fragility >= 65:
            reasons.append("Ataque forte contra defesa frágil")
        return ScoreResult(clamp(score), reasons, [])

    # ── 3.5 MomentumScore ────────────────────────────────────────────────────
    def momentum(self, tf: TeamFeatures) -> ScoreResult:
        w = W.MOMENTUM
        score, warns = _combine([
            ("últimos 5 jogos", _norm(tf.ppg, "ppg"), w["last5"]),
            ("últimos 10 jogos", _norm(tf.last10_ppg, "ppg"), w["last10"]),
            ("casa/fora", _norm(tf.home_away_ppg, "ppg"), w["home_away"]),
            ("tendência de xG", _norm(tf.xg_trend, "xg_trend"), w["xg_trend"]),
            ("sequência", _norm(tf.streak, "streak"), w["streak"]),
        ])
        reasons = []
        if tf.streak is not None and tf.streak >= 3:
            reasons.append("Em sequência de vitórias")
        elif tf.streak is not None and tf.streak <= -3:
            reasons.append("Em má fase recente")
        return ScoreResult(score, reasons, warns)

    # ── 3.6 PressureScore ────────────────────────────────────────────────────
    def pressure(self, tf: TeamFeatures) -> ScoreResult:
        w = W.PRESSURE
        score, warns = _combine([
            ("PPDA", invert_score(_norm(tf.ppda, "ppda")), w["ppda"]),  # baixo PPDA = mais pressão
            ("recuperações altas", _norm(tf.high_recoveries, "high_recoveries"), w["high_recoveries"]),
            ("entradas no último terço", _norm(tf.final_third_entries, "final_third_entries"), w["final_third_entries"]),
            ("posse ofensiva", _norm(tf.off_possession, "off_possession"), w["off_possession"]),
            ("finalizações pós-recuperação", _norm(tf.shots_after_recovery, "shots_after_recovery"), w["shots_after_recovery"]),
        ])
        return ScoreResult(score, [], warns)

    # ── 3.7 EfficiencyScore (+ regressão) ────────────────────────────────────
    def efficiency(self, tf: TeamFeatures) -> ScoreResult:
        att = tf.att_efficiency
        defe = tf.def_efficiency
        reasons: list[str] = []
        warns: list[str] = []
        if att is None:
            warns.append("eficiência ofensiva: dado ausente (xG)")
            score = 50.0
        else:
            score = normalize(att, *W.NORM["att_efficiency"]) or 50.0
            if att >= 1.35:
                reasons.append("Marcou muito acima do xG, risco de regressão")
                warns.append("Conversão acima do esperado (pode regredir)")
                score = clamp(score - (att - 1.35) * 80.0)
            elif att <= 0.8 and (tf.xg or 0) >= 1.0:
                reasons.append("Cria bem, mas finaliza abaixo do esperado")
            elif 0.9 <= att <= 1.15:
                reasons.append("Conversão consistente com o xG")
        if defe is not None and defe >= 1.25:
            reasons.append("Sofre mais gols do que o xGA indica")
        return ScoreResult(clamp(score), reasons, warns)

    # ── 3.8 RiskScore (maior = mais arriscado) ───────────────────────────────
    def risk(self, tf: TeamFeatures, mf: MatchFeatures) -> ScoreResult:
        w = W.RISK
        # Poucos jogos de amostra → mais risco (inverte a normalização).
        risk_sample = invert_score(_norm(float(tf.matches_played), "sample"))
        rotation = 30.0 if mf.knockout else 55.0          # mata-mata rotaciona menos
        abnormal = 65.0 if (mf.knockout or mf.derby) else 40.0
        score, warns = _combine([
            ("amostra", risk_sample, w["sample"]),
            ("volatilidade", _norm(tf.volatility, "volatility"), w["volatility"]),
            ("lesões", _norm(tf.injuries, "injuries"), w["injuries"]),
            ("rotação", rotation, w["rotation"]),
            ("odd esmagada", None, w["odd_crushed"]),      # depende de odds (hoje opcional)
            ("modelo vs mercado", None, w["model_vs_market"]),
            ("contexto atípico", abnormal, w["abnormal_context"]),
        ])
        reasons: list[str] = []
        if tf.matches_played and tf.matches_played < 5:
            reasons.append("Amostra pequena de jogos")
        if tf.att_efficiency is not None and tf.att_efficiency >= 1.35:
            score = clamp(score + 10.0)
            reasons.append("Time pode regredir (marcou acima do xG)")
        return ScoreResult(score, reasons, warns)

    # ── 4.4 CornersPressureScore ─────────────────────────────────────────────
    def corners_pressure(self, tf: TeamFeatures, opp: TeamFeatures,
                         off_pressure: float) -> ScoreResult:
        w = W.CORNERS_PRESSURE
        score, warns = _combine([
            ("cruzamentos", _norm(tf.crosses, "crosses"), w["crosses"]),
            ("ataques laterais", _norm(tf.lateral_attacks, "lateral_attacks"), w["lateral_attacks"]),
            ("finalizações bloqueadas", _norm(tf.blocked_shots, "blocked_shots"), w["blocked_shots"]),
            ("entradas no último terço", _norm(tf.final_third_entries, "final_third_entries"), w["final_third_entries"]),
            ("pressão ofensiva", off_pressure, w["off_pressure"]),
            ("escanteios a favor", _norm(tf.corners_for, "corners_for"), w["corners_for"]),
            ("escanteios cedidos pelo adversário", _norm(opp.corners_against, "corners_against"), w["opp_corners_against"]),
        ])
        reasons = []
        if score >= 70:
            reasons.append("Cenário de muitos escanteios")
        return ScoreResult(score, reasons, warns)

    # ── 4.5 CardsTensionScore ────────────────────────────────────────────────
    def cards_tension(self, tf: TeamFeatures, mf: MatchFeatures,
                      tight_score: float | None = None) -> ScoreResult:
        w = W.CARDS_TENSION
        score, warns = _combine([
            ("média de cartões", _norm(tf.cards_for, "cards_for"), w["cards_for"]),
            ("faltas", _norm(tf.fouls, "fouls"), w["fouls"]),
            ("clássico", 80.0 if mf.derby else 30.0, w["derby"]),
            ("mata-mata", 75.0 if mf.knockout else 35.0, w["knockout"]),
            ("importância", mf.importance, w["importance"]),
            ("árbitro", None, w["referee"]),
            ("placar apertado", tight_score, w["tight_score"]),
        ])
        reasons = []
        if score >= 70:
            reasons.append("Jogo tende a ser truncado / muitos cartões")
        return ScoreResult(score, reasons, warns)

    # ── LiveGameStateScore (5) ───────────────────────────────────────────────
    def live_game_state(self, lf: LiveFeatures, side: str) -> ScoreResult:
        w = W.LIVE_GAME_STATE
        own = lf.home_score if side == "home" else lf.away_score
        opp = lf.away_score if side == "home" else lf.home_score
        deficit = opp - own                                # >0 = perdendo

        game_minute = normalize(float(lf.minute), 0.0, 90.0) or 50.0
        # Urgência: perdendo → alta; empate → média-alta; ganhando → baixa.
        if deficit > 0:
            score_urgency = clamp(70.0 + deficit * 12.0)
        elif deficit == 0:
            score_urgency = 60.0
        else:
            score_urgency = clamp(35.0 + deficit * 8.0)
        recent_pressure = (lf.recent_pressure_home if side == "home"
                           else lf.recent_pressure_away)
        mom = lf.momentum_home if side == "home" else lf.momentum_away
        live_momentum = normalize(mom, 0.8, 1.3) if mom is not None else None
        odds_value = None                                  # odds live opcionais
        fatigue = normalize(float(lf.minute), 45.0, 95.0) or 50.0
        cards = (lf.cards_home if side == "home" else lf.cards_away)
        fouls = (lf.fouls_home if side == "home" else lf.fouls_away)
        card_risk = None
        if cards is not None or fouls is not None:
            card_risk = clamp((normalize(cards, 0.0, 5.0) or 0.0) * 0.6
                              + (normalize(fouls, 5.0, 20.0) or 0.0) * 0.4)

        score, warns = _combine([
            ("minuto do jogo", game_minute, w["game_minute"]),
            ("urgência do placar", score_urgency, w["score_urgency"]),
            ("pressão recente", recent_pressure, w["recent_pressure"]),
            ("momentum ao vivo", live_momentum, w["live_momentum"]),
            ("valor na odd", odds_value, w["odds_value"]),
            ("fadiga", fatigue, w["fatigue"]),
            ("risco de cartão", card_risk, w["card_risk"]),
        ])
        reasons = []
        if deficit >= 1:
            reasons.append("Time precisa do resultado (perdendo)")
        return ScoreResult(score, reasons, warns)

    # ── Bundle pré-jogo (os dois lados) ──────────────────────────────────────
    def analyze_match(self, mf: MatchFeatures) -> dict[str, dict[str, ScoreResult]]:
        """Calcula todos os scores pré-jogo dos DOIS times. Base de markets.py."""
        out: dict[str, dict[str, ScoreResult]] = {}
        base = {}
        for side, tf in (("home", mf.home), ("away", mf.away)):
            base[side] = {
                "offensiveThreat": self.offensive_threat(tf),
                "creation": self.creation(tf),
                "defensiveFragility": self.defensive_fragility(tf),
                "momentum": self.momentum(tf),
                "pressure": self.pressure(tf),
                "efficiency": self.efficiency(tf),
            }
        for side, tf in (("home", mf.home), ("away", mf.away)):
            opp_side = "away" if side == "home" else "home"
            opp = mf.away if side == "home" else mf.home
            b = base[side]
            scores = dict(b)
            scores["matchup"] = self.matchup(
                b["offensiveThreat"].value,
                base[opp_side]["defensiveFragility"].value,
                b["creation"].value)
            scores["pressure"] = b["pressure"]
            scores["cornersPressure"] = self.corners_pressure(
                tf, opp, b["pressure"].value)
            scores["cardsTension"] = self.cards_tension(tf, mf)
            scores["risk"] = self.risk(tf, mf)
            out[side] = scores
        return out
