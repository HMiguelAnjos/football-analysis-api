"""
Serialização domínio ↔ JSON pros modelos cacheados em disco.

O PersistentCache grava JSON; os dataclasses de domínio têm `datetime`
(não-serializável direto), então convertemos aqui. Só os tipos que vão pro
cache persistente precisam de codec — odds ficam em memória (voláteis).
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from src.providers.base import Match, Team, TeamForm


def _dt_to_iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def _iso_to_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def _team_to_dict(t: Team) -> dict:
    return {"id": t.id, "name": t.name, "short_name": t.short_name, "logo": t.logo}


def _team_from_dict(d: dict) -> Team:
    return Team(id=d["id"], name=d.get("name", ""),
                short_name=d.get("short_name", ""), logo=d.get("logo", ""))


def match_to_dict(m: Match) -> dict:
    return {
        "id": m.id, "league_id": m.league_id, "league_name": m.league_name,
        "season": m.season, "utc_kickoff": _dt_to_iso(m.utc_kickoff),
        "status": m.status, "home_team": _team_to_dict(m.home_team),
        "away_team": _team_to_dict(m.away_team), "home_goals": m.home_goals,
        "away_goals": m.away_goals, "minute": m.minute, "venue": m.venue,
        # Campos de torneio (modo Copa).
        "stage": m.stage, "group": m.group, "city": m.city,
        "extra_time_home": m.extra_time_home, "extra_time_away": m.extra_time_away,
        "penalty_home": m.penalty_home, "penalty_away": m.penalty_away,
        "winner": m.winner,
    }


def match_from_dict(d: dict) -> Match:
    return Match(
        id=d["id"], league_id=d["league_id"], league_name=d.get("league_name", ""),
        season=d.get("season", 0), utc_kickoff=_iso_to_dt(d.get("utc_kickoff")),
        status=d.get("status", "scheduled"),
        home_team=_team_from_dict(d["home_team"]),
        away_team=_team_from_dict(d["away_team"]),
        home_goals=d.get("home_goals"), away_goals=d.get("away_goals"),
        minute=d.get("minute"), venue=d.get("venue", ""),
        stage=d.get("stage"), group=d.get("group"), city=d.get("city", ""),
        extra_time_home=d.get("extra_time_home"), extra_time_away=d.get("extra_time_away"),
        penalty_home=d.get("penalty_home"), penalty_away=d.get("penalty_away"),
        winner=d.get("winner"),
    )


def form_to_dict(f: TeamForm) -> dict:
    # TeamForm é plano (números/None) — vars() já é JSON-safe.
    return dict(vars(f))


def form_from_dict(d: dict) -> TeamForm:
    return TeamForm(**d)
