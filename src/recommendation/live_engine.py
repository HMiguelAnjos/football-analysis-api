"""
Motor de recomendações AO VIVO — foco em ESCANTEIOS.

Recebe um snapshot do jogo (estatísticas acumuladas + deltas dos últimos ~10
min + minuto + placar + odd de escanteios, se houver) e classifica a melhor
entrada. Prioriza mercados de escanteios; chute a gol é só UM sinal, nunca o
motivo principal. Toda recomendação carrega justificativa baseada nas stats.

A api-football NÃO fornece "ataques perigosos"/"cruzamentos"; usamos como proxy
de pressão ofensiva: chutes na área, chutes bloqueados, chutes totais, xG ao
vivo, posse e o RITMO recente de escanteios (deltas dos últimos 10 min).

PURO e determinístico — só matemática sobre o snapshot. Sem rede, sem banco.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

# Tipos de recomendação possíveis.
CORNERS_OVER = "corners_over"
TEAM_CORNERS_OVER = "team_corners_over"
NEXT_CORNER = "next_corner"
SHOTS_ON_TARGET = "shots_on_target"
GOAL_PRESSURE = "goal_pressure"
AVOID_ENTRY = "avoid_entry"

# Confiança mínima (0-10) pra virar uma entrada de verdade; abaixo disso o motor
# devolve avoid_entry (não recomenda só porque houve um chute).
MIN_CONFIDENCE = 5.0


@dataclass
class TeamLive:
    name: str
    corners: int = 0
    total_shots: int = 0
    shots_on: int = 0
    shots_insidebox: int = 0
    blocked_shots: int = 0
    possession: float = 0.0          # % (0-100)
    xg: float = 0.0
    # Deltas dos últimos ~10 min (preenchidos pelo worker via snapshots).
    d_corners: float = 0.0
    d_shots: float = 0.0
    d_shots_on: float = 0.0
    d_shots_insidebox: float = 0.0
    d_blocked: float = 0.0
    d_xg: float = 0.0


@dataclass
class LiveStats:
    minute: int
    home: TeamLive
    away: TeamLive
    home_score: int = 0
    away_score: int = 0
    # Odd de over escanteios, se disponível (mercado opcional).
    corners_over_line: Optional[float] = None
    corners_over_odd: Optional[float] = None


@dataclass
class LivePick:
    rec_type: str
    market: str
    line: Optional[float]
    odd: Optional[float]
    confidence: float                # 0-10
    recommendation: str
    reason: str
    stats_used: dict = field(default_factory=dict)


def _recent_pressure(t: TeamLive) -> float:
    """Score de pressão ofensiva RECENTE (últimos ~10 min) — proxy de 'ataques
    perigosos'. Chute na área e xG pesam mais; escanteio e chute fora pesam menos."""
    return (2.0 * t.d_shots_insidebox + 1.5 * t.d_blocked + 1.0 * t.d_shots
            + 4.0 * t.d_xg + 1.0 * t.d_corners)


def _need(score_for: int, score_against: int, minute: int) -> float:
    """Quanto o time PRECISA buscar o jogo (0-1) — empurra escanteios."""
    if score_for < score_against:
        return 1.0                                   # perdendo → vai pra cima
    if score_for == score_against:
        return 0.65 if minute >= 60 else 0.45        # empate (mais urgente tarde)
    return 0.15                                       # ganhando → administra


def _half_line(x: float) -> float:
    """Linha .5 imediatamente abaixo de x (ex.: 9.3 → 8.5)."""
    return math.floor(x) - 0.5 if x == math.floor(x) else math.floor(x) + 0.5


def _corner_pace(s: LiveStats) -> float:
    """Escanteios por MINUTO (blend ritmo recente + média do jogo)."""
    minute = max(s.minute, 1)
    total = s.home.corners + s.away.corners
    recent = (s.home.d_corners + s.away.d_corners) / 10.0   # por min (últimos 10)
    overall = total / minute
    return 0.6 * recent + 0.4 * overall


def _corner_confidence(s: LiveStats, press_dom: float, projected: float,
                       line: float, need_dom: float) -> float:
    minute = max(s.minute, 1)
    score = 0.0
    # + pressão ofensiva recente (até +3)
    score += min(press_dom / 6.0, 1.0) * 3.0
    # + sequência de chutes na área (ataque perigoso) nos últimos 10 (+1.5)
    score += min((s.home.d_shots_insidebox + s.away.d_shots_insidebox) / 4.0, 1.0) * 1.5
    # + aumento de chutes bloqueados (+1)
    score += min((s.home.d_blocked + s.away.d_blocked) / 2.0, 1.0) * 1.0
    # + jogo aberto (chutes/min) (+1)
    open_game = (s.home.total_shots + s.away.total_shots) / minute
    score += min(open_game / 0.35, 1.0) * 1.0
    # + time precisando do resultado (+1.5)
    score += need_dom * 1.5
    # + linha ainda BAIXA: folga entre projeção e linha (+1.5)
    margin = projected - line
    score += min(max(margin, 0.0) / 2.0, 1.0) * 1.5
    # + ritmo recente de escanteios (+1)
    score += min(_corner_pace(s) / 0.4, 1.0) * 1.0
    # + odd com valor (proxy) (+0.7)
    if s.corners_over_odd and s.corners_over_odd >= 1.80:
        score += 0.7

    # Penalidades — jogo parado / linha alta / fim sem pressão.
    if margin < 0.5:
        score -= 2.5                                  # linha alta demais
    if (s.home.d_shots + s.away.d_shots) < 1.0:
        score -= 2.5                                  # jogo parado (poucas chegadas)
    if s.minute >= 85 and press_dom < 3.0:
        score -= 1.5                                  # fim de jogo sem pressão
    return max(0.0, min(round(score, 1), 10.0))


def _suggest_corner_line(current: int, projected: float) -> Optional[float]:
    """Linha de over escanteios beatável (≥ ~2 a mais esperados). None se não
    vale a pena (projeção perto do atual)."""
    if projected - current < 2.0:
        return None
    line = _half_line(projected - 1.5)
    return max(line, current + 0.5)


def _stats_used(s: LiveStats) -> dict:
    return {
        "minute": s.minute,
        "score": f"{s.home_score}-{s.away_score}",
        "corners_total": s.home.corners + s.away.corners,
        "corners_home": s.home.corners,
        "corners_away": s.away.corners,
        "corners_last_10min": round(s.home.d_corners + s.away.d_corners, 1),
        "shots_insidebox_last_10min": round(s.home.d_shots_insidebox + s.away.d_shots_insidebox, 1),
        "blocked_shots_last_10min": round(s.home.d_blocked + s.away.d_blocked, 1),
        "shots_last_10min": round(s.home.d_shots + s.away.d_shots, 1),
        "xg_last_10min": round(s.home.d_xg + s.away.d_xg, 2),
        "possession_home": round(s.home.possession, 0),
    }


def _build_candidates(s: LiveStats) -> list[LivePick]:
    """Todas as entradas que o jogo sugere agora (escanteios + time atacando em
    chutes/gols), cada uma com sua confiança 0-10."""
    minute = s.minute or 0
    minutes_left = max(90 - minute, 0)
    total_corners = s.home.corners + s.away.corners
    press_h = _recent_pressure(s.home)
    press_a = _recent_pressure(s.away)
    dom, dom_press = (s.home, press_h) if press_h >= press_a else (s.away, press_a)
    need_dom = _need(
        s.home_score if dom is s.home else s.away_score,
        s.away_score if dom is s.home else s.home_score,
        minute,
    )
    stats = _stats_used(s)

    candidates: list[LivePick] = []

    # ── 1) Over escanteios TOTAL ─────────────────────────────────────────
    projected = total_corners + _corner_pace(s) * minutes_left
    line = _suggest_corner_line(total_corners, projected)
    if line is not None and minutes_left >= 5:
        conf = _corner_confidence(s, dom_press, projected, line, need_dom)
        candidates.append(LivePick(
            CORNERS_OVER, f"Over {line:g} escanteios", line, s.corners_over_odd, conf,
            "Boa entrada para over escanteios." if conf >= 7 else "Entrada de valor em escanteios.",
            (f"Jogo com volume ofensivo: {stats['shots_insidebox_last_10min']:g} chutes na área e "
             f"{stats['blocked_shots_last_10min']:g} bloqueios nos últimos 10 min, "
             f"{total_corners} escanteios aos {minute}'. Projeção ~{projected:.0f} no fim — "
             f"linha {line:g} com folga."),
            stats,
        ))

    # ── 2) Over escanteios do TIME que pressiona ─────────────────────────
    dom_pace = (0.6 * dom.d_corners / 10.0
                + 0.4 * dom.corners / max(minute, 1))
    dom_proj = dom.corners + dom_pace * minutes_left
    tline = _suggest_corner_line(dom.corners, dom_proj)
    if tline is not None and minutes_left >= 5 and dom_press >= 3.0:
        conf = _corner_confidence(s, dom_press, dom_proj, tline, need_dom)
        candidates.append(LivePick(
            TEAM_CORNERS_OVER, f"{dom.name} over {tline:g} escanteios", tline,
            None, conf,
            f"{dom.name} forçando escanteios.",
            (f"{dom.name} dominando: {dom.shots_insidebox} chutes na área, posse {dom.possession:.0f}% "
             f"e {dom.corners} escanteios. Pressão recente alta — tende a puxar mais cantos."),
            stats,
        ))

    # ── 3) Próximo escanteio (pressão recente forte) ─────────────────────
    if dom_press >= 5.0 and minutes_left >= 2:
        conf = min(round(4.0 + min(dom_press / 4.0, 1.0) * 4.0 + need_dom, 1), 10.0)
        candidates.append(LivePick(
            NEXT_CORNER, f"Próximo escanteio: {dom.name}", None, None, conf,
            f"{dom.name} deve forçar o próximo escanteio.",
            (f"{dom.name} pressionando forte agora ({stats['shots_insidebox_last_10min']:g} chutes na área "
             f"e {stats['shots_last_10min']:g} finalizações nos últimos 10 min)."),
            stats,
        ))

    # ── 4) Time atacando muito → CHUTES NO GOL do time ───────────────────
    sot_pace = 0.6 * dom.d_shots_on / 10.0 + 0.4 * dom.shots_on / max(minute, 1)
    sot_proj = dom.shots_on + sot_pace * minutes_left
    sline = _suggest_corner_line(dom.shots_on, sot_proj)   # mesma lógica de linha .5
    if sline is not None and minutes_left >= 5 and dom_press >= 3.5:
        conf = _team_press_conf(dom_press, need_dom, sot_proj - sline, s)
        candidates.append(LivePick(
            SHOTS_ON_TARGET, f"{dom.name} over {sline:g} chutes no gol", sline, None, conf,
            f"{dom.name} chutando muito a gol.",
            (f"{dom.name} pressionando: {stats['shots_insidebox_last_10min']:g} chutes na área e "
             f"{dom.shots_on} no gol; projeção ~{sot_proj:.0f}. Tende a finalizar mais."),
            stats,
        ))

    # ── 5) Time atacando muito → PRESSÃO DE GOL ──────────────────────────
    shot_press = dom.d_shots_insidebox + dom.d_xg * 3.0 + dom.d_blocked
    if shot_press >= 4.0 and minutes_left >= 5:
        conf = min(round(4.0 + min(shot_press / 5.0, 1.0) * 3.5 + need_dom, 1), 10.0)
        candidates.append(LivePick(
            GOAL_PRESSURE, f"{dom.name} pressão de gol", None, None, conf,
            f"{dom.name} criando muito — pode sair gol.",
            (f"{dom.name} com xG ao vivo {dom.xg:.1f} e {dom.shots_insidebox} chutes na área; "
             f"pressão crescente nos últimos minutos."),
            stats,
        ))

    return candidates


def _team_press_conf(press: float, need: float, margin: float, s: LiveStats) -> float:
    """Confiança 0-10 pra entradas de TIME ATACANDO (chutes/gol)."""
    minute = max(s.minute, 1)
    score = 3.0 + min(press / 6.0, 1.0) * 3.5      # pressão recente (base + até 3.5)
    score += need * 1.5                             # time precisa do resultado
    score += min(max(margin, 0.0) / 2.0, 1.0) * 1.0
    open_game = (s.home.total_shots + s.away.total_shots) / minute
    score += min(open_game / 0.35, 1.0) * 1.0
    if (s.home.d_shots + s.away.d_shots) < 1.0:
        score -= 2.5                                # jogo parado
    if s.minute >= 85 and press < 3.0:
        score -= 1.5
    return max(0.0, min(round(score, 1), 10.0))


def classify_live(s: LiveStats) -> LivePick:
    """Melhor entrada ao vivo do jogo (escanteios primeiro). avoid_entry quando
    nada atinge a confiança mínima."""
    candidates = _build_candidates(s)
    best = max(candidates, key=lambda c: c.confidence, default=None)
    if best is None or best.confidence < MIN_CONFIDENCE:
        minute = s.minute or 0
        stats = _stats_used(s)
        return LivePick(
            AVOID_ENTRY, "Sem entrada", None, None,
            round(best.confidence, 1) if best else 0.0,
            "Sem entrada clara agora.",
            (f"Jogo sem pressão suficiente aos {minute}' "
             f"({stats['shots_last_10min']:g} finalizações e "
             f"{stats['corners_last_10min']:g} escanteios nos últimos 10 min). Esperar."),
            stats,
        )
    return best


def classify_live_all(s: LiveStats) -> list[LivePick]:
    """TODAS as entradas com confiança suficiente (escanteios + time atacando em
    chutes/gols), da maior pra menor. Vazio quando nada qualifica."""
    qualifying = [c for c in _build_candidates(s) if c.confidence >= MIN_CONFIDENCE]
    # 1 por tipo (melhor confiança), ordenado.
    by_type: dict[str, LivePick] = {}
    for c in sorted(qualifying, key=lambda c: c.confidence, reverse=True):
        by_type.setdefault(c.rec_type, c)
    return sorted(by_type.values(), key=lambda c: c.confidence, reverse=True)
