"""Stage timing, `sentinel stats` and `sentinel export` (sentinel-project.md §9.8; ADR-039)."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import delete, insert, select

from sentinel.checks.base import Check
from sentinel.checks.context import PaperContext
from sentinel.checks.runner import run_checks
from sentinel.models import check_run, paper, reference, stage_run
from sentinel.report.export import COLUMNS, export_findings, safe_cell
from sentinel.report.stats import CheckStats, RunStats, StageStats, load_run_stats, render_stats_text
from sentinel.schemas import Finding
from sentinel.stages import record_stage
from tests.conftest import requires_db

LABEL = "test-stats"


# --- pure ---------------------------------------------------------------------------------


@pytest.mark.parametrize(("value", "expected"), [
    ("=HYPERLINK(\"x\")", "'=HYPERLINK(\"x\")"), ("+1", "'+1"), ("-2", "'-2"), ("@SUM(A1)", "'@SUM(A1)"),
    ("Mean 3.45 ...", "Mean 3.45 ..."), (12, 12), (None, None),
])
def test_cells_a_spreadsheet_would_run_are_escaped(value, expected):
    assert safe_cell(value) == expected


def _stats(**over) -> RunStats:
    base = dict(
        run_id=9, status="done", corpus_label="c", config_hash="abcdef0123456789",
        started_at=datetime(2026, 10, 27, 9, 0, tzinfo=timezone.utc),
        finished_at=datetime(2026, 10, 27, 9, 2, 5, tzinfo=timezone.utc), seconds=125.0,
        papers=10, trials=None, load_seconds=0.4,
        checks=[CheckStats(check_id="C02", subjects=10, applicable=8, flagged_subjects=2,
                           flag_rate=0.25, by_status={"clean": 8, "flagged": 3, "not_applicable": 2},
                           flags_by_severity={"high": 1, "low": 2}, seconds=0.05)],
        cascade={"by_status": {"resolved": 90, "unresolved": 10},
                 "by_method": {"doi": 80, "title_exact": 10}, "by_pass": {"crossref": 6, "local": 4}},
        stages=[StageStats(stage="resolve", source=None, corpus_label="c", runs=2,
                           by_status={"done": 1, "stopped": 1}, seconds=4000.0, items=100)],
    )
    return RunStats(**{**base, **over})


def test_render_shows_rates_cascade_and_stage_times():
    text = render_stats_text(_stats())
    assert "Run 9 | done | corpus c | config abcdef012345" in text
    assert "wall time 2 min 05 s; 10 papers" in text
    assert "C02: 2 of 8 applicable subjects flagged (25.0%); 10 checked; time 0.1 s" in text
    assert "flags by severity: 1 high, 2 low" in text
    assert "100 references: resolved 90 (90.0%), unresolved 10 (10.0%)" in text
    assert "resolved by stage: doi 80 (88.9%), title_exact 10 (11.1%)" in text
    assert "title matches found in: crossref 6 (6.7%), local 4 (4.4%)" in text
    assert "resolve: 1 h 06 min over 2 run(s) (1 done, 1 stopped); 100 items" in text


def test_render_says_when_nothing_was_recorded():
    text = render_stats_text(_stats(cascade=None, stages=[], finished_at=None, seconds=None))
    assert "wall time not recorded" in text
    assert "not recorded for this run" in text
    assert "none recorded" in text


# --- against the database -------------------------------------------------------------------


class _FlagsFirstPaper(Check):
    """Flags the first paper, has nothing to check on the second."""

    check_id = "C09"
    check_ver = "1.0.0"
    subject = "paper"

    def __init__(self, flag_id: int) -> None:
        self.flag_id = flag_id

    def run(self, ctx: PaperContext) -> list[Finding]:
        if ctx.paper_id == self.flag_id:
            return [Finding(paper_id=ctx.paper_id, check_id="C09", check_ver="1.0.0",
                            severity="high", status="flagged", summary="=cmd|' /C calc'!A0",
                            evidence={"k": 1}),
                    Finding(paper_id=ctx.paper_id, check_id="C09", check_ver="1.0.0",
                            severity="info", status="clean", summary="1 checked.", evidence={"k": 1})]
        return [Finding(paper_id=ctx.paper_id, check_id="C09", check_ver="1.0.0", severity="info",
                        status="not_applicable", summary="Nothing to check.", evidence={"k": 0})]


@pytest.fixture
def seeded(db_engine):
    with db_engine.begin() as conn:
        ids = [conn.execute(insert(paper).values(title=t, title_norm=t, source="test")
                            .returning(paper.c.id)).scalar_one()
               for t in ("stats test paper one", "stats test paper two")]
        conn.execute(insert(reference), [
            {"citing_paper_id": ids[0], "ordinal": 1, "resolution_status": "resolved",
             "resolution_method": "doi", "resolution_evidence": {"stages": [{"stage": 1}]}},
            {"citing_paper_id": ids[0], "ordinal": 2, "resolution_status": "resolved",
             "resolution_method": "title_exact",
             "resolution_evidence": {"stages": [{"stage": "3-5", "pass": "crossref"}]}},
            {"citing_paper_id": ids[1], "ordinal": 1, "resolution_status": "unresolved",
             "resolution_method": None, "resolution_evidence": None},
        ])
    runs: list[int] = []
    yield db_engine, ids, runs
    with db_engine.begin() as conn:
        conn.execute(delete(paper).where(paper.c.id.in_(ids)))
        conn.execute(delete(check_run).where(check_run.c.id.in_(runs)))
        conn.execute(delete(stage_run).where(stage_run.c.corpus_label == LABEL))


@requires_db
def test_record_stage_marks_done_stopped_and_failed(seeded):
    engine, _, _ = seeded
    with record_stage(engine, "resolve", corpus_label=LABEL) as stage:
        stage.items, stage.counts = 3, {"by_status": {"resolved": 3}}
    with pytest.raises(SystemExit):
        with record_stage(engine, "resolve", corpus_label=LABEL) as stage:
            stage.status = "stopped"
            raise SystemExit(3)
    with pytest.raises(RuntimeError):
        with record_stage(engine, "ingest", source="europepmc", corpus_label=LABEL):
            raise RuntimeError("boom")
    with pytest.raises(ValueError):
        with record_stage(engine, "dance", corpus_label=LABEL):
            pass
    with engine.connect() as conn:
        rows = conn.execute(select(stage_run).where(stage_run.c.corpus_label == LABEL)
                            .order_by(stage_run.c.id)).mappings().all()
    assert [(r["stage"], r["status"], r["n_items"]) for r in rows] == [
        ("resolve", "done", 3), ("resolve", "stopped", None), ("ingest", "failed", None)]
    assert all(r["finished_at"] >= r["started_at"] for r in rows)
    assert rows[0]["counts"] == {"by_status": {"resolved": 3}}


@requires_db
def test_stats_and_export_for_a_run(seeded, tmp_path):
    engine, ids, runs = seeded
    with record_stage(engine, "resolve", corpus_label=LABEL) as stage:
        stage.items = 3
    result = run_checks(engine, [_FlagsFirstPaper(ids[0])], config_hash="test",
                        corpus_label=LABEL, paper_ids=ids)
    runs.append(result.run_id)

    with engine.connect() as conn:
        stats = load_run_stats(conn, result.run_id)
        assert load_run_stats(conn, 0) is None
        n = export_findings(conn, result.run_id, tmp_path / "out" / "findings.csv", "csv")
        assert export_findings(conn, result.run_id, tmp_path / "findings.json", "json") == 3
        with pytest.raises(ValueError):
            export_findings(conn, result.run_id, tmp_path / "x", "xlsx")

    [c09] = stats.checks
    assert (c09.subjects, c09.applicable, c09.flagged_subjects, c09.flag_rate) == (2, 1, 1, 1.0)
    assert c09.by_status == {"flagged": 1, "clean": 1, "not_applicable": 1}
    assert c09.flags_by_severity == {"high": 1}
    assert c09.seconds is not None and stats.load_seconds is not None
    assert stats.cascade == {"by_status": {"resolved": 2, "unresolved": 1},
                             "by_method": {"doi": 1, "title_exact": 1}, "by_pass": {"crossref": 1}}
    [resolve] = [s for s in stats.stages if s.corpus_label == LABEL]
    assert (resolve.stage, resolve.runs, resolve.items) == ("resolve", 1, 3)

    assert n == 3
    with (tmp_path / "out" / "findings.csv").open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert list(rows[0]) == COLUMNS
    flagged = next(r for r in rows if r["status"] == "flagged")
    assert flagged["summary"].startswith("'=")  # never runs as a formula
    assert (flagged["subject_type"], flagged["paper_id"]) == ("paper", str(ids[0]))
    as_json = json.loads((tmp_path / "findings.json").read_text("utf-8"))
    assert next(r for r in as_json if r["status"] == "flagged")["summary"] == "=cmd|' /C calc'!A0"
    assert all(isinstance(r["evidence"], dict) for r in as_json)
