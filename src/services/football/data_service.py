"""
FootballDataService — fachada cache-first sobre o FootballDataProvider.

Estratégia de cache:
  • DADO LENTO/CARO (jogos, forma, stats, ligas, times, jogadores) →
    PersistentCache em DISCO (CACHE_DIR). Sobrevive a restart, não re-queima a
    cota do provider (api-football free = 100 req/dia).
  • DADO VOLÁTIL (odds) → SimpleCache em MEMÓRIA, TTL curto.

Devolve schemas no formato exato do frontend (football_schemas). Tolerante a
falha: provider devolvendo [] / None nunca derruba o endpoint.
"""

from __future__ import annotations

import logging
import os
import statistics
from datetime import datetime, timezone
from typing import Optional

from src import competition, config
from src.providers import registry, serde
from src.providers.base import Match, MatchOdds, TeamForm
from src.schemas.football_schemas import (
    BracketStageSchema,
    GroupSchema,
    LeagueSchema,
    MatchInjurySchema,
    MatchMainOddsSchema,
    MatchOddsSchema,
    MatchSchema,
    MatchStatisticsSchema,
    OddsBoardItemSchema,
    PlayerSchema,
    RecommendationOut,
    TeamSchema,
)
from src.services.football import converters as conv
from src.services.football import front_mappers
from src.utils.cache import PersistentCache, SimpleCache

logger = logging.getLogger(__name__)

# Prefixo de versão das chaves do cache em disco. Bumpe quando o FORMATO dos
# dados cacheados mudar (ex.: novo campo em TeamForm/Match) — assim o cache
# antigo é ignorado em vez de servir dados no formato velho.
_CACHE_V = "v3"


class FootballDataService:
    def __init__(self, disk_cache=None, odds_cache=None) -> None:
        if disk_cache is None:
            disk_cache = PersistentCache(
                path=os.path.join(config.CACHE_DIR, "football_cache.json"),
                name="football_data",
            )
        self._disk = disk_cache
        self._odds_cache = odds_cache or SimpleCache(name="football_odds")

    # --- providers ---------------------------------------------------------

    def _football(self, context: str = "general"):
        return registry.get_football_provider(context)

    def _odds(self):
        return registry.get_odds_provider()

    # --- domínio (cacheado em disco; reusado pelo engine) ------------------

    def matches_by_date_domain(self, date: str) -> list[Match]:
        key = f"{_CACHE_V}:matches:{date}"
        cached = self._disk.get(key)
        if cached is not None:
            return [serde.match_from_dict(d) for d in cached]
        matches = self._football().get_matches_by_date(date) or []
        self._disk.set(key, [serde.match_to_dict(m) for m in matches],
                       config.MATCHES_CACHE_TTL)
        return matches

    def match_domain(self, match_id: int, context: str = "general") -> Optional[Match]:
        key = f"{_CACHE_V}:match:{context}:{match_id}"
        cached = self._disk.get(key)
        if cached is not None:
            return serde.match_from_dict(cached)
        m = self._football(context).get_match(match_id)
        if m is not None:
            self._disk.set(key, serde.match_to_dict(m), config.MATCHES_CACHE_TTL)
        return m

    def team_form(self, team_id: int, last_n: int = 10,
                  context: str = "general") -> Optional[TeamForm]:
        cfg = competition.resolve(context)
        key = f"{_CACHE_V}:form:{context}:{team_id}:{last_n}"
        cached = self._disk.get(key)
        if cached is not None:
            return serde.form_from_dict(cached)
        getter = self._football(context).get_team_form
        try:
            form = getter(team_id, last_n, league_id=cfg.league_ids[0], season=cfg.season)
        except TypeError:
            # Provider sem suporte a league/season (ex.: fixtures simples).
            form = getter(team_id, last_n)
        if form is not None and (form.xg is None or form.xga is None):
            xg_provider = registry.get_xg_provider()
            if xg_provider is not None:
                xg = xg_provider.get_team_xg(team_id)
                if xg is not None:
                    form.xg, form.xga = xg
        if form is not None:
            self._disk.set(key, serde.form_to_dict(form), config.STATS_CACHE_TTL)
        return form

    def match_odds_domain(self, match: Match) -> Optional[MatchOdds]:
        key = f"odds:{match.id}"
        cached = self._odds_cache.get(key)
        if cached is not None:
            return cached
        provider = self._odds()
        if provider is None:
            return None
        odds = provider.get_match_odds(match)
        if odds is not None:
            self._odds_cache.set(key, odds, config.ODDS_CACHE_TTL)
        return odds

    # --- API pública (schemas do front) ------------------------------------

    @staticmethod
    def today_str() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _h2h_for(self, date: str, matches: list[Match]) -> dict[int, dict]:
        """1x2 inline em lote, cacheado em memória (TTL curto, barato)."""
        if not matches:
            return {}
        key = f"h2h:{date}"
        cached = self._odds_cache.get(key)
        if cached is not None:
            return cached
        provider = self._odds()
        getter = getattr(provider, "get_h2h_odds", None) if provider else None
        h2h = getter(matches) if getter else {}
        self._odds_cache.set(key, h2h, config.ODDS_CACHE_TTL)
        return h2h

    def season_matches_domain(self, context: str) -> list[Match]:
        """Todos os jogos do torneio (por temporada), cacheados em disco."""
        cfg = competition.resolve(context)
        key = f"{_CACHE_V}:season:{context}:{cfg.season}"
        cached = self._disk.get(key)
        if cached is not None:
            return [serde.match_from_dict(d) for d in cached]
        getter = getattr(self._football(context), "get_season_matches", None)
        matches = (getter(cfg.league_ids[0], cfg.season) if getter else []) or []
        self._disk.set(key, [serde.match_to_dict(m) for m in matches], config.STATS_CACHE_TTL)
        return matches

    def matches_domain_for(self, date: Optional[str], context: str) -> list[Match]:
        """Jogos do contexto. Torneio = por temporada (filtra por dia só se
        `date` vier). Liga regular = jogos do dia (`date` ou hoje)."""
        cfg = competition.resolve(context)
        if cfg.tournament:
            ms = self.season_matches_domain(context)
            if date:
                ms = [m for m in ms if m.utc_kickoff and m.utc_kickoff.strftime("%Y-%m-%d") == date]
            return ms
        allowed = set(cfg.league_ids)
        return [m for m in self.matches_by_date_domain(date or self.today_str())
                if m.league_id in allowed]

    def matches(self, *, date: Optional[str] = None, league_id: Optional[int] = None,
                status: Optional[str] = None,
                context: str = "general") -> tuple[str, list[MatchSchema]]:
        d = date or self.today_str()
        domain = self.matches_domain_for(date, context)
        if league_id:
            domain = [m for m in domain if m.league_id == league_id]
        if status:
            domain = [m for m in domain if m.status == status]
        h2h = self._h2h_for(f"{context}:{d}", domain)
        out = []
        for m in domain:
            odds = h2h.get(m.id)
            main = MatchMainOddsSchema(**odds) if odds else None
            out.append(conv.match_to_schema(m, main=main, context=context))
        return d, out

    def get_match(self, match_id: int, context: str = "general") -> Optional[MatchSchema]:
        m = self.match_domain(match_id, context=context)
        return conv.match_to_schema(m, context=context) if m else None

    def groups(self, context: str = "general") -> list[GroupSchema]:
        cfg = competition.resolve(context)
        if not cfg.has("groups"):
            return []
        key = f"{_CACHE_V}:groups:{context}:{cfg.season}"
        cached = self._disk.get(key)
        if cached is not None:
            return [GroupSchema.model_validate(d) for d in cached]
        getter = getattr(self._football(context), "get_groups", None)
        if getter is None:
            return []
        domain = getter(cfg.league_ids[0], cfg.season) or []
        out = [conv.group_to_schema(g) for g in domain]
        if out:
            self._disk.set(key, [g.model_dump(mode="json") for g in out], config.STATS_CACHE_TTL)
        return out

    def bracket(self, context: str = "general") -> list[BracketStageSchema]:
        cfg = competition.resolve(context)
        if not cfg.has("bracket"):
            return []
        # Torneio inteiro (por temporada) → todas as fases do mata-mata.
        matches = self.matches_domain_for(None, context)
        return conv.bracket_from_matches(matches)

    def match_statistics(self, match_id: int,
                         context: str = "general") -> Optional[MatchStatisticsSchema]:
        key = f"{_CACHE_V}:stats:{context}:{match_id}"
        cached = self._disk.get(key)
        if cached is not None:
            return MatchStatisticsSchema.model_validate(cached)
        m = self.match_domain(match_id, context=context)
        if m is None:
            return None
        raw = self._football(context).get_match_statistics(match_id)
        home_side = raw.home if raw else {}
        away_side = raw.away if raw else {}
        home_form = self.team_form(m.home_team.id, context=context)
        away_form = self.team_form(m.away_team.id, context=context)

        lineups = self._football(context).get_lineups(match_id) or []
        lu_home = next((lu for lu in lineups if lu.team_id == m.home_team.id), None)
        lu_away = next((lu for lu in lineups if lu.team_id == m.away_team.id), None)

        injuries = self._injuries(m, context)
        recommendation, model_note = self._match_recommendation(m, home_form, away_form, context)

        schema = MatchStatisticsSchema(
            match_id=match_id,
            home=conv.team_match_stats(m.home_team, home_form, home_side),
            away=conv.team_match_stats(m.away_team, away_form, away_side),
            injuries=injuries or None,
            probable_lineup_home=conv.lineup_slots(lu_home) if lu_home else None,
            probable_lineup_away=conv.lineup_slots(lu_away) if lu_away else None,
            recommendation=recommendation,
            model_note=model_note,
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        self._disk.set(key, schema.model_dump(mode="json"), config.STATS_CACHE_TTL)
        return schema

    def _injuries(self, m: Match, context: str = "general") -> list[MatchInjurySchema]:
        getter = getattr(self._football(context), "get_injuries", None)
        if getter is None:
            return []
        out: list[MatchInjurySchema] = []
        for side, team in (("home", m.home_team), ("away", m.away_team)):
            for inj in (getter(team.id) or []):
                out.append(MatchInjurySchema(
                    player_name=inj.name, team_side=side,
                    reason=inj.reason or None, status=inj.type or None,
                ))
        return out

    def _match_recommendation(self, m: Match, home_form, away_form, context: str = "general"):
        """Análise do modelo pra ESTE jogo.

        COM odds → recomendação de VALOR (edge). SEM odds (ex.: fonte grátis) →
        previsão do modelo (probabilidades + odd justa, sem edge), pra a tela
        nunca ficar vazia. Usa a forma; sem forma, cai em médias da liga.
        """
        from src.providers.base import TeamForm
        from src.recommendation.engine import generate_recommendations, predict_markets

        hf = home_form or TeamForm(team_id=m.home_team.id)
        af = away_form or TeamForm(team_id=m.away_team.id)
        scarce = home_form is None or away_form is None

        # 1) Com odds → recomendação de valor (edge).
        odds = self.match_odds_domain(m)
        if odds is not None and odds.markets:
            cands = generate_recommendations(match=m, home_form=hf, away_form=af, odds=odds)
            if cands:
                return front_mappers.candidate_to_out(cands[0]), cands[0].recommendation_reason

        # 2) Sem odds → previsão do modelo (probabilidades + odd justa).
        p = predict_markets(hf, af)
        note = (
            f"Modelo: Casa {p['home']*100:.0f}% · Empate {p['draw']*100:.0f}% · "
            f"Fora {p['away']*100:.0f}%. Over 2.5: {p['over25']*100:.0f}% · "
            f"Ambas marcam: {p['btts_yes']*100:.0f}%. "
            f"Gols esperados {p['lambda_home']:.1f}–{p['lambda_away']:.1f}."
        )
        if scarce:
            note += " (poucos jogos no torneio — baseado em médias.)"
        sel = max(("home", "draw", "away"), key=lambda k: p[k])
        prob = p[sel]
        rec = RecommendationOut(
            id=0, match=f"{m.home_team.name} x {m.away_team.name}", match_id=m.id,
            league=m.league_name or None, market="1x2", selection=sel, line=None,
            odd=None, fair_odd=round(1 / prob, 2) if prob > 0 else None,
            model_prob=round(prob, 4), implied_prob=None, edge=None,
            confidence=front_mappers.confidence_label(prob * 100),
            status="pending", reason=note, bookmaker=None, created_at="",
            context=context, stage=m.stage, group=m.group,
        )
        return rec, note

    def match_odds(self, match_id: int, context: str = "general") -> Optional[MatchOddsSchema]:
        m = self.match_domain(match_id, context=context)
        if m is None:
            return None
        odds = self.match_odds_domain(m)
        return conv.odds_to_schema(odds) if odds else None

    def leagues(self) -> list[LeagueSchema]:
        # Catálogo quase estático — cache longo (a busca de ligas faz 1 chamada
        # POR liga na api-football; sem cache, cada Dashboard/Ligas custava N).
        key = f"{_CACHE_V}:leagues_catalog"
        catalog = self._disk.get(key)
        if catalog is None:
            provider_leagues = self._football().get_leagues() or []
            catalog = [conv.league_to_schema(lg).model_dump(mode="json")
                       for lg in provider_leagues]
            if catalog:
                self._disk.set(key, catalog, config.CATALOG_CACHE_TTL)
        # matches_today por liga (reusa o cache de jogos do dia, 1 chamada).
        try:
            today = self.matches_by_date_domain(self.today_str())
        except Exception:  # noqa: BLE001
            today = []
        counts: dict[int, int] = {}
        for mt in today:
            counts[mt.league_id] = counts.get(mt.league_id, 0) + 1
        out = []
        for d in catalog:
            lg = LeagueSchema.model_validate(d)
            lg.matches_today = counts.get(lg.id)
            out.append(lg)
        return out

    def teams(self, *, league_id: Optional[int] = None,
              search: Optional[str] = None,
              context: str = "general") -> list[TeamSchema]:
        getter = getattr(self._football(context), "get_teams", None)
        if getter is None:
            return []
        cfg = competition.resolve(context)
        lid = league_id or (cfg.league_ids[0] if context != "general" else None)
        domain = getter(league_id=lid, search=search) or []
        # Anexa a forma (W/D/L, recent_form, gols, xG) — cacheada em disco, então
        # recarregar a lista é barato após o 1º fetch.
        return [conv.team_to_schema(t, form=self.team_form(t.id, context=context)) for t in domain]

    def team(self, team_id: int, context: str = "general") -> Optional[TeamSchema]:
        t = self._football(context).get_team(team_id)
        if t is None:
            return None
        form = self.team_form(team_id, context=context)
        return conv.team_to_schema(t, form=form)

    def players(self, *, team_id: Optional[int] = None,
                search: Optional[str] = None,
                context: str = "general") -> list[PlayerSchema]:
        getter = getattr(self._football(context), "get_players", None)
        if getter is None:
            return []
        domain = getter(team_id=team_id, search=search) or []
        return [conv.player_to_schema(p) for p in domain]

    def player(self, player_id: int, context: str = "general") -> Optional[PlayerSchema]:
        p = self._football(context).get_player(player_id)
        return conv.player_to_schema(p) if p else None

    def competition_players(self, context: str = "general") -> list[PlayerSchema]:
        """Todos os jogadores da competição (com stats), cacheados em disco 24h."""
        cfg = competition.resolve(context)
        key = f"{_CACHE_V}:players_all:{context}:{cfg.season}"
        cached = self._disk.get(key)
        if cached is not None:
            return [PlayerSchema.model_validate(d) for d in cached]
        getter = getattr(self._football(context), "get_competition_players", None)
        domain = (getter(cfg.league_ids[0], cfg.season) if getter else []) or []
        out = [conv.player_to_schema(p) for p in domain]
        if out:
            self._disk.set(key, [p.model_dump(mode="json") for p in out],
                           config.CATALOG_CACHE_TTL)
        return out

    # Métrica → função de ordenação (líderes). "free_kicks" não existe na
    # api-football; cobrimos o que a fonte fornece.
    _LEADER_KEYS = {
        "goals": lambda p: p.goals,
        "assists": lambda p: p.assists,
        "shots": lambda p: p.shots,
        "shots_on_target": lambda p: p.shots_on_target,
    }

    def player_leaders(self, *, context: str = "general",
                       metric: str = "goals", limit: int = 20) -> list[PlayerSchema]:
        cfg = competition.resolve(context)
        # Gols/assistências: endpoint dedicado da fonte (1 request, barato).
        cheap = {"goals": "get_top_scorers", "assists": "get_top_assists"}.get(metric)
        if cheap:
            key = f"{_CACHE_V}:leaders:{context}:{metric}:{cfg.season}"
            cached = self._disk.get(key)
            if cached is not None:
                return [PlayerSchema.model_validate(d) for d in cached][:limit]
            getter = getattr(self._football(context), cheap, None)
            if getter is not None:
                domain = getter(cfg.league_ids[0], cfg.season) or []
                out = [conv.player_to_schema(p) for p in domain]
                if out:
                    self._disk.set(key, [p.model_dump(mode="json") for p in out],
                                   config.STATS_CACHE_TTL)
                    return out[:limit]
        # Chutes / chutes no gol: precisa da lista completa (mais caro, cacheado).
        keyf = self._LEADER_KEYS.get(metric, self._LEADER_KEYS["goals"])
        players = self.competition_players(context)
        ranked = sorted(players, key=lambda p: (keyf(p) or 0), reverse=True)
        return ranked[:limit]

    def odds_board(self, *, league_id: Optional[int] = None,
                   market: Optional[str] = None) -> list[OddsBoardItemSchema]:
        """Painel de odds dos jogos do dia. A 'melhor' entrada = maior odd
        (do mercado filtrado, ou a primeira disponível)."""
        _, matches = self.matches(date=self.today_str(), league_id=league_id)
        domain = self.matches_by_date_domain(self.today_str())
        if league_id:
            domain = [m for m in domain if m.league_id == league_id]
        board: list[OddsBoardItemSchema] = []
        for m in domain:
            odds = self.match_odds_domain(m)
            if odds is None or not odds.markets:
                continue
            entries = conv.odds_entries(odds)
            if market:
                entries = [e for e in entries if e.market == market]
            if not entries:
                continue
            best = max(entries, key=lambda e: e.odd)
            board.append(OddsBoardItemSchema(
                match=conv.match_summary(m), best=best, entries=entries,
            ))
        return board
