"""
Ratings de força de time (ataque/defesa) AJUSTADOS POR ADVERSÁRIO.

Por que existe: no contexto de seleções (Copa), a média simples de gols engana
— Cabo Verde marcou 2.08 gols/jogo, mas contra defesas fracas; a Espanha 2.92,
contra defesas fortes. Olhar só o número cru subvaloriza favoritos e infla
zebras. Este módulo resolve, de forma iterativa, um rating de ATAQUE e DEFESA
por time a partir de um conjunto de RESULTADOS (qualifiers, amistosos, Nations
League, …): marcar contra uma defesa forte vale mais que contra uma fraca.

Modelo multiplicativo clássico (estilo força ofensiva/defensiva relativa):

    gols_esperados(t vs o) = avg * ataque(t) * defesa(o)

resolvido por ponto-fixo:

    ataque(t)  = média( gols_marcados / (defesa(adv)  * avg) )
    defesa(t)  = média( gols_sofridos / (ataque(adv) * avg) )

PURO e determinístico — só matemática sobre uma lista de Match finalizados.
Sem rede, sem banco, totalmente testável com resultados sintéticos.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.providers.base import Match

# Faixa sã pros ratings (1.0 = média). Evita explosão por um resultado atípico.
_MIN_RATING = 0.20
_MAX_RATING = 5.00
# Gols por time por jogo quando não há dados (futebol internacional ~1.35).
_DEFAULT_AVG = 1.35
# Lambda (gols esperados) sempre numa faixa razoável.
_MIN_LAMBDA = 0.10
_MAX_LAMBDA = 5.00


def _clamp(v: float, lo: float = _MIN_RATING, hi: float = _MAX_RATING) -> float:
    return max(lo, min(v, hi))


def _clamp_lambda(v: float) -> float:
    return max(_MIN_LAMBDA, min(v, _MAX_LAMBDA))


@dataclass
class TeamRatings:
    """Ratings ataque/defesa por team_id (1.0 = média) + média de gols base."""
    avg: float = _DEFAULT_AVG
    attack: dict[int, float] = field(default_factory=dict)
    defense: dict[int, float] = field(default_factory=dict)

    def has(self, team_id: int) -> bool:
        return team_id in self.attack and team_id in self.defense

    def lambdas(self, home_id: int, away_id: int,
                home_advantage: float = 1.0) -> tuple[float, float]:
        """Gols esperados (lambda_home, lambda_away). home_advantage=1.0 = neutro
        (apropriado pra torneio em sede neutra)."""
        ah = self.attack.get(home_id, 1.0)
        dh = self.defense.get(home_id, 1.0)
        aa = self.attack.get(away_id, 1.0)
        da = self.defense.get(away_id, 1.0)
        lam_home = self.avg * ah * da * home_advantage
        lam_away = self.avg * aa * dh / home_advantage
        return _clamp_lambda(lam_home), _clamp_lambda(lam_away)


def compute_ratings(results: list[Match], *, iterations: int = 12,
                    shrink: float = 1.0) -> TeamRatings:
    """Resolve ratings ataque/defesa a partir de resultados finalizados.

    `results` pode conter duplicatas (o mesmo jogo vindo do histórico de dois
    times) — deduplicamos por id. Adversários fora do torneio entram como
    parâmetros latentes (servem só pra calibrar os times do torneio).

    `shrink` (0..1) regride os ratings à média 1.0 — evita defesas/ataques
    extremos (ex.: 0.20) ganhos contra adversários fracos, que esmagavam o
    total de gols (Under 2.5 em todo jogo). 1.0 = sem encolher; 0.55 = puxa
    ~45% rumo à média.
    """
    games: list[tuple[int, int, int, int]] = []
    seen: set[int] = set()
    for m in results:
        if m is None or m.id in seen:
            continue
        if m.status != "finished" or m.home_goals is None or m.away_goals is None:
            continue
        seen.add(m.id)
        games.append((m.home_team.id, m.away_team.id,
                      int(m.home_goals), int(m.away_goals)))

    if not games:
        return TeamRatings(avg=_DEFAULT_AVG)

    total_goals = sum(gh + ga for _, _, gh, ga in games)
    avg = (total_goals / (2 * len(games))) or _DEFAULT_AVG

    teams = {t for h, a, _, _ in games for t in (h, a)}
    attack = {t: 1.0 for t in teams}
    defense = {t: 1.0 for t in teams}

    # Lista de jogos por time: (adversário, marcados, sofridos).
    by_team: dict[int, list[tuple[int, int, int]]] = {t: [] for t in teams}
    for h, a, gh, ga in games:
        by_team[h].append((a, gh, ga))
        by_team[a].append((h, ga, gh))

    for _ in range(max(iterations, 1)):
        new_attack: dict[int, float] = {}
        for t, ms in by_team.items():
            num = sum(scored for _, scored, _ in ms)
            den = sum(defense[opp] * avg for opp, _, _ in ms)
            new_attack[t] = _clamp(num / den) if den > 0 else 1.0
        attack = new_attack

        new_defense: dict[int, float] = {}
        for t, ms in by_team.items():
            num = sum(conceded for _, _, conceded in ms)
            den = sum(attack[opp] * avg for opp, _, _ in ms)
            new_defense[t] = _clamp(num / den) if den > 0 else 1.0
        defense = new_defense

    if shrink != 1.0:  # regressão à média 1.0 (corta extremos)
        attack = {t: 1.0 + (v - 1.0) * shrink for t, v in attack.items()}
        defense = {t: 1.0 + (v - 1.0) * shrink for t, v in defense.items()}

    return TeamRatings(avg=avg, attack=attack, defense=defense)
