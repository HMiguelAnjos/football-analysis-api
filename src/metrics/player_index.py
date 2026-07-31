"""
Índices compostos de jogador (estilo dos estudos enviados), ADAPTADOS aos
dados disponíveis na api-football (sem xG/xA/toques na área/grandes chances/
cruzamentos/recuperações, que a fonte não fornece).

  • IPO — Índice de Periculosidade Ofensiva
      ChutesNoAlvo·0.40 + Chutes·0.25 + Gols·0.20 + Forma·0.15
  • ICJ — Índice de Criação de Jogadas
      Assistências·0.40 + PassesChave·0.40 + DriblesCertos·0.20
  • ID  — Índice Defensivo
      Desarmes·0.40 + Interceptações·0.35 + DuelosVencidos·0.25
  • IIP — Índice de Influência na Partida
      (IPO·0.45 + ICJ·0.30 + ID·0.25) × ContextoDoJogo

Cada input é convertido para **por-90 minutos** e depois **normalizado 0–100**
em relação ao 95º percentil do elenco/competição — assim os índices ficam
comparáveis entre jogadores de stats de escalas diferentes (gols vs passes).
ContextoDoJogo (1.0 por padrão) escala o IIP quando avaliado PARA UM JOGO
(força do adversário).

PURO: entra lista de PlayerSchema, sai PlayerIndex. Sem rede/banco.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from src.analysis import weights as W
from src.analysis.helpers import clamp, normalize
from src.schemas.football_schemas import PlayerSchema

# Minutos mínimos pra entrar no ranking (corta ruído de quem jogou pouco).
MIN_MINUTES = 30

IPO_W = {"sot": 0.40, "shots": 0.25, "goals": 0.20, "form": 0.15}
ICJ_W = {"assists": 0.40, "key_passes": 0.40, "dribbles": 0.20}
ID_W = {"tackles": 0.40, "interceptions": 0.35, "duels_won": 0.25}
IIP_W = {"ipo": 0.45, "icj": 0.30, "id": 0.25}

_INPUTS = ["sot", "shots", "goals", "form", "assists", "key_passes",
           "dribbles", "tackles", "interceptions", "duels_won"]


@dataclass
class PlayerIndex:
    player: PlayerSchema
    ipo: float
    icj: float
    id: float
    iip: float


def _per90(value: float, minutes: int) -> float:
    return value / (minutes / 90.0) if minutes and minutes > 0 else 0.0


def _raw_inputs(p: PlayerSchema) -> dict[str, float]:
    m = p.minutes or 0
    return {
        "sot": _per90(p.shots_on_target or 0, m),
        "shots": _per90(p.shots or 0, m),
        "goals": _per90(p.goals or 0, m),
        "form": float(p.rating or 0.0),          # rating já é escala ~0-10
        "assists": _per90(p.assists or 0, m),
        "key_passes": _per90(p.key_passes or 0, m),
        "dribbles": _per90(p.dribbles or 0, m),
        "tackles": _per90(p.tackles or 0, m),
        "interceptions": _per90(p.interceptions or 0, m),
        "duels_won": _per90(p.duels_won or 0, m),
    }


def _p95(values: list[float]) -> float:
    """95º percentil (âncora do "100") — evita um outlier dominar a escala."""
    vals = sorted(v for v in values if v > 0)
    if not vals:
        return 1.0
    idx = min(len(vals) - 1, max(0, int(round(len(vals) * 0.95)) - 1))
    return vals[idx] or 1.0


def compute_indices(players: list[PlayerSchema], *,
                    context_factor: float = 1.0) -> list[PlayerIndex]:
    pool = [p for p in players if (p.minutes or 0) >= MIN_MINUTES] or list(players)
    if not pool:
        return []
    raw = {p.id: _raw_inputs(p) for p in pool}
    anchors = {k: _p95([raw[p.id][k] for p in pool]) for k in _INPUTS}

    def nz(pid, k: str) -> float:
        return min(100.0, 100.0 * raw[pid][k] / (anchors[k] or 1.0))

    out: list[PlayerIndex] = []
    for p in pool:
        ipo = sum(nz(p.id, k) * w for k, w in IPO_W.items())
        icj = sum(nz(p.id, k) * w for k, w in ICJ_W.items())
        idef = sum(nz(p.id, k) * w for k, w in ID_W.items())
        iip = (ipo * IIP_W["ipo"] + icj * IIP_W["icj"] + idef * IIP_W["id"]) * context_factor
        out.append(PlayerIndex(
            player=p, ipo=round(ipo, 1), icj=round(icj, 1),
            id=round(idef, 1), iip=round(min(iip, 100.0), 1),
        ))
    return out


# ════════════════════════════════════════════════════════════════════════════
# Versão SPEC-COMPLIANT (IPO/ICJ/ID/IIP com as fórmulas exatas do produto).
#
# Diferente do leaderboard acima (`compute_indices`), aqui:
#   • usa as fórmulas/pesos EXATOS do spec (weights.PLAYER_*_WEIGHTS);
#   • dado AUSENTE = None → sai do somatório e reduz cobertura/confiança
#     (NUNCA vira zero — não pune o jogador);
#   • distingue 0 real (ex.: 0 desarmes em 900') de ausente (None);
#   • devolve coverage / confidence / missing_metrics / breakdown por índice.
#
# É PURO: recebe features (PlayerIndexInput), devolve PlayerIndexes. Sem I/O —
# o service normaliza o provider → input; o motor não chama a fonte.
# ════════════════════════════════════════════════════════════════════════════


@dataclass
class PlayerIndexInput:
    """Contrato de ENTRADA (totais por temporada + minutos). O service preenche
    só o que a fonte REALMENTE dá; o resto fica None (ausente ≠ 0)."""
    minutes: int = 0
    appearances: int = 0
    # Ofensivo (IPO)
    xg: Optional[float] = None
    shots: Optional[float] = None
    shots_on_target: Optional[float] = None
    touches_in_box: Optional[float] = None
    recent_form: Optional[float] = None       # já 0–100 (score), ou None
    # Criação (ICJ)
    xa: Optional[float] = None
    key_passes: Optional[float] = None
    big_chances_created: Optional[float] = None
    accurate_crosses: Optional[float] = None
    # Defesa (ID)
    tackles: Optional[float] = None
    interceptions: Optional[float] = None
    recoveries: Optional[float] = None
    duels_won: Optional[float] = None


@dataclass
class IndexResult:
    score: Optional[float]                 # 0–100, ou None se sem dado nenhum
    coverage: float                        # peso disponível / peso total (0–1)
    confidence: str                        # high|medium|low|unavailable
    available: bool                        # coverage >= piso mínimo
    missing_metrics: list[str] = field(default_factory=list)
    breakdown: dict[str, Optional[float]] = field(default_factory=dict)


@dataclass
class IIPResult:
    score: Optional[float]
    base_score: Optional[float]
    context_factor: float
    coverage: float
    confidence: str
    available: bool
    missing_components: list[str] = field(default_factory=list)


@dataclass
class PlayerIndexes:
    ipo: IndexResult
    icj: IndexResult
    id: IndexResult
    iip: IIPResult
    minutes: int
    low_sample: bool                       # minutos < amostra mínima
    normalization_version: str


def _confidence(coverage: float) -> str:
    if coverage >= W.PLAYER_CONF_HIGH:
        return "high"
    if coverage >= W.PLAYER_CONF_MEDIUM:
        return "medium"
    if coverage > 0:
        return "low"
    return "unavailable"


def _to_per90(total: Optional[float], minutes: int) -> Optional[float]:
    """Total da temporada → por-90. None (ausente) ou sem minutos (sem amostra)
    → None. Não compara totais de jogadores com minutagens diferentes."""
    if total is None or minutes <= 0:
        return None
    return total / minutes * 90.0


def _normalized(name: str, raw: Optional[float], minutes: int) -> Optional[float]:
    """Bruto → 0–100. `recent_form` já é score (só clampa). O resto vira por-90 e
    normaliza pela faixa documentada. None propaga None (ausente)."""
    if raw is None:
        return None
    if name in W.PLAYER_SCORE_METRICS:
        return clamp(float(raw), 0.0, 100.0)
    p90 = _to_per90(raw, minutes)
    if p90 is None:
        return None
    lo, hi = W.PLAYER_NORM_P90[name]
    return normalize(p90, lo, hi)          # já clampa 0–100 e propaga None


def _component(inp: PlayerIndexInput, wmap: dict[str, float], *,
               min_coverage: float) -> IndexResult:
    """Score de um índice reponderando SÓ as métricas disponíveis:
    score = Σ(norm·peso) / Σ(peso disponível). Ausente sai da conta."""
    total_w = sum(wmap.values())
    norm = {name: _normalized(name, getattr(inp, name), inp.minutes) for name in wmap}
    present = {n: v for n, v in norm.items() if v is not None}
    missing = [n for n in wmap if norm[n] is None]
    avail_w = sum(wmap[n] for n in present)
    coverage = round(avail_w / total_w, 3) if total_w else 0.0
    breakdown = {n: (round(v, 1) if v is not None else None) for n, v in norm.items()}
    if avail_w <= 0:
        return IndexResult(None, 0.0, "unavailable", False, missing, breakdown)
    score = sum(present[n] * wmap[n] for n in present) / avail_w
    return IndexResult(
        score=round(clamp(score, 0.0, 100.0), 1), coverage=coverage,
        confidence=_confidence(coverage), available=coverage >= min_coverage,
        missing_metrics=missing, breakdown=breakdown,
    )


def _compute_iip(components: dict[str, IndexResult], weights: dict[str, float],
                 context_factor: float, min_coverage: float) -> IIPResult:
    """IIP = média ponderada dos componentes DISPONÍVEIS × contexto. Se um
    componente falta (score None), reponderа sobre os presentes e reduz a
    cobertura. context_factor inválido (≤0) → 1.0."""
    total_w = sum(weights[k] for k in components)
    present = {k: c for k, c in components.items() if c.score is not None}
    missing = [k for k in components if k not in present]
    cf = context_factor if (context_factor and context_factor > 0) else 1.0
    if not present or total_w <= 0:
        return IIPResult(None, None, cf, 0.0, "unavailable", False, missing)
    avail_w = sum(weights[k] for k in present)
    base = sum(present[k].score * weights[k] for k in present) / avail_w
    base = round(clamp(base, 0.0, 100.0), 1)
    score = round(clamp(base * cf, 0.0, 100.0), 1)
    # Cobertura do IIP combina disponibilidade DO componente E a cobertura dele.
    coverage = round(
        sum(weights[k] * components[k].coverage for k in present) / total_w, 3)
    return IIPResult(score, base, cf, coverage, _confidence(coverage),
                     coverage >= min_coverage, missing)


def compute_player_indexes(inp: PlayerIndexInput, *, context_factor: float = 1.0,
                           component_weights: Optional[dict[str, float]] = None,
                           min_coverage: Optional[float] = None) -> PlayerIndexes:
    """IPO/ICJ/ID/IIP de UM jogador a partir das features brutas. Puro."""
    mc = W.PLAYER_MIN_COVERAGE if min_coverage is None else min_coverage
    cw = component_weights or W.PLAYER_IIP_WEIGHTS
    ipo = _component(inp, W.PLAYER_IPO_WEIGHTS, min_coverage=mc)
    icj = _component(inp, W.PLAYER_ICJ_WEIGHTS, min_coverage=mc)
    idef = _component(inp, W.PLAYER_ID_WEIGHTS, min_coverage=mc)
    iip = _compute_iip({"ipo": ipo, "icj": icj, "id": idef}, cw, context_factor, mc)
    return PlayerIndexes(
        ipo=ipo, icj=icj, id=idef, iip=iip, minutes=inp.minutes,
        low_sample=(inp.minutes or 0) < W.PLAYER_MIN_MINUTES,
        normalization_version=W.PLAYER_INDEX_NORM_VERSION,
    )
