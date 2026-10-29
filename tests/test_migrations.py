"""Migrations run up, down, up against a real Postgres (sentinel-project.md §8).

The test downgrades to base, so TEST_DATABASE_URL must be a disposable
database, never one holding data you want.
"""

from __future__ import annotations

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from tests.conftest import TEST_DATABASE_URL, requires_db

pytestmark = requires_db


def _tables_with_rows(engine) -> list[str]:
    insp = inspect(engine)
    with engine.connect() as conn:
        return [
            t for t in insp.get_table_names()
            if t != "alembic_version"
            and conn.execute(text(f'SELECT EXISTS (SELECT 1 FROM "{t}")')).scalar()
        ]


def test_up_down_up(alembic_cfg):
    command.upgrade(alembic_cfg, "head")
    guard = create_engine(TEST_DATABASE_URL)
    try:
        if occupied := _tables_with_rows(guard):
            pytest.skip(f"refusing to downgrade a database with data in {occupied}")
    finally:
        guard.dispose()
    command.downgrade(alembic_cfg, "base")
    command.upgrade(alembic_cfg, "head")

    engine = create_engine(TEST_DATABASE_URL)
    try:
        insp = inspect(engine)
        assert "paper_title_trgm" in {i["name"] for i in insp.get_indexes("paper")}
        assert "stage_run_corpus" in {i["name"] for i in insp.get_indexes("stage_run")}

        cols = {c["name"]: c for c in insp.get_columns("finding")}
        assert cols["paper_id"]["nullable"] is True
        assert cols["nct_id"]["nullable"] is True
        assert "finding_nct" in {i["name"] for i in insp.get_indexes("finding")}
        checks = {c["name"] for c in insp.get_check_constraints("finding")}
        assert "finding_one_subject" in checks

        uniques = {u["name"] for u in insp.get_unique_constraints("retraction_notice")}
        assert "retraction_notice_source_record_key" in uniques

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
