"""Retraction Watch harvester against a 20-row fixture of real rows.

Author and Institution in the fixture are replaced with REDACTED; every other
value is as published. Expected values below were checked by hand against it.
"""

from __future__ import annotations

import csv
import shutil
from collections import Counter
from datetime import date

import pytest
from sqlalchemy import func, select, text

from sentinel.ingest.retraction_watch import RetractionWatchHarvester
from sentinel.models import ingest_watermark, retraction_notice
from tests.conftest import FIXTURES, requires_db

SAMPLE = FIXTURES / "retraction_watch" / "sample.csv"


def _rows() -> list[dict]:
    with SAMPLE.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _by_id() -> dict[str, dict]:
    return {r["Record ID"]: r for r in _rows()}


@pytest.fixture
def harvester() -> RetractionWatchHarvester:
    return RetractionWatchHarvester(SAMPLE)


# --- normalise: pure, no database --------------------------------------------


def test_every_fixture_row_normalises(harvester):
    notices = [harvester.normalise(r) for r in _rows()]
    assert len(notices) == 20
    assert Counter(n.notice_type for n in notices) == {
        "retraction": 13,
        "expression_of_concern": 3,
        "correction": 2,
        "reinstatement": 2,
    }


@pytest.mark.parametrize(
    ("record_id", "expected"),
    [
        # Uppercase DOI lowercased; PMID '0' is no PMID; month-first date.
        ("73860", {"retracted_doi": "10.1186/s44315-025-00043-9", "retracted_pmid": None,
                   "notice_doi": "10.1186/s44315-026-00053-1", "notice_type": "retraction",
                   "notice_date": date(2026, 3, 6)}),
        # Junk 'xx' prefix on the notice DOI: stored as NULL, not guessed.
        ("70690", {"retracted_doi": "10.1007/978-3-030-00524-5_6", "notice_doi": None}),
        ("70689", {"notice_doi": None}),
        # 'unavailable' DOIs and PMID 0.
        ("73439", {"retracted_doi": None, "retracted_pmid": None, "notice_doi": None}),
        # Blank DOIs and PMIDs.
        ("16405", {"retracted_doi": None, "retracted_pmid": None, "notice_doi": None}),
        ("72927", {"notice_type": "reinstatement", "retracted_doi": "10.2217/rme-2020-0061",
                   "retracted_pmid": "33211632"}),
        ("73582", {"notice_type": "expression_of_concern",
                   "retracted_doi": "10.1096/fj.04-1551fje", "retracted_pmid": "15132987"}),
        ("73628", {"notice_type": "correction", "retracted_doi": "10.1007/s00125-011-2102-z"}),
    ],
)
def test_normalise_cases(harvester, record_id, expected):
    notice = harvester.normalise(_by_id()[record_id])
    assert notice.source == "retraction_watch"
    assert notice.source_record_id == record_id
    for field, value in expected.items():
        assert getattr(notice, field) == value, field


def test_raw_drops_author_institution_and_unnamed_column(harvester):
    for row in _rows():
        raw = harvester.normalise(row).raw
        assert "Author" not in raw
        assert "Institution" not in raw
        assert "" not in raw
        assert "REDACTED" not in raw.values()
        assert raw["Record ID"] == row["Record ID"]


def test_unknown_notice_type_is_rejected(harvester):
    row = dict(_rows()[0], RetractionNature="Withdrawal")
    with pytest.raises(ValueError, match="unknown RetractionNature"):
        harvester.normalise(row)


# --- fetch cursor: pure, no database -----------------------------------------


def test_fetch_resumes_from_cursor(harvester):
    first = list(harvester.fetch(None))
    assert len(first) == 20
    cursor = first[6][1]
    assert cursor.endswith(":7")
    rest = list(harvester.fetch(cursor))
    assert [r["Record ID"] for r, _ in rest] == [r["Record ID"] for r, _ in first[7:]]


def test_fetch_restarts_when_file_changed(harvester):
    assert len(list(harvester.fetch("0000000000000000:7"))) == 20


# --- database: load, re-run, update, resume -----------------------------------


@pytest.fixture
def rw_engine(db_engine):
    """Test database with no Retraction Watch rows except the fixture's own."""
    fixture_ids = set(_by_id())
    with db_engine.begin() as conn:
        present = set(
            conn.execute(
                select(retraction_notice.c.source_record_id).where(
                    retraction_notice.c.source == "retraction_watch"
                )
            ).scalars()
        )
        if present - fixture_ids:
            pytest.skip("retraction_notice holds real data; not a disposable database")
        conn.execute(text("DELETE FROM retraction_notice WHERE source = 'retraction_watch'"))
        conn.execute(text("DELETE FROM ingest_watermark WHERE source = 'retraction_watch'"))
    return db_engine


def _count(engine) -> int:
    with engine.connect() as conn:
        return conn.execute(select(func.count()).select_from(retraction_notice)).scalar_one()


@requires_db
def test_load_then_rerun_adds_no_duplicates(rw_engine, harvester):
    first = harvester.run(rw_engine, batch_size=8)
    assert (first.seen, first.inserted, first.failed) == (20, 20, 0)
    assert _count(rw_engine) == 20

    second = harvester.run(rw_engine, batch_size=8)
    assert (second.inserted, second.updated, second.unchanged) == (0, 0, 20)
    assert _count(rw_engine) == 20

    with rw_engine.connect() as conn:
        row = conn.execute(
            select(retraction_notice).where(retraction_notice.c.source_record_id == "72927")
        ).one()
    assert row.notice_type == "reinstatement"
    assert "Author" not in row.raw


@requires_db
def test_changed_row_updates_in_place(rw_engine, tmp_path):
    RetractionWatchHarvester(SAMPLE).run(rw_engine)

    rows = _rows()
    rows[0]["Reason"] = rows[0]["Reason"] + "+Updated Reason;"
    changed = tmp_path / "changed.csv"
    with changed.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    stats = RetractionWatchHarvester(changed).run(rw_engine)
    assert (stats.inserted, stats.updated, stats.unchanged) == (0, 1, 19)
    assert _count(rw_engine) == 20


@requires_db
def test_killed_run_resumes_where_it_stopped(rw_engine, tmp_path):
    path = tmp_path / "rw.csv"
    shutil.copy(SAMPLE, path)
    harvester = RetractionWatchHarvester(path)

    partial = harvester.run(rw_engine, limit=7, batch_size=5)
    assert partial.seen == 7
    assert _count(rw_engine) == 7
    with rw_engine.connect() as conn:
        mark = conn.execute(
            select(ingest_watermark).where(ingest_watermark.c.source == "retraction_watch")
        ).one()
    assert mark.cursor.endswith(":7")
    assert mark.rows_seen == 7

    rest = harvester.run(rw_engine, resume=True, batch_size=5)
    assert (rest.seen, rest.inserted) == (13, 13)
    assert _count(rw_engine) == 20

    done = harvester.run(rw_engine, resume=True)
    assert done.seen == 0
