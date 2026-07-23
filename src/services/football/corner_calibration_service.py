"""
Calibração contínua dos escanteios (item 6).

Registra a PREVISÃO no kickoff e, quando o jogo termina, preenche o RESULTADO
real (total de escanteios) + erro. Base pra afinar LINE_OFFSET / STYLE_BOOST /
proporção 1T-2T com dado de campo, em vez de chute.

Fonte do real: get_match_statistics do jogo (corner_kicks das duas equipes).
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import FootballCornerPrediction
from src.services.football.corners_service import corner_prediction
from src.services.football.data_service import FootballDataService

logger = logging.getLogger(__name__)

# Sobe quando mudar a lógica do modelo — separa a performance por versão.
MODEL_VERSION = "corners-v1"


def log_predictions(db: Session, data: FootballDataService,
                    context: str = "general") -> int:
    """Registra a previsão dos jogos próximos (1 por jogo). Pula os que já têm
    previsão ou cujas features ainda não existem. Devolve quantos gravou."""
    logged = 0
    for m in data._upcoming_matches(context, only_future=True):
        exists = db.scalar(select(FootballCornerPrediction.id).where(
            FootballCornerPrediction.match_id == m.id,
            FootballCornerPrediction.context == context))
        if exists:
            continue
        pred = corner_prediction(db, data, m.id, context=context)
        if pred is None or pred.note:      # sem features / sem dado → não registra
            continue
        db.add(FootballCornerPrediction(
            match_id=m.id, context=context, league=m.league_name or "",
            match=pred.match, model_version=MODEL_VERSION, kickoff_at=m.utc_kickoff,
            predicted_total=pred.expected_total, predicted_1t=pred.by_half.first_half,
            predicted_2t=pred.by_half.second_half, line=pred.line,
            prob_over=pred.prob_over, confidence=pred.confidence,
            sample_size=pred.sample_size))
        logged += 1
    if logged:
        db.commit()
    return logged


def settle_predictions(db: Session, data: FootballDataService,
                       context: str = "general") -> int:
    """Preenche o resultado real das previsões cujo jogo terminou. Devolve
    quantas liquidou."""
    pending = db.scalars(select(FootballCornerPrediction).where(
        FootballCornerPrediction.context == context,
        FootballCornerPrediction.actual_total.is_(None))).all()
    settled = 0
    for p in pending:
        m = data.match_domain(p.match_id, context=context)
        if m is None or m.status != "finished":
            continue
        stats = data._match_stats_cached(p.match_id, context)
        if not stats:
            continue
        home_c = (stats.get("home") or {}).get("corner_kicks")
        away_c = (stats.get("away") or {}).get("corner_kicks")
        if home_c is None and away_c is None:
            continue
        total = int((home_c or 0) + (away_c or 0))
        p.actual_total = total
        p.result = "over" if total > p.line else ("push" if total == p.line else "under")
        p.error = round(p.predicted_total - total, 2)
        from datetime import datetime, timezone
        p.settled_at = datetime.now(timezone.utc)
        settled += 1
    if settled:
        db.commit()
    return settled


def calibration_report(db: Session) -> dict:
    """Resumo previsão × real dos jogos liquidados: viés (previsto − real), erro
    absoluto médio e taxa da linha recomendada. Base pra afinar o modelo."""
    settled = db.scalars(select(FootballCornerPrediction).where(
        FootballCornerPrediction.actual_total.isnot(None))).all()
    n = len(settled)
    if not n:
        return {"n": 0, "model_version": MODEL_VERSION}
    overs = sum(1 for p in settled if p.result == "over")
    return {
        "n": n,
        "media_prevista": round(sum(p.predicted_total for p in settled) / n, 2),
        "media_real": round(sum((p.actual_total or 0) for p in settled) / n, 2),
        "vies_medio": round(sum((p.error or 0) for p in settled) / n, 2),
        "erro_abs_medio": round(sum(abs(p.error or 0) for p in settled) / n, 2),
        "taxa_over_linha_pct": round(100.0 * overs / n, 1),
        "model_version": MODEL_VERSION,
    }
