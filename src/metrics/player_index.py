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

from dataclasses import dataclass

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
