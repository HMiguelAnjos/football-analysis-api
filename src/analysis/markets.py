"""
MarketRecommendationEngine — cruza os scores de jogo em recomendações por
mercado (PRÉ-JOGO): over gols, ambas marcam, 1x2, escanteios, cartões, chutes.

Cada mercado vira uma ``AnalysisRecommendation`` com edgeScore, riskScore,
grade, confiança, reasons, warnings e rawScores (a seção 6 da spec). O edgeScore
é a CONVICÇÃO do modelo (0–100), combinação ponderada dos scores — não uma odd.
A odd, quando existir, é só informação extra.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from src.analysis import weights as W
from src.analysis.features import MatchFeatures
from src.analysis.grade import confidence, grade
from src.analysis.helpers import clamp, invert_score, normalize, weighted_average
from src.analysis.scores import FootballAnalysisEngine, ScoreResult

# Ordem fixa dos scores num rawScores (espelha a spec).
RAW_KEYS = [
    "offensiveThreat", "creation", "defensiveFragility", "matchup", "momentum",
    "pressure", "efficiency", "risk", "cornersPressure", "cardsTension",
    "liveGameState",
]


@dataclass
class AnalysisRecommendation:
    match_id: int
    market: str
    selection: str
    line: Optional[float]
    odd: Optional[float]
    confidence: float          # 0–100
    edge_score: float          # 0–100 (convicção do modelo)
    risk_score: float          # 0–100
    recommendation_type: str   # "PRE_GAME" | "LIVE"
    grade: str                 # A+ | A | B | C | AVOID
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    raw_scores: dict[str, Optional[float]] = field(default_factory=dict)
    team: Optional[str] = None
    match: Optional[str] = None


def _raw(bundle_side: dict[str, ScoreResult]) -> dict[str, Optional[float]]:
    """Bundle de um lado → rawScores plano (com liveGameState=None no pré-jogo)."""
    return {k: (round(bundle_side[k].value, 1) if k in bundle_side else None)
            for k in RAW_KEYS}


def _collect(results: list[ScoreResult], extra: Optional[list[str]] = None,
             cap: int = 5) -> tuple[list[str], list[str]]:
    """Junta reasons/warnings de vários scores, deduplicando e limitando."""
    reasons: list[str] = list(extra or [])
    warnings: list[str] = []
    for r in results:
        for x in r.reasons:
            if x not in reasons:
                reasons.append(x)
        for x in r.warnings:
            if x not in warnings:
                warnings.append(x)
    return reasons[:cap], warnings[:cap]


class MarketRecommendationEngine:
    """Gera recomendações pré-jogo por mercado a partir das features do jogo."""

    def __init__(self, engine: Optional[FootballAnalysisEngine] = None) -> None:
        self.engine = engine or FootballAnalysisEngine()

    # ─────────────────────────────────────────────────────────────────────────
    def recommend_pre_game(
        self, mf: MatchFeatures, *, match_id: int = 0, home_name: str = "Casa",
        away_name: str = "Fora", include_avoid: bool = False,
    ) -> list[AnalysisRecommendation]:
        b = self.engine.analyze_match(mf)
        match_name = f"{home_name} x {away_name}"
        names = {"home": home_name, "away": away_name}
        ctx = dict(match_id=match_id, match=match_name, names=names, mf=mf)

        recs = [
            self._over_goals(b, **ctx),
            self._btts(b, **ctx),
            self._result_1x2(b, **ctx),
            self._corners(b, **ctx),
            self._cards(b, **ctx),
            self._shots(b, **ctx),
        ]
        recs = [r for r in recs if r is not None]
        if not include_avoid:
            recs = [r for r in recs if r.grade != W.GRADE_FALLBACK]
        recs.sort(key=lambda r: r.confidence, reverse=True)
        return recs

    # ─── 4.1 Over gols ───────────────────────────────────────────────────────
    def _over_goals(self, b, *, match_id, match, names, mf) -> Optional[AnalysisRecommendation]:
        w = W.MARKET_OVER
        edge = weighted_average([
            (b["home"]["offensiveThreat"].value, w["home_off"]),
            (b["away"]["offensiveThreat"].value, w["away_off"]),
            (b["home"]["defensiveFragility"].value, w["home_def_frag"]),
            (b["away"]["defensiveFragility"].value, w["away_def_frag"]),
            ((b["home"]["matchup"].value + b["away"]["matchup"].value) / 2, w["matchup"]),
            ((b["home"]["momentum"].value + b["away"]["momentum"].value) / 2, w["off_momentum"]),
        ]) or 50.0
        # rawScores = lado de maior ameaça ofensiva (o "motor" de gols).
        side = "home" if b["home"]["offensiveThreat"].value >= b["away"]["offensiveThreat"].value else "away"
        risk = (b["home"]["risk"].value + b["away"]["risk"].value) / 2
        reasons, warnings = _collect(
            [b["home"]["offensiveThreat"], b["away"]["offensiveThreat"],
             b["home"]["defensiveFragility"], b["away"]["defensiveFragility"]],
            extra=["Jogo com cenário de gols dos dois lados"] if edge >= 62 else [])
        return self._build(match_id, match, "over_under", "Over", W.DEFAULT_LINES["over_goals"],
                           edge, risk, b[side], reasons, warnings, mf, team=None)

    # ─── 4.2 Ambas marcam ────────────────────────────────────────────────────
    def _btts(self, b, *, match_id, match, names, mf) -> Optional[AnalysisRecommendation]:
        w = W.MARKET_BTTS
        edge = weighted_average([
            (b["home"]["offensiveThreat"].value, w["home_off"]),
            (b["away"]["offensiveThreat"].value, w["away_off"]),
            (b["home"]["defensiveFragility"].value, w["home_def_frag"]),
            (b["away"]["defensiveFragility"].value, w["away_def_frag"]),
            (None, w["recent_freq"]),       # frequência BTTS recente: sem dado hoje
        ]) or 50.0
        # BTTS depende do ataque MAIS FRACO conseguir furar → limita pelo menor off.
        weaker_off = min(b["home"]["offensiveThreat"].value, b["away"]["offensiveThreat"].value)
        edge = clamp(0.7 * edge + 0.3 * weaker_off)
        side = "home" if b["home"]["offensiveThreat"].value <= b["away"]["offensiveThreat"].value else "away"
        risk = (b["home"]["risk"].value + b["away"]["risk"].value) / 2
        reasons, warnings = _collect(
            [b["home"]["offensiveThreat"], b["away"]["offensiveThreat"]],
            extra=["Os dois times marcam e cedem chances"] if edge >= 62 else [])
        warnings = warnings + ["Frequência de ambas marcam: dado ausente"]
        return self._build(match_id, match, "btts", "Sim", None,
                           edge, risk, b[side], reasons, warnings[:5], mf, team=None)

    # ─── 4.3 Resultado 1x2 ───────────────────────────────────────────────────
    def _result_1x2(self, b, *, match_id, match, names, mf) -> Optional[AnalysisRecommendation]:
        w = W.MARKET_1X2

        def side_edge(side: str) -> float:
            opp = "away" if side == "home" else "home"
            if mf.neutral_venue:
                home_field = 50.0
            else:
                home_field = 62.0 if side == "home" else 38.0
            return weighted_average([
                (b[side]["matchup"].value, w["matchup"]),
                (invert_score(b[opp]["matchup"].value), w["opp_matchup_inv"]),
                (b[side]["momentum"].value, w["momentum"]),
                (home_field, w["home_field"]),
                (invert_score(b[side]["defensiveFragility"].value), w["defense"]),
            ]) or 50.0

        e_home, e_away = side_edge("home"), side_edge("away")
        side = "home" if e_home >= e_away else "away"
        edge = max(e_home, e_away)
        risk = b[side]["risk"].value
        reasons, warnings = _collect(
            [b[side]["matchup"], b[side]["momentum"], b[side]["defensiveFragility"]],
            extra=[f"{names[side]} é favorito pelo confronto"] if edge >= 62 else [])
        return self._build(match_id, match, "1x2", names[side], None,
                           edge, risk, b[side], reasons, warnings, mf, team=names[side])

    # ─── 4.4 Escanteios ──────────────────────────────────────────────────────
    def _corners(self, b, *, match_id, match, names, mf) -> Optional[AnalysisRecommendation]:
        w = W.MARKET_CORNERS

        def team_edge(side: str) -> float:
            return weighted_average([
                (b[side]["cornersPressure"].value, w["corners_pressure"]),
                (b[side]["pressure"].value, w["off_pressure"]),
                (b[side]["matchup"].value, w["matchup"]),
            ]) or 50.0

        e_home, e_away = team_edge("home"), team_edge("away")
        edge = (e_home + e_away) / 2
        side = "home" if e_home >= e_away else "away"          # lado que puxa os cantos
        # Linha projetada quando há média de escanteios.
        cf_h, cf_a = mf.home.corners_for, mf.away.corners_for
        if cf_h is not None and cf_a is not None:
            line = max(7.5, round(cf_h + cf_a) - 0.5)
        else:
            line = W.DEFAULT_LINES["corners"]
        risk = (b["home"]["risk"].value + b["away"]["risk"].value) / 2
        reasons, warnings = _collect(
            [b[side]["cornersPressure"], b[side]["pressure"]],
            extra=[f"{names[side]} pressiona e gera escanteios"] if edge >= 62 else [])
        return self._build(match_id, match, "corners", "Over", line,
                           edge, risk, b[side], reasons, warnings, mf, team=None)

    # ─── 4.5 Cartões ─────────────────────────────────────────────────────────
    def _cards(self, b, *, match_id, match, names, mf) -> Optional[AnalysisRecommendation]:
        w = W.MARKET_CARDS
        avg_tension = (b["home"]["cardsTension"].value + b["away"]["cardsTension"].value) / 2
        edge = weighted_average([
            (avg_tension, w["cards_tension"]),
            (60.0 if (mf.knockout or mf.derby) else 45.0, w["tight_match"]),
        ]) or 50.0
        cf_h, cf_a = mf.home.cards_for, mf.away.cards_for
        if cf_h is not None and cf_a is not None:
            line = max(2.5, round(cf_h + cf_a) - 0.5)
        else:
            line = W.DEFAULT_LINES["cards"]
        side = "home" if b["home"]["cardsTension"].value >= b["away"]["cardsTension"].value else "away"
        risk = (b["home"]["risk"].value + b["away"]["risk"].value) / 2
        reasons, warnings = _collect(
            [b["home"]["cardsTension"], b["away"]["cardsTension"]],
            extra=["Jogo com cenário de muitos cartões"] if edge >= 62 else [])
        return self._build(match_id, match, "cards", "Over", line,
                           edge, risk, b[side], reasons, warnings, mf, team=None)

    # ─── 4.6 Chutes ao gol (por time) ────────────────────────────────────────
    def _shots(self, b, *, match_id, match, names, mf) -> Optional[AnalysisRecommendation]:
        w = W.MARKET_SHOTS

        def team_edge(side: str) -> float:
            opp = "away" if side == "home" else "home"
            tf = mf.home if side == "home" else mf.away
            return weighted_average([
                (b[side]["offensiveThreat"].value, w["off_threat"]),
                (b[opp]["defensiveFragility"].value, w["opp_def_frag"]),
                (normalize(tf.shots_on_target, *W.NORM["shots_on_target"]), w["shots_on_target"]),
                (b[side]["pressure"].value, w["pressure"]),
            ]) or 50.0

        e_home, e_away = team_edge("home"), team_edge("away")
        side = "home" if e_home >= e_away else "away"
        edge = max(e_home, e_away)
        tf = mf.home if side == "home" else mf.away
        if tf.shots_on_target is not None:
            line = max(1.5, round(tf.shots_on_target) - 0.5)
        else:
            line = W.DEFAULT_LINES["shots_on_target"]
        risk = b[side]["risk"].value
        reasons, warnings = _collect(
            [b[side]["offensiveThreat"], b[side]["pressure"]],
            extra=[f"{names[side]} finaliza muito no alvo"] if edge >= 62 else [])
        return self._build(match_id, match, "player_shots_on_target",
                           f"{names[side]} Over {line:g} chutes no alvo", line,
                           edge, risk, b[side], reasons, warnings, mf, team=names[side])

    # ─── Construtor comum ────────────────────────────────────────────────────
    def _build(self, match_id, match, market, selection, line, edge, risk,
               bundle_side, reasons, warnings, mf, team) -> AnalysisRecommendation:
        edge = clamp(edge)
        risk = clamp(risk)
        odd = self._odd_for(mf, market, selection, line)
        return AnalysisRecommendation(
            match_id=match_id, market=market, selection=selection, line=line,
            odd=odd, confidence=round(confidence(edge, risk), 1),
            edge_score=round(edge, 1), risk_score=round(risk, 1),
            recommendation_type="PRE_GAME", grade=grade(edge, risk),
            reasons=reasons, warnings=warnings, raw_scores=_raw(bundle_side),
            team=team, match=match,
        )

    @staticmethod
    def _odd_for(mf: MatchFeatures, market, selection, line) -> Optional[float]:
        """Odd informativa quando existir no dict de odds (hoje opcional)."""
        if not mf.odds:
            return None
        mk = mf.odds.get(market) or {}
        val = mk.get(selection)
        return float(val) if isinstance(val, (int, float)) else None
