"""The fixture set (sentinel-project.md §9.9): real papers and trials with
hand-checked expected findings.

Every check runs on every item with the thresholds in `.env.example`, and the
findings must equal `expected` exactly: check ID, status, severity, and which
reference or statistic each per-item finding is about. An extra finding, a
missing one, or a different severity fails, so "zero unexpected findings"
(§9.10 item 3) is what this asserts. Inputs were frozen from the database by
`tests/fixtures/set/freeze.py`; the expectations were written by hand from the
reference strings, notices, statistics and registry dates (see each file's
`notes` and `why`).
"""

from __future__ import annotations

import json
from collections import Counter

import pytest
from dotenv import dotenv_values

from sentinel.checks.c01_existence import C01ReferenceExistence
from sentinel.checks.c02_retraction import C02Retraction
from sentinel.checks.c03_statcheck import C03StatCheck
from sentinel.checks.c04_grim import C04Grim
from sentinel.checks.c05_results_reporting import C05ResultsReporting
from sentinel.checks.context import PaperContext, TrialContext
from tests.conftest import FIXTURES, ROOT

SET = FIXTURES / "set"
FILES = sorted(SET.glob("papers/*.json")) + sorted(SET.glob("trials/*.json"))
ITEMS = {p.stem: json.loads(p.read_text("utf-8")) for p in FILES}
MANIFEST = json.loads((SET / "manifest.json").read_text("utf-8"))

ENV = dotenv_values(ROOT / ".env.example")


def _f(key: str) -> float:
    return float(ENV[f"SENTINEL_{key}"])


PAPER_CHECKS = [
    C01ReferenceExistence(near_match_min=_f("C01_NEAR_MATCH_MIN"),
                          same_title_min=_f("C01_SAME_TITLE_MIN"),
                          year_tolerance=int(_f("RESOLVE_YEAR_TOLERANCE"))),
    C02Retraction(grace_months=int(_f("C02_GRACE_MONTHS"))),
    C03StatCheck(alpha=_f("C03_ALPHA"), allow_one_tailed=ENV["SENTINEL_C03_ALLOW_ONE_TAILED"] == "true"),
    C04Grim(max_n=int(_f("C04_MAX_N")), min_decimals=int(_f("C04_MIN_DECIMALS")),
            epsilon=_f("C04_EPSILON")),
]
TRIAL_CHECKS = [C05ResultsReporting(grace_months=int(_f("C05_GRACE_MONTHS")))]


def _item(finding, ctx) -> str | None:
    """Which reference or statistic a per-item finding is about; None for a summary."""
    ev = finding.evidence
    if "reference_id" in ev:
        ref = next(r for r in ctx.references if r.reference_id == ev["reference_id"])
        return f"ref {ref.ordinal}"
    if "stat_test_id" in ev:
        pos = next(i for i, s in enumerate(ctx.stat_tests, 1) if s.stat_test_id == ev["stat_test_id"])
        return f"stat {pos}"
    if finding.check_id == "C04" and "label" in ev:
        return f"mean {ev['label']}"
    return None


def run_item(doc: dict) -> list[tuple]:
    if doc["subject"] == "paper":
        ctx = PaperContext.model_validate(doc["context"])
        findings = [f for c in PAPER_CHECKS for f in c.run(ctx)]
    else:
        ctx = TrialContext.model_validate(doc["context"])
        findings = [f for c in TRIAL_CHECKS for f in c.run(ctx)]
    # C02's timing band is compared too: a right severity from the wrong band is a bug.
    return sorted((f.check_id, f.status, f.severity, _item(f, ctx),
                   f.evidence.get("band") if f.check_id == "C02" and _item(f, ctx) else None)
                  for f in findings)


def _expected(doc: dict) -> list[tuple]:
    return sorted((e["check"], e["status"], e["severity"], e.get("item"), e.get("band"))
                  for e in doc["expected"])


@pytest.mark.parametrize("name", list(ITEMS))
def test_findings_match_hand_checked_expectations(name):
    doc = ITEMS[name]
    assert doc["expected"] is not None, f"{name} has not been checked by hand yet"
    assert run_item(doc) == _expected(doc)


# --- the set itself meets §9.9 --------------------------------------------------------------


def _with(category: str) -> list[dict]:
    return [d for d in ITEMS.values() if category in d["categories"]]


def _flags(doc: dict, check: str) -> list[dict]:
    return [e for e in doc["expected"] if e["check"] == check and e["status"] == "flagged"]


def test_size_and_manifest():
    assert 30 <= len(ITEMS) <= 50
    assert {m["id"] for m in MANIFEST} == set(ITEMS)


def test_retracted_citations_cover_all_three_timing_bands():
    c02 = [d for d in ITEMS.values() if _flags(d, "C02")]
    assert len(c02) >= 5
    bands = Counter(e["band"] for d in c02 for e in _flags(d, "C02"))
    assert {"after_grace", "in_press", "before"} <= set(bands)


def test_at_least_five_papers_with_hand_checked_statistical_inconsistencies():
    assert len([d for d in ITEMS.values() if _flags(d, "C03")]) >= 5


def test_non_journal_references_are_never_flagged():
    papers = _with("c01_non_journal_references")
    assert len(papers) >= 5
    for d in papers:
        kinds = [r for r in d["context"]["references"] if r["publication_type"] in ("book", "thesis")
                 or any(w in (r["raw_string"] or "").lower()
                        for w in ("personal communication", "thesis", "dissertation", "unpublished"))]
        assert kinds, d["id"]
        flagged = {e["item"] for e in _flags(d, "C01")}
        assert not flagged & {f"ref {r['ordinal']}" for r in kinds}, d["id"]


def test_clean_papers_have_no_flags_at_all():
    clean = _with("clean")
    assert len(clean) >= 5
    for d in clean:
        assert all(e["status"] != "flagged" for e in d["expected"]), d["id"]
        assert run_item(d) == _expected(d)


def test_trials_cover_overdue_posted_and_out_of_scope():
    def n(reason_status: str) -> int:
        return sum(1 for d in ITEMS.values() if d["subject"] == "trial"
                   for e in d["expected"] if e["status"] == reason_status)
    assert n("flagged") >= 3
    assert len(_with("c05_results_posted")) >= 3
    out = _with("c05_out_of_scope_phase") + _with("c05_out_of_scope_observational")
    assert len(out) >= 2
    assert all(e["status"] == "not_applicable" for d in out for e in d["expected"])


@pytest.mark.parametrize("category", ["edge_unicode_author", "edge_doi_trailing_punctuation",
                                      "edge_no_year"])
def test_edge_cases_are_present(category):
    assert _with(category)


def test_every_flag_and_gap_says_why():
    for d in ITEMS.values():
        for e in d["expected"]:
            if e["status"] in ("flagged", "insufficient_data"):
                assert e.get("why"), (d["id"], e)
