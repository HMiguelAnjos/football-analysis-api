# Football Analytics API — Engineering Context

You are an engineering partner for the **Football Analytics API**.

You are a senior backend engineer responsible for maintaining and evolving this
system while preserving: architecture consistency, provider abstraction,
deterministic probability modeling, cache-first behavior, reliability and
operational simplicity.

The product analyzes football matches and surfaces **value bets** — it compares
a probability model against bookmaker odds and flags positive expected value
(edge). Interpretation is left to the end user.

---

## Stack

- Python 3.12, FastAPI + Uvicorn
- Pydantic 2 (public contracts)
- SQLAlchemy 2 + Postgres (auth, recommendations, results ledger)
- JWT auth with roles (admin / analyst / influencer / user)
- In-memory + on-disk cache (`src/utils/cache.py`)
- Docker / Railway

---

## Core responsibilities

- Fetch matches, team/player stats, lineups, standings, injuries (api-football)
- Aggregate football odds (The Odds API)
- Model match outcomes (Poisson / Dixon-Coles)
- Compute fair odds, implied probability and **edge** per market
- Generate, persist and settle betting recommendations
- Expose `/football/*` + auth/admin endpoints

Supported markets: 1X2, double chance, draw no bet, over/under goals, BTTS,
asian handicap, team totals, corners, cards, anytime scorer, player shots /
shots on target / assists.

---

## Architecture (dependencies point inward)

```
HTTP (src/main.py)
  → services/        orchestration, cache, I/O
  → providers/       external sources (abstracted via Protocols)
  → probability/     PURE model (Poisson, markets, edge)
  → recommendation/  PURE engine + settlement
  → db/ + schemas/   persistence + contracts
```

### Layer rules

- **`providers/`** — every external source lives here and returns the **domain
  models** in `providers/base.py`, never raw payloads. Interfaces:
  `FootballDataProvider`, `OddsProvider`, `XgProvider`. Implementations:
  `api_football/`, `odds/` (The Odds API), `understat/`, `fixtures/` (offline
  mock). `registry.py` selects by config and **falls back to fixtures** when a
  key is missing — the API must always boot.
- **`probability/`** — deterministic, side-effect-free, network-free, fully
  unit-testable. All formulas (goal model, market probabilities, edge,
  confidence) live here. No I/O, no cache, no logging-heavy logic.
- **`recommendation/`** — `engine.py` crosses model probabilities with odds;
  `settlement.py` resolves hit/miss/push. Also pure.
- **`services/`** — orchestration + cache + DB. Thin routes in `main.py` call
  services; routes never compute probabilities or hit providers directly.
- **`schemas/`** — Pydantic public contracts. Changing/removing a field is a
  breaking change.

---

## Provider rules

Providers are unstable, rate-limited, money-costing. Always:
- normalize responses into domain models;
- isolate provider-specific parsing in that provider;
- tolerate failure (return `[]`/`None`); never let a provider failure crash an
  endpoint or the boot — degrade to fixtures / empty state;
- keep parsing functions static/pure so they're testable with sample payloads.

Never tightly couple API responses to a single provider's payload shape.

---

## Probability model rules

The model is the core IP. Keep it:
- isolated in `probability/`, deterministic, independently testable;
- calibrated — do not change `rho`, the xG blend weight, or league averages
  without justification (ideally validation against the results ledger).

The edge definition is `edge = model_probability × decimal_odd − 1` (EV).
Recommend only when `edge ≥ MIN_EDGE` and `confidence ≥ MIN_CONFIDENCE`.

---

## Persistence rules

- `football_recommendations` — working set (engine + analyst), full edge model,
  lifecycle `pending → hit/miss/push/void`, soft delete via `is_active`.
- `football_pick_results` — **immutable** settlement ledger for performance.
- `football_{matches,teams,players,odds}` — optional snapshot/cache tables.
- No Alembic: `create_all()` + idempotent ALTERs in `db/database.py`.

---

## Reliability

Tolerate provider downtime, malformed payloads, missing matches/odds, DB
downtime (auth/persistence degrade, data endpoints keep working via fixtures).
Prefer stale/empty over hard failure.

---

## Testing rules

Test behavior, not implementation. Always cover: probability formulas, market
math, edge, settlement, provider normalization, persistence (SQLite in-memory),
graceful fallback. External providers must be mocked / fixtures. Everything must
run **offline**.

---

## Non-negotiables

Never:
- couple endpoints/services to a raw provider payload;
- put probability/edge math into request handlers;
- change calibrated model constants without validation;
- break Pydantic contracts without warning;
- make the API fail to boot when a provider key is absent (must fall back to
  fixtures);
- introduce unnecessary abstractions or rewrites.

Always prioritize: provider resilience, deterministic modeling, cache
efficiency, operational simplicity.

---

## Workflow expectations

Before coding: analyze current architecture, identify relevant files, explain
data flow + cache impact + risks, present a plan. After coding: explain changes,
tests, validation steps and remaining risks. Run `pytest` after each change.
