"""Shared test setup. Tests never read the developer's `.env` or call live APIs.

Database tests need a disposable Postgres: set TEST_DATABASE_URL (not
SENTINEL_-prefixed, so it never reaches Settings). They skip when it's unset.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"


def _test_database_url() -> str | None:
    """TEST_DATABASE_URL from the environment, or else from `.env` (pytest
    doesn't load `.env` itself). Only this one key is read; no SENTINEL_
    setting leaks into the tests."""
    if os.environ.get("TEST_DATABASE_URL"):
        return os.environ["TEST_DATABASE_URL"]
    try:
        from dotenv import dotenv_values
    except ImportError:  # pragma: no cover
        return None
    return dotenv_values(ROOT / ".env").get("TEST_DATABASE_URL") or None


TEST_DATABASE_URL = _test_database_url()

requires_db = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="TEST_DATABASE_URL not set"
)

# A complete set of the settings ADR-022 requires. Values are arbitrary but
# valid; tests that care about a value set it explicitly.
REQUIRED_ENV = {
    "SENTINEL_RANDOM_SEED": "1",
    "SENTINEL_DATABASE_URL": "postgresql+psycopg://u:p@localhost:5432/unused",
    "SENTINEL_CONTACT_EMAIL": "test@example.org",
    "SENTINEL_USER_AGENT": "sentinel-tests",
    "SENTINEL_STRIP_PERSONAL_FIELDS": "true",
    "SENTINEL_CHECKS_ENABLED": "C01,C02,C03,C04,C05",
    "SENTINEL_RESOLVE_TRIGRAM_MIN": "0.85",
    "SENTINEL_RESOLVE_TRIGRAM_STRICT": "0.92",
    "SENTINEL_RESOLVE_YEAR_TOLERANCE": "1",
    "SENTINEL_RESOLVE_AMBIGUOUS_MARGIN": "0.02",
    "SENTINEL_C01_NEAR_MATCH_MIN": "0.6",
    "SENTINEL_C01_SAME_TITLE_MIN": "0.85",
    "SENTINEL_C02_GRACE_MONTHS": "6",
    "SENTINEL_C03_ALPHA": "0.05",
    "SENTINEL_C03_ALLOW_ONE_TAILED": "true",
    "SENTINEL_C04_MAX_N": "100",
    "SENTINEL_C04_MIN_DECIMALS": "2",
    "SENTINEL_C04_EPSILON": "1e-9",
    "SENTINEL_C05_GRACE_MONTHS": "12",
    "SENTINEL_CORPUS_QUERY": 'ACCESSION_TYPE:nct AND PUB_TYPE:"Randomized Controlled Trial"',
}


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Remove every SENTINEL_ variable inherited from the shell."""
    for key in list(os.environ):
        if key.startswith("SENTINEL_"):
            monkeypatch.delenv(key)
    return monkeypatch


@pytest.fixture
def alembic_cfg(clean_env) -> Iterator[Config]:
    """Alembic pointed at TEST_DATABASE_URL, with no `.env` involved."""
    from sentinel.config import get_settings

    for key, value in REQUIRED_ENV.items():
        clean_env.setenv(key, value)
    clean_env.setenv("SENTINEL_DATABASE_URL", TEST_DATABASE_URL)
    get_settings.cache_clear()
    yield Config(str(ROOT / "alembic.ini"))
    get_settings.cache_clear()


@pytest.fixture
def db_engine(alembic_cfg) -> Iterator[Engine]:
    """An engine on the test database, migrated to head."""
    command.upgrade(alembic_cfg, "head")
    engine = create_engine(TEST_DATABASE_URL)
    yield engine
    engine.dispose()
