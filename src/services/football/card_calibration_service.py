"""Calibração contínua dos cartões (item 6) — previsão × resultado real."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from src import config
from src.db.models import FootballCardPrediction, FootballPenduradoLog
from src.services.football.cards_service import card_prediction, pendurado_effects
from src.services.football.data_service import FootballDataService

logger = logging.getLogger(__name__)

MODEL_VERSION = "cards-v1"


def log_predictions(db: Session, data: FootballDataService,
                    context: str = "general") -> int:
    logged = 0
    for m in data._upcoming_matches(context, only_future=True):
        if db.scalar(select(FootballCardPrediction.id).where(
                FootballCardPrediction.match_id == m.id,
                FootballCardPrediction.context == context)):
            continue
        pred = card_prediction(db, data, m.id, context=context)
        if pred is None or pred.note:
            continue
        db.add(FootballCardPrediction(
            match_id=m.id, context=context, league=m.league_name or "",
            match=pred.match, model_version=MODEL_VERSION, kickoff_at=m.utc_kickoff,
            predicted_total=pred.expected_total, predicted_1t=pred.by_half.first_half,
            predicted_2t=pred.by_half.second_half, line=pred.line,
            prob_over=pred.prob_over, referee_factor=pred.referee_factor,
            sample_size=pred.sample_size))
        logged += 1
    if logged:
        db.commit()
    return logged


def settle_predictions(db: Session, data: FootballDataService,
                       context: str = "general") -> int:
    pending = db.scalars(select(FootballCardPrediction).where(
        FootballCardPrediction.context == context,
        FootballCardPrediction.actual_total.is_(None))).all()
    settled = 0
    for p in pending:
        m = data.match_domain(p.match_id, context=context)
        if m is None or m.status != "finished":
            continue
        ev = data.match_card_events(p.match_id, context)
        teams = ev.get("teams") or {}
        if not teams:
            continue
        total = sum(d.get("total", 0) for d in teams.values())
        p.actual_total = int(total)
        p.result = "over" if total > p.line else ("push" if total == p.line else "under")
        p.error = round(p.predicted_total - total, 2)
        p.settled_at = datetime.now(timezone.utc)
        settled += 1
    if settled:
        db.commit()
    return settled


def log_pendurados(db: Session, data: FootballDataService,
                   context: str = "general") -> int:
    """Log SEPARADO da regra do pendurado (Fase B) pros jogos próximos das ligas
    BR. 1 linha por jogador-jogo em que a regra ativou."""
    logged = 0
    for m in data._upcoming_matches(context, only_future=True):
        if m.league_id not in config.BRAZIL_LEAGUE_IDS:
            continue
        if db.scalar(select(FootballPenduradoLog.id).where(
                FootballPenduradoLog.match_id == m.id,
                FootballPenduradoLog.context == context)):
            continue
        _, _, _, logs = pendurado_effects(data, m, context)
        for lg in logs:
            db.add(FootballPenduradoLog(match_id=m.id, context=context, **lg))
            logged += 1
    if logged:
        db.commit()
    return logged


def settle_pendurados(db: Session, data: FootballDataService,
                      context: str = "general") -> int:
    """Preenche got_card (o jogador levou amarelo?) nos logs cujo jogo terminou —
    valida os fatores ↑/↓ com dado próprio."""
    pending = db.scalars(select(FootballPenduradoLog).where(
        FootballPenduradoLog.context == context,
        FootballPenduradoLog.got_card.is_(None))).all()
    settled = 0
    for p in pending:
        m = data.match_domain(p.match_id, context=context)
        if m is None or m.status != "finished":
            continue
        players = data.match_card_events(p.match_id, context).get("players") or set()
        p.got_card = p.player_id in players
        p.settled_at = datetime.now(timezone.utc)
        settled += 1
    if settled:
        db.commit()
    return settled


def pendurado_report(db: Session) -> dict:
    """Desempenho da regra do pendurado, SEPARADO por efeito: quantos foram
    marcados ↑/↓ e quantos realmente levaram cartão. Valida boost/damp."""
    rows = db.scalars(select(FootballPenduradoLog).where(
        FootballPenduradoLog.got_card.isnot(None))).all()
    out = {"model_version": MODEL_VERSION}
    for eff in ("boost", "damp"):
        grp = [r for r in rows if r.effect == eff]
        n = len(grp)
        hits = sum(1 for r in grp if r.got_card)
        out[eff] = {"n": n, "levaram_cartao": hits,
                    "taxa_pct": round(100.0 * hits / n, 1) if n else None}
    return out


def calibration_report(db: Session) -> dict:
    settled = db.scalars(select(FootballCardPrediction).where(
        FootballCardPrediction.actual_total.isnot(None))).all()
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
