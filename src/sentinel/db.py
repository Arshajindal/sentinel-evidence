"""SQLAlchemy Core engine (ADR-004: Core, not the ORM; Alembic owns schema)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Connection, Engine, create_engine

from sentinel.config import Settings, get_settings


def build_engine(settings: Settings) -> Engine:
    return create_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,
        echo=settings.db_echo,
        connect_args={
            "options": f"-c statement_timeout={settings.db_statement_timeout_ms}"
        },
    )


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    return build_engine(get_settings())


@contextmanager
def connect() -> Iterator[Connection]:
    """A connection with autocommit-off, transactional semantics."""
    with get_engine().connect() as conn:
        yield conn
