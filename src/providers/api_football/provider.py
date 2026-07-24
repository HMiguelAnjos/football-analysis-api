"""
ApiFootballProvider — normaliza payloads da api-football pros modelos internos.

Implementa FootballDataProvider. Toda a tradução payload-cru → domínio mora
AQUI; o resto do sistema nunca vê o formato da api-football. Falha graciosa:
métodos devolvem [] / None quando a fonte falha, e o registry pode cair pro
provider de fixtures.

As funções de parsing são estáticas e puras (testáveis com payloads de exemplo
sem rede).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from src import config
from src.providers.api_football.client import ApiFootballClient
from src.providers.base import (
    Group,
    Injury,
    League,
    Lineup,
    LineupPlayer,
    Match,
    MatchStatistics,
    PlayerSeasonStats,
    Standing,
    Team,
    TeamForm,
)

logger = logging.getLogger(__name__)


def _status_of(short: str) -> str:
    """Mapeia o status code da api-football pros nossos 3 estados."""
    if short in ("1H", "2H", "HT", "ET", "BT", "P", "LIVE", "INT"):
        return "live"
    if short in ("FT", "AET", "PEN"):
        return "finished"
    return "scheduled"


def _parse_dt(iso: Optional[str]) -> Optional[datetime]:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _parse_round(round_str: str) -> tuple[Optional[str], Optional[str]]:
    """`league.round` da api-football → (stage, group).

    Ex.: "Group A - 2" → ("group", "A"); "Round of 16" → ("round_of_16", None);
    "Quarter-finals" → ("quarter", None); "Final" → ("final", None).
    """
    r = (round_str or "").strip().lower()
    if not r:
        return None, None
    if r.startswith("group"):
        # "group a - 2" → grupo "A"
        parts = round_str.split()
        grp = parts[1].upper() if len(parts) > 1 else None
        return "group", grp
    if "round of 16" in r or "1/8" in r:
        return "round_of_16", None
    if "quarter" in r:
        return "quarter", None
    if "semi" in r:
        return "semi", None
    if "3rd place" in r or "third place" in r:
        return "third_place", None
    if "final" in r:
        return "final", None
    return "knockout", None


class ApiFootballProvider:
    name = "api_football"

    def __init__(self, client: Optional[ApiFootballClient] = None) -> None:
        if client is None:
            if not config.API_FOOTBALL_KEY:
                raise ValueError("ApiFootballProvider precisa de API_FOOTBALL_KEY")
            client = ApiFootballClient(config.API_FOOTBALL_KEY)
        self._client = client
        # Allow-list dos jogos do dia = ligas acompanhadas (DEFAULT_LEAGUE_IDS).
        # Evita trazer o mundo inteiro de /fixtures?date=.
        self._leagues = list(dict.fromkeys(config.DEFAULT_LEAGUE_IDS))
        self._season = config.CURRENT_SEASON

    # --- parsing (estático, puro) ------------------------------------------

    @staticmethod
    def parse_match(item: dict[str, Any]) -> Optional[Match]:
        fixture = item.get("fixture", {})
        league = item.get("league", {})
        teams = item.get("teams", {})
        goals = item.get("goals", {})
        score = item.get("score", {}) or {}
        home = teams.get("home", {})
        away = teams.get("away", {})
        if not fixture.get("id") or not home.get("id") or not away.get("id"):
            return None
        stage, group = _parse_round(league.get("round", ""))
        venue = fixture.get("venue") or {}
        et = score.get("extratime") or {}
        pen = score.get("penalty") or {}
        winner = "home" if home.get("winner") else ("away" if away.get("winner") else None)
        return Match(
            id=int(fixture["id"]),
            league_id=int(league.get("id", 0)),
            league_name=league.get("name", ""),
            season=int(league.get("season", 0) or 0),
            utc_kickoff=_parse_dt(fixture.get("date")),
            status=_status_of((fixture.get("status") or {}).get("short", "NS")),
            home_team=Team(id=int(home["id"]), name=home.get("name", ""),
                           logo=home.get("logo", "")),
            away_team=Team(id=int(away["id"]), name=away.get("name", ""),
                           logo=away.get("logo", "")),
            home_goals=goals.get("home"),
            away_goals=goals.get("away"),
            minute=(fixture.get("status") or {}).get("elapsed"),
            referee=fixture.get("referee") or "",
            venue=venue.get("name", "") or "",
            stage=stage, group=group, city=venue.get("city", "") or "",
            extra_time_home=et.get("home"), extra_time_away=et.get("away"),
            penalty_home=pen.get("home"), penalty_away=pen.get("away"),
            winner=winner,
        )

    @staticmethod
    def parse_team_form(team_id: int, stats: dict[str, Any]) -> TeamForm:
        """Normaliza /teams/statistics da api-football em TeamForm."""
        goals = stats.get("goals", {})
        gf = goals.get("for", {}).get("average", {})
        ga = goals.get("against", {}).get("average", {})
        fixtures = stats.get("fixtures", {})
        played = (fixtures.get("played", {}) or {}).get("total", 0) or 0

        def _f(v: Any) -> Optional[float]:
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        def _i(block: dict) -> Optional[int]:
            try:
                return int((block or {}).get("total"))
            except (TypeError, ValueError):
                return None

        # `form` da api-football é cronológico (mais antigo → recente). O front
        # quer os últimos ~5 com o mais recente à ESQUERDA.
        raw_form = stats.get("form") or ""
        recent_form = raw_form[-5:][::-1] if raw_form else None

        return TeamForm(
            team_id=team_id,
            matches_played=int(played),
            goals_for=_f(gf.get("total")) or 0.0,
            goals_against=_f(ga.get("total")) or 0.0,
            home_goals_for=_f(gf.get("home")),
            home_goals_against=_f(ga.get("home")),
            away_goals_for=_f(gf.get("away")),
            away_goals_against=_f(ga.get("away")),
            wins=_i(fixtures.get("wins")),
            draws=_i(fixtures.get("draws")),
            losses=_i(fixtures.get("loses")),
            recent_form=recent_form,
        )

    @staticmethod
    def parse_standings(payload: list[dict[str, Any]]) -> list[Standing]:
        out: list[Standing] = []
        if not payload:
            return out
        league = payload[0].get("league", {})
        groups = league.get("standings", []) or []
        for group in groups:
            for row in group:
                team = row.get("team", {})
                all_ = row.get("all", {})
                goals = all_.get("goals", {})
                out.append(Standing(
                    rank=int(row.get("rank", 0)),
                    team=Team(id=int(team.get("id", 0)), name=team.get("name", ""),
                              logo=team.get("logo", "")),
                    points=int(row.get("points", 0)),
                    played=int(all_.get("played", 0)),
                    win=int(all_.get("win", 0)),
                    draw=int(all_.get("draw", 0)),
                    lose=int(all_.get("lose", 0)),
                    goals_for=int(goals.get("for", 0)),
                    goals_against=int(goals.get("against", 0)),
                    goal_diff=int(row.get("goalsDiff", 0)),
                ))
        return out

    # --- FootballDataProvider ----------------------------------------------

    def get_matches_by_date(self, date: str) -> list[Match]:
        items = self._client.response("fixtures", {"date": date})
        matches = [self.parse_match(it) for it in items]
        result = [m for m in matches if m is not None]
        if self._leagues:
            allowed = set(self._leagues)
            result = [m for m in result if m.league_id in allowed]
        return result

    def get_season_matches(self, league_id: int, season: int) -> list[Match]:
        """Todos os jogos de uma competição/temporada (torneio inteiro)."""
        items = self._client.response("fixtures", {"league": league_id, "season": season})
        return [m for m in (self.parse_match(it) for it in items) if m is not None]

    def get_match(self, match_id: int) -> Optional[Match]:
        items = self._client.response("fixtures", {"id": match_id})
        return self.parse_match(items[0]) if items else None

    def get_match_statistics(self, match_id: int) -> Optional[MatchStatistics]:
        items = self._client.response("fixtures/statistics", {"fixture": match_id})
        if not items:
            return None

        def _to_dict(team_block: dict) -> dict[str, float]:
            d: dict[str, float] = {}
            for s in team_block.get("statistics", []):
                key = (s.get("type") or "").lower().replace(" ", "_")
                val = s.get("value")
                if isinstance(val, str) and val.endswith("%"):
                    val = val.rstrip("%")
                try:
                    d[key] = float(val)
                except (TypeError, ValueError):
                    continue
            return d

        home = _to_dict(items[0]) if len(items) > 0 else {}
        away = _to_dict(items[1]) if len(items) > 1 else {}
        return MatchStatistics(match_id=match_id, home=home, away=away)

    def get_team(self, team_id: int) -> Optional[Team]:
        items = self._client.response("teams", {"id": team_id})
        if not items:
            return None
        t = items[0].get("team", {})
        return Team(id=int(t.get("id", team_id)), name=t.get("name", ""),
                    logo=t.get("logo", ""))

    def get_teams(self, league_id: Optional[int] = None,
                  search: Optional[str] = None) -> list[Team]:
        """Times de uma liga (ou de todas as ligas acompanhadas)."""
        league_ids = [league_id] if league_id else self._leagues
        out: list[Team] = []
        seen: set[int] = set()
        for lid in league_ids:
            items = self._client.response("teams", {
                "league": lid, "season": self._season,
            })
            for it in items:
                t = it.get("team", {})
                tid = int(t.get("id", 0) or 0)
                if not tid or tid in seen:
                    continue
                seen.add(tid)
                out.append(Team(id=tid, name=t.get("name", ""), logo=t.get("logo", "")))
        if search:
            s = search.strip().lower()
            out = [t for t in out if s in t.name.lower()]
        return out

    def get_players(self, team_id: Optional[int] = None,
                    search: Optional[str] = None) -> list[PlayerSeasonStats]:
        """Elenco com stats da temporada (por time) ou busca por nome.

        Sem team_id mas COM search (≥3 chars), usa /players/profiles?search=
        (perfis por nome, sem stats da temporada). Sem nenhum dos dois, [].
        """
        if team_id is None:
            if not search or len(search.strip()) < 3:
                return []
            profiles = self._client.response("players/profiles", {
                "search": search.strip(),
            })
            out: list[PlayerSeasonStats] = []
            for block in profiles:
                p = block.get("player", {})
                pid = int(p.get("id", 0) or 0)
                if not pid:
                    continue
                out.append(PlayerSeasonStats(
                    player_id=pid,
                    name=p.get("name") or f"{p.get('firstname','')} {p.get('lastname','')}".strip(),
                    team_id=0, position=p.get("position", "") or "",
                ))
            return out
        items = self._client.response("players", {
            "team": team_id, "season": self._season,
        })
        out = [self._parse_player_block(b, default_team=team_id) for b in items]
        out = [p for p in out if p is not None]
        if search:
            s = search.strip().lower()
            out = [p for p in out if s in p.name.lower()]
        return out

    @staticmethod
    def _parse_player_block(block: dict[str, Any],
                            default_team: int = 0) -> Optional[PlayerSeasonStats]:
        """Bloco {player, statistics:[...]} da api-football → PlayerSeasonStats.
        Agrega as entradas de statistics (jogador pode ter mais de uma)."""
        player = block.get("player", {})
        pid = int(player.get("id", 0) or 0)
        if not pid:
            return None
        stats_list = block.get("statistics", []) or [{}]

        def _i(v) -> int:
            try:
                return int(v)
            except (TypeError, ValueError):
                return 0

        def _f(v) -> float:
            try:
                return float(v)
            except (TypeError, ValueError):
                return 0.0

        appearances = minutes = goals = assists = 0
        shots = sot = 0.0
        key_passes = passes = dribbles = dribbles_att = 0
        tackles = interceptions = duels = duels_won = 0
        fouls_drawn = fouls_committed = yellow = red = pen_scored = 0
        ratings: list[float] = []
        acc: list[float] = []
        position = ""
        team_id = default_team
        team_name = ""
        for st in stats_list:
            games = st.get("games", {}) or {}
            gl = st.get("goals", {}) or {}
            sh = st.get("shots", {}) or {}
            ps = st.get("passes", {}) or {}
            tk = st.get("tackles", {}) or {}
            du = st.get("duels", {}) or {}
            dr = st.get("dribbles", {}) or {}
            fl = st.get("fouls", {}) or {}
            cd = st.get("cards", {}) or {}
            pen = st.get("penalty", {}) or {}
            appearances += _i(games.get("appearences"))
            minutes += _i(games.get("minutes"))
            goals += _i(gl.get("total"))
            assists += _i(gl.get("assists"))
            shots += _f(sh.get("total"))
            sot += _f(sh.get("on"))
            key_passes += _i(ps.get("key"))
            passes += _i(ps.get("total"))
            dribbles += _i(dr.get("success"))
            dribbles_att += _i(dr.get("attempts"))
            tackles += _i(tk.get("total"))
            interceptions += _i(tk.get("interceptions"))
            duels += _i(du.get("total"))
            duels_won += _i(du.get("won"))
            fouls_drawn += _i(fl.get("drawn"))
            fouls_committed += _i(fl.get("committed"))
            yellow += _i(cd.get("yellow"))
            red += _i(cd.get("red")) + _i(cd.get("yellowred"))
            pen_scored += _i(pen.get("scored"))
            r = _f(games.get("rating"))
            if r > 0:
                ratings.append(r)
            a = _f(ps.get("accuracy"))
            if a > 0:
                acc.append(a)
            position = position or (games.get("position", "") or "")
            team = st.get("team", {}) or {}
            if not team_id:
                team_id = int(team.get("id", 0) or 0)
            team_name = team_name or (team.get("name", "") or "")
        return PlayerSeasonStats(
            player_id=pid, name=player.get("name", ""), team_id=team_id,
            team_name=team_name, position=position, appearances=appearances,
            minutes=minutes, goals=goals, assists=assists, shots=shots,
            shots_on_target=sot, key_passes=key_passes, passes=passes,
            pass_accuracy=round(sum(acc) / len(acc), 1) if acc else None,
            dribbles=dribbles, dribbles_attempts=dribbles_att, tackles=tackles,
            interceptions=interceptions, duels=duels, duels_won=duels_won,
            fouls_drawn=fouls_drawn, fouls_committed=fouls_committed,
            yellow_cards=yellow, red_cards=red, penalty_scored=pen_scored,
            rating=round(sum(ratings) / len(ratings), 2) if ratings else None,
        )

    def get_top_scorers(self, league_id: int, season: int) -> list[PlayerSeasonStats]:
        """Top artilheiros (1 request, já ordenado pela api-football)."""
        items = self._client.response("players/topscorers", {
            "league": league_id, "season": season,
        })
        return [p for p in (self._parse_player_block(b) for b in items) if p]

    def get_top_assists(self, league_id: int, season: int) -> list[PlayerSeasonStats]:
        """Top assistências (1 request)."""
        items = self._client.response("players/topassists", {
            "league": league_id, "season": season,
        })
        return [p for p in (self._parse_player_block(b) for b in items) if p]

    def get_competition_players(self, league_id: int, season: int,
                                max_pages: int = 40) -> list[PlayerSeasonStats]:
        """Todos os jogadores de uma competição/temporada, com stats (paginado).
        Caro (1 request por página) — o data_service cacheia em disco por 24h."""
        out: list[PlayerSeasonStats] = []
        page = 1
        while page <= max_pages:
            data = self._client.get("players", {
                "league": league_id, "season": season, "page": page,
            })
            for block in (data.get("response") or []):
                p = self._parse_player_block(block)
                if p is not None:
                    out.append(p)
            paging = data.get("paging") or {}
            total = int(paging.get("total", 1) or 1)
            if page >= total:
                break
            page += 1
        return out

    def get_team_form(self, team_id: int, last_n: int = 10,
                      league_id: Optional[int] = None,
                      season: Optional[int] = None) -> Optional[TeamForm]:
        # api-football exige league+season pra stats. Default = 1ª liga geral;
        # o data_service passa league/season do contexto (ex.: Copa).
        league = league_id or (self._leagues[0] if self._leagues else 39)
        data = self._client.get("teams/statistics", {
            "team": team_id, "league": league, "season": season or self._season,
        })
        stats = data.get("response")
        if not isinstance(stats, dict) or not stats:
            return None
        return self.parse_team_form(team_id, stats)

    def get_recent_results(self, team_id: int, last_n: int = 15) -> list[Match]:
        """Últimos N jogos do time em TODAS as competições (1 chamada barata).
        Filtra finalizados — base dos ratings de força ajustados por adversário."""
        items = self._client.response("fixtures", {"team": team_id, "last": last_n})
        out: list[Match] = []
        for it in items:
            m = self.parse_match(it)
            if m is not None and m.status == "finished" \
                    and m.home_goals is not None and m.away_goals is not None:
                out.append(m)
        return out

    def get_live_matches(self) -> list[Match]:
        """Todos os jogos ao vivo agora (1 chamada: fixtures?live=all). Base do
        refresh de odds por evento — barato, cobre o mundo inteiro de uma vez."""
        items = self._client.response("fixtures", {"live": "all"})
        out: list[Match] = []
        for it in items:
            m = self.parse_match(it)
            if m is not None and m.status == "live":
                out.append(m)
        return out

    def get_red_cards(self, fixture_id: int) -> dict[int, int]:
        """Expulsões por time_id no jogo (eventos do tipo Card / vermelho).
        1 chamada (fixtures/events). {} se a fonte não fornecer."""
        items = self._client.response("fixtures/events", {"fixture": fixture_id})
        out: dict[int, int] = {}
        for ev in items:
            if (ev.get("type") or "").lower() != "card":
                continue
            detail = (ev.get("detail") or "").lower()
            is_red = "red" in detail or "second yellow" in detail
            if not is_red:
                continue
            tid = int((ev.get("team", {}) or {}).get("id", 0) or 0)
            if tid:
                out[tid] = out.get(tid, 0) + 1
        return out

    def get_match_cards(self, fixture_id: int) -> dict[int, int]:
        """Total de cartões (amarelo + vermelho) por team_id no jogo, via eventos
        (1 chamada: fixtures/events). A estatística agregada não traz amarelos —
        os eventos sim. {} se a fonte não fornecer."""
        items = self._client.response("fixtures/events", {"fixture": fixture_id})
        out: dict[int, int] = {}
        for ev in items:
            if (ev.get("type") or "").lower() != "card":
                continue
            tid = int((ev.get("team", {}) or {}).get("id", 0) or 0)
            if tid:
                out[tid] = out.get(tid, 0) + 1
        return out

    def get_match_card_events(self, fixture_id: int) -> dict:
        """Cartões por time e por TEMPO (o evento tem minuto) + ids de quem levou.
        1 chamada (fixtures/events). {"teams": {tid: {total,1t,2t}}, "players":
        set(player_id que levou cartão)}."""
        items = self._client.response("fixtures/events", {"fixture": fixture_id})
        teams: dict[int, dict] = {}
        players: set[int] = set()
        for ev in items:
            if (ev.get("type") or "").lower() != "card":
                continue
            tid = int((ev.get("team", {}) or {}).get("id", 0) or 0)
            minute = int((ev.get("time") or {}).get("elapsed") or 0)
            half = "1t" if minute <= 45 else "2t"
            if tid:
                d = teams.setdefault(tid, {"total": 0, "1t": 0, "2t": 0})
                d["total"] += 1
                d[half] += 1
            pid = int((ev.get("player") or {}).get("id", 0) or 0)
            if pid:
                players.add(pid)
        return {"teams": teams, "players": players}

    def get_live_player_shots(self, fixture_id: int) -> list[dict]:
        """Chutes de cada jogador NO JOGO atual (1 chamada: fixtures/players).
        Base do especialista em chutes a gol ao vivo. {} se a fonte não fornece."""
        items = self._client.response("fixtures/players", {"fixture": fixture_id})
        out: list[dict] = []
        for blk in items:
            tid = int((blk.get("team", {}) or {}).get("id", 0) or 0)
            for p in blk.get("players", []) or []:
                player = p.get("player", {}) or {}
                pid = int(player.get("id", 0) or 0)
                if not pid:
                    continue
                st = (p.get("statistics") or [{}])[0] or {}
                sh = st.get("shots") or {}
                g = st.get("games") or {}
                go = st.get("goals") or {}
                tk = st.get("tackles") or {}
                cd = st.get("cards") or {}

                def _i(v) -> int:
                    try:
                        return int(v)
                    except (TypeError, ValueError):
                        return 0

                out.append({
                    "player_id": pid, "name": player.get("name", "") or "",
                    "team_id": tid, "minutes": _i(g.get("minutes")),
                    "shots_total": _i(sh.get("total")), "shots_on": _i(sh.get("on")),
                    "goals": _i(go.get("total")), "assists": _i(go.get("assists")),
                    "tackles": _i(tk.get("total")),
                    "yellow": _i(cd.get("yellow")), "red": _i(cd.get("red")),
                })
        return out

    def get_squad(self, team_id: int) -> list[PlayerSeasonStats]:
        """Elenco atual do time (só id/nome/posição, SEM stats — 1 chamada).
        Base pra montar props ANTES do time jogar no torneio."""
        items = self._client.response("players/squads", {"team": team_id})
        out: list[PlayerSeasonStats] = []
        if items:
            for p in items[0].get("players", []) or []:
                pid = int(p.get("id", 0) or 0)
                if not pid:
                    continue
                num = p.get("number")
                try:
                    num = int(num) if num is not None else None
                except (TypeError, ValueError):
                    num = None
                out.append(PlayerSeasonStats(
                    player_id=pid, name=p.get("name", "") or "",
                    team_id=team_id, position=p.get("position", "") or "",
                    number=num,
                ))
        return out

    def get_team_player_stats(self, team_id: int, season: int,
                              league_id: Optional[int] = None,
                              max_pages: int = 3) -> list[PlayerSeasonStats]:
        """Elenco COM stats da temporada em LOTE (/players?team&season, paginado).
        Substitui o N+1 do props (get_squad + get_player_season por jogador):
        ~1-2 requests por time em vez de ~16. O data_service cacheia 24h.

        `league_id` → conta SÓ aquela competição (crucial pra cartão: amarelo de
        Copa/Libertadores não conta pra suspensão do Brasileirão)."""
        out: list[PlayerSeasonStats] = []
        page = 1
        while page <= max_pages:
            params = {"team": team_id, "season": season, "page": page}
            if league_id:
                params["league"] = league_id
            data = self._client.get("players", params)
            for block in (data.get("response") or []):
                p = self._parse_player_block(block, default_team=team_id)
                if p is not None:
                    out.append(p)
            paging = data.get("paging") or {}
            total = int(paging.get("total", 1) or 1)
            if page >= total:
                break
            page += 1
        return out

    def get_player_season(self, player_id: int, season: int,
                          national_team_id: Optional[int] = None) -> Optional[PlayerSeasonStats]:
        """Stats de UM jogador na temporada, AGREGADAS por todas as competições
        (clube + seleção). Se `national_team_id` vier, soma os jogos PELA SELEÇÃO
        (blocos cujo time é a seleção) — proxy de titular pra props pré-jogo."""
        items = self._client.response("players", {"id": player_id, "season": season})
        if not items:
            return None
        stats = self._parse_player_block(items[0])
        if stats and national_team_id:
            nt = 0
            for s in items[0].get("statistics", []) or []:
                tid = int((s.get("team", {}) or {}).get("id", 0) or 0)
                if tid == national_team_id:
                    try:
                        nt += int((s.get("games", {}) or {}).get("appearences") or 0)
                    except (TypeError, ValueError):
                        pass
            stats.nt_appearances = nt
        return stats

    def get_player(self, player_id: int) -> Optional[PlayerSeasonStats]:
        items = self._client.response("players", {
            "id": player_id, "season": self._season,
        })
        if not items:
            return None
        block = items[0]
        player = block.get("player", {})
        stats_list = block.get("statistics", []) or [{}]
        st = stats_list[0]
        games = st.get("games", {})
        goals = st.get("goals", {})
        shots = st.get("shots", {})

        def _i(v: Any) -> int:
            try:
                return int(v)
            except (TypeError, ValueError):
                return 0

        return PlayerSeasonStats(
            player_id=player_id,
            name=player.get("name", ""),
            team_id=int((st.get("team", {}) or {}).get("id", 0) or 0),
            position=games.get("position", "") or "",
            appearances=_i(games.get("appearences")),
            minutes=_i(games.get("minutes")),
            goals=_i(goals.get("total")),
            assists=_i(goals.get("assists")),
            shots=float(_i(shots.get("total"))),
            shots_on_target=float(_i(shots.get("on"))),
        )

    def get_current_season(self, league_id: int) -> Optional[int]:
        """Temporada ATUAL da liga (o campo season varia por liga: Europa usa o
        ano de início — 2025 = 2025-26; Brasileirão usa o ano civil — 2026).
        Lê o flag `current: true` de /leagues. None se indisponível."""
        items = self._client.response("leagues", {"id": league_id})
        if not items:
            return None
        for s in items[0].get("seasons", []) or []:
            if s.get("current"):
                try:
                    return int(s.get("year"))
                except (TypeError, ValueError):
                    return None
        return None

    def get_leagues(self) -> list[League]:
        out: list[League] = []
        for lid in self._leagues:
            items = self._client.response("leagues", {"id": lid})
            if not items:
                continue
            lg = items[0].get("league", {})
            country = (items[0].get("country", {}) or {}).get("name", "")
            out.append(League(id=int(lg.get("id", lid)), name=lg.get("name", ""),
                              country=country, season=self._season,
                              logo=lg.get("logo", "")))
        return out

    def get_standings(self, league_id: int, season: int) -> list[Standing]:
        payload = self._client.response("standings", {
            "league": league_id, "season": season,
        })
        return self.parse_standings(payload)

    def get_groups(self, league_id: int, season: int) -> list[Group]:
        """Classificação agrupada por grupo (ex.: Copa do Mundo)."""
        payload = self._client.response("standings", {
            "league": league_id, "season": season,
        })
        if not payload:
            return []
        groups_raw = (payload[0].get("league", {}) or {}).get("standings", []) or []
        out: list[Group] = []
        for grp in groups_raw:
            rows: list[Standing] = []
            name = ""
            for row in grp:
                team = row.get("team", {})
                all_ = row.get("all", {})
                goals = all_.get("goals", {})
                name = row.get("group", name) or name
                rows.append(Standing(
                    rank=int(row.get("rank", 0)),
                    team=Team(id=int(team.get("id", 0)), name=team.get("name", ""),
                              logo=team.get("logo", "")),
                    points=int(row.get("points", 0)),
                    played=int(all_.get("played", 0)),
                    win=int(all_.get("win", 0)), draw=int(all_.get("draw", 0)),
                    lose=int(all_.get("lose", 0)),
                    goals_for=int(goals.get("for", 0)),
                    goals_against=int(goals.get("against", 0)),
                    goal_diff=int(row.get("goalsDiff", 0)),
                    group=row.get("group"), form=row.get("form"),
                ))
            out.append(Group(name=name or "Grupo", standings=rows))
        return out

    def get_lineups(self, match_id: int) -> list[Lineup]:
        items = self._client.response("fixtures/lineups", {"fixture": match_id})
        out: list[Lineup] = []
        for block in items:
            team = block.get("team", {})
            tid = int(team.get("id", 0))

            def _players(key: str, starter: bool) -> list[LineupPlayer]:
                res = []
                for p in block.get(key, []) or []:
                    pl = p.get("player", {})
                    res.append(LineupPlayer(
                        player_id=int(pl.get("id", 0)),
                        name=pl.get("name", ""),
                        number=pl.get("number"),
                        position=pl.get("pos", "") or "",
                        is_starter=starter,
                    ))
                return res

            out.append(Lineup(
                match_id=match_id, team_id=tid,
                formation=block.get("formation", "") or "",
                starters=_players("startXI", True),
                substitutes=_players("substitutes", False),
            ))
        return out

    def get_injuries(self, team_id: int) -> list[Injury]:
        items = self._client.response("injuries", {
            "team": team_id, "season": self._season,
        })
        out: list[Injury] = []
        for it in items:
            pl = it.get("player", {})
            out.append(Injury(
                player_id=int(pl.get("id", 0)),
                name=pl.get("name", ""),
                team_id=team_id,
                reason=pl.get("reason", "") or "",
                type=pl.get("type", "") or "injury",
            ))
        return out
