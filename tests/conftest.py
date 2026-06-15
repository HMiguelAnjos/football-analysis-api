"""Fixtures compartilhadas dos testes."""

from __future__ import annotations

import pytest


@pytest.fixture
def db_session(tmp_path):
    """SQLite em arquivo temporário — isola do Postgres real."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from src.db import models  # noqa: F401  (registra os modelos no metadata)
    from src.db.database import Base

    engine = create_engine(f"sqlite:///{tmp_path}/test.db", future=True)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture(autouse=True)
def _reset_providers():
    """Zera os singletons de provider entre testes (evita vazar config)."""
    from src.providers import registry
    registry.reset()
    yield
    registry.reset()


@pytest.fixture(autouse=True)
def _isolate_cache_dir(tmp_path, monkeypatch):
    """Isola o cache em disco por teste e força modo fixtures (offline):
    todos os providers — inclusive odds — usam mock, então a suíte roda sem
    nenhuma chave de API."""
    from src import config
    monkeypatch.setattr(config, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(config, "USE_FIXTURES", True)
    yield
