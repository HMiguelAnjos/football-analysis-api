"""
Rate limiter in-memory (jun/2026) — protege /auth/* contra brute-force de
senha e spam de reset, sem dependência externa.

Janela deslizante por chave (scope + IP). Best-effort:
  • Memória por processo. O Railway roda instância única, então serve bem.
    Pra multi-réplica ou pra resistir a rotação de IP, evoluir pra store
    compartilhado (Redis) + limite POR CONTA (email) além do por-IP.
  • IP do cliente vem do X-Forwarded-For (Railway/nginx ficam na frente).
    O header é spoofável, então isso eleva a barra mas não é à prova de
    atacante determinado — o objetivo aqui é matar brute-force ingênuo.

Uso (dependency factory):
    @app.post("/auth/login")
    def login(..., _rl=Depends(rate_limit(max_calls=10, window_seconds=300, scope="login"))):
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict

from fastapi import HTTPException, Request

# Estado global protegido por lock (endpoints sync rodam em threadpool).
_lock = threading.Lock()
_store: dict[str, list[float]] = defaultdict(list)
_calls_since_sweep = 0
_SWEEP_EVERY = 500  # varre o store a cada N chamadas pra não crescer sem limite


def _client_ip(request: Request) -> str:
    """IP real do cliente. Prefere o 1º IP do X-Forwarded-For (Railway põe o
    IP de origem lá); cai pro peer direto se não houver."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"


def _sweep(now: float) -> None:
    """Remove chaves cujos timestamps já expiraram (1h de folga)."""
    horizon = 3600.0
    for key in list(_store.keys()):
        recent = [t for t in _store[key] if now - t < horizon]
        if recent:
            _store[key] = recent
        else:
            del _store[key]


def rate_limit(*, max_calls: int, window_seconds: float, scope: str):
    """Cria uma dependency que limita `max_calls` por (scope, IP) dentro de
    `window_seconds`. Excedeu → 429 com Retry-After."""

    def _dependency(request: Request) -> None:
        global _calls_since_sweep
        now = time.monotonic()
        key = f"{scope}:{_client_ip(request)}"
        with _lock:
            _calls_since_sweep += 1
            if _calls_since_sweep >= _SWEEP_EVERY:
                _calls_since_sweep = 0
                _sweep(now)

            recent = [t for t in _store[key] if now - t < window_seconds]
            if len(recent) >= max_calls:
                retry = int(window_seconds - (now - recent[0])) + 1
                raise HTTPException(
                    status_code=429,
                    detail="Muitas tentativas. Tente novamente em alguns minutos.",
                    headers={"Retry-After": str(max(retry, 1))},
                )
            recent.append(now)
            _store[key] = recent

    return _dependency


def _reset_for_tests() -> None:
    """Limpa o estado — só pra isolamento de teste."""
    with _lock:
        _store.clear()
        global _calls_since_sweep
        _calls_since_sweep = 0
