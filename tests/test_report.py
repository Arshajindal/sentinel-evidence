"""Finding validation, subject detection, card rendering, and the vertical
slice end to end against the database."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError
from sqlalchemy import delete, select, text

from sentinel.checks.c05_results_reporting import C05ResultsReporting
from sentinel.checks.runner import run_checks
from sentinel.models import check_run, finding, trial
from sentinel.report.card import (
    CardFinding,
    TrialCard,
    detect_subject,
    load_trial_card,
    render_json,
    render_text,
)
from sentinel.schemas import Finding
from tests.conftest import requires_db
from tests.test_clinicaltrials import _client, _serve, _study

# --- Finding -------------------------------------------------------------------

VALID = dict(check_id="C05", check_ver="1.0.0", severity="info", status="clean",
             summary="ok", evidence={"k": 1})


def test_finding_needs_exactly_one_subject():
    Finding(nct_id="NCT00000001", **VALID)
    Finding(paper_id=1, **VALID)
    with pytest.raises(ValidationError):
        Finding(**VALID)
    with pytest.raises(ValidationError):
        Finding(paper_id=1, nct_id="NCT00000001", **VALID)


def test_finding_rejects_empty_evidence():
    with pytest.raises(ValidationError):
        Finding(nct_id="NCT00000001", **{**VALID, "evidence": {}})


def test_finding_never_says_fabricated():
    with pytest.raises(ValidationError, match="fabricated"):
        Finding(nct_id="NCT00000001", **{**VALID, "summary": "Reference Fabricated"})


# --- subject detection (§9.8) ---------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("NCT02016378", ("trial", "NCT02016378")),
        ("nct02016378", ("trial", "NCT02016378")),
        ("10.1186/s44315-025-00043-9", ("doi", "10.1186/s44315-025-00043-9")),
        ("42", ("paper", 42)),
    ],
)
def test_detect_subject(value, expected):
    assert detect_subject(value) == expected


@pytest.mark.parametrize("value", ["NCT123", "PMC12345", "abc"])
def test_detect_subject_rejects(value):
    with pytest.raises(ValueError):
        detect_subject(value)


# --- rendering --------------------------------------------------------------------


def _card(findings) -> TrialCard:
    return TrialCard(nct_id="NCT02016378", brief_title="A trial", overall_status="COMPLETED",
                     study_type="INTERVENTIONAL", phase="PHASE3", lead_sponsor="Org",
                     sponsor_class="OTHER", findings=findings)


def test_render_text_shows_finding_and_run():
    f = CardFinding(check_id="C05", check_ver="1.0.0", severity="medium", status="flagged",
                    summary="No results posted on ClinicalTrials.gov 45 months after "
                            "primary completion.",
                    evidence={"primary_completion_date": "2022-12-31", "grace_months": 12},
                    run_id=7, run_started_at="2026-10-18T10:00:00+00:00",
                    config_hash="abcdef0123456789")
    text = render_text(_card([f]))
    assert "NCT02016378: A trial" in text
    assert "[MEDIUM] C05 flagged: No results posted" in text
    assert "run 7 | 2026-10-18 | check v1.0.0 | config abcdef01" in text
    assert "primary completion date 2022-12-31" in text
    assert json.loads(render_json(_card([f])))["findings"][0]["status"] == "flagged"


def test_render_text_without_findings():
    assert "No findings yet" in render_text(_card([]))


# --- end to end: ingest -> check -> report ----------------------------------------


@pytest.fixture
def slice_engine(db_engine):
    ids = ("NCT05711641", "NCT02016378")
    with db_engine.connect() as conn:
        if conn.execute(select(trial.c.nct_id).where(trial.c.nct_id.not_in(ids))).first():
            pytest.skip("trial holds real data; not a disposable database")
    runs: list[int] = []
    yield db_engine, runs
    with db_engine.begin() as conn:
        conn.execute(delete(trial).where(trial.c.nct_id.in_(ids)))
        if runs:
            conn.execute(delete(check_run).where(check_run.c.id.in_(runs)))


@requires_db
def test_vertical_slice(slice_engine):
    engine, runs = slice_engine
    client = _client(_serve(_study("NCT02016378"), _study("NCT05711641")))
    client.lookup(engine, "NCT02016378")
    client.lookup(engine, "NCT05711641")
    with engine.begin() as conn:  # pin the fetch date so the month count is fixed
        conn.execute(text(
            "UPDATE trial SET ingested_at = '2026-10-18T12:00:00Z' "
            "WHERE nct_id IN ('NCT02016378', 'NCT05711641')"
        ))

    result = run_checks(engine, [C05ResultsReporting(grace_months=12)],
                        config_hash="test", corpus_label="test",
                        nct_ids=["NCT02016378", "NCT05711641"])
    runs.append(result.run_id)
    assert result.counts == {"C05": {"flagged": 1, "clean": 1}}

    with engine.connect() as conn:
        run = conn.execute(select(check_run).where(check_run.c.id == result.run_id)).one()
        stored = conn.execute(select(finding).where(finding.c.run_id == result.run_id)).all()
        overdue = load_trial_card(conn, "NCT02016378")
        posted = load_trial_card(conn, "NCT05711641")
    assert (run.status, run.checks, run.config_hash) == ("done", ["C05"], "test")
    assert all(f.evidence for f in stored)
    assert [(f.check_id, f.status, f.severity) for f in overdue.findings] == [
        ("C05", "flagged", "medium")]
    assert "45 months after primary completion" in render_text(overdue)
    assert [(f.check_id, f.status) for f in posted.findings] == [("C05", "clean")]
