"""
Camada de banco (SQLAlchemy 2.x) — feature/login-area.

Engine + sessão + Base declarativa + dependency `get_db` pro FastAPI.
Postgres em todos os ambientes (DATABASE_URL em src.config).

`init_db()` cria as tabelas (create_all) — suficiente pra esta fase.
Migrations (Alembic) ficam pra quando o schema crescer.
"""

from __future__ import annotations

import logging
from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from src.config import DATABASE_URL

logger = logging.getLogger(__name__)

# pool_pre_ping evita conexões mortas (Railway/Postgres fecha idle).
engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    """Base declarativa de todos os modelos."""


def get_db() -> Generator[Session, None, None]:
    """Dependency do FastAPI — abre/fecha uma sessão por request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Migrações leves (jun/2026): create_all() só cria TABELAS novas, nunca
# faz ALTER em tabelas existentes (e o projeto não usa Alembic). Quando
# adicionamos uma coluna a um modelo que já tem tabela em produção, ela
# precisa ser criada manualmente. Esta lista de ALTERs idempotentes
# (ADD COLUMN IF NOT EXISTS, suportado pelo Postgres ≥ 9.6) roda no boot
# e garante que o schema acompanhe os modelos sem migration framework.
#
# Cada entrada deve ser idempotente e segura pra rodar N vezes.
_LIGHTWEIGHT_MIGRATIONS: list[str] = [
    # role/plan — área administrativa + planos. Defaults seguros pra
    # usuários já cadastrados (todos viram 'user'/'free').
    "ALTER TABLE users "
    "ADD COLUMN IF NOT EXISTS role VARCHAR(20) NOT NULL DEFAULT 'user'",
    "ALTER TABLE users "
    "ADD COLUMN IF NOT EXISTS plan VARCHAR(32) NOT NULL DEFAULT 'free'",
    # context/stage/group — modo Copa do Mundo. Picks legados viram 'general'.
    "ALTER TABLE football_recommendations "
    "ADD COLUMN IF NOT EXISTS context VARCHAR(20) NOT NULL DEFAULT 'general'",
    "ALTER TABLE football_recommendations ADD COLUMN IF NOT EXISTS stage VARCHAR(20)",
    "ALTER TABLE football_recommendations ADD COLUMN IF NOT EXISTS \"group\" VARCHAR(10)",
    "ALTER TABLE football_pick_results "
    "ADD COLUMN IF NOT EXISTS context VARCHAR(20) NOT NULL DEFAULT 'general'",
    "ALTER TABLE football_pick_results ADD COLUMN IF NOT EXISTS stage VARCHAR(20)",
]


def _run_lightweight_migrations() -> None:
    """Roda os ALTERs idempotentes. Best-effort por statement — um que
    falha (ex.: tabela ainda não existe num deploy fresco) não impede os
    outros. create_all() já rodou antes, então as tabelas base existem."""
    for stmt in _LIGHTWEIGHT_MIGRATIONS:
        try:
            with engine.begin() as conn:
                conn.execute(text(stmt))
        except Exception as exc:  # pragma: no cover - depende de infra
            logger.info("Migration leve falhou (segue): %s — %s", stmt[:60], exc)


def init_db() -> bool:
    """
    Cria as tabelas (create_all) + roda migrações leves. Best-effort: se o
    Postgres não estiver acessível (ex.: dev sem banco rodando), loga e
    segue — o resto da API continua de pé; só os endpoints de auth/picks
    ficam indisponíveis. Retorna True se conectou/criou, False caso contrário.
    """
    # Importa os modelos pra registrar no metadata antes do create_all.
    from src.db import models  # noqa: F401

    try:
        Base.metadata.create_all(bind=engine)
        _run_lightweight_migrations()
        logger.info("DB pronto (tabelas criadas/verificadas + migrações leves).")
        return True
    except Exception as exc:  # pragma: no cover - depende de infra
        logger.warning(
            "DB indisponível (%s) — endpoints de auth/picks ficarão fora até o "
            "Postgres responder. Resto da API segue normal.", exc,
        )
        return False
