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
_CACHE_V = "v8"  # bump: novas ligas (Série B, Libertadores, Copa do Brasil)

# Nome/país amigável por liga (api-football). Duplo uso: (1) fallback pra garantir
# que TODA liga configurada apareça no filtro mesmo se a chamada de catálogo falhar;
# (2) rótulo claro — "Serie A · Brazil" se confundia com a Itália no dropdown.
_LEAGUE_DISPLAY: dict[int, tuple[str, str]] = {
    39: ("Premier League", "England"),
    140: ("La Liga", "Spain"),
    135: ("Serie A", "Italy"),
    78: ("Bundesliga", "Germany"),
    61: ("Ligue 1", "France"),
    71: ("Brasileirão Série A", "Brazil"),
    2: ("Champions League", "Europe"),
    72: ("Brasileirão Série B", "Brazil"),
    13: ("Libertadores", "América do Sul"),
    73: ("Copa do Brasil", "Brazil"),
}

# Piso de probabilidade POR MERCADO no feed pré-jogo. Os que mais erram no ledger
# (over/under ~56%, BTTS volátil) exigem mais confiança que o piso global. O
# resto (dupla chance, team_total, gol cedo) usa config.MIN_PICK_PROB. Ajustável
# conforme a validação semanal.
_OPP_MARKET_FLOOR = {"over_under": 0.68, "btts": 0.63}


class FootballDataService:
    def __init__(self, disk_cache=None, odds_cache=None) -> None:
        if disk_cache is None:
            disk_cache = PersistentCache(
                path=os.path.join(config.CACHE_DIR, "football_cache.json"),
                name="football_data",
            )
        self._disk = disk_cache
        self._odds_cache = odds_cache or SimpleCache(name="football_odds")
        # Memo em memória dos ratings de força por contexto (reconstrução do
        # disco é barata, mas evita refazê-la a cada jogo no loop de oportunidades).
        self._ratings_mem: dict[str, tuple] = {}

    # --- providers ---------------------------------------------------------

    def _football(self, context: str = "general"):
        return registry.get_football_provider(context)

    def _odds(self, context: str = "general"):
        return registry.get_odds_provider(context)

    # --- domínio (cacheado em disco; reusado pelo engine) ------------------

    def matches_by_date_domain(self, date: str) -> list[Match]:
        key = f"{_CACHE_V}:matches:{date}"
        cached = self._disk.get(key)
        if cached is not None:
            return [serde.match_from_dict(d) for d in cached]
        matches = self._football().get_matches_by_date(date) or []
        # Data futura (sem jogo ao vivo) → TTL longo; hoje/passado → curto, pra
        # não segurar placar ao vivo. Reduz a repetição da varredura de 3 dias.
        ttl = config.FIXTURES_FUTURE_TTL if date > self.today_str() else config.MATCHES_CACHE_TTL
        self._disk.set(key, [serde.match_to_dict(m) for m in matches], ttl)
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

    def _league_season(self, league_id: Optional[int], context: str = "general") -> int:
        """Temporada atual da LIGA (detectada da api-football; varia por liga —
        Brasileirão=2026, Europa=2025). Cacheada longa (muda 1×/ano). Fallback:
        a season do contexto (config.CURRENT_SEASON)."""
        cfg = competition.resolve(context)
        if not league_id:
            return cfg.season
        key = f"{_CACHE_V}:leagueseason:{league_id}"
        cached = self._disk.get(key)
        if cached is not None:
            return int(cached)
        season = None
        getter = getattr(self._football(context), "get_current_season", None)
        if getter is not None:
            try:
                season = getter(league_id)
            except Exception:  # noqa: BLE001
                season = None
        season = int(season) if season else cfg.season
        self._disk.set(key, season, config.CATALOG_CACHE_TTL)
        return season

    def team_form(self, team_id: int, last_n: int = 10, context: str = "general",
                  league_id: Optional[int] = None) -> Optional[TeamForm]:
        cfg = competition.resolve(context)
        # Liga REAL do time (do jogo) + temporada atual DELA — sem isso, um time
        # do Brasileirão era consultado como se fosse da Premier e vinha vazio.
        lid = league_id or cfg.league_ids[0]
        season = self._league_season(league_id, context)
        key = f"{_CACHE_V}:form:{context}:{lid}:{season}:{team_id}:{last_n}"
        cached = self._disk.get(key)
        if cached is not None:
            return serde.form_from_dict(cached)
        getter = self._football(context).get_team_form
        try:
            form = getter(team_id, last_n, league_id=lid, season=season)
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

    @staticmethod
    def _odds_key(match_id: int, context: str) -> str:
        return f"odds:{context}:{match_id}"

    @staticmethod
    def _odds_ttl(match: Match) -> int:
        """TTL das odds por estado: ao vivo curto (fallback; o worker invalida
        por gol), pré-jogo/encerrado longo (odds andam devagar)."""
        if match.status == "live":
            return config.ODDS_LIVE_TTL
        return config.ODDS_PREMATCH_TTL

    def match_odds_domain(self, match: Match, *, context: str = "general",
                          force: bool = False) -> Optional[MatchOdds]:
        """Odds por jogo (event_odds, caro). TTL por estado do jogo; `force`
        ignora o cache (usado pelo refresh por evento ao vivo)."""
        key = self._odds_key(match.id, context)
        if not force:
            cached = self._odds_cache.get(key)
            if cached is not None:
                return cached
        provider = self._odds(context)
        if provider is None:
            return None
        odds = provider.get_match_odds(match)
        if odds is not None:
            self._odds_cache.set(key, odds, self._odds_ttl(match))
        return odds

    def invalidate_odds(self, match_id: int, context: str = "general") -> None:
        """Descarta as odds cacheadas de um jogo (chamado quando há gol ao vivo)."""
        self._odds_cache.invalidate(self._odds_key(match_id, context))

    def live_matches(self, context: str = "general") -> list[Match]:
        """Jogos ao vivo do contexto, via 1 chamada agregada barata
        (fixtures?live=all), filtrados pelas ligas do contexto. Cache curtíssimo
        pra não repetir a chamada dentro do mesmo tick."""
        key = f"live:{context}"
        cached = self._odds_cache.get(key)
        if cached is not None:
            return cached
        getter = getattr(self._football(context), "get_live_matches", None)
        if getter is None:
            return []
        try:
            allowed = set(competition.resolve(context).league_ids)
            live = [m for m in (getter() or []) if not allowed or m.league_id in allowed]
        except Exception:  # noqa: BLE001 — provider instável nunca derruba o worker
            logger.warning("live_matches: falha buscando jogos ao vivo (%s)", context)
            live = []
        self._odds_cache.set(key, live, 30)
        return live

    # --- API pública (schemas do front) ------------------------------------

    @staticmethod
    def today_str() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _h2h_for(self, key_suffix: str, matches: list[Match],
                 context: str = "general") -> dict[int, dict]:
        """1x2 inline em lote, cacheado em memória (TTL curto, barato)."""
        if not matches:
            return {}
        key = f"h2h:{key_suffix}"
        cached = self._odds_cache.get(key)
        if cached is not None:
            return cached
        provider = self._odds(context)
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
        """Jogos das LIGAS acompanhadas no dia (`date` ou hoje)."""
        cfg = competition.resolve(context)
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
        h2h = self._h2h_for(f"{context}:{d}", domain, context)
        out = []
        for m in domain:
            odds = h2h.get(m.id)
            main = MatchMainOddsSchema(**odds) if odds else None
            out.append(conv.match_to_schema(m, main=main, context=context))
        return d, out

    def matches_upcoming(self, *, league_id: Optional[int] = None,
                         status: Optional[str] = None, context: str = "general",
                         limit: int = 40) -> list[MatchSchema]:
        """Próximos jogos (hoje + 3 dias) das ligas acompanhadas, ordenados por
        horário. Preenche as telas quando NÃO há jogo hoje — a lista por data
        (`matches`) mostra só o dia pedido e fica vazia num dia sem partida."""
        domain = self._upcoming_matches(context, only_future=False)
        if league_id:
            domain = [m for m in domain if m.league_id == league_id]
        if status:
            domain = [m for m in domain if m.status == status]
        domain.sort(key=lambda m: (m.utc_kickoff is None, m.utc_kickoff))
        domain = domain[:limit]
        h2h = self._h2h_for(f"{context}:upcoming", domain, context)
        out: list[MatchSchema] = []
        for m in domain:
            odds = h2h.get(m.id)
            main = MatchMainOddsSchema(**odds) if odds else None
            out.append(conv.match_to_schema(m, main=main, context=context))
        return out

    def get_match(self, match_id: int, context: str = "general") -> Optional[MatchSchema]:
        m = self.match_domain(match_id, context=context)
        return conv.match_to_schema(m, context=context) if m else None

    def match_statistics(self, match_id: int,
                         context: str = "general") -> Optional[MatchStatisticsSchema]:
        # ":c2" = recomendação calibrada (invalida cache com edge cru antigo).
        key = f"{_CACHE_V}:stats:c2:{context}:{match_id}"
        cached = self._disk.get(key)
        if cached is not None:
            return MatchStatisticsSchema.model_validate(cached)
        m = self.match_domain(match_id, context=context)
        if m is None:
            return None
        raw = self._football(context).get_match_statistics(match_id)
        home_side = raw.home if raw else {}
        away_side = raw.away if raw else {}
        home_form = self.team_form(m.home_team.id, context=context, league_id=m.league_id)
        away_form = self.team_form(m.away_team.id, context=context, league_id=m.league_id)

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
        from src.recommendation.engine import predict_markets

        hf = home_form or TeamForm(team_id=m.home_team.id)
        af = away_form or TeamForm(team_id=m.away_team.id)
        scarce = home_form is None or away_form is None

        # Gols esperados via ratings de força (torneio) ou modelo de forma — a
        # MESMA fonte usada na tabela de mercados, pra a análise bater com ela.
        lam = self._lambdas(m, hf, af, context)

        # 1) MELHOR mercado que o modelo julga PROVÁVEL (mesma régua da aba
        #    Recomendações: prob ≥ piso, odds opcionais). Bate com a tabela.
        rows = self.match_markets(m.id, context=context)
        focus = {"1x2", "over_under", "btts"}

        def _prob_of(r):
            return ((1 + r.edge) / r.odd) if (r.edge is not None and r.odd) else (r.model_prob or 0.0)

        cands = [
            r for r in rows
            if r.market in focus and _prob_of(r) >= config.MIN_PICK_PROB
            and not (r.market == "over_under" and r.line != 2.5)
            and (r.odd is None or config.MIN_ODD <= r.odd <= config.MAX_ODD)
        ]
        if cands:
            best = max(cands, key=_prob_of)
            sample = min(hf.matches_played, af.matches_played)
            out = self._prob_out(m, best, _prob_of(best), sample, context)
            return out, out.reason

        # 2) Sem valor claro → previsão do modelo (probabilidades, sem edge).
        p = predict_markets(hf, af, lambdas=lam)
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
        odds = self.match_odds_domain(m, context=context)
        return conv.odds_to_schema(odds) if odds else None

    def _ratings(self, context: str):
        """Ratings de força (ataque/defesa) das seleções do torneio, AJUSTADOS
        POR ADVERSÁRIO a partir do histórico recente (qualifiers/amistosos/Nations
        League). É o prior que conserta o modelo subvalorizando favoritos no
        início do torneio. None fora de torneio ou sem dados suficientes."""
        import time as _t

        from src.probability import TeamRatings, compute_ratings

        cfg = competition.resolve(context)
        if not cfg.tournament:
            return None

        memo = self._ratings_mem.get(context)
        if memo and (_t.monotonic() - memo[1]) < 600:
            return memo[0]

        def _remember(r):
            self._ratings_mem[context] = (r, _t.monotonic())
            return r

        # ":s55" = ratings com shrink à média (invalida cache de ratings extremos).
        key = f"{_CACHE_V}:ratings:s55:{context}:{cfg.season}"
        cached = self._disk.get(key)
        if cached is not None:
            return _remember(TeamRatings(
                avg=cached.get("avg", 1.35),
                attack={int(k): v for k, v in cached.get("attack", {}).items()},
                defense={int(k): v for k, v in cached.get("defense", {}).items()},
            ))

        getter = getattr(self._football(context), "get_recent_results", None)
        if getter is None:
            return None
        team_ids: set[int] = set()
        for sm_ in self.season_matches_domain(context):
            team_ids.add(sm_.home_team.id)
            team_ids.add(sm_.away_team.id)
        if not team_ids:
            return None

        results: dict[int, Match] = {}
        for tid in team_ids:
            try:
                for rm in (getter(tid, config.RATINGS_RECENT_N) or []):
                    results[rm.id] = rm
            except Exception:  # noqa: BLE001 — provider instável nunca derruba o feed
                logger.warning("ratings: falha buscando resultados do time %s", tid)
        if len(results) < 5:  # dados insuficientes → fallback no modelo de forma
            return None

        ratings = compute_ratings(list(results.values()),
                                  iterations=config.RATINGS_ITERATIONS,
                                  shrink=config.RATINGS_SHRINK)
        self._disk.set(key, {
            "avg": ratings.avg,
            "attack": {str(k): v for k, v in ratings.attack.items()},
            "defense": {str(k): v for k, v in ratings.defense.items()},
        }, config.STATS_CACHE_TTL)
        return _remember(ratings)

    def _lambdas(self, m: Match, hf: TeamForm, af: TeamForm, context: str) -> tuple[float, float]:
        """Gols esperados do jogo. Em torneio, usa os ratings de força (ajustados
        por adversário) quando ambos os times têm rating; senão cai no modelo de
        forma (expected_goals). Fonte única pra TODOS os mercados → consistência."""
        from src.probability import expected_goals

        ratings = self._ratings(context)
        if ratings is not None and ratings.has(m.home_team.id) and ratings.has(m.away_team.id):
            return ratings.lambdas(m.home_team.id, m.away_team.id,
                                   home_advantage=config.LEAGUE_HOME_ADV)
        return expected_goals(hf, af)

    def match_markets(self, match_id: int, context: str = "general"):
        """Probabilidades do modelo + odd justa (+ odd/edge se houver odds) pros
        principais mercados de gols do jogo. Base dos blocos 'Probabilidades' e
        'Mercados' da análise."""
        import math
        import statistics as _st
        from src.probability import build_score_matrix, edge as _edge
        from src.probability.markets import (
            btts, double_chance, draw_no_bet, match_winner, over_under, team_totals,
        )
        from src.providers.base import TeamForm
        from src.schemas.football_schemas import MarketLineSchema

        m = self.match_domain(match_id, context=context)
        if m is None:
            return []
        hf = self.team_form(m.home_team.id, context=context, league_id=m.league_id) or TeamForm(team_id=m.home_team.id)
        af = self.team_form(m.away_team.id, context=context, league_id=m.league_id) or TeamForm(team_id=m.away_team.id)
        lam_h, lam_a = self._lambdas(m, hf, af, context)
        sm = build_score_matrix(lam_h, lam_a)

        odd_map: dict[tuple, list[float]] = {}
        odds = self.match_odds_domain(m, context=context)
        if odds:
            for mk, mo in odds.markets.items():
                for s in mo.selections:
                    odd_map.setdefault((mk, s.name, s.line), []).append(s.price)

        rows = []

        def add(market: str, selection: str, prob: float, line=None):
            prices = odd_map.get((market, selection, line))
            odd = round(_st.mean(prices), 2) if prices else None
            rows.append(MarketLineSchema(
                market=market, selection=selection, line=line,
                model_prob=round(prob, 4), fair_odd=round(1 / prob, 2) if prob > 0 else 0.0,
                odd=odd, edge=round(_edge(prob, odd), 4) if odd else None,
                confidence=round(min(prob, 0.97) * 100, 1),
            ))

        w = match_winner(sm)
        for sel in ("home", "draw", "away"):
            add("1x2", sel, w[sel])
        for sel, val in double_chance(sm).items():
            add("double_chance", sel, val)
        for sel, val in draw_no_bet(sm).items():
            add("dnb", sel, val)
        for line in (0.5, 1.5, 2.5, 3.5):
            ou = over_under(sm, line)
            add("over_under", "over", ou["over"], line)
            add("over_under", "under", ou["under"], line)
        b = btts(sm)
        add("btts", "yes", b["yes"])
        add("btts", "no", b["no"])
        # Discrepância: time marca 2+ (team total over 1.5) — favorito domina.
        add("team_total", "home", team_totals(sm, 1.5, home=True)["over"], 1.5)
        add("team_total", "away", team_totals(sm, 1.5, home=False)["over"], 1.5)
        # Gol cedo: prob de sair gol no 1º tempo (~45% dos gols esperados saem no 1T).
        p_1h = 1.0 - math.exp(-0.45 * (lam_h + lam_a))
        add("first_half_goal", "yes", p_1h)
        # Gol RÁPIDO: prob de sair gol até os 30 min (1/3 do jogo).
        p_30 = 1.0 - math.exp(-(lam_h + lam_a) / 3.0)
        add("first_30_goal", "yes", p_30)

        # ESCANTEIOS + CARTÕES pré-jogo — usa a média por time da agregação de
        # stats da liga. Projeta o total (Poisson) e liquida via settlement.
        if config.ENABLE_STATS_AGGREGATION:
            from src.probability.markets import poisson_over_under
            ha = self._team_advanced_stats(m.home_team.id, context)
            aa = self._team_advanced_stats(m.away_team.id, context)
            knockout = bool(m.stage and m.stage != "group")
            # Linha ~1.5 abaixo da média projetada → over provável (≥ piso) e que
            # ainda paga (não é o total exato, quase coin-flip).
            if ha and aa and ha.corners_for and aa.corners_for:
                c_mean = ha.corners_for + aa.corners_for
                c_line = max(6.5, round(c_mean) - 1.5)
                add("corners", "over", poisson_over_under(c_mean, c_line)["over"], c_line)
            if ha and aa and ha.cards_for and aa.cards_for:
                k_mean = (ha.cards_for + aa.cards_for) * (1.15 if knockout else 1.0)
                k_line = max(1.5, round(k_mean) - 1.5)
                add("cards", "over", poisson_over_under(k_mean, k_line)["over"], k_line)

        # Calibração do edge: de-vig + blend modelo×mercado por grupo de mercado;
        # edge irreal (> MAX_EDGE = erro do modelo) vira None (mostra "—").
        from collections import defaultdict
        from src.probability import remove_vig
        groups: dict[tuple, list] = defaultdict(list)
        for r in rows:
            groups[(r.market, r.line)].append(r)
        for grp in groups.values():
            godds = [g.odd for g in grp]
            if not all(godds) or len(godds) < 2:
                for g in grp:
                    g.edge = None if g.odd is None else g.edge
                continue
            devig = remove_vig(godds)
            for g, mkt_p in zip(grp, devig):
                final = config.MODEL_MARKET_BLEND * g.model_prob + (1 - config.MODEL_MARKET_BLEND) * mkt_p
                ev = final * g.odd - 1.0
                g.edge = round(ev, 4) if abs(ev) <= config.MAX_EDGE else None
        return rows

    def _upcoming_matches(self, context: str, *, only_future: bool = False) -> list[Match]:
        """Jogos relevantes pra apostar nas próximas ~72h.

        only_future=True → SÓ jogos que ainda não começaram (kickoff > agora):
        usado em props/recomendações, cujo modelo é PRÉ-JOGO — não faz sentido
        recomendar partida em andamento ou já encerrada. only_future=False
        mantém uma janela de 3h após o kickoff (ao vivo).

        Varre HOJE + próximos 3 dias: a api-football indexa fixtures por data,
        então a janela de 72h só enxerga jogos futuros (ex.: rodada de terça)
        se buscarmos cada dia — não só o de hoje."""
        from datetime import timedelta
        now = datetime.now(timezone.utc)
        horizon = now + timedelta(hours=72)
        floor = now if only_future else now - timedelta(hours=3)
        allowed = set(competition.resolve(context).league_ids)
        seen: set[int] = set()
        out = []
        for day in range(4):                       # hoje, +1, +2, +3
            date_str = (now + timedelta(days=day)).strftime("%Y-%m-%d")
            for m in self.matches_by_date_domain(date_str):
                if m.league_id not in allowed or m.id in seen:
                    continue
                if m.status == "finished":
                    continue
                if only_future and m.status == "live":
                    continue            # feeds pré-jogo não recomendam jogo em andamento
                if m.utc_kickoff is None:
                    # Sem horário não dá pra garantir futuro → fora do modo estrito.
                    if not only_future:
                        seen.add(m.id)
                        out.append(m)
                    continue
                if floor <= m.utc_kickoff <= horizon:
                    seen.add(m.id)
                    out.append(m)
        return out

    # Rótulo PT-BR + motivo de uma linha de mercado calibrada, no feed de valor.
    @staticmethod
    def _opp_selection(market: str, selection: str, line, home: str, away: str) -> str:
        if market == "1x2":
            return {"home": home, "away": away, "draw": "Empate"}.get(selection, selection)
        if market == "over_under":
            return "Over" if selection == "over" else "Under"
        if market == "btts":
            return "Sim" if selection == "yes" else "Não"
        if market == "double_chance":
            return {"home_draw": f"{home} ou empate",
                    "draw_away": f"empate ou {away}",
                    "home_away": f"{home} ou {away}"}.get(selection, selection)
        if market == "dnb":
            return {"home": f"{home} (DNB)", "away": f"{away} (DNB)"}.get(selection, selection)
        if market == "team_total":
            return {"home": f"{home} marca 2+", "away": f"{away} marca 2+"}.get(selection, selection)
        if market == "first_half_goal":
            return "Gol no 1º tempo"
        if market == "first_30_goal":
            return "Gol até 30 min"
        if market == "corners":
            return "Over escanteios"
        if market == "cards":
            return "Over cartões"
        return selection

    def opportunities(self, *, context: str = "general", limit: int = 30,
                      league_id: Optional[int] = None,
                      min_edge: Optional[float] = None, min_odd: Optional[float] = None,
                      max_odd: Optional[float] = None):
        """Recomendações PRÉ-JOGO — 1X2, over/under e BTTS que o modelo julga
        PROVÁVEIS (prob ≥ MIN_PICK_PROB), ordenadas pela probabilidade.

        Funciona COM ou SEM odds: a odd é só informação extra. Sem The Odds API,
        recomenda puramente pela previsão do modelo (foco em confiança, não em
        'valor de mercado'). Só jogos que ainda não começaram. `league_id` filtra
        por liga (o feed é caro → filtrar no servidor, não só no cliente).
        """
        from src.providers.base import TeamForm

        # Cache de topo (pré-jogo muda pouco): sem ele o feed recomputava todos os
        # jogos futuros a cada request — a lentidão relatada. Só no caminho padrão
        # (sem overrides de odds), que é o do endpoint.
        use_cache = min_edge is None and min_odd is None and max_odd is None
        ck = f"oppfeed:{context}:{league_id or 'all'}"
        if use_cache:
            cached = self._odds_cache.get(ck)
            if cached is not None:
                return cached[:limit]

        min_odd = config.MIN_ODD if min_odd is None else min_odd
        max_odd = config.MAX_ODD if max_odd is None else max_odd
        # Foco CURADO (jul/2026, guiado pela validação): 1X2 saiu (34% no ledger,
        # marginal no confidence-first); os fracos ganharam piso maior. Sobra o
        # que acerta + as "coisas que podem acontecer" (gol cedo, discrepância).
        focus = {"over_under", "btts", "double_chance", "team_total",
                 "first_half_goal", "first_30_goal", "corners", "cards"}

        matches = self._upcoming_matches(context, only_future=True)
        if league_id:
            matches = [m for m in matches if m.league_id == league_id]
        out = []
        for m in matches:
            rows = self.match_markets(m.id, context=context)
            if not rows:
                continue
            hf = self.team_form(m.home_team.id, context=context, league_id=m.league_id) or TeamForm(team_id=m.home_team.id)
            af = self.team_form(m.away_team.id, context=context, league_id=m.league_id) or TeamForm(team_id=m.away_team.id)
            sample = min(hf.matches_played, af.matches_played)
            # Discrepância: favorito do 1x2 muito provável (>=58%) → confronto desigual.
            w1x2 = {r.selection: (r.model_prob or 0) for r in rows if r.market == "1x2"}
            tag = "Discrepância alta" if max(w1x2.get("home", 0), w1x2.get("away", 0)) >= 0.58 else None
            match_picks = []
            for r in rows:
                if r.market not in focus:
                    continue
                # Só a linha PRINCIPAL de gols (2.5). Over 0.5 / Under 3.5 são
                # quase-certezas triviais — não servem como recomendação.
                if r.market == "over_under" and r.line != 2.5:
                    continue
                # "Casa ou Fora" (sem empate) é pouco intuitivo — fora.
                if r.market == "double_chance" and r.selection == "home_away":
                    continue
                # Prob exibida: coerente com o edge quando há odds; senão, modelo puro.
                prob = ((1 + r.edge) / r.odd) if (r.edge is not None and r.odd) else (r.model_prob or 0.0)
                # Piso POR MERCADO: os que erram muito (over/under, BTTS) exigem
                # mais confiança pra entrar; o resto usa o piso global.
                if prob < _OPP_MARKET_FLOOR.get(r.market, config.MIN_PICK_PROB):
                    continue
                if r.odd is not None and not (min_odd <= r.odd <= max_odd):
                    continue
                match_picks.append(self._prob_out(m, r, prob, sample, context, tag=tag))
            # Até 3 entradas por jogo, com NO MÁXIMO 1 dupla chance (senão o feed
            # vira só "favorito não perde", seguro mas sem graça).
            match_picks.sort(key=lambda r: (r.model_prob or 0, r.edge or 0), reverse=True)
            capped, dc = [], 0
            for p in match_picks:
                if p.market == "double_chance":
                    if dc >= 1:
                        continue
                    dc += 1
                capped.append(p)
                if len(capped) >= 3:
                    break
            out.extend(capped)
        out.sort(key=lambda r: (r.model_prob or 0, r.edge or 0), reverse=True)
        if use_cache:
            self._odds_cache.set(ck, out, config.LIVE_FEED_TTL * 8)
        return out[:limit]

    def analysis_opportunities(self, *, context: str = "general", limit: int = 30,
                               include_avoid: bool = False):
        """Recomendações PRÉ-JOGO da ENGINE DE ANÁLISE (scores 0–100 + grade +
        reasons/warnings). MODO COMPLEMENTAR — não toca no opportunities().

        Monta as features a partir do que já temos (TeamForm) e roda a
        MarketRecommendationEngine. Dado ausente vira score neutro 50 + warning,
        então o grade fica baixo onde não há informação (honesto)."""
        from src.analysis.features import MatchFeatures
        from src.analysis.markets import MarketRecommendationEngine
        from src.providers.base import TeamForm

        ck = f"analysisfeed:pre:{context}:{int(include_avoid)}"
        cached = self._odds_cache.get(ck)
        if cached is not None:
            return cached[:limit]

        # Stats avançadas (xG/finalizações/escanteios/passes-chave...) da liga.
        use_adv = config.ENABLE_STATS_AGGREGATION

        engine = MarketRecommendationEngine()
        out = []
        for m in self._upcoming_matches(context, only_future=True):
            hf = self.team_form(m.home_team.id, context=context, league_id=m.league_id) or TeamForm(team_id=m.home_team.id)
            af = self.team_form(m.away_team.id, context=context, league_id=m.league_id) or TeamForm(team_id=m.away_team.id)
            h_adv = self._team_advanced_stats(m.home_team.id, context) if use_adv else None
            a_adv = self._team_advanced_stats(m.away_team.id, context) if use_adv else None
            mf = MatchFeatures.from_domain(m, hf, af, home_adv=h_adv, away_adv=a_adv)
            if use_adv:
                # Ponto 1 (grátis): passes-chave por jogo do elenco → Criação.
                kp_h = self._team_key_passes(m.home_team.id, context, hf.matches_played)
                kp_a = self._team_key_passes(m.away_team.id, context, af.matches_played)
                if kp_h is not None:
                    mf.home.key_passes = kp_h
                if kp_a is not None:
                    mf.away.key_passes = kp_a
                # Ponto 2: Understat (PPDA + xG/xGA) pras ligas europeias, se ligado.
                self._merge_understat(mf.home, m.home_team.name, m, context)
                self._merge_understat(mf.away, m.away_team.name, m, context)
            recs = engine.recommend_pre_game(
                mf, match_id=m.id, home_name=m.home_team.name,
                away_name=m.away_team.name, include_avoid=include_avoid)
            out.extend(self._analysis_out(m, r, context) for r in recs[:3])
        out.sort(key=lambda r: r.confidence, reverse=True)
        self._odds_cache.set(ck, out, config.LIVE_FEED_TTL * 8)  # pré-jogo muda pouco
        return out[:limit]

    def _match_stats_cached(self, match_id: int, context: str) -> dict:
        """Estatísticas de UM jogo finalizado (imutáveis) → cache em DISCO bem
        longo, COMPARTILHADO entre os dois times. {} quando a fonte não fornece."""
        key = f"matchstats:{context}:{match_id}"
        cached = self._disk.get(key)
        if cached is not None:
            return cached
        getter = getattr(self._football(context), "get_match_statistics", None)
        data: dict = {}
        if getter is not None:
            try:
                st = getter(match_id)
                if st is not None:
                    data = {"home": st.home or {}, "away": st.away or {}}
            except Exception:  # noqa: BLE001
                data = {}
        self._disk.set(key, data, config.STATS_MATCH_TTL)
        return data

    def match_player_stats(self, match_id: int, context: str = "general") -> list[dict]:
        """Estatística FINAL por jogador do jogo (gols, finalizações no alvo,
        desarmes, assistências) via fixtures/players. Cache em DISCO longo
        (jogo finalizado é imutável). Base da liquidação dos props de jogador.
        [] se a fonte não fornecer."""
        key = f"matchplayers:{context}:{match_id}"
        cached = self._disk.get(key)
        if cached is not None:
            return cached
        getter = getattr(self._football(context), "get_live_player_shots", None)
        data: list = []
        if getter is not None:
            try:
                data = getter(match_id) or []
            except Exception:  # noqa: BLE001
                data = []
        self._disk.set(key, data, config.STATS_MATCH_TTL)
        return data

    def _match_cards_cached(self, match_id: int, context: str) -> dict:
        """Cartões (amarelo+vermelho) por team_id no jogo, via eventos — cache em
        DISCO longo (imutável), COMPARTILHADO entre os dois times. {} se sem dado
        ou se ENABLE_CARDS_FROM_EVENTS=0."""
        if not config.ENABLE_CARDS_FROM_EVENTS:
            return {}
        key = f"matchcards:{context}:{match_id}"
        cached = self._disk.get(key)
        if cached is not None:
            return {int(k): v for k, v in cached.items()}
        getter = getattr(self._football(context), "get_match_cards", None)
        data: dict = {}
        if getter is not None:
            try:
                data = getter(match_id) or {}
            except Exception:  # noqa: BLE001
                data = {}
        self._disk.set(key, data, config.STATS_MATCH_TTL)
        return data

    def _team_advanced_stats(self, team_id: int, context: str):
        """Médias por jogo (xG, finalizações no alvo, escanteios, cartões, posse)
        dos últimos N jogos do time. Cacheado em disco. None se sem dado —
        a engine então mantém o fallback neutro 50."""
        import dataclasses

        from src.analysis.features import TeamAdvancedStats, aggregate_advanced

        # int(USE_FIXTURES) na chave: evita cache de modo offline (fixtures, sem
        # stats) vazar pro modo real e vice-versa.
        key = (f"{_CACHE_V}:teamadv:{int(config.USE_FIXTURES)}:{context}:"
               f"{team_id}:{config.CURRENT_SEASON}")
        cached = self._disk.get(key)
        if cached is not None:
            return TeamAdvancedStats(**cached) if cached else None

        getter = getattr(self._football(context), "get_recent_results", None)
        samples: list[dict] = []
        if getter is not None:
            try:
                matches = getter(team_id, config.STATS_AGG_LAST_N) or []
            except Exception:  # noqa: BLE001
                matches = []
            for mm in matches:
                stats = self._match_stats_cached(mm.id, context)
                if not stats:
                    continue
                is_home = mm.home_team.id == team_id
                own = stats.get("home" if is_home else "away") or {}
                opp = stats.get("away" if is_home else "home") or {}
                if not (own or opp):
                    continue
                sample = {"own": own, "opp": opp}
                # Cartões REAIS via eventos (a estatística não traz amarelos).
                cards = self._match_cards_cached(mm.id, context).get(team_id)
                if cards is not None:
                    sample["cards"] = cards
                samples.append(sample)

        adv = aggregate_advanced(samples)
        self._disk.set(key, dataclasses.asdict(adv) if adv.sample else {},
                       config.STATS_AGG_TTL)
        return adv if adv.sample else None

    def _team_key_passes(self, team_id: int, context: str,
                         matches: int) -> Optional[float]:
        """Passes-chave POR JOGO do time, agregados do elenco (a api-football já
        dá key_passes por jogador — dado GRÁTIS que não usávamos). Proxy de
        criação. None quando não há dado (mantém o fallback neutro 50)."""
        if matches <= 0:
            return None
        try:
            pool = self.team_player_pool(team_id, context)
        except Exception:  # noqa: BLE001
            return None
        total = sum((getattr(p, "key_passes", 0) or 0) for p in pool)
        if total <= 0:
            return None
        return total / matches

    def _merge_understat(self, tf, team_name: str, match: Match, context: str) -> None:
        """Preenche PPDA (e xG/xGA se faltarem) via Understat — ligas europeias.
        Best-effort: se o provider de xG não for o Understat ou não achar o time,
        não mexe em nada (degrada pro estado atual)."""
        prov = registry.get_xg_provider()
        getter = getattr(prov, "get_team_advanced", None)
        if getter is None:
            return
        try:
            adv = getter(team_name, match.league_id, match.season)
        except Exception:  # noqa: BLE001
            return
        if not adv:
            return
        if adv.get("ppda") is not None:
            tf.ppda = adv["ppda"]
        if tf.xg is None and adv.get("xg") is not None:
            tf.xg = adv["xg"]
        if tf.xga is None and adv.get("xga") is not None:
            tf.xga = adv["xga"]

    def live_analysis(self, *, context: str = "general", limit: int = 30,
                      include_avoid: bool = False):
        """Recomendações AO VIVO da ENGINE DE ANÁLISE (LiveGameStateScore +
        regras de escanteios/gols/cartões da seção 5). Monta LiveFeatures das
        estatísticas ao vivo que já temos. MODO COMPLEMENTAR ao live_*."""
        from src.analysis.features import LiveFeatures
        from src.analysis.helpers import normalize as _normalize
        from src.analysis.live import LiveRecommendationEngine

        ck = f"analysisfeed:live:{context}:{int(include_avoid)}"
        cached = self._odds_cache.get(ck)
        if cached is not None:
            return cached[:limit]

        def _g(d, key):
            return d.get(key) if isinstance(d, dict) else None

        def _cards(d):
            # /fixtures/statistics raramente traz amarelos (só vermelhos) → contar
            # só vermelho subestima. Sem amarelo, deixa None (fallback).
            if not isinstance(d, dict):
                return None
            y = d.get("yellow_cards")
            if y is None:
                return None
            return y + (d.get("red_cards") or 0)

        eng = LiveRecommendationEngine()
        out = []
        for m in self.live_matches(context):
            if m.home_goals is None or m.away_goals is None:
                continue
            stats = self._live_stats(m, context)
            mom_h, mom_a = self._momentum(m, context)
            red_h, red_a = self._red_cards(m, context)
            h = (stats.home if stats else {}) or {}
            a = (stats.away if stats else {}) or {}
            lf = LiveFeatures(
                minute=m.minute or 0, home_score=m.home_goals, away_score=m.away_goals,
                shots_home=_g(h, "total_shots"), shots_away=_g(a, "total_shots"),
                shots_on_home=_g(h, "shots_on_goal"), shots_on_away=_g(a, "shots_on_goal"),
                xg_home=_g(h, "expected_goals"), xg_away=_g(a, "expected_goals"),
                corners_home=_g(h, "corner_kicks"), corners_away=_g(a, "corner_kicks"),
                insidebox_home=_g(h, "shots_insidebox"), insidebox_away=_g(a, "shots_insidebox"),
                blocked_home=_g(h, "blocked_shots"), blocked_away=_g(a, "blocked_shots"),
                possession_home=_g(h, "ball_possession"), possession_away=_g(a, "ball_possession"),
                fouls_home=_g(h, "fouls"), fouls_away=_g(a, "fouls"),
                cards_home=_cards(h), cards_away=_cards(a),
                red_home=red_h, red_away=red_a,
                momentum_home=mom_h, momentum_away=mom_a,
                recent_pressure_home=_normalize(mom_h, 0.85, 1.3),
                recent_pressure_away=_normalize(mom_a, 0.85, 1.3),
            )
            knockout = bool(m.stage and m.stage != "group")
            recs = eng.recommend_live(
                lf, match_id=m.id, home_name=m.home_team.name,
                away_name=m.away_team.name, knockout=knockout,
                include_avoid=include_avoid)
            out.extend(self._analysis_out(m, r, context) for r in recs)
        out.sort(key=lambda r: r.confidence, reverse=True)
        self._odds_cache.set(ck, out, config.LIVE_FEED_TTL)
        return out[:limit]

    def _analysis_out(self, m: Match, r, context: str):
        """AnalysisRecommendation (engine) → AnalysisRecommendationOut (contrato)."""
        from src.schemas.football_schemas import AnalysisRecommendationOut
        return AnalysisRecommendationOut(
            match_id=m.id, match=r.match or f"{m.home_team.name} x {m.away_team.name}",
            league=m.league_name or None, market=r.market, selection=r.selection,
            line=r.line, odd=r.odd, confidence=r.confidence, edge_score=r.edge_score,
            risk_score=r.risk_score, recommendation_type=r.recommendation_type,
            grade=r.grade, reasons=r.reasons, warnings=r.warnings,
            raw_scores=r.raw_scores, team=r.team, context=context,
            stage=m.stage, group=m.group,
            kickoff=m.utc_kickoff.isoformat() if m.utc_kickoff else None,
        )

    @staticmethod
    def _prob_confidence(prob: float, edge=None) -> str:
        """Confiança pela PROBABILIDADE (produto confiança-first): quanto mais
        provável, maior a confiança. Se há odds e o modelo discorda MUITO do
        mercado (edge no teto), não deixa subir pra 'high' (cautela)."""
        if prob >= 0.75:
            label = "high"
        elif prob >= 0.65:
            label = "medium"
        else:
            label = "low"
        if label == "high" and edge is not None and edge >= 0.90 * config.MAX_EDGE:
            label = "medium"
        return label

    def _prob_out(self, m: Match, r, prob: float, sample: int,
                  context: str, tag: str | None = None) -> RecommendationOut:
        """Linha de mercado → recomendação por PROBABILIDADE (odds opcionais).
        Odd/edge entram só como informação extra quando existem."""
        edge = r.edge
        conf_label = self._prob_confidence(prob, edge)
        sel = self._opp_selection(
            r.market, r.selection, r.line, m.home_team.name, m.away_team.name)
        line_txt = f" {r.line:g}" if r.line is not None else ""
        reason = f"Estimativa {prob*100:.0f}% para {sel}{line_txt}."
        if r.odd is not None:
            reason += f" Odd {r.odd:.2f}"
            if edge is not None:
                reason += f" · valor {edge*100:+.1f}%"
            reason += "."
        return RecommendationOut(
            id=0, match=f"{m.home_team.name} x {m.away_team.name}",
            match_id=m.id, league=m.league_name or None, market=r.market,
            selection=sel, line=r.line, odd=r.odd,
            fair_odd=round(1 / prob, 2) if prob > 0 else None,
            model_prob=round(prob, 4),
            implied_prob=round(1 / r.odd, 4) if r.odd else None,
            edge=edge, confidence=conf_label,
            status="pending", reason=reason,
            bookmaker=None, created_at="", stage=m.stage, group=m.group,
            kickoff=m.utc_kickoff.isoformat() if m.utc_kickoff else None,
            context=context, tag=tag,
        )

    def live_opportunities(self, *, context: str = "general", limit: int = 30,
                           min_edge: Optional[float] = None,
                           min_odd: Optional[float] = None,
                           max_odd: Optional[float] = None):
        """Picks AO VIVO pelo modelo IN-PLAY: placar + minuto + expulsões +
        MOMENTUM (pressão atual do jogo via estatísticas ao vivo). Recomenda o
        que é PROVÁVEL (prob ≥ MIN_PICK_PROB), ordenado por probabilidade.

        Funciona COM ou SEM odds — a odd é só informação extra. Sem The Odds
        API, roda 100% pelo que está acontecendo em campo + histórico."""
        import statistics as _st

        from src.probability import inplay_market_probs
        from src.providers.base import TeamForm

        min_odd = config.MIN_ODD if min_odd is None else min_odd
        max_odd = config.MAX_ODD if max_odd is None else max_odd

        ck = f"livefeed:opps:{context}:{min_odd}:{max_odd}"
        cached = self._odds_cache.get(ck)
        if cached is not None:
            return cached[:limit]

        out: list[RecommendationOut] = []
        for m in self.live_matches(context):
            if m.home_goals is None or m.away_goals is None:
                continue
            hf = self.team_form(m.home_team.id, context=context, league_id=m.league_id) or TeamForm(team_id=m.home_team.id)
            af = self.team_form(m.away_team.id, context=context, league_id=m.league_id) or TeamForm(team_id=m.away_team.id)
            lh, la = self._lambdas(m, hf, af, context)
            red_h, red_a = self._red_cards(m, context)
            mom_h, mom_a = self._momentum(m, context)
            probs = inplay_market_probs(
                lh, la, m.minute, m.home_goals, m.away_goals,
                red_home=red_h, red_away=red_a, mom_home=mom_h, mom_away=mom_a,
                ou_lines=(2.5,))
            sample = min(hf.matches_played, af.matches_played)

            # Odds são OPCIONAIS (só enriquecem o card quando existem).
            odd_map: dict[tuple, float] = {}
            odds = self.match_odds_domain(m, context=context)
            if odds and odds.markets:
                tmp: dict[tuple, list[float]] = {}
                for mk, mo in odds.markets.items():
                    for s in mo.selections:
                        tmp.setdefault((mk, s.name, s.line), []).append(s.price)
                odd_map = {k: round(_st.mean(v), 2) for k, v in tmp.items() if v}

            cands = [
                ("1x2", "home", None, probs["home"]),
                ("1x2", "draw", None, probs["draw"]),
                ("1x2", "away", None, probs["away"]),
                ("over_under", "over", 2.5, probs["over_2.5"]),
                ("over_under", "under", 2.5, probs["under_2.5"]),
                ("btts", "yes", None, probs["btts_yes"]),
                ("btts", "no", None, probs["btts_no"]),
            ]
            for mk, sel, line, model_p in cands:
                if model_p < config.MIN_PICK_PROB:
                    continue
                odd = odd_map.get((mk, sel, line))
                if odd is not None and not (min_odd <= odd <= max_odd):
                    continue
                edge = round(model_p * odd - 1.0, 4) if odd else None
                out.append(self._live_out(m, mk, sel, line, model_p, odd, edge,
                                          sample, context, red_h, red_a, mom_h, mom_a))
        out.sort(key=lambda r: (r.model_prob or 0, r.edge or 0), reverse=True)
        self._odds_cache.set(ck, out, config.LIVE_FEED_TTL)
        return out[:limit]

    def _live_stats(self, m: Match, context: str):
        """Estatísticas ao vivo do jogo (chutes, posse, escanteios...) — base do
        momentum. Cacheado curto. None quando a fonte não fornece."""
        getter = getattr(self._football(context), "get_match_statistics", None)
        if getter is None:
            return None
        key = f"livestats:{context}:{m.id}"
        cached = self._odds_cache.get(key)
        if cached is not None:
            return cached
        try:
            st = getter(m.id)
        except Exception:  # noqa: BLE001
            st = None
        self._odds_cache.set(key, st, 60)
        return st

    def _momentum(self, m: Match, context: str) -> tuple[float, float]:
        """Multiplicadores de momentum (casa, fora) pela pressão atual do jogo."""
        from src.probability import momentum_multipliers
        st = self._live_stats(m, context)
        if st is None:
            return (1.0, 1.0)
        return momentum_multipliers(st.home, st.away)

    def _live_card_tiles(self, m: Match, side: str, mom: float, *, context: str,
                         conf_label: str, projection=None, projection_delta=None) -> dict:
        """Tiles do card AO VIVO de jogador (pressão, finalizações do time/na área,
        ritmo, valor, minuto, placar) — só com DADO real das estatísticas ao vivo.
        'Ataques perigosos' não existe na api-football → usamos finalizações na área."""
        st = self._live_stats(m, context)
        own = (st.home if side == "home" else st.away) if st else None
        opp = (st.away if side == "home" else st.home) if st else None
        own = own or {}
        opp = opp or {}
        pressure = round(max(0.0, min(100.0, (mom - 0.85) / 0.45 * 100)))
        team_shots = own.get("total_shots")
        box_shots = own.get("shots_insidebox")
        total = (own.get("total_shots") or 0) + (opp.get("total_shots") or 0)
        minute = max(m.minute or 1, 1)
        pace = round(min(10.0, (total / minute) * 15), 1)
        pace_label = "Rápido" if pace >= 6.5 else "Moderado" if pace >= 4 else "Lento"
        pressure_label = "Alta" if pressure >= 60 else "Média" if pressure >= 40 else "Baixa"
        stars = 4 if conf_label == "high" else 3 if conf_label == "medium" else 2
        score = (f"{m.home_goals}-{m.away_goals}"
                 if m.home_goals is not None and m.away_goals is not None else None)
        return {
            "minute": m.minute, "score": score,
            "pressure": pressure, "pressure_label": pressure_label,
            "projection": projection, "projection_delta": projection_delta,
            "team_shots": int(team_shots) if team_shots is not None else None,
            "box_shots": int(box_shots) if box_shots is not None else None,
            "pace": pace, "pace_label": pace_label,
            "value_stars": stars,
        }

    def _live_player_shots(self, match_id: int, context: str) -> list[dict]:
        """get_live_player_shots cacheado curto (memória), COMPARTILHADO entre os
        feeds de chutes e gols ao vivo — evita dobrar as chamadas ao provider
        quando o usuário alterna entre as abas."""
        getter = getattr(self._football(context), "get_live_player_shots", None)
        if getter is None:
            return []
        key = f"liveplayershots:{context}:{match_id}"
        cached = self._odds_cache.get(key)
        if cached is not None:
            return cached
        data = getter(match_id) or []
        self._odds_cache.set(key, data, config.LIVE_FEED_TTL)
        return data

    def live_shots(self, *, context: str = "general", limit: int = 40):
        """ESPECIALISTA EM CHUTES A GOL ao vivo: pra cada jogador em campo,
        projeta os chutes no gol que ainda vêm (taxa de temporada + ritmo no
        jogo + pressão do time) e recomenda quando é provável (≥ MIN_PICK_PROB)
        ele bater a próxima linha. Ordena pelos mais prováveis."""
        from src.probability import live_shots_remaining, prob_at_least, remaining_fraction

        ck = f"livefeed:shots:{context}"
        cached = self._odds_cache.get(ck)
        if cached is not None:
            return cached[:limit]

        out: list[RecommendationOut] = []
        for m in self.live_matches(context):
            getter = getattr(self._football(context), "get_live_player_shots", None)
            if getter is None:
                break
            try:
                live_players = self._live_player_shots(m.id, context)
            except Exception:  # noqa: BLE001
                continue
            if not live_players:
                continue

            # Taxa de temporada (chutes no gol/jogo) por jogador.
            pool = (self.team_player_pool(m.home_team.id, context, season=self._league_season(m.league_id, context))
                    + self.team_player_pool(m.away_team.id, context, season=self._league_season(m.league_id, context)))
            rate_by_id = {
                int(p.id): (p.shots_on_target / p.appearances if p.appearances else 0.0)
                for p in pool
            }
            num_by_id = {int(p.id): p.number for p in pool}
            mom_h, mom_a = self._momentum(m, context)
            minute = m.minute or 0
            minutes_left = remaining_fraction(minute) * 90.0

            for lp in live_players:
                mp = lp.get("minutes", 0)
                if mp <= 0:                       # nem entrou em campo
                    continue
                already = lp.get("shots_on", 0)
                season_per90 = rate_by_id.get(lp["player_id"], 0.0)
                mom = mom_h if lp["team_id"] == m.home_team.id else mom_a
                rem = live_shots_remaining(season_per90, already, mp, minutes_left, mom)
                if rem <= 0.05:
                    continue
                p1 = prob_at_least(1, rem)        # pelo menos +1 no gol
                p2 = prob_at_least(2, rem)        # pelo menos +2 no gol
                if p2 >= config.MIN_PICK_PROB:
                    line, prob = already + 1.5, p2
                elif p1 >= config.MIN_PICK_PROB:
                    line, prob = already + 0.5, p1
                else:
                    continue
                out.append(self._shot_out(m, lp, line, prob, already, rem, mom, context,
                                          number=num_by_id.get(lp["player_id"])))
        out.sort(key=lambda r: r.model_prob or 0, reverse=True)
        self._odds_cache.set(ck, out, config.LIVE_FEED_TTL)
        return out[:limit]

    def _shot_out(self, m: Match, lp: dict, line: float, prob: float, already: int,
                  rem: float, mom: float, context: str, *, number=None) -> RecommendationOut:
        """Recomendação de chutes a gol ao vivo → RecommendationOut."""
        name = lp.get("name", "Jogador")
        is_home = lp["team_id"] == m.home_team.id
        team_name = m.home_team.name if is_home else m.away_team.name
        minute = m.minute if m.minute is not None else 0
        mom_txt = " · time pressionando" if mom >= 1.08 else ""
        conf = self._prob_confidence(prob)
        tiles = self._live_card_tiles(
            m, "home" if is_home else "away", mom, context=context, conf_label=conf,
            projection=round(already + rem, 1), projection_delta=round(rem, 1))
        # Mercado de contagem é INTEIRO ("mais de N" = N ou mais); meia-linha
        # fica só no `line` pro settlement.
        threshold = int(line + 0.5)
        return RecommendationOut(
            id=0, match=f"{m.home_team.name} x {m.away_team.name}",
            match_id=m.id, league=m.league_name or None,
            market="player_shots_on_target",
            selection=f"{name} — Mais de {threshold} chutes no gol",
            line=line, odd=None, fair_odd=round(1 / prob, 2) if prob > 0 else None,
            model_prob=round(prob, 4), implied_prob=None, edge=None,
            confidence=conf,
            status="live", reason=(
                f"AO VIVO {minute}' · {name} já tem {already} chute(s) no gol "
                f"(projeção +{rem:.1f}{mom_txt}). {prob*100:.0f}% de fazer {threshold}+ no gol."
            ),
            bookmaker=None, created_at="", stage=m.stage, group=m.group,
            kickoff=m.utc_kickoff.isoformat() if m.utc_kickoff else None,
            context=context, team=team_name, player_number=number, stats_used=tiles,
        )

    def live_goals(self, *, context: str = "general", limit: int = 40):
        """GOLS AO VIVO: jogador que pode (ainda) marcar — taxa de gol da
        temporada × tempo restante × pressão do time, + batedor de pênalti.
        Pula quem já marcou (bet de artilheiro já ganha)."""
        import math

        from src.probability import remaining_fraction
        from src.recommendation.player_props import MATCH_PEN_GOALS

        ck = f"livefeed:goals:{context}"
        cached = self._odds_cache.get(ck)
        if cached is not None:
            return cached[:limit]

        out: list[RecommendationOut] = []
        for m in self.live_matches(context):
            getter = getattr(self._football(context), "get_live_player_shots", None)
            if getter is None:
                break
            try:
                live_players = self._live_player_shots(m.id, context)
            except Exception:  # noqa: BLE001
                continue
            if not live_players:
                continue

            pool = (self.team_player_pool(m.home_team.id, context, season=self._league_season(m.league_id, context))
                    + self.team_player_pool(m.away_team.id, context, season=self._league_season(m.league_id, context)))
            # Gol de bola rolando por jogo + batedor de pênalti por time.
            rate_by_id = {
                int(p.id): (max((p.goals or 0) - (p.penalty_scored or 0), 0) / p.appearances
                            if p.appearances else 0.0)
                for p in pool
            }
            num_by_id = {int(p.id): p.number for p in pool}
            taker_by_team: dict[int, int] = {}
            best_pen: dict[int, int] = {}
            for p in pool:
                tid = int(p.team_id or 0)
                pen = p.penalty_scored or 0
                if pen >= 1 and pen > best_pen.get(tid, 0):
                    best_pen[tid] = pen
                    taker_by_team[tid] = int(p.id)
            mom_h, mom_a = self._momentum(m, context)
            minute = m.minute or 0
            frac = remaining_fraction(minute)

            for lp in live_players:
                if lp.get("minutes", 0) <= 0 or lp.get("goals", 0) >= 1:
                    continue                          # fora / já marcou (já ganhou)
                rate = rate_by_id.get(lp["player_id"], 0.0)
                mom = mom_h if lp["team_id"] == m.home_team.id else mom_a
                rem_lambda = rate * frac * mom
                if lp["player_id"] == taker_by_team.get(lp["team_id"]):
                    rem_lambda += MATCH_PEN_GOALS * frac    # pênalti só pro batedor
                if rem_lambda <= 0.02:
                    continue
                prob = 1.0 - math.exp(-rem_lambda)
                if prob < config.LIVE_GOAL_MIN_PROB:
                    continue
                out.append(self._goal_out(
                    m, lp, prob, mom, context,
                    is_taker=lp["player_id"] == taker_by_team.get(lp["team_id"]),
                    number=num_by_id.get(lp["player_id"])))
        out.sort(key=lambda r: r.model_prob or 0, reverse=True)
        self._odds_cache.set(ck, out, config.LIVE_FEED_TTL)
        return out[:limit]

    def _goal_out(self, m: Match, lp: dict, prob: float, mom: float,
                  context: str, is_taker: bool = False, *, number=None) -> RecommendationOut:
        """Recomendação de 'jogador pode marcar' ao vivo → RecommendationOut."""
        name = lp.get("name", "Jogador")
        is_home = lp["team_id"] == m.home_team.id
        team_name = m.home_team.name if is_home else m.away_team.name
        minute = m.minute if m.minute is not None else 0
        # Gol é raro: escala de confiança própria (50%+ = forte; 38%+ = média).
        conf = "high" if prob >= 0.50 else "medium" if prob >= 0.38 else "low"
        notes = []
        if mom >= 1.08:
            notes.append("time pressionando")
        if is_taker:
            notes.append("cobra os pênaltis")
        note_txt = (" · " + ", ".join(notes)) if notes else ""
        tiles = self._live_card_tiles(
            m, "home" if is_home else "away", mom, context=context, conf_label=conf)
        return RecommendationOut(
            id=0, match=f"{m.home_team.name} x {m.away_team.name}",
            match_id=m.id, league=m.league_name or None, market="anytime_scorer",
            selection=f"{name} — Marcar a qualquer momento",
            line=None, odd=None, fair_odd=round(1 / prob, 2) if prob > 0 else None,
            model_prob=round(prob, 4), implied_prob=None, edge=None, confidence=conf,
            status="live", reason=(
                f"AO VIVO {minute}' · {name}{note_txt}. "
                f"{prob*100:.0f}% de marcar no tempo que resta."
            ),
            bookmaker=None, created_at="", stage=m.stage, group=m.group,
            kickoff=m.utc_kickoff.isoformat() if m.utc_kickoff else None,
            context=context, team=team_name, player_number=number, stats_used=tiles,
        )

    def _red_cards(self, m: Match, context: str) -> tuple[int, int]:
        """Expulsões (mandante, visitante) do jogo ao vivo. Cacheado curto pra
        não re-bater a cada refresh (mudam raramente)."""
        getter = getattr(self._football(context), "get_red_cards", None)
        if getter is None:
            return (0, 0)
        key = f"reds:{context}:{m.id}"
        cached = self._odds_cache.get(key)
        if cached is not None:
            return cached
        try:
            by_team = getter(m.id) or {}
        except Exception:  # noqa: BLE001 — eventos instáveis não derrubam o feed
            by_team = {}
        res = (int(by_team.get(m.home_team.id, 0)), int(by_team.get(m.away_team.id, 0)))
        self._odds_cache.set(key, res, 60)
        return res

    def _live_out(self, m: Match, market: str, selection: str, line, prob: float,
                  odd, edge, sample: int, context: str,
                  red_home: int = 0, red_away: int = 0,
                  mom_home: float = 1.0, mom_away: float = 1.0) -> RecommendationOut:
        """Pick ao vivo → RecommendationOut (placar/minuto/expulsão/pressão no
        motivo). Odd/edge são opcionais."""
        conf_label = self._prob_confidence(prob, edge)
        sel = self._opp_selection(market, selection, line,
                                  m.home_team.name, m.away_team.name)
        line_txt = f" {line:g}" if line is not None else ""
        minute = m.minute if m.minute is not None else 0
        red_txt = ""
        if red_home or red_away:
            red_txt = f" · {11 - red_home}x{11 - red_away} em campo"
        mom_txt = ""
        if mom_home >= 1.08:
            mom_txt = f" · {m.home_team.name} pressionando"
        elif mom_away >= 1.08:
            mom_txt = f" · {m.away_team.name} pressionando"
        odd_txt = ""
        if odd is not None:
            odd_txt = f" Odd {odd:.2f}." + (f" Valor {edge*100:+.1f}%." if edge is not None else "")
        return RecommendationOut(
            id=0, match=f"{m.home_team.name} x {m.away_team.name}",
            match_id=m.id, league=m.league_name or None, market=market,
            selection=sel, line=line, odd=odd,
            fair_odd=round(1 / prob, 2) if prob > 0 else None,
            model_prob=round(prob, 4), implied_prob=round(1 / odd, 4) if odd else None,
            edge=edge, confidence=conf_label,
            status="live", reason=(
                f"AO VIVO {minute}' · {m.home_goals}-{m.away_goals}{red_txt}{mom_txt} · "
                f"estimativa {prob*100:.0f}% para {sel}{line_txt}.{odd_txt}"
            ),
            bookmaker=None, created_at="", stage=m.stage, group=m.group,
            kickoff=m.utc_kickoff.isoformat() if m.utc_kickoff else None,
            context=context,
        )

    # Posições que entram no pool de props. Inclui ZAGUEIRO por causa de desarmes
    # e cartões (defensor é o maior desarmador/faltoso). Só goleiro fica de fora.
    # Props de finalização/gol filtram por projeção, então zagueiro não polui.
    _PROP_POSITIONS = ("Attacker", "Midfielder", "Defender")
    # Teto de jogadores no pool por time. Com a busca em LOTE (1-2 requests) o
    # enriquecimento é grátis, então o teto é só tamanho do pool: 30 cobre todos
    # os jogadores de linha. Cada mercado pega o top N por sua stat.
    _SQUAD_ENRICH_CAP = 30

    def team_player_pool(self, team_id: int, context: str = "general",
                         season: Optional[int] = None,
                         league_id: Optional[int] = None) -> list[PlayerSchema]:
        """Elenco do time com taxa de chute/gol da TEMPORADA (clube+seleção).
        `season` = temporada atual da liga do time (Brasileirão=2026, Europa=
        2025); None cai na season do contexto. `league_id` conta SÓ aquela
        competição (essencial pro cartão: amarelo de Copa não conta pra
        suspensão do Brasileirão). Cacheado por time+season+liga."""
        season = season or config.CURRENT_SEASON
        # ":n6" = pool por liga (amarelos só da competição p/ pendurado correto).
        key = f"{_CACHE_V}:squadpool:n6:{context}:{team_id}:{season}:{league_id or 'all'}"
        cached = self._disk.get(key)
        if cached is not None:
            return [PlayerSchema.model_validate(d) for d in cached]

        # Busca em LOTE (1-2 requests/time) em vez do N+1 antigo (get_squad +
        # get_player_season por jogador, ~16 requests). /players?team&season já
        # traz o elenco COM stats da temporada.
        bulk = getattr(self._football(context), "get_team_player_stats", None)
        pool: list[PlayerSchema] = []
        if bulk is not None:
            try:
                squad = bulk(team_id, season, league_id=league_id) or []
            except Exception:  # noqa: BLE001
                logger.warning("props: falha buscando elenco do time %s", team_id)
                squad = []
            # Só posições ofensivas com jogos na temporada (dado real p/ props).
            relevant = [s for s in squad
                        if s.position in self._PROP_POSITIONS and s.appearances > 0]
            # Atacantes primeiro (o teto não pode cortar os finalizadores).
            relevant.sort(key=lambda s: 0 if s.position == "Attacker" else 1)
            for stats in relevant[: self._SQUAD_ENRICH_CAP]:
                stats.team_id = team_id
                pool.append(conv.player_to_schema(stats))

        # Fallback: time que já jogou no torneio (squad indisponível/sem stats).
        if not pool:
            pool = [p for p in self.competition_players(context) if p.team_id == team_id]

        if pool:
            self._disk.set(key, [p.model_dump(mode="json") for p in pool],
                           config.CATALOG_CACHE_TTL)
        return pool

    def _starters(self, match_id: int, context: str) -> dict[int, set[int]]:
        """Titulares por team_id quando a escalação está disponível (~1h antes
        do jogo). {} se ainda não saiu. Cacheado curto."""
        key = f"lineups:{context}:{match_id}"
        cached = self._odds_cache.get(key)
        if cached is not None:
            return cached
        getter = getattr(self._football(context), "get_lineups", None)
        out: dict[int, set[int]] = {}
        if getter is not None:
            try:
                for lu in (getter(match_id) or []):
                    ids = {p.player_id for p in (lu.starters or []) if p.player_id}
                    if ids:
                        out[lu.team_id] = ids
            except Exception:  # noqa: BLE001
                out = {}
        self._odds_cache.set(key, out, 300)
        return out

    def match_props(self, match_id: int, context: str = "general"):
        """Player props recomendadas pra ESTE jogo (projeção × adversário).
        Quando a escalação já saiu, recomenda SÓ titulares."""
        from src.providers.base import TeamForm
        from src.recommendation.player_props import generate_player_props

        m = self.match_domain(match_id, context=context)
        if m is None:
            return []
        hf = self.team_form(m.home_team.id, context=context, league_id=m.league_id) or TeamForm(team_id=m.home_team.id)
        af = self.team_form(m.away_team.id, context=context, league_id=m.league_id) or TeamForm(team_id=m.away_team.id)

        starters = self._starters(match_id, context)
        season = self._league_season(m.league_id, context)
        # Ligas BR: conta stats SÓ da competição (amarelo de Copa não conta pro
        # pendurado do Brasileirão). Fora do Brasil mantém cross-comp (evita
        # regredir copas com poucos jogos).
        is_br = m.league_id in config.BRAZIL_LEAGUE_IDS
        pool_league = m.league_id if is_br else None

        def pool(team_id: int):
            players = self.team_player_pool(team_id, context, season=season,
                                            league_id=pool_league)
            xi = starters.get(team_id)
            if xi:                                    # escalação saiu → só titulares
                return [p for p in players if int(p.id) in xi]
            # Sem escalação: usa o elenco já enriquecido (atacantes primeiro,
            # cortado no _SQUAD_ENRICH_CAP) e ordena pelos que MAIS JOGARAM a
            # temporada na liga — proxy de titularidade que vale pra clube.
            return sorted(players, key=lambda p: p.appearances or 0, reverse=True)

        # Ligas BR: janela estratégica do cartão (jogo fácil agora → difícil
        # depois) por time, pra diferenciar "risco de cartão" de "gancho".
        home_ctx = away_ctx = None
        if is_br:
            home_ctx, away_ctx = self._card_contexts(m, season, context)

        picks = generate_player_props(
            match=m, home_form=hf, away_form=af,
            home_players=pool(m.home_team.id),
            away_players=pool(m.away_team.id),
            include_cards=is_br,     # cartão só nas ligas BR (regra de suspensão)
            home_card_ctx=home_ctx, away_card_ctx=away_ctx,
        )
        return [front_mappers.prop_to_out(pk, m) for pk in picks]

    # Margem de posições na tabela pra considerar "jogo de agora mais fácil que o
    # próximo": adversário atual STRATEGIC_RANK_MARGIN+ posições ABAIXO do próximo.
    _STRATEGIC_RANK_MARGIN = 4

    def _standings_rank(self, league_id: Optional[int], season: int,
                        context: str = "general") -> dict[int, int]:
        """{team_id: posição na tabela} da liga. Cache longo. {} se indisponível."""
        if not league_id:
            return {}
        key = f"{_CACHE_V}:standrank:r2:{context}:{league_id}:{season}"
        cached = self._disk.get(key)
        if cached is not None:
            return {int(k): int(v) for k, v in cached.items()}
        ranks: dict[int, int] = {}
        getter = getattr(self._football(context), "get_standings", None)
        if getter is not None:
            try:
                for s in getter(league_id, season) or []:
                    tid = getattr(s.team, "id", None)
                    if tid and s.rank:
                        ranks[int(tid)] = int(s.rank)
            except Exception as exc:  # noqa: BLE001
                logger.warning("standings rank falhou (liga %s): %s", league_id, exc)
        self._disk.set(key, ranks, config.CATALOG_CACHE_TTL)
        return ranks

    def _season_matches_cached(self, league_id: Optional[int], season: int,
                               context: str = "general") -> list[Match]:
        """Todos os jogos da temporada da liga (1 chamada, cache 6h). Base pra
        achar o PRÓXIMO adversário de cada time sem chamada por time."""
        if not league_id:
            return []
        key = f"{_CACHE_V}:seasonmatches:{context}:{league_id}:{season}"
        cached = self._disk.get(key)
        if cached is not None:
            return [serde.match_from_dict(d) for d in cached]
        getter = getattr(self._football(context), "get_season_matches", None)
        matches: list[Match] = []
        if getter is not None:
            try:
                matches = getter(league_id, season) or []
            except Exception:  # noqa: BLE001
                logger.warning("season matches falhou (liga %s)", league_id)
        self._disk.set(key, [serde.match_to_dict(mm) for mm in matches],
                       config.STATS_CACHE_TTL)
        return matches

    @staticmethod
    def _next_opponent(team_id: int, current: Match, season_matches: list[Match]):
        """(opp_id, opp_name) do PRÓXIMO jogo do time depois do atual, ou
        (None, None). Ignora finalizados e o próprio jogo atual."""
        cur_ko = current.utc_kickoff
        cands = [
            mm for mm in season_matches
            if mm.id != current.id and mm.status != "finished"
            and team_id in (mm.home_team.id, mm.away_team.id)
            and not (cur_ko is not None and mm.utc_kickoff is not None
                     and mm.utc_kickoff <= cur_ko)
        ]
        if not cands:
            return None, None
        cands.sort(key=lambda x: (x.utc_kickoff is None, x.utc_kickoff))
        nxt = cands[0]
        return ((nxt.away_team.id, nxt.away_team.name) if nxt.home_team.id == team_id
                else (nxt.home_team.id, nxt.home_team.name))

    def _card_contexts(self, m: Match, season: int, context: str):
        """Janela estratégica do cartão pros dois times: {"strategic","next_opp"}.
        strategic = adversário de AGORA está bem abaixo (na tabela) do PRÓXIMO
        adversário do time — jogo fácil agora, difícil depois."""
        ranks = self._standings_rank(m.league_id, season, context)
        smatches = self._season_matches_cached(m.league_id, season, context)

        def ctx(team_id: int, opp_now_id: int) -> dict:
            nxt_id, nxt_name = self._next_opponent(team_id, m, smatches)
            if not nxt_id:
                return {"strategic": False, "next_opp": None}
            r_now, r_next = ranks.get(opp_now_id), ranks.get(nxt_id)
            strategic = bool(r_now and r_next
                             and (r_now - r_next) >= self._STRATEGIC_RANK_MARGIN)
            return {"strategic": strategic, "next_opp": nxt_name}

        return ctx(m.home_team.id, m.away_team.id), ctx(m.away_team.id, m.home_team.id)

    def props(self, *, context: str = "general", limit: int = 40,
              max_matches: int = 6, league_id: Optional[int] = None):
        """Feed GLOBAL de player props dos próximos jogos (artilheiro, chutes no
        gol). Agrega match_props dos jogos próximos; resultado cacheado em disco
        (o caro é o elenco/temporada, já cacheado por time/jogador). `league_id`
        filtra por liga (feed caro → filtro no servidor, não só no cliente)."""
        # ":n9" = amarelos só da liga (pendurado correto) + gancho com piso menor.
        key = f"{_CACHE_V}:propsfeed:n9:{context}:{league_id or 'all'}:{limit}:{max_matches}"
        cached = self._disk.get(key)
        if cached is not None:
            return [RecommendationOut.model_validate(d) for d in cached]

        # Props são pré-jogo → só partidas que ainda não começaram.
        matches = self._upcoming_matches(context, only_future=True)
        if league_id:
            matches = [m for m in matches if m.league_id == league_id]
        matches = matches[:max_matches]
        out: list[RecommendationOut] = []
        for m in matches:
            try:
                out.extend(self.match_props(m.id, context=context))
            except Exception:  # noqa: BLE001 — um jogo ruim não derruba o feed
                logger.warning("props feed: falha no jogo %s", m.id)
        out.sort(key=lambda r: (r.model_prob or 0), reverse=True)
        out = out[:limit]
        self._disk.set(key, [r.model_dump(mode="json") for r in out],
                       config.MATCHES_CACHE_TTL)
        return out

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
        # Nome amigável + GARANTE toda liga configurada, mesmo se a chamada de
        # catálogo (1/liga) falhou no cold start (senão a liga sumia do filtro
        # por 24h — foi o "Série A brasileira faltando"). Ordena por config.
        by_id = {d["id"]: d for d in catalog}
        normalized: list[dict] = []
        for lid in config.DEFAULT_LEAGUE_IDS:
            disp = _LEAGUE_DISPLAY.get(lid)
            d = by_id.get(lid)
            if d is None and disp is None:
                continue
            if d is None:                       # api não devolveu → fallback
                d = LeagueSchema(id=lid, name=disp[0], country=disp[1]).model_dump(mode="json")
            elif disp:                          # rótulo claro (ex.: Brasileirão)
                d = {**d, "name": disp[0], "country": disp[1]}
            normalized.append(d)
        catalog = normalized
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
        "key_passes": lambda p: p.key_passes,
        "dribbles": lambda p: p.dribbles,
        "tackles": lambda p: p.tackles,
        "interceptions": lambda p: p.interceptions,
        "duels_won": lambda p: p.duels_won,
        "fouls_drawn": lambda p: p.fouls_drawn,
        "fouls_committed": lambda p: p.fouls_committed,
        "rating": lambda p: p.rating or 0,
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

    def player_index_ranking(self, *, context: str = "general",
                             index: str = "iip", limit: int = 20) -> list[PlayerSchema]:
        """Ranking por índice composto (ipo|icj|id|iip). Calcula sobre o elenco
        da competição (cacheado) e devolve os jogadores com os índices anexados."""
        from src.metrics.player_index import compute_indices
        players = self.competition_players(context)
        idxs = compute_indices(players)
        for pi in idxs:
            pi.player.ipo = pi.ipo
            pi.player.icj = pi.icj
            pi.player.idef = pi.id
            pi.player.iip = pi.iip
        key = {"ipo": lambda x: x.ipo, "icj": lambda x: x.icj,
               "id": lambda x: x.id, "iip": lambda x: x.iip}.get(index, lambda x: x.iip)
        idxs.sort(key=key, reverse=True)
        return [pi.player for pi in idxs[:limit]]

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
