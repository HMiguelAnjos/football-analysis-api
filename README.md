# Football Analytics API

Backend de análise de futebol: jogos do dia, estatísticas de times e jogadores,
odds de várias casas, **modelo de probabilidade** (Poisson/Dixon-Coles) e um
**engine de recomendação de valor** que compara a probabilidade do modelo com a
odd da casa e identifica *edge* (valor esperado positivo).

Cobre os principais mercados — 1X2, dupla chance, draw no bet, over/under de
gols, BTTS, handicap asiático, totais de time, escanteios, cartões e player
props (jogador para marcar, chutes, finalizações no alvo, assistências).

> **Provider-agnóstico:** a fonte de dados é abstraída atrás de interfaces.
> Hoje suporta **api-football** (dados) e **The Odds API** (odds), com gancho
> para **understat** (xG) e espaço para **sportmonks** no futuro. Sem chaves de
> API, a aplicação sobe em **modo fixtures** (dados mock) — ótima pra dev/CI.

---

## Stack

- **Python 3.12** + **FastAPI** (`uvicorn[standard]`)
- **Pydantic 2** — contratos públicos (schemas)
- **SQLAlchemy 2** + **Postgres** — auth, recomendações e ledger de resultados
- **JWT** (auth) com roles (admin / analyst / influencer / user)
- Cache **em memória** + **em disco** (`PersistentCache`)
- Deploy: **Docker** (Railway-ready)

---

## Arquitetura

Dependências apontando sempre "pra dentro":

```
HTTP (main.py)
  → services/         (orquestração + cache + I/O)
  → providers/        (fontes externas, trocáveis — abstração por Protocol)
  → probability/      (modelo puro: Poisson, mercados, edge)
  → recommendation/   (engine + settlement — puro)
  → db/ + schemas/    (persistência + contratos)
```

| Camada | Papel |
|---|---|
| `providers/` | Fontes externas isoladas. `base.py` define os modelos de domínio + interfaces (`FootballDataProvider`, `OddsProvider`, `XgProvider`). Implementações: `api_football/`, `odds/` (The Odds API), `understat/`, `fixtures/` (mock offline). `registry.py` escolhe por config com fallback automático. |
| `probability/` | **Puro, sem I/O.** `poisson.py` (matriz de placar Dixon-Coles), `team_strength.py` (gols esperados a partir da forma), `markets.py` (probabilidade de cada mercado), `edge.py` (odd↔prob, EV, confiança). |
| `recommendation/` | `engine.py` (gera recomendações cruzando modelo × odds), `settlement.py` (liquida hit/miss/push). Puro. |
| `services/` | Fachadas cache-first. `football/data_service.py`, `football/generation_service.py`, `recommendation_service.py` (CRUD), `pick_results_service.py` (settlement + performance). |
| `workers/` | `settlement_worker.py` — liquida recomendações pending em background. |
| `db/` | SQLAlchemy. `User` + tabelas `football_*`. |

### Por que provider-agnóstico

Os serviços só falam os **modelos de domínio** (`providers/base.py`), nunca o
payload cru de uma API. Trocar api-football por sportmonks = escrever um novo
provider que devolve os mesmos dataclasses. Zero acoplamento.

### Cache (cache-first, dois níveis)

Os providers externos custam dinheiro / têm cota (api-football free = **100
req/dia**), então caching é central:

| Dado | Onde | TTL | Sobrevive a restart? |
|---|---|---|---|
| Jogos do dia / detalhe do jogo | **disco** (`PersistentCache`) | `MATCHES_CACHE_TTL` (5min) | ✅ |
| Forma do time, stats, standings, ligas, jogadores | **disco** | `STATS_CACHE_TTL` (6h) | ✅ |
| Odds (voláteis) | **memória** (`SimpleCache`) | `ODDS_CACHE_TTL` (2min) | ❌ (refaz na hora) |

O cache em disco vive em `CACHE_DIR` (`football_cache.json`). **Em produção,
aponte `CACHE_DIR` para um volume persistente** (ex.: Railway Volume em
`/data`) — assim o cache sobrevive a redeploy e não re-queima a cota dos
providers. As tabelas `football_{matches,teams,players,odds}` existem para uma
evolução futura de cache **compartilhado entre instâncias** (via Postgres).

### Modo Copa do Mundo (competition context)

O produto tem dois **contextos de competição**, alternáveis: `general` (futebol)
e `world_cup` (Copa do Mundo). A lógica é **centralizada** em
[src/competition.py](src/competition.py) (um registry mapeia contexto → ligas,
season, sport keys, features) — sem `if` espalhado. O mesmo `Match`/serviço é
reusado; campos de torneio (`stage`, `group`, `city`, pênaltis, `winner`) são
opcionais e só preenchidos na Copa.

- **Endpoints gerais** (`/football/*`) = contexto `general`.
- **Endpoints da Copa** (`/football/world-cup/*`) = contexto `world_cup`,
  delegando aos **mesmos serviços** (zero duplicação de lógica). Inclui os
  exclusivos: `/groups` (grupos), `/bracket` (mata-mata) e `GET /football/context`
  (catálogo de contextos).
- **Isolamento no banco:** `football_recommendations` e `football_pick_results`
  têm coluna `context` — picks da Copa **nunca** se misturam com os gerais.
- Na api-football a Copa é a liga `WORLD_CUP_LEAGUE_ID` (=1); o `round` vira
  `stage`/`group`. Odds de partida usam `WORLD_CUP_ODDS_SPORT_KEY`.

> **Pendente (fase 2):** mercados de outright (campeão, classificar, artilheiro)
> e o **frontend** (toggle Futebol/Copa + telas: visão geral, grupos, mata-mata).

### Modelo de probabilidade

1. **Forma → gols esperados** (`team_strength.expected_goals`): força ofensiva/
   defensiva relativa à média da liga, com splits casa/fora, blend de xG (quando
   disponível) e ajuste de descanso.
2. **Gols esperados → matriz de placar** (`poisson.build_score_matrix`):
   Poisson bivariado com correção de **Dixon-Coles** para placares baixos.
3. **Matriz → probabilidade de cada mercado** (`markets.py`).
4. **Probabilidade × odd da casa → edge** (`edge.py`):
   `edge = model_prob × odd − 1` (valor esperado). Recomenda quando
   `edge ≥ MIN_EDGE` e `confidence ≥ MIN_CONFIDENCE`.

---

## Instalação

```bash
python -m venv .venv
source .venv/Scripts/activate          # Windows
# source .venv/bin/activate       # Linux/macOS

pip install -r requirements.txt        # runtime
pip install -r requirements-dev.txt    # + pytest/httpx (testes)
```

---

## Como rodar

### 1. Offline (sem nenhuma chave — modo fixtures)

```bash
# Windows PowerShell
$env:USE_FIXTURES=1; $env:ENABLE_SETTLEMENT_WORKER=0; uvicorn src.main:app --reload
```
```bash
# bash
USE_FIXTURES=1 ENABLE_SETTLEMENT_WORKER=0 uvicorn src.main:app --reload
```

Sobe com 2 jogos mock e odds — dá pra exercitar o engine de ponta a ponta.

### 2. Com Postgres + dados reais

```bash
docker compose -f docker-compose.dev.yml up -d           # sobe Postgres local
cp .env.example .env                                      # preencha as chaves
uvicorn src.main:app --reload
```

- API: `http://localhost:8000`
- Swagger: `http://localhost:8000/docs`

---

## Variáveis de ambiente

Resumo (lista completa e comentada em [`.env.example`](.env.example)):

| Variável | Default | Para que serve |
|---|---|---|
| `USE_FIXTURES` | `0` | `1` força dados mock em todos os providers |
| `FOOTBALL_PROVIDER` | `api_football` | Fonte de dados (`api_football` \| `fixtures`) |
| `API_FOOTBALL_KEY` | — | Chave da api-football (sem ela → fixtures) |
| `API_FOOTBALL_RAPIDAPI` | `0` | `1` se a chave for via RapidAPI |
| `DEFAULT_LEAGUE_IDS` | PL, LaLiga, … | Ligas acompanhadas (IDs api-football) |
| `CURRENT_SEASON` | `2025` | Temporada (ano de início) |
| `WORLD_CUP_LEAGUE_ID` | `1` | ID da Copa do Mundo na api-football |
| `WORLD_CUP_SEASON` | `2026` | Temporada (ano) da Copa |
| `WORLD_CUP_ODDS_SPORT_KEY` | `soccer_fifa_world_cup` | Sport key da Copa no The Odds API |
| `ODDS_PROVIDER` | `the_odds_api` | Fonte de odds (`the_odds_api` \| `fixtures`) |
| `ODDS_API_KEY` | — | Chave da The Odds API (sem ela → fixtures) |
| `ODDS_REGIONS` | `eu` | Região de mercado (`us`/`uk`/`eu`/`au`) |
| `ODDS_SPORT_KEYS` | soccer_* | Ligas no The Odds API |
| `XG_PROVIDER` | `none` | `none` \| `understat` \| `fixtures` |
| `MIN_EDGE` | `0.03` | Edge mínimo (EV) pra recomendar |
| `MIN_ODD` / `MAX_ODD` | `1.30` / `8.0` | Faixa de odd aceitável |
| `MIN_CONFIDENCE` | `40` | Confiança mínima (0-100) |
| `ENABLE_SETTLEMENT_WORKER` | `1` | Liga a liquidação automática |
| `DATABASE_URL` | postgres local | Conexão Postgres |
| `JWT_SECRET` | dev default | **TROQUE em produção** |
| `ADMIN_EMAILS` | — | Emails promovidos a admin no boot (CSV) |
| `RESEND_API_KEY` | — | Email de recuperação de senha (opcional) |
| `CACHE_DIR` | `/tmp` | Cache em disco (use volume em prod) |

---

## Endpoints

Tudo (exceto `/health` e `/auth/*`) exige **JWT** no header
`Authorization: Bearer <token>`.

### Auth
| Rota | Descrição |
|---|---|
| `POST /auth/register` | Cadastro → token |
| `POST /auth/login` | Login → token |
| `GET /auth/me` | Usuário atual |
| `POST /auth/password/forgot` · `POST /auth/password/reset` | Recuperação de senha |

### Football — dados
| Rota | Descrição |
|---|---|
| `GET /football/matches/today?date=YYYY-MM-DD` | Jogos do dia |
| `GET /football/matches/{id}` | Detalhe do jogo |
| `GET /football/matches/{id}/statistics` | Estatísticas do jogo |
| `GET /football/matches/{id}/odds` | Odds normalizadas por mercado |
| `GET /football/matches/{id}/lineups` | Escalações |
| `GET /football/leagues` | Ligas acompanhadas |
| `GET /football/leagues/{id}/standings?season=` | Classificação |
| `GET /football/teams/{id}` | Time |
| `GET /football/players/{id}` | Jogador (stats da temporada) |

### Football — recomendações
| Rota | Permissão | Descrição |
|---|---|---|
| `GET /football/recommendations` | autenticado | Feed de recomendações ativas |
| `GET /football/recommendations/live` | autenticado | Pending de jogos já iniciados (por edge) |
| `GET /football/recommendations/{id}` | autenticado | Detalhe |
| `POST /football/recommendations/generate` | admin/analyst | Roda o engine e persiste |
| `POST /football/recommendations` | admin/analyst | Entrada manual de analista |
| `PATCH /football/recommendations/{id}/deactivate` | admin/analyst | Soft delete |
| `GET /football/pick-results?only_shown=` | autenticado | Performance (accuracy + ROI por mercado) |

### Admin
| Rota | Permissão | Descrição |
|---|---|---|
| `GET /admin/users` · `PATCH /admin/users/{id}` | admin | Gestão de usuários |
| `GET /admin/performance` | admin/analyst | Performance detalhada |

### Modelo de recomendação (payload)

```jsonc
{
  "id": 12, "match_id": 1001, "league": "Premier League",
  "home_team": "Liverpool", "away_team": "Manchester City",
  "market": "1x2", "selection": "home", "line": null, "bookmaker": "Bet365",
  "odd": 2.10, "fair_odd": 1.80,
  "implied_probability": 0.476, "model_probability": 0.555,
  "edge": 0.16, "confidence_score": 62.0,
  "recommendation_reason": "Modelo estima 55.5% ...",
  "source": "engine", "status": "pending", "was_shown_to_user": true,
  "actual_result": null, "generated_at": "2026-06-14T...", "settled_at": null
}
```

`status`: `pending` → `hit` | `miss` | `push` | `void` (liquidado pelo worker).

---

## Exemplos de chamadas

```bash
# 1) Cadastro + login
curl -s -X POST localhost:8000/auth/register \
  -H 'content-type: application/json' \
  -d '{"email":"voce@email.com","password":"senha123","name":"Você"}'

TOKEN=$(curl -s -X POST localhost:8000/auth/login \
  -H 'content-type: application/json' \
  -d '{"email":"voce@email.com","password":"senha123"}' | jq -r .access_token)

# 2) Jogos do dia
curl -s localhost:8000/football/matches/today -H "Authorization: Bearer $TOKEN" | jq

# 3) Odds de um jogo
curl -s localhost:8000/football/matches/1001/odds -H "Authorization: Bearer $TOKEN" | jq

# 4) Gerar recomendações (precisa role admin/analyst)
curl -s -X POST localhost:8000/football/recommendations/generate \
  -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"date":"2026-06-14","persist":true}' | jq

# 5) Feed de recomendações
curl -s localhost:8000/football/recommendations -H "Authorization: Bearer $TOKEN" | jq

# 6) Performance
curl -s localhost:8000/football/pick-results -H "Authorization: Bearer $TOKEN" | jq
```

> Para virar admin: cadastre-se, coloque seu email em `ADMIN_EMAILS` e
> reinicie a API (a promoção roda no boot).

---

## Testes

```bash
pytest -q
```

Cobre o modelo de probabilidade (Poisson, mercados, edge), o settlement, o
engine de recomendação, a normalização dos providers, a persistência (SQLite
em memória) e um smoke test da app. Tudo roda **offline** (modo fixtures).

---

## Deploy (Railway)

1. `Dockerfile` (Python 3.12-slim) instala `requirements.txt`, copia `src/` e
   sobe `uvicorn src.main:app --host 0.0.0.0 --port ${PORT}`.
2. Configure as env vars (mínimo: `DATABASE_URL`, `JWT_SECRET`, e as chaves de
   provider — `API_FOOTBALL_KEY`, `ODDS_API_KEY`).
3. Monte um **volume persistente** e set `CACHE_DIR` pra ele.
4. CORS já aceita `*.railway.app` e `*.vercel.app` via regex.

---

## Próximos passos recomendados

1. **Calibrar o modelo** com resultados reais (ajustar `rho`, pesos de xG,
   `MIN_EDGE`) usando o ledger `football_pick_results`.
2. **Provider de xG** — implementar a coleta do understat (gancho pronto em
   `providers/understat/`).
3. **Player props com dados granulares** — escalação provável + minutos
   esperados melhoram bastante anytime-scorer / chutes.
4. **Cache compartilhado (Redis)** para múltiplas instâncias.
5. **Sportmonks** como provider alternativo (interface já pronta).
6. **Mercados de escanteios/cartões** ligados às médias reais por time da
   api-football (hoje o modelo está pronto; falta plugar a fonte).
```
