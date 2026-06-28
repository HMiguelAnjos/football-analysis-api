"""
GenerationService — orquestra a geração de recomendações.

Fluxo (cache-first, tolerante a falha):
  jogos do dia → forma dos dois times → odds → engine → persiste (UPSERT skip).

Jogos sem odds ou sem forma são pulados (logados), nunca derrubam o lote.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from src import config
from src.providers.base import Match
from src.recommendation.engine import RecommendationCandidate, generate_recommendations
from src.services import recommendation_service as rec_svc
from src.services.football.data_service import FootballDataService

logger = logging.getLogger(__name__)

# Mercados que o settlement resolve a partir do placar final (settlement.py) e
# cujo CÓDIGO de seleção (home/draw/away, over/under, yes/no, home_draw/...) o
# match_markets já produz. Base da geração confidence-first SEM odds.
_SETTLEABLE_MARKETS = {"1x2", "double_chance", "over_under", "btts"}


@dataclass
class _ModelPick:
    """Pick do modelo SEM odds (confidence-first) — formato que rec_svc.upsert_prop
    aceita (mesmos atributos de um PropPick)."""
    market: str
    selection: str
    line: Optional[float]
    fair_odd: float
    model_probability: float
    confidence_score: float
    recommendation_reason: str


class GenerationService:
    def __init__(self, data_service: Optional[FootballDataService] = None) -> None:
        self._data = data_service or FootballDataService()

    def generate_for_match(
        self,
        match: Match,
        *,
        context: str = "general",
        min_edge: Optional[float] = None,
        min_confidence: Optional[float] = None,
    ) -> list[RecommendationCandidate]:
        """Roda o engine pra UM jogo. [] se faltar forma ou odds."""
        home_form = self._data.team_form(match.home_team.id, context=context)
        away_form = self._data.team_form(match.away_team.id, context=context)
        if home_form is None or away_form is None:
            logger.info("generation: sem forma pro jogo %s — pulado", match.id)
            return []
        odds = self._data.match_odds_domain(match, context=context)
        if odds is None or not odds.markets:
            logger.info("generation: sem odds pro jogo %s — pulado", match.id)
            return []
        return generate_recommendations(
            match=match, home_form=home_form, away_form=away_form, odds=odds,
            min_edge=min_edge, min_confidence=min_confidence,
        )

    def generate(
        self,
        db: Session,
        *,
        context: str = "general",
        date: Optional[str] = None,
        match_ids: Optional[list[int]] = None,
        min_edge: Optional[float] = None,
        min_confidence: Optional[float] = None,
        persist: bool = True,
    ) -> dict:
        """Gera (e opcionalmente persiste) recomendações pro escopo/contexto dado.

        Devolve {generated, persisted, matches_analyzed, candidates}.
        """
        if match_ids:
            matches = [self._data.match_domain(mid) for mid in match_ids]
            matches = [m for m in matches if m is not None]
        else:
            matches = self._data.matches_domain_for(date, context)

        all_candidates: list[RecommendationCandidate] = []
        persisted = 0
        prop_count = 0
        market_model = 0          # picks de mercado confidence-first (sem odds)
        # Jogadores da competição (cacheado) → agrupados por time pras props.
        players_by_team = self._players_by_team(context)
        for match in matches:
            try:
                candidates = self.generate_for_match(
                    match, context=context, min_edge=min_edge, min_confidence=min_confidence,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("generation: erro no jogo %s (%s)", match.id, exc)
                candidates = []
            all_candidates.extend(candidates)
            if persist:
                for c in candidates:
                    try:
                        _, created = rec_svc.upsert_from_candidate(
                            db, c, context=context, kickoff_at=match.utc_kickoff,
                        )
                        if created:
                            persisted += 1
                    except Exception as exc:  # noqa: BLE001
                        db.rollback()
                        logger.warning("generation: upsert falhou (%s)", exc)

            # SEM odds (ODDS_PROVIDER=none) o engine de valor não emite nada.
            # Fallback CONFIDENCE-FIRST: persiste os mercados prováveis do modelo
            # (códigos settláveis) pra fechar o loop de validação.
            if not candidates:
                cf = self._confidence_first_for_match(match, context)
                if persist:
                    for pick in cf:
                        try:
                            _, created = rec_svc.upsert_prop(
                                db, pick, match=match, context=context,
                                kickoff_at=match.utc_kickoff,
                            )
                            if created:
                                persisted += 1
                                market_model += 1
                        except Exception as exc:  # noqa: BLE001
                            db.rollback()
                            logger.warning("generation: market upsert falhou (%s)", exc)
                else:
                    market_model += len(cf)

            # Player props (projeção do modelo, independem de odds).
            if players_by_team:
                prop_count += self._generate_props(
                    db, match, context, players_by_team, persist,
                )

        return {
            "generated": len(all_candidates) + market_model + prop_count,
            "persisted": persisted + prop_count,
            "matches_analyzed": len(matches),
            "market_model": market_model,
            "player_props": prop_count,
            "candidates": all_candidates,
        }

    def _confidence_first_for_match(self, match: Match, context: str,
                                    per_match: int = 3) -> list[_ModelPick]:
        """Picks de MERCADO prováveis do modelo (sem odds), em CÓDIGO settlável.
        Reusa match_markets (mesmos λ calibrados do feed que o usuário vê)."""
        try:
            rows = self._data.match_markets(match.id, context=context)
        except Exception as exc:  # noqa: BLE001
            logger.warning("generation: match_markets falhou no jogo %s (%s)", match.id, exc)
            return []
        picks: list = []
        for r in rows:
            if r.market not in _SETTLEABLE_MARKETS:
                continue
            if r.market == "over_under" and r.line != 2.5:
                continue            # só a linha principal de gols
            if r.market == "double_chance" and r.selection == "home_away":
                continue            # "casa ou fora" é pouco intuitivo
            if (r.model_prob or 0) < config.MIN_PICK_PROB:
                continue
            picks.append(r)
        picks.sort(key=lambda r: r.model_prob or 0, reverse=True)
        out: list[_ModelPick] = []
        for r in picks[:per_match]:
            p = r.model_prob or 0.0
            out.append(_ModelPick(
                market=r.market, selection=r.selection, line=r.line,
                fair_odd=r.fair_odd or (round(1 / p, 2) if p else 0.0),
                model_probability=p,
                confidence_score=round(min(p, 0.97) * 100, 1),
                recommendation_reason=(
                    f"Modelo estima {p * 100:.0f}% para {r.selection} ({r.market})."
                ),
            ))
        return out

    def _players_by_team(self, context: str) -> dict[int, list]:
        try:
            players = self._data.competition_players(context)
        except Exception as exc:  # noqa: BLE001
            logger.info("generation: sem jogadores p/ props (%s)", exc)
            return {}
        out: dict[int, list] = {}
        for p in players:
            if p.team_id:
                out.setdefault(p.team_id, []).append(p)
        return out

    def _generate_props(self, db, match, context, players_by_team, persist) -> int:
        from src.recommendation.player_props import generate_player_props
        home_form = self._data.team_form(match.home_team.id, context=context)
        away_form = self._data.team_form(match.away_team.id, context=context)
        if home_form is None or away_form is None:
            return 0
        hp = players_by_team.get(match.home_team.id, [])
        ap = players_by_team.get(match.away_team.id, [])
        if not hp and not ap:
            return 0
        picks = generate_player_props(
            match=match, home_form=home_form, away_form=away_form,
            home_players=hp, away_players=ap,
        )
        if not persist:
            return len(picks)
        n = 0
        for pick in picks:
            try:
                _, created = rec_svc.upsert_prop(
                    db, pick, match=match, context=context, kickoff_at=match.utc_kickoff,
                )
                if created:
                    n += 1
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                logger.warning("generation: prop upsert falhou (%s)", exc)
        return n
