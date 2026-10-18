"""Migrations run up, down, up against a real Postgres (sentinel-project.md §8).

Needs a disposable database: set TEST_DATABASE_URL (not SENTINEL_-prefixed, so
it never reaches Settings). Skipped when unset. The test downgrades to base,
so never point it at a database holding data you want.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from sentinel.config import get_settings
from tests.conftest import REQUIRED_ENV

ROOT = Path(__file__).resolve().parent.parent
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="TEST_DATABASE_URL not set"
)


@pytest.fixture
def alembic_cfg(clean_env):
    for key, value in REQUIRED_ENV.items():
        clean_env.setenv(key, value)
    clean_env.setenv("SENTINEL_DATABASE_URL", TEST_DATABASE_URL)
    get_settings.cache_clear()
    yield Config(str(ROOT / "alembic.ini"))
    get_settings.cache_clear()


def test_up_down_up(alembic_cfg):
    command.upgrade(alembic_cfg, "head")
    command.downgrade(alembic_cfg, "base")
    command.upgrade(alembic_cfg, "head")

    engine = create_engine(TEST_DATABASE_URL)
    try:
        insp = inspect(engine)
        assert "paper_title_trgm" in {i["name"] for i in insp.get_indexes("paper")}

        cols = {c["name"]: c for c in insp.get_columns("finding")}
        assert cols["paper_id"]["nullable"] is True
        assert cols["nct_id"]["nullable"] is True
        assert "finding_nct" in {i["name"] for i in insp.get_indexes("finding")}
        checks = {c["name"] for c in insp.get_check_constraints("finding")}
        assert "finding_one_subject" in checks

        # A finding must have exactly one subject. Rolled back, so nothing persists.
        with engine.connect() as conn:
            run_id = conn.execute(
                text(
                    "INSERT INTO check_run (config_hash, checks) "
                    "VALUES ('x', ARRAY['C05']) RETURNING id"
                )
            ).scalar_one()
            with pytest.raises(IntegrityError):
                conn.execute(
                    text(
                        "INSERT INTO finding (run_id, check_id, check_ver, "
                        "severity, status, summary, evidence) VALUES "
                        "(:r, 'C05', '1.0.0', 'info', 'clean', 's', '{}')"
                    ),
                    {"r": run_id},
                )
            conn.rollback()
    finally:
        engine.dispose()
