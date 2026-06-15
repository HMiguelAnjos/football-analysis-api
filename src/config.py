"""
Configuração central da Football Analytics API.

Tudo lido de env vars com defaults seguros pra dev local. Os providers
externos (api-football, the-odds-api, understat) são OPCIONAIS: sem chaves,
o sistema cai automaticamente no modo fixtures (dados mock em disco) e a API
sobe sem erro — útil pra dev e CI offline.
"""

import os

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------
_raw = os.getenv(
    "ALLOWED_ORIGINS",
    "http://localhost:5173,http://localhost:5174,http://localhost:3000,null,*",
)
if _raw.strip() == "*":
    ALLOWED_ORIGINS: list[str] = ["*"]
else:
    ALLOWED_ORIGINS = [o.strip() for o in _raw.split(",") if o.strip()]

# ---------------------------------------------------------------------------
# Servidor
# ---------------------------------------------------------------------------
PORT: int = int(os.getenv("PORT", "8000"))
HOST: str = os.getenv("HOST", "127.0.0.1")


def _flag(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------
# Modo fixtures (offline) — sobe sem chaves de API
# ---------------------------------------------------------------------------
# USE_FIXTURES=1 força TODOS os providers a lerem de src/providers/fixtures/data.
# Default OFF, mas cada provider individual também cai em fixtures se faltar a
# chave correspondente (degradação graciosa, nunca derruba o boot).
USE_FIXTURES: bool = _flag("USE_FIXTURES", "0")

# ---------------------------------------------------------------------------
# Seleção de providers
# ---------------------------------------------------------------------------
# Provider de DADOS (jogos, times, jogadores, stats, lineups, standings).
#   "api_football" | "fixtures"
FOOTBALL_PROVIDER: str = os.getenv("FOOTBALL_PROVIDER", "api_football").strip().lower()
# Provider de ODDS.  "the_odds_api" | "fixtures"
ODDS_PROVIDER: str = os.getenv("ODDS_PROVIDER", "the_odds_api").strip().lower()
# Provider de métricas avançadas (xG/xA).  "understat" | "none" | "fixtures"
XG_PROVIDER: str = os.getenv("XG_PROVIDER", "none").strip().lower()

# ---------------------------------------------------------------------------
# api-football (https://www.api-football.com) — jogos, stats, lineups, standings
# ---------------------------------------------------------------------------
# Suporta os dois modos de hospedagem do provider:
#   • Direto (dashboard.api-football.com): base v3.football.api-sports.io,
#     header `x-apisports-key`.
#   • Via RapidAPI: base api-football-v1.p.rapidapi.com/v3, headers
#     `x-rapidapi-key` + `x-rapidapi-host`. Setar API_FOOTBALL_RAPIDAPI=1.
API_FOOTBALL_KEY: str | None = os.getenv("API_FOOTBALL_KEY") or None
API_FOOTBALL_RAPIDAPI: bool = _flag("API_FOOTBALL_RAPIDAPI", "0")
API_FOOTBALL_BASE_URL: str = os.getenv(
    "API_FOOTBALL_BASE_URL",
    "https://api-football-v1.p.rapidapi.com/v3"
    if API_FOOTBALL_RAPIDAPI
    else "https://v3.football.api-sports.io",
)
API_FOOTBALL_HOST: str = os.getenv(
    "API_FOOTBALL_HOST", "api-football-v1.p.rapidapi.com"
)

# Ligas acompanhadas por padrão (IDs da api-football). Default: principais
# ligas europeias + Brasileirão. CSV de inteiros.
#   39=Premier League, 140=La Liga, 135=Serie A, 78=Bundesliga,
#   61=Ligue 1, 71=Brasileirão Série A, 2=Champions League.
_leagues_raw = os.getenv("DEFAULT_LEAGUE_IDS", "39,140,135,78,61,71,2")
DEFAULT_LEAGUE_IDS: list[int] = [
    int(x) for x in _leagues_raw.split(",") if x.strip().isdigit()
]
# Temporada corrente (ano de início). Ex: 2025 = temporada 2025/26.
CURRENT_SEASON: int = int(os.getenv("CURRENT_SEASON", "2025"))

# ---------------------------------------------------------------------------
# Modo Copa do Mundo (competition context = "world_cup")
# ---------------------------------------------------------------------------
# Na api-football, a Copa do Mundo é a liga de id 1. Temporada = ano do torneio.
WORLD_CUP_LEAGUE_ID: int = int(os.getenv("WORLD_CUP_LEAGUE_ID", "1"))
WORLD_CUP_SEASON: int = int(os.getenv("WORLD_CUP_SEASON", "2026"))
# Sport key da Copa no The Odds API (odds de partida).
WORLD_CUP_ODDS_SPORT_KEY: str = os.getenv(
    "WORLD_CUP_ODDS_SPORT_KEY", "soccer_fifa_world_cup"
)
# Provider de DADOS da Copa: "openfootball" (grátis, sem chave) | "api_football"
# | "fixtures". Default openfootball — Copa não depende da api-football paga.
WORLD_CUP_PROVIDER: str = os.getenv("WORLD_CUP_PROVIDER", "openfootball").strip().lower()
# Base dos JSONs públicos do openfootball (domínio público, sem chave).
OPENFOOTBALL_BASE_URL: str = os.getenv(
    "OPENFOOTBALL_BASE_URL",
    "https://raw.githubusercontent.com/openfootball/worldcup.json/master",
)

# ---------------------------------------------------------------------------
# The Odds API — odds de futebol
# ---------------------------------------------------------------------------
ODDS_API_KEY: str | None = os.getenv("ODDS_API_KEY") or None
# Região (us | uk | eu | au). EU tem mais cobertura de mercados de futebol.
ODDS_REGIONS: str = os.getenv("ODDS_REGIONS", "eu")
# CSV de bookmakers específicos. Vazio = todos do region (média mais robusta).
ODDS_BOOKMAKERS: str = os.getenv("ODDS_BOOKMAKERS", "")
# Sport keys do The Odds API acompanhadas. CSV.
#   soccer_epl, soccer_spain_la_liga, soccer_italy_serie_a,
#   soccer_germany_bundesliga, soccer_france_ligue_one,
#   soccer_brazil_campeonato, soccer_uefa_champs_league
_sk = os.getenv(
    "ODDS_SPORT_KEYS",
    "soccer_epl,soccer_spain_la_liga,soccer_italy_serie_a,"
    "soccer_germany_bundesliga,soccer_france_ligue_one,"
    "soccer_brazil_campeonato,soccer_uefa_champs_league",
)
ODDS_SPORT_KEYS: list[str] = [s.strip() for s in _sk.split(",") if s.strip()]

# ---------------------------------------------------------------------------
# Understat (xG/xA) — opcional, scraping público
# ---------------------------------------------------------------------------
UNDERSTAT_BASE_URL: str = os.getenv("UNDERSTAT_BASE_URL", "https://understat.com")

# ---------------------------------------------------------------------------
# Engine de recomendação
# ---------------------------------------------------------------------------
# Edge mínimo (EV) pra uma seleção virar recomendação. 0.03 = +3% de valor
# esperado sobre a odd da casa. Calibrável por env sem redeploy.
MIN_EDGE: float = float(os.getenv("MIN_EDGE", "0.03"))
# Odd mínima/máxima aceitável (evita favoritos extremos e zebras improváveis).
MIN_ODD: float = float(os.getenv("MIN_ODD", "1.30"))
MAX_ODD: float = float(os.getenv("MAX_ODD", "8.0"))
# Confiança mínima (0-100) pra recomendar.
MIN_CONFIDENCE: float = float(os.getenv("MIN_CONFIDENCE", "40"))

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
CACHE_DIR: str = os.getenv("CACHE_DIR", "/tmp")
# TTL (segundos) do cache de jogos do dia / stats. Dados de futebol mudam
# devagar fora do jogo ao vivo.
MATCHES_CACHE_TTL: int = int(os.getenv("MATCHES_CACHE_TTL", "900"))      # 15 min
STATS_CACHE_TTL: int = int(os.getenv("STATS_CACHE_TTL", str(6 * 3600)))  # 6 h
ODDS_CACHE_TTL: int = int(os.getenv("ODDS_CACHE_TTL", "120"))
# Catálogos quase estáticos (ligas) — cache longo, economiza muita chamada.
CATALOG_CACHE_TTL: int = int(os.getenv("CATALOG_CACHE_TTL", str(24 * 3600)))

# ---------------------------------------------------------------------------
# Worker de settlement (fecha recomendações como hit/miss/push)
# ---------------------------------------------------------------------------
ENABLE_SETTLEMENT_WORKER: bool = _flag("ENABLE_SETTLEMENT_WORKER", "1")
SETTLEMENT_INTERVAL_SECONDS: int = int(
    os.getenv("SETTLEMENT_INTERVAL_SECONDS", str(15 * 60))
)

# ---------------------------------------------------------------------------
# Banco de dados + Auth
# ---------------------------------------------------------------------------
DATABASE_URL: str = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg2://football:football@localhost:5432/football",
)
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg2://", 1)

IS_RAILWAY: bool = any(k.startswith("RAILWAY_") for k in os.environ)

_JWT_SECRET_DEFAULT = "dev-secret-change-me-in-prod"
JWT_SECRET: str = os.getenv("JWT_SECRET", _JWT_SECRET_DEFAULT)
JWT_SECRET_IS_DEFAULT: bool = JWT_SECRET == _JWT_SECRET_DEFAULT
JWT_ALGORITHM: str = os.getenv("JWT_ALGORITHM", "HS256")
JWT_EXPIRE_MINUTES: int = int(os.getenv("JWT_EXPIRE_MINUTES", str(60 * 24 * 7)))

# Exige JWT em TODAS as rotas (exceto /health e /auth/*). Desligue só em dev
# (REQUIRE_AUTH=0) pra testar os endpoints sem login. NUNCA desligue em
# produção — as rotas de dados ficam totalmente abertas.
REQUIRE_AUTH: bool = _flag("REQUIRE_AUTH", "1")

PASSWORD_RESET_EXPIRE_MINUTES: int = int(
    os.getenv("PASSWORD_RESET_EXPIRE_MINUTES", "60")
)

RESEND_API_KEY: str | None = os.getenv("RESEND_API_KEY") or None
RESEND_FROM_EMAIL: str = os.getenv("RESEND_FROM_EMAIL", "onboarding@resend.dev")
FRONTEND_URL: str = os.getenv("FRONTEND_URL", "http://localhost:5173").rstrip("/")

_admin_raw = os.getenv("ADMIN_EMAILS", "")
ADMIN_EMAILS: list[str] = [
    e.strip().lower() for e in _admin_raw.split(",") if e.strip()
]
