"""C05 results reporting. Pure: contexts are built in memory, no database.

The fixture pair is two real studies saved on 2026-10-18, so `fetched_on` is
that date. Assertions are exact on check ID, status and severity (§9.9).
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from sentinel.checks.c05_results_reporting import C05ResultsReporting, months_between
from sentinel.checks.context import TrialContext
from sentinel.ingest.clinicaltrials import ClinicalTrialsClient
from sentinel.schemas import TrialRecord
from tests.conftest import FIXTURES
from tests.test_clinicaltrials import DROP_PATHS

SAVED_ON = date(2026, 10, 18)
C05 = C05ResultsReporting(grace_months=12)
FORBIDDEN = ("fabricated", "non-compliant", "noncompliant", "violation", "violat")


def _fixture_ctx(nct_id: str) -> TrialContext:
    raw = json.loads((FIXTURES / "clinicaltrials" / f"{nct_id}.json").read_text("utf-8"))
    client = ClinicalTrialsClient(
        None, base_url="https://unused.test", drop_paths=DROP_PATHS, strip_personal_fields=True
    )
    return TrialContext(trial=client.normalise(raw), fetched_on=SAVED_ON)


def _raw(start: str | None = "2020-01-10", completion: str | None = "2024-01-15",
         countries: tuple[str, ...] = ("United States",), oversight: dict | None = None) -> dict:
    status = {}
    if start is not None:
        status["startDateStruct"] = {"date": start}
    if completion is not None:
        status["completionDateStruct"] = {"date": completion}
    protocol = {
        "statusModule": status,
        "contactsLocationsModule": {"locations": [{"country": c} for c in countries]},
    }
    if oversight is not None:
        protocol["oversightModule"] = oversight
    return {"protocolSection": protocol}


def _ctx(fetched_on: date = SAVED_ON, **fields) -> TrialContext:
    base = {
        "nct_id": "NCT00000001",
        "overall_status": "COMPLETED",
        "study_type": "INTERVENTIONAL",
        "phase": "PHASE3",
        "primary_completion_date": date(2024, 1, 15),
        "results_first_posted": None,
        "lead_sponsor": "Org",
        "sponsor_class": "OTHER",
        "raw": _raw(),
        "outcomes": [],
    }
    return TrialContext(trial=TrialRecord(**{**base, **fields}), fetched_on=fetched_on)


def _one(ctx: TrialContext):
    findings = C05.run(ctx)
    assert len(findings) == 1
    return findings[0]


# --- the fixture pair ----------------------------------------------------------


def test_fires_on_real_overdue_trial():
    f = _one(_fixture_ctx("NCT02016378"))
    assert (f.check_id, f.status, f.severity) == ("C05", "flagged", "medium")
    assert f.nct_id == "NCT02016378"
    assert f.summary == (
        "No results posted on ClinicalTrials.gov 45 months after primary completion.")
    assert f.evidence["primary_completion_date"] == "2022-12-31"  # from "2022-12"
    assert f.evidence["months_since_primary_completion"] == 45
    assert f.evidence["countries"] == ["United States"]
    assert f.evidence["reason"] == "no_results_after_grace"


def test_does_not_fire_on_real_trial_outside_fdaaa_reach():
    # Brazil-only and not FDA-regulated; it also posted results, but the scope
    # gate decides first (ADR-031).
    f = _one(_fixture_ctx("NCT05711641"))
    assert (f.check_id, f.status, f.severity) == ("C05", "not_applicable", "info")
    assert f.evidence["reason"] == "outside_fdaaa_scope"
    assert f.evidence["countries"] == ["Brazil"]
    assert f.evidence["us_site"] is False


def test_does_not_fire_on_trial_with_results():
    f = _one(_ctx(results_first_posted=date(2025, 1, 1)))
    assert (f.check_id, f.status, f.severity) == ("C05", "clean", "info")


# --- every branch -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("fields", "status", "reason"),
    [
        ({"study_type": "OBSERVATIONAL", "phase": None}, "not_applicable", "not_interventional"),
        ({"phase": "PHASE1"}, "not_applicable", "phase_excluded"),
        ({"phase": "EARLY_PHASE1"}, "not_applicable", "phase_excluded"),
        ({"phase": "EARLY_PHASE1,PHASE1"}, "not_applicable", "phase_excluded"),
        ({"phase": None}, "insufficient_data", "phase_missing"),
        ({"overall_status": "TERMINATED"}, "not_applicable", "not_completed"),
        ({"overall_status": "RECRUITING"}, "not_applicable", "not_completed"),
        ({"primary_completion_date": None}, "insufficient_data", "primary_completion_missing"),
        ({"results_first_posted": date(2025, 1, 1)}, "clean", "results_posted"),
        ({"primary_completion_date": date(2026, 12, 31)}, "clean",
         "primary_completion_after_fetch"),
    ],
)
def test_branches(fields, status, reason):
    f = _one(_ctx(**fields))
    assert f.status == status
    assert f.severity == "info"
    assert f.evidence["reason"] == reason


@pytest.mark.parametrize("phase", ["PHASE1,PHASE2", "PHASE2", "PHASE2,PHASE3", "PHASE4", "NA"])
def test_phases_in_scope(phase):
    assert _one(_ctx(phase=phase)).status == "flagged"


@pytest.mark.parametrize(
    ("fetched_on", "status"),
    [
        (date(2025, 1, 15), "clean"),    # exactly 12 months: not more than the window
        (date(2025, 2, 14), "clean"),    # 12 whole months
        (date(2025, 2, 15), "flagged"),  # 13 months
    ],
)
def test_grace_boundary(fetched_on, status):
    f = _one(_ctx(fetched_on=fetched_on, primary_completion_date=date(2024, 1, 15)))
    assert f.status == status


def test_grace_comes_from_config():
    ctx = _ctx(fetched_on=date(2025, 2, 15), primary_completion_date=date(2024, 1, 15))
    assert C05ResultsReporting(grace_months=24).run(ctx)[0].status == "clean"
    assert C05ResultsReporting(grace_months=6).run(ctx)[0].status == "flagged"


@pytest.mark.parametrize(
    ("start", "end", "months"),
    [
        (date(2024, 1, 15), date(2024, 1, 15), 0),
        (date(2024, 1, 15), date(2024, 2, 14), 0),
        (date(2024, 1, 15), date(2024, 2, 15), 1),
        (date(2022, 12, 31), date(2026, 10, 18), 45),
        (date(2024, 1, 31), date(2024, 2, 29), 0),
    ],
)
def test_months_between(start, end, months):
    assert months_between(start, end) == months


def test_evidence_always_populated_and_wording_is_safe():
    contexts = [
        _fixture_ctx("NCT02016378"),
        _fixture_ctx("NCT05711641"),
        _ctx(study_type="OBSERVATIONAL"),
        _ctx(phase=None),
        _ctx(primary_completion_date=None),
    ]
    for ctx in contexts:
        f = _one(ctx)
        for key in ("study_type", "phase", "overall_status", "record_fetched_on",
                    "grace_months", "sponsor_class", "countries", "reason"):
            assert key in f.evidence
        assert not any(word in f.summary.lower() for word in FORBIDDEN)


def test_run_is_deterministic():
    ctx = _fixture_ctx("NCT02016378")
    assert C05.run(ctx) == C05.run(ctx)


# --- FDAAA era gate (ADR-031) ------------------------------------------------------


@pytest.mark.parametrize(
    ("start", "completion", "status", "reason"),
    [
        # Started on or before 2007-09-27 and completed before 2007-12-26: out.
        ("2004-03-01", "2006-05-01", "not_applicable", "before_fdaaa"),
        ("2007-09-27", "2007-12-25", "not_applicable", "before_fdaaa"),
        # Initiated after 2007-09-27: covered.
        ("2007-09-28", "2007-12-01", "flagged", "no_results_after_grace"),
        # Started earlier but still ongoing on 2007-12-26: covered.
        ("2005-01-01", "2007-12-26", "flagged", "no_results_after_grace"),
        # Month-only dates are read at their earliest day.
        ("2007-09", "2007-11", "not_applicable", "before_fdaaa"),    # could be 1 Sep
        ("2007-10", "2007-11", "flagged", "no_results_after_grace"),  # 1 Oct is after
        ("2007-01", "2007-12", "not_applicable", "before_fdaaa"),    # could be 1 Dec
        ("2006", "2008", "flagged", "no_results_after_grace"),        # 1 Jan 2008
        # One date missing: decide on the other.
        (None, "2009-06-01", "flagged", "no_results_after_grace"),
        (None, "2006-06-01", "not_applicable", "before_fdaaa"),
        ("2010-02-01", None, "flagged", "no_results_after_grace"),
        # Both missing: can't tell.
        (None, None, "insufficient_data", "era_dates_missing"),
    ],
)
def test_fdaaa_era_gate(start, completion, status, reason):
    f = _one(_ctx(raw=_raw(start, completion),
                  primary_completion_date=date(2006, 1, 15)))
    assert (f.status, f.evidence["reason"]) == (status, reason)
    assert f.evidence["start_date_registered"] == start
    assert f.evidence["completion_date_registered"] == completion


@pytest.mark.parametrize(
    ("countries", "oversight", "status", "reason"),
    [
        (("United States",), None, "flagged", "no_results_after_grace"),
        (("Canada", "United States"), None, "flagged", "no_results_after_grace"),
        (("Egypt",), {"isFdaRegulatedDrug": True}, "flagged", "no_results_after_grace"),
        (("Egypt",), {"isFdaRegulatedDevice": True}, "flagged", "no_results_after_grace"),
        (("Egypt",), {"isFdaRegulatedDrug": False, "isFdaRegulatedDevice": False},
         "not_applicable", "outside_fdaaa_scope"),
        (("China", "Spain"), None, "not_applicable", "outside_fdaaa_scope"),
        ((), None, "not_applicable", "outside_fdaaa_scope"),  # no locations recorded
    ],
)
def test_fdaaa_reach_gate(countries, oversight, status, reason):
    f = _one(_ctx(raw=_raw(countries=countries, oversight=oversight)))
    assert (f.status, f.evidence["reason"]) == (status, reason)


def test_real_trials_are_inside_the_era():
    for nct_id in ("NCT02016378", "NCT05711641"):
        assert _one(_fixture_ctx(nct_id)).evidence["reason"] != "before_fdaaa"


def test_check_version_bumped_for_era_gate():
    assert C05.check_ver == "1.1.0"


def test_singular_month():
    f = _one(_ctx(fetched_on=date(2024, 2, 15), primary_completion_date=date(2024, 1, 15)))
    assert "1 month ago" in f.summary
