"""Teste do relatório de calibração de escanteios (previsão × real)."""

from __future__ import annotations

from src.db.models import FootballCornerPrediction
from src.services.football.corner_calibration_service import calibration_report


def test_calibration_report_aggregates(db_session):
    db = db_session
    db.add_all([
        # previu 10, saiu 11 (over) → erro -1
        FootballCornerPrediction(match_id=1, predicted_total=10.0, line=8.5,
                                 actual_total=11, result="over", error=-1.0),
        # previu 9, saiu 6 (under) → erro +3
        FootballCornerPrediction(match_id=2, predicted_total=9.0, line=7.5,
                                 actual_total=6, result="under", error=3.0),
        # previu 10, saiu 9 (over) → erro +1
        FootballCornerPrediction(match_id=3, predicted_total=10.0, line=8.5,
                                 actual_total=9, result="over", error=1.0),
        # ainda não liquidado → fora do relatório
        FootballCornerPrediction(match_id=4, predicted_total=8.0, line=6.5),
    ])
    db.commit()

    rep = calibration_report(db)
    assert rep["n"] == 3                                   # só os liquidados
    assert rep["media_prevista"] == round((10 + 9 + 10) / 3, 2)
    assert rep["media_real"] == round((11 + 6 + 9) / 3, 2)
    assert rep["vies_medio"] == round((-1 + 3 + 1) / 3, 2)    # viés = previsto - real
    assert rep["erro_abs_medio"] == round((1 + 3 + 1) / 3, 2)
    assert rep["taxa_over_linha_pct"] == round(100 * 2 / 3, 1)  # 2 de 3 bateram o over


def test_calibration_report_empty(db_session):
    assert calibration_report(db_session)["n"] == 0
