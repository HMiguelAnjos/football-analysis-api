"""
OpenFootballProvider — dados da Copa do Mundo via openfootball/worldcup.json.

Fonte pública (domínio público), **grátis e sem chave**. JSON estático por
edição (2022, 2026, …), atualizado pela comunidade conforme o torneio acontece.
Não tem dados ao vivo minuto-a-minuto nem stats avançadas (shots/posse/xG) —
mas tem jogos, placares, gols, grupos e mata-mata, que é o suficiente pro
modo Copa (inclusive a forma das seleções, derivada dos resultados).

Implementa FootballDataProvider. Os métodos que a fonte não cobre devolvem
[]/None (degradação graciosa). Tudo cacheado por temporada em memória; o
data_service ainda cacheia por cima em disco.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import requests

from src import config
from src.providers.base import (
    Group,
    League,
    Match,
    Standing,
    Team,
    TeamForm,
)

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 15


def _sid(s: str) -> int:
    """ID estável e determinístico a partir de uma string (hash não-randômico)."""
    return int(hashlib.md5(s.encode("utf-8")).hexdigest()[:8], 16)


def _ko_stage(round_str: str) -> str:
    r = (round_str or "").lower()
    if "round of 32" in r:
        return "round_of_32"
    if "round of 16" in r:
        return "round_of_16"
    if "quarter" in r:
        return "quarter"
    if "semi" in r:
        return "semi"
    if "third" in r:
        return "third_place"
    if "final" in r:
        return "final"
    return "knockout"


def _split_ground(ground: str) -> tuple[str, str]:
    """'Al Bayt Stadium, Al Khor' → ('Al Bayt Stadium', 'Al Khor')."""
    if not ground:
        return "", ""
    parts = [p.strip() for p in ground.split(",")]
    if len(parts) >= 2:
        return parts[0], parts[-1]
    return parts[0], ""


class OpenFootballProvider:
    name = "openfootball"

    def __init__(self, base_url: Optional[str] = None) -> None:
        self._base = (base_url or config.OPENFOOTBALL_BASE_URL).rstrip("/")
        self._raw: dict[int, dict] = {}   # season → payload (memo)
        self._season = config.WORLD_CUP_SEASON

    # ── fetch + parsing ────────────────────────────────────────────────────

    def _payload(self, season: int) -> dict:
        if season in self._raw:
            return self._raw[season]
        url = f"{self._base}/{season}/worldcup.json"
        try:
            resp = requests.get(url, timeout=DEFAULT_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning("openfootball: fetch %s falhou (%s)", season, exc)
            data = {"name": f"World Cup {season}", "matches": []}
        self._raw[season] = data
        return data

    @staticmethod
    def _kickoff(date: Optional[str], time: Optional[str] = None) -> Optional[datetime]:
        """Combina date ('YYYY-MM-DD') + time ('13:00 UTC-6' / '19:00') num
        datetime UTC. Aplica o offset de fuso quando presente."""
        if not date:
            return None
        try:
            d = datetime.strptime(date, "%Y-%m-%d")
        except (ValueError, TypeError):
            return None
        hh = mm = off = 0
        for part in (time or "").split():
            if ":" in part:
                try:
                    hh, mm = (int(x) for x in part.split(":")[:2])
                except ValueError:
                    pass
            elif part.upper().startswith("UTC"):
                try:
                    off = int(part[3:])
                except ValueError:
                    off = 0
        # Horário local (no offset informado) → UTC = local − offset.
        local = d.replace(hour=hh, minute=mm, tzinfo=timezone.utc)
        return local - timedelta(hours=off)

    def _to_match(self, m: dict[str, Any], season: int, league_name: str) -> Optional[Match]:
        t1 = m.get("team1", "")
        t2 = m.get("team2", "")
        if not t1 or not t2:
            return None
        grp = m.get("group")
        if grp:
            stage, group = "group", str(grp).replace("Group", "").strip()
        else:
            stage, group = _ko_stage(m.get("round", "")), None

        score = m.get("score") or {}
        ft = score.get("ft") or []
        et = score.get("et") or []
        pen = score.get("p") or score.get("pen") or []
        hg = ft[0] if len(ft) >= 2 else None
        ag = ft[1] if len(ft) >= 2 else None
        finished = hg is not None

        winner = None
        if finished:
            if len(pen) == 2 and pen[0] != pen[1]:
                winner = "home" if pen[0] > pen[1] else "away"
            else:
                base = et if len(et) == 2 else ft
                if len(base) == 2 and base[0] != base[1]:
                    winner = "home" if base[0] > base[1] else "away"

        venue, city = _split_ground(m.get("ground", ""))
        date = m.get("date")
        return Match(
            id=_sid(f"{season}:{t1}:{t2}:{date}"),
            league_id=config.WORLD_CUP_LEAGUE_ID,
            league_name=league_name,
            season=int(season),
            utc_kickoff=self._kickoff(date, m.get("time")),
            status="finished" if finished else "scheduled",
            home_team=Team(id=_sid(t1), name=t1),
            away_team=Team(id=_sid(t2), name=t2),
            home_goals=hg, away_goals=ag,
            stage=stage, group=group, venue=venue, city=city,
            extra_time_home=et[0] if len(et) == 2 else None,
            extra_time_away=et[1] if len(et) == 2 else None,
            penalty_home=pen[0] if len(pen) == 2 else None,
            penalty_away=pen[1] if len(pen) == 2 else None,
            winner=winner,
        )

    def _matches(self, season: int) -> list[Match]:
        data = self._payload(season)
        name = data.get("name", f"World Cup {season}")
        out = [self._to_match(m, season, name) for m in data.get("matches", [])]
        return [m for m in out if m is not None]

    # ── FootballDataProvider ───────────────────────────────────────────────

    def get_season_matches(self, league_id: int, season: int) -> list[Match]:
        return self._matches(season)

    def get_matches_by_date(self, date: str) -> list[Match]:
        return [
            m for m in self._matches(self._season)
            if m.utc_kickoff and m.utc_kickoff.strftime("%Y-%m-%d") == date
        ]

    def get_match(self, match_id: int) -> Optional[Match]:
        return next((m for m in self._matches(self._season) if m.id == match_id), None)

    def get_groups(self, league_id: int, season: int) -> list[Group]:
        """Classificação computada a partir dos resultados da fase de grupos."""
        tally: dict[str, dict[int, dict]] = {}   # group → team_id → stats
        teams: dict[int, Team] = {}
        for m in self._matches(season):
            if m.stage != "group" or not m.group or m.home_goals is None:
                continue
            g = tally.setdefault(m.group, {})
            for t in (m.home_team, m.away_team):
                teams[t.id] = t
                g.setdefault(t.id, {"pts": 0, "p": 0, "w": 0, "d": 0, "l": 0,
                                    "gf": 0, "ga": 0, "form": []})
            h, a = g[m.home_team.id], g[m.away_team.id]
            hg, ag = m.home_goals, m.away_goals
            h["p"] += 1; a["p"] += 1
            h["gf"] += hg; h["ga"] += ag
            a["gf"] += ag; a["ga"] += hg
            if hg > ag:
                h["pts"] += 3; h["w"] += 1; a["l"] += 1
                h["form"].append("W"); a["form"].append("L")
            elif ag > hg:
                a["pts"] += 3; a["w"] += 1; h["l"] += 1
                a["form"].append("W"); h["form"].append("L")
            else:
                h["pts"] += 1; a["pts"] += 1; h["d"] += 1; a["d"] += 1
                h["form"].append("D"); a["form"].append("D")

        groups: list[Group] = []
        for gname in sorted(tally.keys()):
            rows = []
            for tid, s in tally[gname].items():
                rows.append((s, teams[tid]))
            rows.sort(key=lambda x: (x[0]["pts"], x[0]["gf"] - x[0]["ga"], x[0]["gf"]),
                      reverse=True)
            standings = []
            for rank, (s, team) in enumerate(rows, start=1):
                standings.append(Standing(
                    rank=rank, team=team, points=s["pts"], played=s["p"],
                    win=s["w"], draw=s["d"], lose=s["l"], goals_for=s["gf"],
                    goals_against=s["ga"], goal_diff=s["gf"] - s["ga"],
                    group=gname, form="".join(reversed(s["form"][-5:])),
                ))
            groups.append(Group(name=f"Group {gname}", standings=standings))
        return groups

    def get_team(self, team_id: int) -> Optional[Team]:
        for m in self._matches(self._season):
            if m.home_team.id == team_id:
                return m.home_team
            if m.away_team.id == team_id:
                return m.away_team
        return None

    def get_teams(self, league_id: Optional[int] = None,
                  search: Optional[str] = None) -> list[Team]:
        # Só seleções reais = as que jogam a fase de grupos. Jogos de mata-mata
        # têm "times" placeholder ("1A", "W101") até serem definidos — fora.
        matches = self._matches(self._season)
        group_matches = [m for m in matches if m.stage == "group"]
        source = group_matches or matches
        seen: dict[int, Team] = {}
        for m in source:
            seen.setdefault(m.home_team.id, m.home_team)
            seen.setdefault(m.away_team.id, m.away_team)
        teams = list(seen.values())
        if search:
            s = search.strip().lower()
            teams = [t for t in teams if s in t.name.lower()]
        return sorted(teams, key=lambda t: t.name)

    def get_team_form(self, team_id: int, last_n: int = 10,
                      league_id: Optional[int] = None,
                      season: Optional[int] = None) -> Optional[TeamForm]:
        """Forma derivada dos resultados da seleção no torneio."""
        season = season or self._season
        played = [
            m for m in self._matches(season)
            if m.home_goals is not None
            and team_id in (m.home_team.id, m.away_team.id)
        ]
        played.sort(key=lambda m: m.utc_kickoff or datetime.min.replace(tzinfo=timezone.utc))
        if not played:
            return None
        gf = ga = w = d = ls = 0
        form: list[str] = []
        for m in played:
            is_home = m.home_team.id == team_id
            mine = m.home_goals if is_home else m.away_goals
            theirs = m.away_goals if is_home else m.home_goals
            gf += mine; ga += theirs
            if mine > theirs:
                w += 1; form.append("W")
            elif mine < theirs:
                ls += 1; form.append("L")
            else:
                d += 1; form.append("D")
        n = len(played)
        return TeamForm(
            team_id=team_id, matches_played=n,
            goals_for=round(gf / n, 2), goals_against=round(ga / n, 2),
            wins=w, draws=d, losses=ls,
            recent_form="".join(reversed(form[-5:])),
        )

    def get_recent_results(self, team_id: int, last_n: int = 15) -> list[Match]:
        """Resultados do time no torneio (openfootball não tem histórico fora
        dele). Pouco sinal — só os jogos da própria Copa já finalizados."""
        played = [
            m for m in self._matches(self._season)
            if m.status == "finished" and m.home_goals is not None
            and team_id in (m.home_team.id, m.away_team.id)
        ]
        return played[:last_n]

    def get_live_matches(self) -> list[Match]:
        """openfootball é estático (sem tempo real) — nunca há jogo ao vivo."""
        return []

    def get_leagues(self) -> list[League]:
        data = self._payload(self._season)
        return [League(id=config.WORLD_CUP_LEAGUE_ID, name=data.get("name", "World Cup"),
                       country="World", season=self._season)]

    def get_standings(self, league_id: int, season: int) -> list[Standing]:
        out: list[Standing] = []
        for g in self.get_groups(league_id, season):
            out.extend(g.standings)
        return out

    # ── Não cobertos pelo openfootball (degradação graciosa) ───────────────
    def get_match_statistics(self, match_id: int):
        return None

    def get_lineups(self, match_id: int) -> list:
        return []

    def get_injuries(self, team_id: int) -> list:
        return []

    def get_player(self, player_id: int):
        return None

    def get_players(self, team_id: Optional[int] = None,
                    search: Optional[str] = None) -> list:
        return []

    def get_h2h_odds(self, matches: list) -> dict:
        return {}

    def get_match_odds(self, match):
        return None
