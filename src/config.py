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
# ligas europeias + competições brasileiras + continentais. CSV de inteiros.
#   39=Premier League, 140=La Liga, 135=Serie A, 78=Bundesliga,
#   61=Ligue 1, 71=Brasileirão Série A, 2=Champions League,
#   72=Brasileirão Série B, 13=CONMEBOL Libertadores, 73=Copa do Brasil.
# 13 e 73 são COPAS (poucos jogos por time na competição → a forma pode ficar
# rasa; o modelo cai em médias da liga quando a amostra é pequena).
_leagues_raw = os.getenv("DEFAULT_LEAGUE_IDS", "39,140,135,78,61,71,2,72,13,73")
DEFAULT_LEAGUE_IDS: list[int] = [
    int(x) for x in _leagues_raw.split(",") if x.strip().isdigit()
]
# Temporada corrente (ano de início). Ex: 2025 = temporada 2025/26.
CURRENT_SEASON: int = int(os.getenv("CURRENT_SEASON", "2025"))

# (Removido jul/2026) O modo Copa do Mundo e o provider openfootball foram
# retirados — o produto voltou a ser 100% baseado em ligas regulares.

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
# Understat fica atrás de Cloudflare → scrape direto é bloqueado. Com uma chave
# do ScraperAPI (que o usuário já tem), as páginas são buscadas via proxy que
# fura o Cloudflare. Sem a chave, o Understat fica indisponível (degrada).
SCRAPER_API_KEY: str | None = os.getenv("SCRAPER_API_KEY") or None

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
# Teto de edge: acima disso é quase certamente ERRO do modelo (não valor real),
# então filtramos. Mercado de futebol raramente tem edge real > ~15%.
MAX_EDGE: float = float(os.getenv("MAX_EDGE", "0.15"))
# Peso do MODELO no blend com o mercado (0=só mercado, 1=só modelo). O mercado
# de odds é eficiente; o modelo entra como um ajuste, não pra sobrepor.
MODEL_MARKET_BLEND: float = float(os.getenv("MODEL_MARKET_BLEND", "0.40"))
# Probabilidade MÍNIMA pra virar recomendação (confiança > valor puro). O
# produto prioriza o que é PROVÁVEL de acontecer — não azarão com valor magro
# (ex.: não recomenda "Senegal vencer" a 20% só porque a odd está alta). 0.55 =
# o modelo precisa realmente favorecer a seleção. Calibrável por env.
MIN_PICK_PROB: float = float(os.getenv("MIN_PICK_PROB", "0.60"))
# Prob mínima pro "jogador pode marcar" AO VIVO. Gol é evento raro — mesmo o
# melhor atacante fica < 60% num jogo, então o piso aqui é menor.
LIVE_GOAL_MIN_PROB: float = float(os.getenv("LIVE_GOAL_MIN_PROB", "0.33"))
# Cache curto (memória) dos feeds AO VIVO. Troca de aba / auto-refresh
# reaproveita o cálculo recente em vez de re-bater no provider toda vez.
LIVE_FEED_TTL: int = int(os.getenv("LIVE_FEED_TTL", "15"))

# ---------------------------------------------------------------------------
# Ratings de força de seleção (priors do modelo em torneios)
# ---------------------------------------------------------------------------
# Em torneios (Copa), a amostra de jogos do próprio torneio é minúscula (0-2),
# então o modelo regride tudo à média e subvaloriza favoritos. Solução: derivar
# ratings ataque/defesa AJUSTADOS POR ADVERSÁRIO do histórico recente de cada
# seleção (qualifiers, amistosos, Nations League) via api-football.
# Nº de jogos recentes (todas as competições) buscados por seleção.
RATINGS_RECENT_N: int = int(os.getenv("RATINGS_RECENT_N", "15"))
# Iterações do ponto-fixo ataque/defesa (converge rápido).
RATINGS_ITERATIONS: int = int(os.getenv("RATINGS_ITERATIONS", "12"))
# Regressão à média dos ratings (0..1). Times "ganham" defesas/ataques extremos
# contra adversários fracos em eliminatórias/amistosos, o que derrubava o total
# de gols (Under 2.5 em todo jogo). 0.55 puxa ~45% rumo à média → totais sãos.
RATINGS_SHRINK: float = float(os.getenv("RATINGS_SHRINK", "0.55"))
# Vantagem de casa da LIGA: mandante marca ~12% mais (lambda_home ×, lambda_away
# ÷). 1.0 = neutro. Antes era 1.0 (herança da Copa em sede neutra), o que ZERAVA
# a vantagem de casa nas ligas — corrigido jul/2026.
LEAGUE_HOME_ADV: float = float(os.getenv("LEAGUE_HOME_ADV", "1.12"))

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
CACHE_DIR: str = os.getenv("CACHE_DIR", "/tmp")
# TTL (segundos) do cache de jogos do dia / stats. Dados de futebol mudam
# devagar fora do jogo ao vivo.
MATCHES_CACHE_TTL: int = int(os.getenv("MATCHES_CACHE_TTL", "900"))      # 15 min
# Fixtures de datas FUTURAS não têm jogo ao vivo → cache longo (a grade muda
# pouco: horário e, raramente, adiamento). Hoje/passado seguem no TTL curto
# acima pra o placar ao vivo não ficar velho. Corta as buscas repetidas da
# janela de 3 dias (feed pré-jogo) no gasto da api-football.
FIXTURES_FUTURE_TTL: int = int(os.getenv("FIXTURES_FUTURE_TTL", str(6 * 3600)))  # 6 h
STATS_CACHE_TTL: int = int(os.getenv("STATS_CACHE_TTL", str(6 * 3600)))  # 6 h
# TTL do 1x2 inline em lote (sport_odds bulk, barato).
ODDS_CACHE_TTL: int = int(os.getenv("ODDS_CACHE_TTL", "120"))
# TTL das odds POR JOGO (event_odds, caro: markets×regions por jogo). Pré-jogo
# elas andam devagar → TTL longo economiza muito. Ao vivo, o worker invalida
# por EVENTO (gol); o TTL_LIVE é só fallback quando nada acontece.
ODDS_PREMATCH_TTL: int = int(os.getenv("ODDS_PREMATCH_TTL", "900"))      # 15 min
ODDS_LIVE_TTL: int = int(os.getenv("ODDS_LIVE_TTL", "600"))             # 10 min

# ---------------------------------------------------------------------------
# Worker de odds ao vivo (refresh por evento)
# ---------------------------------------------------------------------------
# Detecta gols via 1 chamada agregada barata (fixtures?live=all) e só então
# reaquece as odds do jogo — em vez de re-buscar tudo a cada N minutos.
ENABLE_LIVE_ODDS_WORKER: bool = _flag("ENABLE_LIVE_ODDS_WORKER", "0")
LIVE_ODDS_POLL_SECONDS: int = int(os.getenv("LIVE_ODDS_POLL_SECONDS", "90"))
# Worker de recomendações AO VIVO (foco escanteios) — gera/persiste no banco.
# Precisa de Postgres pra persistir.
ENABLE_LIVE_RECO_WORKER: bool = _flag("ENABLE_LIVE_RECO_WORKER", "0")
LIVE_RECO_POLL_SECONDS: int = int(os.getenv("LIVE_RECO_POLL_SECONDS", "120"))
# Worker que GERA e PERSISTE as recomendações pré-jogo (confidence-first, sem
# odds) periodicamente — sem ele, nada é salvo no banco automaticamente e não
# há o que liquidar/validar. Precisa de Postgres.
ENABLE_GENERATION_WORKER: bool = _flag("ENABLE_GENERATION_WORKER", "0")
GENERATION_INTERVAL_SECONDS: int = int(os.getenv("GENERATION_INTERVAL_SECONDS", "1800"))
# Janela após o kickoff em que um jogo é considerado "possivelmente ao vivo".
# Fora dela o worker nem chama a api-football (economia off-hours).
LIVE_WINDOW_HOURS: float = float(os.getenv("LIVE_WINDOW_HOURS", "3.0"))
# Fallback: reaquece a odd do jogo ao vivo a cada N segundos MESMO sem evento
# (gol/expulsão). 600 = 10 min. Garante odd nunca mais velha que isso.
LIVE_ODDS_FALLBACK_SECONDS: int = int(os.getenv("LIVE_ODDS_FALLBACK_SECONDS", "600"))
# TETO DE GASTO: máximo de jogos cujas odds são reaquecidas por tick (backstop
# pra evitar pico de chamadas na The Odds API se muita coisa mudar de uma vez).
LIVE_ODDS_MAX_REFRESH_PER_TICK: int = int(os.getenv("LIVE_ODDS_MAX_REFRESH_PER_TICK", "8"))
# Catálogos quase estáticos (ligas) — cache longo, economiza muita chamada.
CATALOG_CACHE_TTL: int = int(os.getenv("CATALOG_CACHE_TTL", str(24 * 3600)))

# ── Agregação de estatísticas por jogo (xG/finalizações/escanteios/cartões) ──
# Liga os scores avançados da engine de análise com DADO REAL, agregando as
# stats dos últimos N jogos da liga (api-football /fixtures/statistics).
# Degrada gracioso: sem stat suficiente, cai no fallback neutro (score 50).
ENABLE_STATS_AGGREGATION: bool = _flag("ENABLE_STATS_AGGREGATION", "1")
STATS_AGG_LAST_N: int = int(os.getenv("STATS_AGG_LAST_N", "10"))
# Cartões reais por jogo via /fixtures/events (a estatística agregada não traz
# amarelos). +1 chamada por jogo na agregação (cacheada). Pôr 0 pra economizar.
ENABLE_CARDS_FROM_EVENTS: bool = _flag("ENABLE_CARDS_FROM_EVENTS", "1")
# Agregado por time: cache médio (forma muda devagar). Stats de UM jogo
# finalizado nunca mudam → cache bem longo (compartilhado entre os dois times).
STATS_AGG_TTL: int = int(os.getenv("STATS_AGG_TTL", str(6 * 3600)))
STATS_MATCH_TTL: int = int(os.getenv("STATS_MATCH_TTL", str(7 * 24 * 3600)))

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
