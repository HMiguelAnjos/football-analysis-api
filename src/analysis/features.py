"""
Features de análise — a fronteira entre o DOMÍNIO e a ENGINE.

A engine (``scores.py``) só conhece estes dataclasses, nunca ``TeamForm`` /
``MatchStatistics`` direto. Cada feature é ``Optional``: quando a fonte não
fornece, fica ``None`` e a engine usa fallback neutro 50 + warning.

Aqui mora a extração (domínio → features). Conforme plugarmos novas fontes
(understat, coleta própria), só preenchemos mais campos — pesos e scores não
mudam.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from src.analysis.helpers import safe_divide
from src.providers.base import Match, MatchStatistics, TeamForm


def _streak(recent_form: Optional[str]) -> Optional[float]:
    """Sequência atual a partir de 'WWDLW' (recente à esquerda):
    +N vitórias seguidas, -N derrotas seguidas, 0 se empate/quebra."""
    if not recent_form:
        return None
    first = recent_form[0].upper()
    if first not in ("W", "L"):
        return 0.0
    n = 0
    for c in recent_form.upper():
        if c == first:
            n += 1
        else:
            break
    return float(n) if first == "W" else float(-n)


@dataclass
class TeamAdvancedStats:
    """Médias por jogo agregadas das estatísticas de partida (api-football
    /fixtures/statistics) dos últimos N jogos. None onde a fonte não fornece."""
    xg: Optional[float] = None
    xga: Optional[float] = None
    shots_on_for: Optional[float] = None
    shots_on_against: Optional[float] = None
    shots_total_for: Optional[float] = None
    corners_for: Optional[float] = None
    corners_against: Optional[float] = None
    cards_for: Optional[float] = None
    fouls_for: Optional[float] = None
    possession: Optional[float] = None
    sample: int = 0


def aggregate_advanced(samples: list[dict]) -> TeamAdvancedStats:
    """Agrega amostras [{'own': stats_dict, 'opp': stats_dict}, ...] em médias.
    Cada chave é média só sobre os jogos em que ela existe (ignora ausentes)."""
    if not samples:
        return TeamAdvancedStats()

    def avg(getter) -> Optional[float]:
        vals = [v for v in (getter(s) for s in samples) if v is not None]
        return sum(vals) / len(vals) if vals else None

    def own(key):
        return lambda s: s.get("own", {}).get(key)

    def opp(key):
        return lambda s: s.get("opp", {}).get(key)

    def cards(s) -> Optional[float]:
        # Preferência: contagem REAL de cartões via eventos (chave "cards",
        # amarelo+vermelho). Sem ela, a estatística agregada raramente traz
        # amarelos (só vermelhos) → só usa se houver amarelo; senão fallback-50.
        c = s.get("cards")
        if c is not None:
            return c
        d = s.get("own", {})
        y = d.get("yellow_cards")
        if y is None:
            return None
        return y + (d.get("red_cards") or 0)

    return TeamAdvancedStats(
        xg=avg(own("expected_goals")),
        xga=avg(opp("expected_goals")),
        shots_on_for=avg(own("shots_on_goal")),
        shots_on_against=avg(opp("shots_on_goal")),
        shots_total_for=avg(own("total_shots")),
        corners_for=avg(own("corner_kicks")),
        corners_against=avg(opp("corner_kicks")),
        cards_for=avg(cards),
        fouls_for=avg(own("fouls")),
        possession=avg(own("ball_possession")),
        sample=len(samples),
    )


@dataclass
class TeamFeatures:
    """Métricas de UM time, todas opcionais (None = dado ausente)."""
    # Ofensivo
    xg: Optional[float] = None
    xg_per_shot: Optional[float] = None
    big_chances: Optional[float] = None
    box_touches: Optional[float] = None
    shots_on_target: Optional[float] = None
    goals_for: Optional[float] = None
    off_form: Optional[float] = None
    # Criação
    xa: Optional[float] = None
    key_passes: Optional[float] = None
    progressive_passes: Optional[float] = None
    final_third_passes: Optional[float] = None
    accurate_crosses: Optional[float] = None
    # Defesa (maior = mais frágil)
    xga: Optional[float] = None
    big_chances_conceded: Optional[float] = None
    shots_on_target_conceded: Optional[float] = None
    goals_conceded_recent: Optional[float] = None
    def_errors: Optional[float] = None
    ppda: Optional[float] = None
    # Momento
    ppg: Optional[float] = None
    last10_ppg: Optional[float] = None
    home_away_ppg: Optional[float] = None
    xg_trend: Optional[float] = None
    streak: Optional[float] = None
    # Pressão
    high_recoveries: Optional[float] = None
    final_third_entries: Optional[float] = None
    off_possession: Optional[float] = None
    shots_after_recovery: Optional[float] = None
    # Escanteios
    corners_for: Optional[float] = None
    corners_against: Optional[float] = None
    crosses: Optional[float] = None
    lateral_attacks: Optional[float] = None
    blocked_shots: Optional[float] = None
    # Cartões
    cards_for: Optional[float] = None
    fouls: Optional[float] = None
    # Meta / risco
    matches_played: int = 0
    injuries: Optional[float] = None
    volatility: Optional[float] = None

    @property
    def att_efficiency(self) -> Optional[float]:
        """gols marcados / xG (>1 = supera o xG; risco de regressão)."""
        if self.goals_for is None or self.xg is None or self.xg == 0:
            return None
        return safe_divide(self.goals_for, self.xg, 1.0)

    @property
    def def_efficiency(self) -> Optional[float]:
        """gols sofridos / xGA (>1 = sofre mais que o esperado)."""
        if self.goals_conceded_recent is None or self.xga is None or self.xga == 0:
            return None
        return safe_divide(self.goals_conceded_recent, self.xga, 1.0)

    @classmethod
    def from_form(cls, form: Optional[TeamForm]) -> "TeamFeatures":
        """Extrai o que ``TeamForm`` (api-football + xG quando há) oferece.
        O resto fica None (fallback 50 na engine)."""
        if form is None:
            return cls()
        xg_per_shot = None
        if form.xg is not None and form.shots_for:
            xg_per_shot = form.xg / form.shots_for
        return cls(
            xg=form.xg,
            xg_per_shot=xg_per_shot,
            shots_on_target=form.shots_on_target_for,
            goals_for=form.goals_for,
            off_form=form.goals_for,                 # proxy de forma ofensiva
            xga=form.xga,
            goals_conceded_recent=form.goals_against,
            ppg=form.points_per_game,
            last10_ppg=form.points_per_game,
            streak=_streak(form.recent_form),
            off_possession=form.possession,
            corners_for=form.corners_for,
            corners_against=form.corners_against,
            cards_for=form.cards_for,
            matches_played=form.matches_played or 0,
        )

    def merge_advanced(self, adv: Optional["TeamAdvancedStats"]) -> "TeamFeatures":
        """Sobrepõe as métricas agregadas das stats por jogo (xG, finalizações,
        escanteios, cartões, posse) — preenche o que o TeamForm deixou em None.
        Só aplica valores presentes; mantém o resto. Retorna self (encadeável)."""
        if adv is None:
            return self
        if adv.xg is not None:
            self.xg = adv.xg
        if adv.xga is not None:
            self.xga = adv.xga
        if adv.shots_on_for is not None:
            self.shots_on_target = adv.shots_on_for
        if adv.shots_on_against is not None:
            self.shots_on_target_conceded = adv.shots_on_against
        if adv.xg is not None and adv.shots_total_for:
            self.xg_per_shot = adv.xg / adv.shots_total_for
        if adv.corners_for is not None:
            self.corners_for = adv.corners_for
        if adv.corners_against is not None:
            self.corners_against = adv.corners_against
        if adv.cards_for is not None:
            self.cards_for = adv.cards_for
        if adv.fouls_for is not None:
            self.fouls = adv.fouls_for
        if adv.possession is not None:
            self.off_possession = adv.possession
        return self


@dataclass
class MatchFeatures:
    """Confronto: features dos dois times + contexto do jogo."""
    home: TeamFeatures
    away: TeamFeatures
    knockout: bool = False
    derby: bool = False
    neutral_venue: bool = False
    importance: Optional[float] = None        # 0–100 (None = desconhecido)
    # Odds opcionais: {market: {selection: odd}}. Vazio/None = sem odds.
    odds: dict = field(default_factory=dict)

    @classmethod
    def from_domain(cls, match: Optional[Match], home_form: Optional[TeamForm],
                    away_form: Optional[TeamForm], *, odds: Optional[dict] = None,
                    home_adv: Optional["TeamAdvancedStats"] = None,
                    away_adv: Optional["TeamAdvancedStats"] = None) -> "MatchFeatures":
        # Em LIGA regular o jogo é mando de campo normal (não sede neutra) — o
        # `knockout` genérico continua (útil pra fases finais de copas), mas a
        # vantagem de casa NÃO é mais zerada como era na Copa. neutral_venue só
        # deve ser True para competições realmente em sede única.
        knockout = bool(match and match.stage and match.stage != "group")
        return cls(
            home=TeamFeatures.from_form(home_form).merge_advanced(home_adv),
            away=TeamFeatures.from_form(away_form).merge_advanced(away_adv),
            knockout=knockout,
            neutral_venue=False,
            importance=80.0 if knockout else None,
            odds=odds or {},
        )


@dataclass
class LiveFeatures:
    """Estado AO VIVO do jogo (base do LiveGameStateScore e da live engine)."""
    minute: int = 0
    home_score: int = 0
    away_score: int = 0
    # Por time (ao vivo)
    shots_home: Optional[float] = None
    shots_away: Optional[float] = None
    shots_on_home: Optional[float] = None
    shots_on_away: Optional[float] = None
    xg_home: Optional[float] = None
    xg_away: Optional[float] = None
    corners_home: Optional[float] = None
    corners_away: Optional[float] = None
    cards_home: Optional[float] = None
    cards_away: Optional[float] = None
    insidebox_home: Optional[float] = None
    insidebox_away: Optional[float] = None
    blocked_home: Optional[float] = None
    blocked_away: Optional[float] = None
    possession_home: Optional[float] = None
    possession_away: Optional[float] = None
    fouls_home: Optional[float] = None
    fouls_away: Optional[float] = None
    red_home: int = 0
    red_away: int = 0
    # Pressão recente (0–100) e momentum (multiplicador ~0.8–1.3) por lado.
    recent_pressure_home: Optional[float] = None
    recent_pressure_away: Optional[float] = None
    momentum_home: Optional[float] = None
    momentum_away: Optional[float] = None
    subs_home: int = 0
    subs_away: int = 0
    odds_live: dict = field(default_factory=dict)
