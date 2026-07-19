"""
Engine de recomendação.

Junta o modelo de probabilidade (probability/*) com as odds reais do mercado
(MatchOdds normalizado pelo odds provider) e produz recomendações de valor:
para cada (mercado, seleção, book) compara a probabilidade do modelo com a
odd da casa e emite uma recomendação quando o edge (EV) passa os thresholds.

A entrada de odds usa SELEÇÕES CANÔNICAS (home/draw/away, over/under, yes/no,
…) — a normalização do nome do book pra esses tokens é responsabilidade do
odds provider, mantendo este engine independente de fonte.

PURO: sem rede, sem banco. Recebe dados já buscados, devolve candidatos.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from src import config
from src.probability import (
    build_score_matrix,
    confidence_score,
    edge as compute_edge,
    expected_goals,
    fair_odd,
    implied_probability,
)
from src.probability.markets import (
    anytime_scorer,
    asian_handicap,
    btts,
    double_chance,
    draw_no_bet,
    match_winner,
    over_under,
    player_over_line,
    poisson_over_under,
    team_totals,
)
from src.probability.poisson import ScoreMatrix
from src.providers.base import MatchOdds, PlayerSeasonStats, TeamForm
from src.providers.base import Match


@dataclass
class RecommendationCandidate:
    """Recomendação calculada (ainda não persistida)."""
    match_id: int
    league: str
    home_team: str
    away_team: str
    market: str
    selection: str
    line: Optional[float]
    bookmaker: str
    odd: float
    fair_odd: float
    implied_probability: float
    model_probability: float
    edge: float
    confidence_score: float
    recommendation_reason: str
    stage: Optional[str] = None
    group: Optional[str] = None


# Probabilidade abaixo da qual nem consideramos (evita NaN/odds absurdas).
_MIN_MODEL_PROB = 0.02


def _goal_market_probs(market: str, sm: ScoreMatrix, line: Optional[float]) -> dict[str, float]:
    """Probabilidades do modelo pros mercados derivados de gols, por seleção
    canônica. Devolve {} se o mercado não é derivado de gols."""
    if market == "1x2":
        return match_winner(sm)
    if market == "double_chance":
        return double_chance(sm)
    if market == "dnb":
        return draw_no_bet(sm)
    if market == "btts":
        return btts(sm)
    if market == "over_under" and line is not None:
        return over_under(sm, line)
    if market == "asian_handicap" and line is not None:
        return asian_handicap(sm, line)
    if market == "team_total_home" and line is not None:
        return team_totals(sm, line, home=True)
    if market == "team_total_away" and line is not None:
        return team_totals(sm, line, home=False)
    return {}


def _reason(market: str, selection: str, model_p: float, implied_p: float, edge_v: float) -> str:
    return (
        f"Modelo estima {model_p*100:.1f}% pra {selection} ({market}); "
        f"odd da casa implica {implied_p*100:.1f}%. "
        f"Edge de {edge_v*100:+.1f}% (valor esperado)."
    )


def generate_recommendations(
    *,
    match: Match,
    home_form: TeamForm,
    away_form: TeamForm,
    odds: MatchOdds,
    player_stats: Optional[dict[str, PlayerSeasonStats]] = None,
    min_edge: Optional[float] = None,
    min_odd: Optional[float] = None,
    max_odd: Optional[float] = None,
    min_confidence: Optional[float] = None,
    lambdas: Optional[tuple[float, float]] = None,
) -> list[RecommendationCandidate]:
    """Gera recomendações de valor pra UMA partida.

    Args:
        match: jogo (times, liga).
        home_form / away_form: forma recente (base do lambda esperado).
        odds: snapshot de odds normalizado (seleções canônicas).
        player_stats: {nome_normalizado: PlayerSeasonStats} pros mercados de
            jogador. Opcional — sem isso, props de jogador são puladas.
        min_edge/min_odd/max_odd/min_confidence: overrides dos thresholds
            (default = config).
    """
    min_edge = config.MIN_EDGE if min_edge is None else min_edge
    min_odd = config.MIN_ODD if min_odd is None else min_odd
    max_odd = config.MAX_ODD if max_odd is None else max_odd
    min_confidence = config.MIN_CONFIDENCE if min_confidence is None else min_confidence

    lam_h, lam_a = lambdas if lambdas is not None else expected_goals(home_form, away_form)
    sm = build_score_matrix(lam_h, lam_a)

    sample = min(home_form.matches_played, away_form.matches_played)
    has_xg = home_form.xg is not None and away_form.xg is not None

    out: list[RecommendationCandidate] = []

    for market_key, market_odds in odds.markets.items():
        for sel in market_odds.selections:
            if not (min_odd <= sel.price <= max_odd):
                continue

            model_p = _selection_model_prob(
                market_key, sel.name, sel.line, sm, match, player_stats,
            )
            if model_p is None or model_p < _MIN_MODEL_PROB:
                continue

            edge_v = compute_edge(model_p, sel.price)
            if edge_v < min_edge:
                continue

            implied_p = implied_probability(sel.price)
            conf = confidence_score(
                edge_value=edge_v,
                model_probability=model_p,
                matches_sample=sample,
                has_xg=has_xg,
            )
            if conf < min_confidence:
                continue

            out.append(
                RecommendationCandidate(
                    match_id=match.id,
                    league=match.league_name,
                    home_team=match.home_team.name,
                    away_team=match.away_team.name,
                    market=market_key,
                    selection=sel.name,
                    line=sel.line,
                    bookmaker=sel.bookmaker,
                    odd=round(sel.price, 3),
                    fair_odd=round(fair_odd(model_p), 3),
                    implied_probability=round(implied_p, 4),
                    model_probability=round(model_p, 4),
                    edge=round(edge_v, 4),
                    confidence_score=conf,
                    recommendation_reason=_reason(
                        market_key, sel.name, model_p, implied_p, edge_v
                    ),
                    stage=match.stage, group=match.group,
                )
            )

    # Melhor edge primeiro.
    out.sort(key=lambda c: c.edge, reverse=True)
    return out


def predict_markets(home_form: TeamForm, away_form: TeamForm,
                    lambdas: Optional[tuple[float, float]] = None) -> dict:
    """Previsão do modelo SÓ a partir da forma (sem odds de casa).

    Devolve probabilidades dos principais mercados + gols esperados. Usado pra
    dar "análise" mesmo quando não há odds de casa.
    `lambdas` permite injetar gols esperados de uma fonte melhor (ratings de
    força em torneios) em vez de derivar da forma.
    """
    lam_h, lam_a = lambdas if lambdas is not None else expected_goals(home_form, away_form)
    sm = build_score_matrix(lam_h, lam_a)
    w = match_winner(sm)
    ou = over_under(sm, 2.5)
    bt = btts(sm)
    return {
        "home": w["home"], "draw": w["draw"], "away": w["away"],
        "over25": ou["over"], "under25": ou["under"], "btts_yes": bt["yes"],
        "lambda_home": round(lam_h, 2), "lambda_away": round(lam_a, 2),
    }


def _selection_model_prob(
    market_key: str,
    selection: str,
    line: Optional[float],
    sm: ScoreMatrix,
    match: Match,
    player_stats: Optional[dict[str, PlayerSeasonStats]],
) -> Optional[float]:
    """Probabilidade do modelo pra UMA seleção. None se não modelamos."""
    # Mercados de gols.
    probs = _goal_market_probs(market_key, sm, line)
    if probs:
        return probs.get(selection)

    # Player props.
    if player_stats is not None:
        stats = player_stats.get(_norm(selection.split("|")[0]))
        # Formato esperado da seleção de prop: "Player Name|over" ou só o nome
        # (anytime scorer). line vem em sel.line.
        if market_key == "anytime_scorer" and stats is not None:
            return anytime_scorer(stats.goals_per90)
        if stats is not None and line is not None:
            side = "over" if "over" in selection.lower() else "under"
            rate = {
                "player_shots": stats.shots_per90,
                "player_shots_on_target": stats.shots_on_target_per90,
                "player_assists": stats.assists_per90,
            }.get(market_key)
            if rate is not None:
                return player_over_line(rate, line).get(side)

    return None


def _norm(name: str) -> str:
    return " ".join(name.strip().lower().split())
