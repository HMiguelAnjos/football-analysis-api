"""
LiveRecommendationEngine — recomendações AO VIVO por mercado a partir do estado
atual do jogo (``LiveFeatures``), seguindo as regras da seção 5 da spec.

Mercados: escanteios (prioridade), gols e cartões. Cada um vira uma
``AnalysisRecommendation`` (recommendation_type="LIVE") com edge/risk/grade/
reasons/warnings/rawScores. Usa o ``LiveGameStateScore`` da engine.
"""

from __future__ import annotations

from typing import Optional

from src.analysis import weights as W
from src.analysis.features import LiveFeatures
from src.analysis.grade import confidence, grade
from src.analysis.helpers import clamp, normalize, weighted_average
from src.analysis.markets import RAW_KEYS, AnalysisRecommendation
from src.analysis.scores import FootballAnalysisEngine


def _ln(value, key: str):
    """Normaliza por uma faixa AO VIVO (acumulada no jogo)."""
    lo, hi = W.LIVE_NORM[key]
    return normalize(value, lo, hi)


def _side(lf: LiveFeatures, side: str, attr: str):
    return getattr(lf, f"{attr}_{side}")


def _live_pressure(lf: LiveFeatures, side: str) -> float:
    """Pressão ofensiva atual de um lado (0–100) a partir das estatísticas
    acumuladas. NÃO usa posse como fator dominante."""
    w = W.LIVE_PRESSURE
    score = weighted_average([
        (_ln(_side(lf, side, "insidebox"), "insidebox"), w["insidebox"]),
        (_ln(_side(lf, side, "shots_on"), "shots_on"), w["shots_on"]),
        (_ln(_side(lf, side, "corners"), "corners"), w["corners"]),
        (_ln(_side(lf, side, "blocked"), "blocked"), w["blocked"]),
        (_ln(_side(lf, side, "possession"), "possession"), w["possession"]),
    ])
    return score if score is not None else 50.0


class LiveRecommendationEngine:
    def __init__(self, engine: Optional[FootballAnalysisEngine] = None) -> None:
        self.engine = engine or FootballAnalysisEngine()

    def recommend_live(
        self, lf: LiveFeatures, *, match_id: int = 0, home_name: str = "Casa",
        away_name: str = "Fora", knockout: bool = False, derby: bool = False,
        include_avoid: bool = False,
    ) -> list[AnalysisRecommendation]:
        names = {"home": home_name, "away": away_name}
        match = f"{home_name} x {away_name}"
        recs = [
            self._corners(lf, match_id, match, names),
            self._goals(lf, match_id, match, names),
            self._cards(lf, match_id, match, names, knockout, derby),
        ]
        recs = [r for r in recs if r is not None]
        if not include_avoid:
            recs = [r for r in recs if r.grade != W.GRADE_FALLBACK]
        recs.sort(key=lambda r: r.confidence, reverse=True)
        return recs

    # ─── Escanteios ao vivo (prioridade) ─────────────────────────────────────
    def _corners(self, lf, match_id, match, names) -> Optional[AnalysisRecommendation]:
        lo, hi = W.LIVE_CORNERS_MINUTE
        if not (lo <= lf.minute <= hi):           # fora da janela útil
            return None
        w = W.LIVE_CORNERS

        def edge_side(side: str) -> float:
            gs = self.engine.live_game_state(lf, side).value
            lp = _live_pressure(lf, side)
            rp = _side(lf, side, "recent_pressure")
            return weighted_average([
                (gs, w["game_state"]), (lp, w["live_pressure"]),
                (rp, w["recent_pressure"]),
            ]) or 50.0

        e_home, e_away = edge_side("home"), edge_side("away")
        side = "home" if e_home >= e_away else "away"
        edge = max(e_home, e_away)               # quem pressiona puxa os cantos
        current = int((lf.corners_home or 0) + (lf.corners_away or 0))
        line = float(current) + 3.5
        risk = self._live_risk(lf, late_cut=80)
        own = lf.home_score if side == "home" else lf.away_score
        opp = lf.away_score if side == "home" else lf.home_score
        reasons = [f"{names[side]} pressionando ({lf.minute}')"]
        if opp - own >= 0:
            reasons.append(f"{names[side]} precisa do resultado")
        warnings = self._missing_corner_warnings(lf, side)
        raw = self._raw_live(lf, side, corners_pressure=_live_pressure(lf, side))
        return self._build(match_id, match, "corners", "Over", line, edge, risk,
                           reasons, warnings, raw, team=None)

    # ─── Gols ao vivo ────────────────────────────────────────────────────────
    def _goals(self, lf, match_id, match, names) -> Optional[AnalysisRecommendation]:
        w = W.LIVE_GOALS
        xg_total = (lf.xg_home or 0) + (lf.xg_away or 0) if (lf.xg_home or lf.xg_away) else None
        son_total = (lf.shots_on_home or 0) + (lf.shots_on_away or 0)
        open_game = min(_live_pressure(lf, "home"), _live_pressure(lf, "away"))
        edge = weighted_average([
            (_ln(xg_total, "live_xg_total"), w["live_xg"]),
            (_ln(son_total, "shots_on_total"), w["shots_on"]),
            (open_game, w["open_game"]),
            (_ln(son_total, "shots_on_total"), w["def_ceding"]),
        ]) or 50.0
        current = int(lf.home_score + lf.away_score)
        line = float(current) + 1.5
        risk = self._live_risk(lf, late_cut=82)
        reasons = ["Jogo aberto, volume de finalizações" if edge >= 62 else f"Jogo {lf.minute}'"]
        if xg_total is not None and _ln(xg_total, "live_xg_total") and _ln(xg_total, "live_xg_total") >= 65:
            reasons.append("xG ao vivo alto")
        warnings = [] if xg_total is not None else ["xG ao vivo: dado ausente"]
        raw = self._raw_live(lf, "home", pressure=open_game)
        return self._build(match_id, match, "over_under", "Over", line, edge, risk,
                           reasons, warnings, raw, team=None)

    # ─── Cartões ao vivo ─────────────────────────────────────────────────────
    def _cards(self, lf, match_id, match, names, knockout, derby) -> Optional[AnalysisRecommendation]:
        w = W.LIVE_CARDS
        fouls_total = (lf.fouls_home or 0) + (lf.fouls_away or 0)
        cards_total = (lf.cards_home or 0) + (lf.cards_away or 0)
        cards_tension = weighted_average([
            (_ln(fouls_total, "fouls_total"), 0.6),
            (_ln(cards_total, "cards_total"), 0.4),
        ]) or 50.0
        tight = abs(lf.home_score - lf.away_score) <= 1
        tight_2h = 70.0 if (tight and lf.minute >= 45) else (45.0 if tight else 30.0)
        context = 70.0 if (knockout or derby) else 40.0
        edge = weighted_average([
            (cards_tension, w["cards_tension"]), (_ln(fouls_total, "fouls_total"), w["fouls"]),
            (tight_2h, w["tight_2h"]), (context, w["context"]),
        ]) or 50.0
        current = int(cards_total)
        line = float(current) + 1.5
        risk = self._live_risk(lf, late_cut=85)
        reasons = []
        if tight and lf.minute >= 45:
            reasons.append("Placar apertado no 2º tempo")
        if edge >= 62:
            reasons.append("Jogo truncado / muitas faltas")
        warnings = [] if (lf.fouls_home is not None) else ["Faltas ao vivo: dado ausente"]
        raw = self._raw_live(lf, "home", cards_tension=cards_tension)
        return self._build(match_id, match, "cards", "Over", line, edge, risk,
                           reasons, warnings, raw, team=None)

    # ─── Comuns ──────────────────────────────────────────────────────────────
    def _live_risk(self, lf: LiveFeatures, *, late_cut: int) -> float:
        risk = 40.0
        if lf.minute >= late_cut:                 # pouco tempo p/ o evento sair
            risk += 20.0
        if lf.red_home or lf.red_away:            # expulsão muda o jogo
            risk += 8.0
        return clamp(risk)

    @staticmethod
    def _missing_corner_warnings(lf: LiveFeatures, side: str) -> list[str]:
        out = []
        if _side(lf, side, "insidebox") is None:
            out.append("Finalizações na área ao vivo: dado ausente")
        if _side(lf, side, "recent_pressure") is None:
            out.append("Pressão recente: dado ausente")
        return out[:3]

    @staticmethod
    def _raw_live(lf: LiveFeatures, side: str, **extra) -> dict[str, Optional[float]]:
        raw = {k: None for k in RAW_KEYS}
        eng = FootballAnalysisEngine()
        raw["liveGameState"] = round(eng.live_game_state(lf, side).value, 1)
        for k, v in extra.items():
            key = {"corners_pressure": "cornersPressure",
                   "cards_tension": "cardsTension", "pressure": "pressure"}.get(k, k)
            if v is not None:
                raw[key] = round(v, 1)
        return raw

    def _build(self, match_id, match, market, selection, line, edge, risk,
               reasons, warnings, raw, team) -> AnalysisRecommendation:
        edge, risk = clamp(edge), clamp(risk)
        return AnalysisRecommendation(
            match_id=match_id, market=market, selection=selection, line=line,
            odd=None, confidence=round(confidence(edge, risk), 1),
            edge_score=round(edge, 1), risk_score=round(risk, 1),
            recommendation_type="LIVE", grade=grade(edge, risk),
            reasons=reasons, warnings=warnings, raw_scores=raw, team=team, match=match,
        )
