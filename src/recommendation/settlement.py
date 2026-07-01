"""
Liquidação (settlement) de uma seleção a partir do resultado final do jogo.

PURO: dado (market, selection, line, MatchResult), devolve hit | miss | push |
void. Mercados de gols liquidam sempre que há placar; escanteios/cartões
liquidam se as stats vieram; player props que exigem dado granular (chutes do
jogador, etc.) sem fonte confiável → void (não inventa resultado).
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from typing import Optional

HIT = "hit"
MISS = "miss"
PUSH = "push"
VOID = "void"


@dataclass
class MatchResult:
    home_goals: int
    away_goals: int
    corners: Optional[float] = None       # total de escanteios
    cards: Optional[float] = None         # total de cartões
    scorers: list[str] = field(default_factory=list)  # nomes normalizados
    # Stats granulares de jogador {nome_normalizado: {stat: valor}} (opcional).
    player_stats: dict[str, dict[str, float]] = field(default_factory=dict)


def _ou(value: float, line: float) -> str:
    """Resolve um over (genérico). Linha .5 nunca dá push."""
    if value > line:
        return HIT
    if value < line:
        return MISS
    return PUSH


def _norm(s: str) -> str:
    """Normaliza nome de jogador pra comparação: minúsculo, sem acento, sem
    espaço duplo. Fontes diferentes da api-football (elenco vs fixtures/players)
    às vezes grafam o mesmo jogador com/sem diacrítico ('Vinícius' vs 'Vinicius')
    — sem isso, um artilheiro real vira MISS falso na liquidação."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.strip().lower().split())


def settle(market: str, selection: str, line: Optional[float], result: MatchResult) -> str:
    h, a = result.home_goals, result.away_goals
    total = h + a
    sel = selection.lower()

    if market == "1x2":
        winner = "home" if h > a else "away" if a > h else "draw"
        return HIT if sel == winner else MISS

    if market == "double_chance":
        winner = "home" if h > a else "away" if a > h else "draw"
        mapping = {
            "home_draw": {"home", "draw"},
            "home_away": {"home", "away"},
            "draw_away": {"draw", "away"},
        }
        return HIT if winner in mapping.get(sel, set()) else MISS

    if market == "dnb":
        if h == a:
            return PUSH
        winner = "home" if h > a else "away"
        return HIT if sel == winner else MISS

    if market == "over_under" and line is not None:
        side = _ou(total, line)
        if side == PUSH:
            return PUSH
        if sel == "over":
            return HIT if side == HIT else MISS
        if sel == "under":
            return HIT if side == MISS else MISS
        return VOID

    if market == "btts":
        both = h >= 1 and a >= 1
        if sel == "yes":
            return HIT if both else MISS
        if sel == "no":
            return HIT if not both else MISS
        return VOID

    if market == "team_total_home" and line is not None:
        side = _ou(h, line)
        return _resolve_ou_side(sel, side)

    if market == "team_total_away" and line is not None:
        side = _ou(a, line)
        return _resolve_ou_side(sel, side)

    if market == "asian_handicap" and line is not None:
        # line é do ponto de vista do mandante. Sem quarter-lines no settle
        # simples — quarter dividiria a aposta (tratar como push parcial fora
        # de escopo aqui; arredonda pro resultado da meia-linha mais próxima).
        margin = (h + line) - a
        if sel == "home":
            return HIT if margin > 0 else PUSH if margin == 0 else MISS
        if sel == "away":
            return HIT if margin < 0 else PUSH if margin == 0 else MISS
        return VOID

    if market == "corners" and line is not None:
        if result.corners is None:
            return VOID
        return _resolve_ou_side(sel, _ou(result.corners, line))

    if market == "cards" and line is not None:
        if result.cards is None:
            return VOID
        return _resolve_ou_side(sel, _ou(result.cards, line))

    if market == "anytime_scorer":
        if not result.scorers:
            return VOID
        name = _player_name(selection)
        return HIT if _norm(name) in {_norm(s) for s in result.scorers} else MISS

    if market in ("player_shots", "player_shots_on_target", "player_assists",
                  "player_tackles") and line is not None:
        stat_key = {
            "player_shots": "shots",
            "player_shots_on_target": "shots_on_target",
            "player_assists": "assists",
            "player_tackles": "tackles",
        }[market]
        name = _player_name(selection)
        stats = result.player_stats.get(_norm(name))
        if stats is None or stat_key not in stats:
            return VOID
        # Props são sempre "Mais de N" (over). A meia-linha em `line` (N-0.5)
        # resolve sem push: over (N-0.5) ≡ valor ≥ N.
        return _resolve_ou_side("over", _ou(stats[stat_key], line))

    return VOID


def _player_name(selection: str) -> str:
    """Extrai o nome do jogador da seleção ('Nome — Mais de 2 desarmes' → 'Nome';
    'Nome|over' → 'Nome'). Tolera os dois formatos."""
    s = selection.split("—")[0]      # em-dash do rótulo dos props
    s = s.split("|")[0]              # formato antigo
    return s.strip()


def _resolve_ou_side(sel: str, side: str) -> str:
    if side == PUSH:
        return PUSH
    if sel == "over":
        return HIT if side == HIT else MISS
    if sel == "under":
        return HIT if side == MISS else MISS
    return VOID


def profit_units(status: str, odd: float) -> float:
    """Retorno em unidades pra stake=1."""
    if status == HIT:
        return round(odd - 1.0, 3)
    if status == MISS:
        return -1.0
    return 0.0  # push/void devolve a stake
