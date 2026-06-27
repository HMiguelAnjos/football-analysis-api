"""Football Analytics API — entrypoint FastAPI."""

import logging
import sys as _sys
from contextlib import asynccontextmanager
from types import SimpleNamespace

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware

from src import config
from src.config import (
    ALLOWED_ORIGINS,
    ENABLE_SETTLEMENT_WORKER,
    IS_RAILWAY,
    PORT,
)
from src.db.database import get_db
from src.schemas.auth_schemas import (
    ForgotPasswordRequest,
    GenericMessage,
    LoginRequest,
    ResetPasswordConfirm,
    Token,
    UserAdminUpdate,
    UserCreate,
    UserOut,
)
from src import competition
from src.schemas.football_schemas import (
    BracketStageSchema,
    ContextSchema,
    GenerateRequest,
    GroupSchema,
    LeagueSchema,
    LivePickCreate,
    LivePickOut,
    LivePickUpdate,
    LiveRecoOut,
    MarketLineSchema,
    MatchListResponse,
    MatchOddsSchema,
    MatchSchema,
    MatchStatisticsSchema,
    OddsBoardItemSchema,
    PerformanceSummary,
    PickResultOut,
    PlayerSchema,
    RecommendationOut,
    TeamSchema,
)
from src.services import auth_service
from src.services import recommendation_service as rec_svc
from src.services.football import front_mappers
from src.services.football.data_service import FootballDataService
from src.services.football.generation_service import GenerationService
from src.services.permissions import (
    MANAGE_RECOMMENDATIONS,
    VIEW_PERFORMANCE,
    VIEW_USERS,
    has_permission,
)
from src.services.rate_limit import rate_limit

# ── Logging: INFO/WARNING → stdout, ERROR+ → stderr (preserva split p/ cloud)─
_log_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
_stdout_handler = logging.StreamHandler(_sys.stdout)
_stdout_handler.setLevel(logging.INFO)
_stdout_handler.addFilter(lambda r: r.levelno < logging.ERROR)
_stdout_handler.setFormatter(_log_formatter)
_stderr_handler = logging.StreamHandler(_sys.stderr)
_stderr_handler.setLevel(logging.ERROR)
_stderr_handler.setFormatter(_log_formatter)
_root = logging.getLogger()
_root.setLevel(logging.INFO)
for _h in list(_root.handlers):
    _root.removeHandler(_h)
_root.addHandler(_stdout_handler)
_root.addHandler(_stderr_handler)
logger = logging.getLogger(__name__)

for _uv in ("uvicorn", "uvicorn.error", "uvicorn.access"):
    _ul = logging.getLogger(_uv)
    _ul.handlers.clear()
    _ul.propagate = True

# ── Instâncias compartilhadas ───────────────────────────────────────────────
data_service = FootballDataService()
generation_service = GenerationService(data_service)


# ── Lifespan ────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Football Analytics API ready - port %d", PORT)
    if config.JWT_SECRET_IS_DEFAULT:
        logger.error(
            "[SEGURANCA] JWT_SECRET está no DEFAULT público%s — defina JWT_SECRET já.",
            " EM PRODUÇÃO (Railway)" if IS_RAILWAY else "",
        )
    from src.db.database import init_db
    if init_db():
        if config.ADMIN_EMAILS:
            try:
                from src.db.database import SessionLocal
                _db = SessionLocal()
                try:
                    n = auth_service.promote_admins_by_email(_db, config.ADMIN_EMAILS)
                    if n:
                        logger.info("bootstrap: %d admin(s) promovido(s)", n)
                finally:
                    _db.close()
            except Exception as exc:  # noqa: BLE001
                logger.warning("bootstrap admin falhou (%s)", exc)

    if ENABLE_SETTLEMENT_WORKER:
        try:
            from src.workers.settlement_worker import start_settlement_worker
            await start_settlement_worker()
        except Exception as exc:  # noqa: BLE001
            logger.warning("settlement worker não subiu (%s)", exc)

    if config.ENABLE_LIVE_ODDS_WORKER:
        try:
            from src.workers.live_odds_worker import start_live_odds_worker
            await start_live_odds_worker()
        except Exception as exc:  # noqa: BLE001
            logger.warning("live odds worker não subiu (%s)", exc)

    if config.ENABLE_LIVE_RECO_WORKER:
        try:
            from src.workers.live_reco_worker import start_live_reco_worker
            await start_live_reco_worker()
        except Exception as exc:  # noqa: BLE001
            logger.warning("live reco worker não subiu (%s)", exc)
    yield


app = FastAPI(
    title="Football Analytics API",
    description="Análise de futebol: jogos, estatísticas, odds e recomendações de valor.",
    version="1.0.0",
    lifespan=lifespan,
)

# ── Auth gate global (tudo atrás de login, exceto whitelist pública) ─────────
_PUBLIC_PREFIXES = ("/health", "/auth/", "/docs", "/redoc", "/openapi.json")


def _is_public_path(method: str, path: str) -> bool:
    return method == "OPTIONS" or path == "/" or path.startswith(_PUBLIC_PREFIXES)


async def _require_auth_dispatch(request: Request, call_next):
    if _is_public_path(request.method, request.url.path):
        return await call_next(request)
    header = request.headers.get("authorization", "")
    token = header[7:].strip() if header[:7].lower() == "bearer " else ""
    if not token or auth_service.decode_token(token) is None:
        return JSONResponse(status_code=401, content={"detail": "Autenticação necessária"})
    return await call_next(request)


if config.REQUIRE_AUTH:
    app.add_middleware(BaseHTTPMiddleware, dispatch=_require_auth_dispatch)
else:
    logger.warning(
        "[DEV] REQUIRE_AUTH=0 — rotas SEM login. Use só em desenvolvimento."
    )
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_origin_regex=(
        r"https://.*\.(up\.)?railway\.app"
        r"|https://.*\.vercel\.app"
    ),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(StarletteHTTPException)
async def _sanitize_http_exception(request: Request, exc: StarletteHTTPException):
    if exc.status_code >= 500:
        logger.warning("HTTP %s em %s: %s", exc.status_code, request.url.path, exc.detail)
        detail = "Erro interno. Tente novamente." if IS_RAILWAY else exc.detail
    else:
        detail = exc.detail
    return JSONResponse(
        status_code=exc.status_code, content={"detail": detail},
        headers=getattr(exc, "headers", None),
    )


# ── Auth dependencies ───────────────────────────────────────────────────────
# auto_error=False: header ausente vira creds=None (sem 403 automático) — assim
# quando REQUIRE_AUTH=0 conseguimos liberar a rota sem o bearer barrar antes.
_bearer = HTTPBearer(auto_error=False)

# Usuário sintético usado quando REQUIRE_AUTH=0 (dev) — admin pra liberar tudo.
_SYSTEM_USER = SimpleNamespace(
    id=0, name="system", email="system@local", role="admin", plan="free",
    is_active=True,
)


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
):
    if not config.REQUIRE_AUTH:
        return _SYSTEM_USER
    if creds is None:
        raise HTTPException(status_code=401, detail="Autenticação necessária")
    user_id = auth_service.decode_token(creds.credentials)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Token inválido ou expirado")
    user = auth_service.get_user_by_id(db, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="Usuário inválido")
    return user


def require_permission(permission: str):
    def _dep(user=Depends(get_current_user)):
        if not config.REQUIRE_AUTH:
            return user  # _SYSTEM_USER tem acesso total em dev
        if not has_permission(user, permission):
            raise HTTPException(status_code=403, detail="Acesso negado")
        return user
    return _dep


# ── Health ──────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok"}


# ── Auth ────────────────────────────────────────────────────────────────────
@app.post("/auth/register", response_model=Token, status_code=201)
def auth_register(
    body: UserCreate,
    db: Session = Depends(get_db),
    _rl=Depends(rate_limit(max_calls=5, window_seconds=3600, scope="register")),
):
    if auth_service.get_user_by_email(db, body.email):
        raise HTTPException(status_code=409, detail="Email já cadastrado")
    user = auth_service.create_user(db, email=body.email, password=body.password, name=body.name)
    return Token(access_token=auth_service.create_access_token(user.id),
                 user=UserOut.model_validate(user))


@app.post("/auth/login", response_model=Token)
def auth_login(
    body: LoginRequest,
    db: Session = Depends(get_db),
    _rl=Depends(rate_limit(max_calls=10, window_seconds=300, scope="login")),
):
    user = auth_service.authenticate(db, body.email, body.password)
    if not user:
        raise HTTPException(status_code=401, detail="Email ou senha incorretos")
    return Token(access_token=auth_service.create_access_token(user.id),
                 user=UserOut.model_validate(user))


@app.get("/auth/me", response_model=UserOut)
def auth_me(user=Depends(get_current_user)):
    return UserOut.model_validate(user)


@app.post("/auth/password/forgot", response_model=GenericMessage)
def auth_password_forgot(
    body: ForgotPasswordRequest,
    db: Session = Depends(get_db),
    _rl=Depends(rate_limit(max_calls=5, window_seconds=900, scope="forgot")),
):
    user = auth_service.get_user_by_email(db, body.email)
    if user and user.is_active:
        from src.services.email_service import send_password_reset_email
        token = auth_service.create_password_reset_token(user.id)
        send_password_reset_email(to=user.email, name=user.name, reset_token=token)
    return GenericMessage(
        message="Se houver uma conta com esse email, enviamos um link de recuperação."
    )


@app.post("/auth/password/reset", response_model=Token)
def auth_password_reset(
    body: ResetPasswordConfirm,
    db: Session = Depends(get_db),
    _rl=Depends(rate_limit(max_calls=10, window_seconds=300, scope="reset")),
):
    user_id = auth_service.decode_password_reset_token(body.token)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Token inválido ou expirado")
    user = auth_service.get_user_by_id(db, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    auth_service.update_password(db, user, body.new_password)
    return Token(access_token=auth_service.create_access_token(user.id),
                 user=UserOut.model_validate(user))


# ── Admin ───────────────────────────────────────────────────────────────────
@app.get("/admin/users", response_model=list[UserOut])
def admin_list_users(
    _user=Depends(require_permission(VIEW_USERS)),
    db: Session = Depends(get_db),
    search: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    users = auth_service.list_users(db, search=search, limit=limit, offset=offset)
    return [UserOut.model_validate(u) for u in users]


@app.patch("/admin/users/{user_id}", response_model=UserOut)
def admin_update_user(
    user_id: int,
    body: UserAdminUpdate,
    admin=Depends(require_permission(VIEW_USERS)),
    db: Session = Depends(get_db),
):
    target = auth_service.get_user_by_id(db, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    if target.id == admin.id and (
        (body.role is not None and body.role != "admin") or body.is_active is False
    ):
        raise HTTPException(status_code=400, detail="Você não pode rebaixar ou desativar a si mesmo")
    updated = auth_service.update_user_admin(
        db, target, plan=body.plan, role=body.role, is_active=body.is_active,
    )
    return UserOut.model_validate(updated)


@app.get("/admin/performance")
def admin_performance(
    _user=Depends(require_permission(VIEW_PERFORMANCE)),
    db: Session = Depends(get_db),
    only_shown: bool = Query(False),
):
    from src.services.pick_results_service import performance_breakdown
    try:
        return performance_breakdown(db, only_shown=only_shown)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Erro ao calcular performance: {exc}")


# ── Football: dados ─────────────────────────────────────────────────────────
@app.get("/football/matches/today", response_model=MatchListResponse)
def matches_today(date: str | None = Query(None, description="YYYY-MM-DD (UTC); default hoje")):
    try:
        d, matches = data_service.matches(date=date)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Erro ao buscar jogos: {exc}")
    return MatchListResponse(date=d, matches=matches)


@app.get("/football/matches", response_model=MatchListResponse)
def matches_list(
    date: str | None = Query(None),
    league_id: int | None = Query(None),
    status: str | None = Query(None),
    country: str | None = Query(None),  # aceito mas não filtrado (provider não expõe)
):
    try:
        d, matches = data_service.matches(date=date, league_id=league_id, status=status)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Erro ao buscar jogos: {exc}")
    return MatchListResponse(date=d, matches=matches)


@app.get("/football/matches/{match_id}", response_model=MatchSchema)
def match_detail(match_id: int):
    m = data_service.get_match(match_id)
    if m is None:
        raise HTTPException(status_code=404, detail="Jogo não encontrado")
    return m


@app.get("/football/matches/{match_id}/statistics", response_model=MatchStatisticsSchema)
def match_statistics(match_id: int):
    s = data_service.match_statistics(match_id)
    if s is None:
        raise HTTPException(status_code=404, detail="Estatísticas indisponíveis")
    return s


@app.get("/football/matches/{match_id}/odds", response_model=MatchOddsSchema)
def match_odds(match_id: int):
    o = data_service.match_odds(match_id)
    if o is None:
        raise HTTPException(status_code=404, detail="Odds indisponíveis para este jogo")
    return o


@app.get("/football/matches/{match_id}/markets", response_model=list[MarketLineSchema])
def match_markets(match_id: int):
    return data_service.match_markets(match_id)


@app.get("/football/matches/{match_id}/props", response_model=list[RecommendationOut])
def match_props(match_id: int):
    return data_service.match_props(match_id)


@app.get("/football/props", response_model=list[RecommendationOut])
def props_feed(limit: int = Query(40, ge=1, le=100)):
    """Feed global de player props (artilheiro, chutes no gol) dos próximos jogos."""
    try:
        return data_service.props(limit=limit)
    except Exception as exc:  # noqa: BLE001
        logger.warning("props feed falhou: %s", exc)
        return []


@app.get("/football/leagues", response_model=list[LeagueSchema])
def leagues():
    return data_service.leagues()


@app.get("/football/teams", response_model=list[TeamSchema])
def teams_list(
    league_id: int | None = Query(None),
    search: str | None = Query(None),
):
    return data_service.teams(league_id=league_id, search=search)


@app.get("/football/teams/{team_id}", response_model=TeamSchema)
def team_detail(team_id: int):
    t = data_service.team(team_id)
    if t is None:
        raise HTTPException(status_code=404, detail="Time não encontrado")
    return t


_LEADER_METRIC = ("^(goals|assists|shots|shots_on_target|key_passes|dribbles|"
                  "tackles|interceptions|duels_won|fouls_drawn|fouls_committed|rating)$")


@app.get("/football/players", response_model=list[PlayerSchema])
def players_list(
    team_id: int | None = Query(None),
    search: str | None = Query(None),
):
    return data_service.players(team_id=team_id, search=search)


@app.get("/football/players/leaders", response_model=list[PlayerSchema])
def players_leaders(
    metric: str = Query("goals", pattern=_LEADER_METRIC),
    limit: int = Query(20, ge=1, le=100),
):
    """Ranking de jogadores por métrica (gols, assistências, chutes, chutes no
    gol) na competição/temporada do contexto."""
    return data_service.player_leaders(metric=metric, limit=limit)


_INDEX_METRIC = "^(ipo|icj|id|iip)$"


@app.get("/football/players/index", response_model=list[PlayerSchema])
def players_index(
    index: str = Query("iip", pattern=_INDEX_METRIC),
    limit: int = Query(20, ge=1, le=100),
):
    """Ranking por índice composto: IPO (periculosidade ofensiva), ICJ (criação),
    ID (defensivo) ou IIP (influência na partida) — escala 0–100."""
    return data_service.player_index_ranking(index=index, limit=limit)


@app.post("/football/players/populate")
def players_populate(
    _user=Depends(require_permission(MANAGE_RECOMMENDATIONS)),
    db: Session = Depends(get_db),
):
    """Popula a tabela football_players (stats + índices). Admin/analyst."""
    from src.services.players_service import populate_players
    return populate_players(db, data_service)


@app.get("/football/players/{player_id}", response_model=PlayerSchema)
def player_detail(player_id: int):
    p = data_service.player(player_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Jogador não encontrado")
    return p


@app.get("/football/odds", response_model=list[OddsBoardItemSchema])
def odds_board(
    league_id: int | None = Query(None),
    market: str | None = Query(None),
):
    try:
        return data_service.odds_board(league_id=league_id, market=market)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Erro ao montar painel de odds: {exc}")


# ── Football: recomendações ─────────────────────────────────────────────────
@app.get("/football/recommendations", response_model=list[RecommendationOut])
def recommendations_active(
    db: Session = Depends(get_db),
    league_id: str | None = Query(None),
    market: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
):
    """Feed de recomendações ativas. Autenticação via gate global (se ligado).
    Sem banco (Postgres fora), degrada pra lista vazia em vez de 500."""
    try:
        recs = rec_svc.list_recommendations(
            db, only_active=True, status=status, league_id=league_id,
            market=market, limit=limit,
        )
        return [front_mappers.rec_to_out(r) for r in recs]
    except Exception as exc:  # noqa: BLE001
        logger.warning("recommendations indisponível (DB?): %s", exc)
        return []


@app.get("/football/recommendations/opportunities", response_model=list[RecommendationOut])
def recommendations_opportunities(limit: int = Query(30, ge=1, le=100)):
    """Melhores apostas de valor (1X2) dos jogos próximos — ao vivo, sem banco."""
    try:
        return data_service.opportunities(limit=limit)
    except Exception as exc:  # noqa: BLE001
        logger.warning("opportunities falhou: %s", exc)
        return []


@app.get("/football/live-opportunities", response_model=list[RecommendationOut])
def live_opportunities(limit: int = Query(30, ge=1, le=100)):
    """Picks de valor AO VIVO (modelo in-play × odd ao vivo)."""
    try:
        return data_service.live_opportunities(limit=limit)
    except Exception as exc:  # noqa: BLE001
        logger.warning("live opportunities falhou: %s", exc)
        return []


@app.get("/football/live-shots", response_model=list[RecommendationOut])
def live_shots(limit: int = Query(40, ge=1, le=100)):
    """Especialista em CHUTES A GOL ao vivo (jogadores prováveis de chutar mais)."""
    try:
        return data_service.live_shots(limit=limit)
    except Exception as exc:  # noqa: BLE001
        logger.warning("live shots falhou: %s", exc)
        return []


@app.get("/football/live-goals", response_model=list[RecommendationOut])
def live_goals(limit: int = Query(40, ge=1, le=100)):
    """GOLS ao vivo: jogador que ainda pode marcar (taxa + pressão + pênalti)."""
    try:
        return data_service.live_goals(limit=limit)
    except Exception as exc:  # noqa: BLE001
        logger.warning("live goals falhou: %s", exc)
        return []


# ── Recomendações AO VIVO persistidas (foco escanteios) ─────────────────────
@app.get("/football/live-recommendations/match/{match_id}", response_model=list[LiveRecoOut])
def live_recs_by_match(match_id: int, db: Session = Depends(get_db)):
    from src.services import live_reco_service as lrs
    return [front_mappers.live_reco_to_out(r) for r in lrs.list_by_match(db, match_id)]


@app.get("/football/live-recommendations/pending", response_model=list[LiveRecoOut])
def live_recs_pending(db: Session = Depends(get_db),
                      context: str | None = Query(None)):
    from src.services import live_reco_service as lrs
    return [front_mappers.live_reco_to_out(r) for r in lrs.list_pending(db, context)]


@app.patch("/football/live-recommendations/{rec_id}/status", response_model=LiveRecoOut)
def live_rec_set_status(rec_id: int, status: str = Query(...),
                        db: Session = Depends(get_db)):
    from src.services import live_reco_service as lrs
    row = lrs.update_status(db, rec_id, status)
    if row is None:
        raise HTTPException(status_code=404, detail="Recomendação não encontrada")
    return front_mappers.live_reco_to_out(row)


@app.patch("/football/live-recommendations/{rec_id}/result", response_model=LiveRecoOut)
def live_rec_set_result(rec_id: int,
                        result: str = Query(..., pattern="^(green|red|void|pending)$"),
                        db: Session = Depends(get_db)):
    from src.services import live_reco_service as lrs
    row = lrs.set_result(db, rec_id, result)
    if row is None:
        raise HTTPException(status_code=404, detail="Recomendação não encontrada")
    return front_mappers.live_reco_to_out(row)


@app.get("/football/recommendations/live", response_model=list[LivePickOut])
def recommendations_live(db: Session = Depends(get_db), limit: int = Query(50, ge=1, le=200)):
    """Entradas ao vivo publicadas por analistas (source=analyst, ativas)."""
    try:
        recs = rec_svc.list_live_picks(db, limit=limit)
        return [front_mappers.livepick_to_out(r) for r in recs]
    except Exception as exc:  # noqa: BLE001
        logger.warning("live-picks indisponível (DB?): %s", exc)
        return []


@app.post("/football/recommendations/generate", response_model=list[RecommendationOut])
def recommendations_generate(
    body: GenerateRequest,
    _user=Depends(require_permission(MANAGE_RECOMMENDATIONS)),
    db: Session = Depends(get_db),
):
    """Roda o engine de probabilidade × odds e persiste as recomendações de
    valor. Devolve a lista gerada. Admin + analyst (ou aberto se REQUIRE_AUTH=0)."""
    try:
        result = generation_service.generate(
            db, date=body.date, match_ids=body.match_ids,
            min_edge=body.min_edge, min_confidence=body.min_confidence,
            persist=body.persist,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Erro ao gerar recomendações: {exc}")
    if body.persist:
        recs = rec_svc.list_recommendations(db, only_active=True, limit=200)
        return [front_mappers.rec_to_out(r) for r in recs]
    return [front_mappers.candidate_to_out(c) for c in result["candidates"]]


@app.get("/football/recommendations/{rec_id}", response_model=RecommendationOut)
def recommendation_detail(rec_id: int, db: Session = Depends(get_db)):
    rec = rec_svc.get_recommendation(db, rec_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="Recomendação não encontrada")
    return front_mappers.rec_to_out(rec)


# ── Football: entradas ao vivo (CRUD de analista) ───────────────────────────
@app.post("/football/live-picks", response_model=LivePickOut, status_code=201)
def live_pick_create(
    body: LivePickCreate,
    user=Depends(require_permission(MANAGE_RECOMMENDATIONS)),
    db: Session = Depends(get_db),
):
    rec = rec_svc.create_live_pick(
        db, match=body.match, match_id=body.match_id, league=body.league,
        market=body.market, selection=body.selection, line=body.line,
        odd=body.odd, confidence=body.confidence, reason=body.reason,
        created_by_id=getattr(user, "id", 0),
        created_by_name=getattr(user, "name", "") or "analista",
    )
    return front_mappers.livepick_to_out(rec)


@app.patch("/football/live-picks/{pick_id}", response_model=LivePickOut)
def live_pick_update(
    pick_id: int,
    body: LivePickUpdate,
    _user=Depends(require_permission(MANAGE_RECOMMENDATIONS)),
    db: Session = Depends(get_db),
):
    rec = rec_svc.update_live_pick(
        db, pick_id, status=body.status, odd=body.odd,
        confidence=body.confidence, reason=body.reason,
    )
    if rec is None:
        raise HTTPException(status_code=404, detail="Entrada não encontrada")
    return front_mappers.livepick_to_out(rec)


@app.delete("/football/live-picks/{pick_id}", status_code=204)
def live_pick_delete(
    pick_id: int,
    _user=Depends(require_permission(MANAGE_RECOMMENDATIONS)),
    db: Session = Depends(get_db),
):
    rec = rec_svc.deactivate(db, pick_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="Entrada não encontrada")
    return None


# ── Football: resultados / performance ──────────────────────────────────────
@app.get("/football/pick-results", response_model=list[PickResultOut])
def pick_results(db: Session = Depends(get_db), limit: int = Query(200, ge=1, le=1000)):
    """Histórico de resultados liquidados (ledger imutável). Vazio se DB fora."""
    from sqlalchemy import select
    from src.db.models import FootballPickResult
    try:
        rows = db.scalars(
            select(FootballPickResult)
            .where(FootballPickResult.context == competition.GENERAL)
            .order_by(FootballPickResult.settled_at.desc())
            .limit(limit)
        ).all()
        return [front_mappers.pickresult_to_out(r) for r in rows]
    except Exception as exc:  # noqa: BLE001
        logger.warning("pick-results indisponível (DB?): %s", exc)
        return []


@app.get("/football/performance", response_model=PerformanceSummary)
def performance(db: Session = Depends(get_db)):
    """Resumo de performance. Sem banco, devolve resumo zerado (não 500)."""
    try:
        return front_mappers.performance_summary(db)
    except Exception as exc:  # noqa: BLE001
        logger.warning("performance indisponível (DB?): %s", exc)
        from src.schemas.football_schemas import PerfTotals
        return PerformanceSummary(totals=PerfTotals())


# ── Contexto de competição (catálogo: futebol geral / Copa do Mundo) ────────
@app.get("/football/context", response_model=list[ContextSchema])
def football_contexts():
    """Lista os contextos disponíveis + features. O front decide qual está
    ativo (persistido no cliente); 'general' é o default."""
    return [
        ContextSchema(key=c.key, label=c.label, active=(c.key == competition.GENERAL),
                      features=c.features)
        for c in competition.all_contexts()
    ]


# ── Router da Copa do Mundo (mesmos serviços, context="world_cup") ──────────
# Rotas finas que delegam aos MESMOS serviços context-aware — sem duplicar
# lógica. Tudo isolado por context: jogos, grupos, mata-mata, recomendações,
# entradas ao vivo, resultados e performance da Copa.
_WC = competition.WORLD_CUP
wc = APIRouter(prefix="/football/world-cup", tags=["world-cup"])


@wc.get("/matches/today", response_model=MatchListResponse)
def wc_matches_today(date: str | None = Query(None)):
    d, matches = data_service.matches(date=date, context=_WC)
    return MatchListResponse(date=d, matches=matches)


@wc.get("/matches", response_model=MatchListResponse)
def wc_matches(date: str | None = Query(None), status: str | None = Query(None)):
    d, matches = data_service.matches(date=date, status=status, context=_WC)
    return MatchListResponse(date=d, matches=matches)


@wc.get("/matches/{match_id}", response_model=MatchSchema)
def wc_match(match_id: int):
    m = data_service.get_match(match_id, context=_WC)
    if m is None:
        raise HTTPException(status_code=404, detail="Jogo não encontrado")
    return m


@wc.get("/matches/{match_id}/statistics", response_model=MatchStatisticsSchema)
def wc_match_stats(match_id: int):
    s = data_service.match_statistics(match_id, context=_WC)
    if s is None:
        raise HTTPException(status_code=404, detail="Estatísticas indisponíveis")
    return s


@wc.get("/matches/{match_id}/odds", response_model=MatchOddsSchema)
def wc_match_odds(match_id: int):
    o = data_service.match_odds(match_id, context=_WC)
    if o is None:
        raise HTTPException(status_code=404, detail="Odds indisponíveis")
    return o


@wc.get("/matches/{match_id}/markets", response_model=list[MarketLineSchema])
def wc_match_markets(match_id: int):
    return data_service.match_markets(match_id, context=_WC)


@wc.get("/matches/{match_id}/props", response_model=list[RecommendationOut])
def wc_match_props(match_id: int):
    return data_service.match_props(match_id, context=_WC)


@wc.get("/props", response_model=list[RecommendationOut])
def wc_props_feed(limit: int = Query(40, ge=1, le=100)):
    """Feed global de player props da Copa (artilheiro, chutes no gol)."""
    try:
        return data_service.props(context=_WC, limit=limit)
    except Exception as exc:  # noqa: BLE001
        logger.warning("wc props feed falhou: %s", exc)
        return []


@wc.get("/live-opportunities", response_model=list[RecommendationOut])
def wc_live_opportunities(limit: int = Query(30, ge=1, le=100)):
    """Picks de valor AO VIVO da Copa (modelo in-play × odd ao vivo)."""
    try:
        return data_service.live_opportunities(context=_WC, limit=limit)
    except Exception as exc:  # noqa: BLE001
        logger.warning("wc live opportunities falhou: %s", exc)
        return []


@wc.get("/live-shots", response_model=list[RecommendationOut])
def wc_live_shots(limit: int = Query(40, ge=1, le=100)):
    """Especialista em CHUTES A GOL ao vivo da Copa."""
    try:
        return data_service.live_shots(context=_WC, limit=limit)
    except Exception as exc:  # noqa: BLE001
        logger.warning("wc live shots falhou: %s", exc)
        return []


@wc.get("/live-goals", response_model=list[RecommendationOut])
def wc_live_goals(limit: int = Query(40, ge=1, le=100)):
    """GOLS ao vivo da Copa: jogador que ainda pode marcar."""
    try:
        return data_service.live_goals(context=_WC, limit=limit)
    except Exception as exc:  # noqa: BLE001
        logger.warning("wc live goals falhou: %s", exc)
        return []


@wc.get("/groups", response_model=list[GroupSchema])
def wc_groups():
    return data_service.groups(context=_WC)


@wc.get("/bracket", response_model=list[BracketStageSchema])
def wc_bracket():
    return data_service.bracket(context=_WC)


@wc.get("/teams", response_model=list[TeamSchema])
def wc_teams(search: str | None = Query(None)):
    return data_service.teams(search=search, context=_WC)


@wc.get("/teams/{team_id}", response_model=TeamSchema)
def wc_team(team_id: int):
    t = data_service.team(team_id, context=_WC)
    if t is None:
        raise HTTPException(status_code=404, detail="Seleção não encontrada")
    return t


@wc.get("/players/leaders", response_model=list[PlayerSchema])
def wc_players_leaders(
    metric: str = Query("goals", pattern=_LEADER_METRIC),
    limit: int = Query(20, ge=1, le=100),
):
    """Líderes da Copa por métrica (gols, assistências, chutes, chutes no gol)."""
    return data_service.player_leaders(context=_WC, metric=metric, limit=limit)


@wc.get("/players/index", response_model=list[PlayerSchema])
def wc_players_index(
    index: str = Query("iip", pattern=_INDEX_METRIC),
    limit: int = Query(20, ge=1, le=100),
):
    """Ranking da Copa por índice composto (IPO/ICJ/ID/IIP, 0–100)."""
    return data_service.player_index_ranking(context=_WC, index=index, limit=limit)


@wc.get("/players", response_model=list[PlayerSchema])
def wc_players(search: str | None = Query(None)):
    return data_service.players(search=search, context=_WC)


@wc.post("/players/populate")
def wc_players_populate(
    _user=Depends(require_permission(MANAGE_RECOMMENDATIONS)),
    db: Session = Depends(get_db),
):
    from src.services.players_service import populate_players
    return populate_players(db, data_service, context=_WC)


@wc.get("/recommendations", response_model=list[RecommendationOut])
def wc_recommendations(
    db: Session = Depends(get_db),
    market: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
):
    try:
        recs = rec_svc.list_recommendations(
            db, context=_WC, only_active=True, status=status, market=market, limit=limit,
        )
        return [front_mappers.rec_to_out(r) for r in recs]
    except Exception as exc:  # noqa: BLE001
        logger.warning("wc recommendations indisponível (DB?): %s", exc)
        return []


@wc.get("/recommendations/opportunities", response_model=list[RecommendationOut])
def wc_opportunities(limit: int = Query(30, ge=1, le=100)):
    try:
        return data_service.opportunities(context=_WC, limit=limit)
    except Exception as exc:  # noqa: BLE001
        logger.warning("wc opportunities falhou: %s", exc)
        return []


@wc.get("/recommendations/live", response_model=list[LivePickOut])
def wc_live(db: Session = Depends(get_db), limit: int = Query(50, ge=1, le=200)):
    try:
        recs = rec_svc.list_live_picks(db, context=_WC, limit=limit)
        return [front_mappers.livepick_to_out(r) for r in recs]
    except Exception as exc:  # noqa: BLE001
        logger.warning("wc live-picks indisponível (DB?): %s", exc)
        return []


@wc.post("/recommendations/generate", response_model=list[RecommendationOut])
def wc_generate(
    body: GenerateRequest,
    _user=Depends(require_permission(MANAGE_RECOMMENDATIONS)),
    db: Session = Depends(get_db),
):
    try:
        result = generation_service.generate(
            db, context=_WC, date=body.date, match_ids=body.match_ids,
            min_edge=body.min_edge, min_confidence=body.min_confidence,
            persist=body.persist,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Erro ao gerar recomendações: {exc}")
    if body.persist:
        recs = rec_svc.list_recommendations(db, context=_WC, only_active=True, limit=200)
        return [front_mappers.rec_to_out(r) for r in recs]
    return [front_mappers.candidate_to_out(c) for c in result["candidates"]]


@wc.get("/recommendations/{rec_id}", response_model=RecommendationOut)
def wc_recommendation_detail(rec_id: int, db: Session = Depends(get_db)):
    rec = rec_svc.get_recommendation(db, rec_id)
    if rec is None or rec.context != _WC:
        raise HTTPException(status_code=404, detail="Recomendação não encontrada")
    return front_mappers.rec_to_out(rec)


@wc.post("/live-picks", response_model=LivePickOut, status_code=201)
def wc_live_create(
    body: LivePickCreate,
    user=Depends(require_permission(MANAGE_RECOMMENDATIONS)),
    db: Session = Depends(get_db),
):
    rec = rec_svc.create_live_pick(
        db, context=_WC, match=body.match, match_id=body.match_id, league=body.league,
        market=body.market, selection=body.selection, line=body.line, odd=body.odd,
        confidence=body.confidence, reason=body.reason,
        created_by_id=getattr(user, "id", 0),
        created_by_name=getattr(user, "name", "") or "analista",
    )
    return front_mappers.livepick_to_out(rec)


@wc.patch("/live-picks/{pick_id}", response_model=LivePickOut)
def wc_live_update(
    pick_id: int, body: LivePickUpdate,
    _user=Depends(require_permission(MANAGE_RECOMMENDATIONS)),
    db: Session = Depends(get_db),
):
    rec = rec_svc.update_live_pick(
        db, pick_id, status=body.status, odd=body.odd,
        confidence=body.confidence, reason=body.reason,
    )
    if rec is None:
        raise HTTPException(status_code=404, detail="Entrada não encontrada")
    return front_mappers.livepick_to_out(rec)


@wc.delete("/live-picks/{pick_id}", status_code=204)
def wc_live_delete(
    pick_id: int,
    _user=Depends(require_permission(MANAGE_RECOMMENDATIONS)),
    db: Session = Depends(get_db),
):
    if rec_svc.deactivate(db, pick_id) is None:
        raise HTTPException(status_code=404, detail="Entrada não encontrada")
    return None


@wc.get("/pick-results", response_model=list[PickResultOut])
def wc_pick_results(db: Session = Depends(get_db), limit: int = Query(200, ge=1, le=1000)):
    from sqlalchemy import select
    from src.db.models import FootballPickResult
    try:
        rows = db.scalars(
            select(FootballPickResult)
            .where(FootballPickResult.context == _WC)
            .order_by(FootballPickResult.settled_at.desc())
            .limit(limit)
        ).all()
        return [front_mappers.pickresult_to_out(r) for r in rows]
    except Exception as exc:  # noqa: BLE001
        logger.warning("wc pick-results indisponível (DB?): %s", exc)
        return []


@wc.get("/performance", response_model=PerformanceSummary)
def wc_performance(db: Session = Depends(get_db)):
    try:
        return front_mappers.performance_summary(db, context=_WC)
    except Exception as exc:  # noqa: BLE001
        logger.warning("wc performance indisponível (DB?): %s", exc)
        from src.schemas.football_schemas import PerfTotals
        return PerformanceSummary(totals=PerfTotals())


app.include_router(wc)
