# Engine de Análise (`src/analysis/`)

Camada **pura** (sem I/O/rede/cache) que transforma estatísticas de futebol em
**scores 0–100**, e os scores em **recomendações com grade** (A+/A/B/C/AVOID),
explicáveis (reasons) e auditáveis (warnings).

```
domínio (TeamForm / MatchStatistics)
  → features.py   extração (dado ausente = None)
  → scores.py     FootballAnalysisEngine — 11 scores
  → markets.py    MarketRecommendationEngine (pré-jogo)
  → live.py       LiveRecommendationEngine (ao vivo)
  → grade.py      (edge, risco) → grade + confiança
```

Integração no serviço: `data_service.analysis_opportunities()` (pré-jogo) e
`live_analysis()` (ao vivo). Endpoints: `GET /football/analysis` e
`/football/live-analysis` (+ `/football/world-cup/...`). **Não substitui** os
feeds existentes (`opportunities`, `live_*`) — é um modo complementar.

## Os 11 scores
OffensiveThreat, Creation, DefensiveFragility, Matchup, Momentum, Pressure,
Efficiency (com flag de regressão), Risk, CornersPressure, CardsTension,
LiveGameState. Cada um devolve `value` + `reasons` + `warnings`.

**Regra de ouro:** dado faltando nunca quebra — vira **50 neutro + warning**.
Por isso, sem dado rico (xA, PPDA, grandes chances...), o edge fica perto de 50
e os grades caem (C/AVOID). É proposital: o grade sobe sozinho quando os dados
chegam, sem mexer em código.

## Como calibrar os pesos (tudo em `weights.py`)

**Nada de número mágico fora de `weights.py`.** Lá ficam:

| O que | Constante | Efeito |
|---|---|---|
| Faixas de normalização (valor bruto → 0–100) | `NORM`, `LIVE_NORM` | desloca a sensibilidade de cada métrica |
| Pesos de cada score | `OFFENSIVE_THREAT`, `DEFENSIVE_FRAGILITY`, … | importância relativa de cada componente (somam ~1.0) |
| Pesos por mercado | `MARKET_OVER`, `MARKET_1X2`, `LIVE_CORNERS`, … | como os scores viram o edge do mercado |
| Limiares de grade | `GRADE_TIERS`, `RISK_HARD_CAP` | quão exigente é cada grade |
| Penalidade de risco na confiança | `CONFIDENCE_RISK_PENALTY` | quanto o risco corrói a confiança |
| Linhas-padrão | `DEFAULT_LINES` | linha usada quando não há projeção |
| Janela de minuto p/ escanteios ao vivo | `LIVE_CORNERS_MINUTE` | quando recomendar cantos in-play |

### Passo a passo recomendado
1. **Ajuste `NORM` primeiro.** Pegue a distribuição real de cada métrica (ex.:
   xG/jogo de seleções) e ponha `(lo, hi)` ~ p10/p90. Faixa errada distorce o
   score antes de qualquer peso.
2. **Depois os pesos do score** (`OFFENSIVE_THREAT`, etc.). Mantenha soma ~1.0.
3. **Por último os pesos de mercado e os `GRADE_TIERS`**, validando contra o
   histórico em `football_pick_results` (ledger imutável de resultados): veja se
   os grades A/A+ realmente acertam mais que B/C.
4. **Rode `pytest`** — os testes (`tests/test_analysis_*.py`) travam o
   comportamento esperado (fallback-50, regressão, escanteios pressionando…).

### Adicionar uma métrica que hoje cai em fallback (ex.: PPDA real)
1. Preencha o campo em `TeamFeatures`/`LiveFeatures` no extractor de `features.py`.
2. Garanta a faixa em `NORM`/`LIVE_NORM`.
3. Pronto — o score passa a usar o dado real; **peso e fórmula não mudam**.

## Dados hoje vs. ausentes

### Agregação de stats por jogo (ligas de CLUBE)
Para o contexto `general` (Premier, Brasileirão, Bundesliga, etc.), o serviço
agrega as estatísticas dos últimos `STATS_AGG_LAST_N` jogos via api-football
`/fixtures/statistics` (`data_service._team_advanced_stats`) e preenche as
features com **dado real**: xG, xGA, finalizações no alvo (feitas/cedidas),
escanteios (a favor/contra), cartões, faltas, posse — e deriva xG por finalização.
Liga/desliga com `ENABLE_STATS_AGGREGATION`. Cache: stats de jogo finalizado são
imutáveis → cache longo e compartilhado entre os dois times; agregado por time
~6h. Degrada gracioso: sem a stat, mantém o fallback-50.

**A Copa do Mundo (`world_cup`) NÃO usa essa agregação** — api-football não cobre
seleções de forma confiável. Lá os scores seguem com gols + forma (resto 50).

### Ainda sem fonte (fallback-50 + warning, em qualquer contexto)
xA por time, passes-chave/progressivos/último terço, grandes chances, toques na
área, PPDA, recuperações altas, erros defensivos, cruzamentos precisos, ataques
perigosos ao vivo, perfil de árbitro, odds live. (Exigiriam Opta/StatsBomb;
understat só cobre clubes europeus e está como stub.)
