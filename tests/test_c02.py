"""C02 retraction status (sentinel-project.md §9.7; ADR-017, ADR-036). Pure.

`papers.json` holds 12 real corpus papers that cite works with Retraction
Watch notices (13 references), with the expected band for each written from
the notice and dates. The corpus has no real "retracted while in press" case,
so that band, and the edge rules, use constructed contexts below.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from sentinel.checks.c02_retraction import C02Retraction
from sentinel.checks.context import NoticeContext, PaperContext, ReferenceContext
from tests.conftest import FIXTURES

PAPERS = json.loads((FIXTURES / "c02" / "papers.json").read_text("utf-8"))
C02 = C02Retraction(grace_months=6)
PUB = date(2021, 6, 15)


def _per_reference(findings):
    return {f.evidence["reference_id"]: f for f in findings if "reference_id" in f.evidence}


def _summary(findings):
    return next(f for f in findings if "reference_id" not in f.evidence)


# --- real papers ----------------------------------------------------------------------


@pytest.mark.parametrize("case", PAPERS, ids=[str(p["paper"]["paper_id"]) for p in PAPERS])
def test_real_papers(case):
    ctx = PaperContext.model_validate(case["paper"])
    findings = C02.run(ctx)
    by_ref = _per_reference(findings)
    summary = _summary(findings)
    for ref_id, exp in case["expected"].items():
        ref_id = int(ref_id)
        if exp["severity"] is None:
            assert ref_id not in by_ref, exp["why"]
            assert summary.evidence["bands"].get(exp["band"]) >= 1
        else:
            f = by_ref[ref_id]
            assert (f.evidence["band"], f.severity, f.status) == (exp["band"], exp["severity"], "flagged"), exp["why"]
    assert summary.status == "clean"


def test_real_fixture_covers_the_bands_that_occur():
    bands = {e["band"] for p in PAPERS for e in p["expected"].values()}
    assert {"after_grace", "before", "replaced", "expression_of_concern", "corrected"} <= bands


# --- constructed contexts ---------------------------------------------------------------


def _notice(kind="retraction", d=date(2020, 1, 15), reason="Error in Data;") -> NoticeContext:
    return NoticeContext(notice_type=kind, notice_date=d, notice_doi=None, reason=reason,
                         source="retraction_watch", source_record_id="1", matched_on="doi")


def _ctx(*notices, pub=PUB, extra_refs=0) -> PaperContext:
    ref = ReferenceContext(
        reference_id=1, ordinal=4, raw_string="Smith J. Retracted work. J Med. 2019;1:1.",
        citation_tag="mixed-citation", publication_type=None, parsed_doi="10.1/x",
        parsed_pmid=None, parsed_title="Retracted work", parsed_first_author="smith",
        parsed_year=2019, parsed_container="J Med", resolution_status="resolved",
        resolution_method="doi", resolved_paper_id=9, resolution_evidence={},
        notices=tuple(notices))
    others = tuple(ref.model_copy(update={"reference_id": 10 + i, "ordinal": 10 + i, "notices": ()})
                   for i in range(extra_refs))
    return PaperContext(paper_id=1, publication_date=pub, references=(ref, *others))


def _one(ctx):
    found = _per_reference(C02.run(ctx))
    return found.get(1)


@pytest.mark.parametrize(
    ("notice_date", "band", "severity"),
    [
        (date(2021, 6, 15), "before", "low"),          # same day: cited before / at retraction
        (date(2022, 1, 1), "before", "low"),
        (date(2021, 3, 15), "in_press", "low"),        # 3 months
        (date(2020, 12, 15), "in_press", "low"),       # exactly 6 whole months: in press
        (date(2020, 12, 16), "in_press", "low"),       # 5 whole months
        (date(2020, 11, 15), "after_grace", "high"),   # 7 months
        (date(2015, 1, 1), "after_grace", "high"),
    ],
)
def test_timing_bands(notice_date, band, severity):
    f = _one(_ctx(_notice(d=notice_date)))
    assert (f.evidence["band"], f.severity, f.status) == (band, severity, "flagged")


def test_in_press_wording_and_gap():
    f = _one(_ctx(_notice(d=date(2021, 3, 15))))
    assert f.summary == ("Reference 4 cites a work retracted on 2021-03-15, while this paper was "
                         "in press (3 months before publication).")
    assert f.evidence["gap_months"] == 3


def test_grace_comes_from_config():
    ctx = _ctx(_notice(d=date(2020, 11, 15)))  # 7 months
    assert C02Retraction(grace_months=12).run(ctx)[0].evidence["band"] == "in_press"


@pytest.mark.parametrize("reason", ["Retract and Replace;", "Withdrawn as Out of Date;",
                                    "Error in Analyses;Retract and Replace;"])
def test_replaced_is_low_whatever_the_timing(reason):
    f = _one(_ctx(_notice(d=date(2010, 1, 1), reason=reason)))
    assert (f.evidence["band"], f.severity) == ("replaced", "low")
    assert "retracted and replaced" in f.summary


def test_plain_retraction_beside_a_replace_notice_is_not_softened():
    f = _one(_ctx(_notice(d=date(2010, 1, 1), reason="Retract and Replace;"),
                  _notice(d=date(2011, 1, 1), reason="Misconduct by Author;")))
    assert (f.evidence["band"], f.severity) == ("after_grace", "high")


def test_earliest_retraction_dates_the_band():
    f = _one(_ctx(_notice(d=date(2021, 5, 1)), _notice(d=date(2018, 1, 1))))
    assert f.evidence["band"] == "after_grace"
    assert "2018-01-01" in f.summary


def test_reinstated_is_not_flagged():
    findings = C02.run(_ctx(_notice(d=date(2018, 1, 1)), _notice("reinstatement", d=date(2019, 1, 1))))
    assert _per_reference(findings) == {}
    assert _summary(findings).evidence["bands"] == {"reinstated": 1}


def test_reinstatement_before_the_retraction_does_not_count():
    f = _one(_ctx(_notice("reinstatement", d=date(2017, 1, 1)), _notice(d=date(2018, 1, 1))))
    assert f.evidence["band"] == "after_grace"


def test_expression_of_concern_any_timing():
    for d in (date(2010, 1, 1), date(2025, 1, 1)):
        f = _one(_ctx(_notice("expression_of_concern", d=d)))
        assert (f.evidence["band"], f.severity) == ("expression_of_concern", "medium")


def test_retraction_outranks_concern():
    f = _one(_ctx(_notice("expression_of_concern", d=date(2015, 1, 1)), _notice(d=date(2016, 1, 1))))
    assert f.evidence["band"] == "after_grace"


def test_correction_is_never_flagged():
    findings = C02.run(_ctx(_notice("correction")))
    assert _per_reference(findings) == {}
    assert "1 corrected" in _summary(findings).summary


def test_unknown_citing_date_is_insufficient_data():
    f = _one(_ctx(_notice(), pub=None))
    assert (f.status, f.evidence["band"]) == ("insufficient_data", "citing_date_unknown")


def test_unknown_retraction_date_is_insufficient_data():
    f = _one(_ctx(_notice(d=None)))
    assert (f.status, f.evidence["band"]) == ("insufficient_data", "retraction_date_unknown")


def test_evidence_records_the_notice():
    f = _one(_ctx(_notice(d=date(2015, 1, 1))))
    n = f.evidence["notices"][0]
    assert (n["type"], n["date"], n["source"], n["matched_on"]) == (
        "retraction", "2015-01-01", "retraction_watch", "doi")
    assert f.evidence["citing_publication_date"] == "2021-06-15"
    assert f.evidence["grace_months"] == 6


def test_paper_without_notices_gets_only_a_clean_summary():
    ctx = _ctx(extra_refs=3)  # four references, none with a notice
    findings = C02.run(ctx)
    assert len(findings) == 1
    assert (findings[0].status, findings[0].summary) == (
        "clean", "4 of 4 references checked against Retraction Watch: no notices.")


def test_summary_without_references():
    s = _summary(C02.run(PaperContext(paper_id=1, publication_date=PUB)))
    assert (s.status, s.summary) == ("insufficient_data", "No reference list available.")


def test_wording_never_overstates():
    for ctx in (_ctx(_notice(d=date(2010, 1, 1), reason="Falsification/Fabrication of Data;")),
                _ctx(_notice("expression_of_concern"))):
        for f in C02.run(ctx):
            assert "fabricat" not in f.summary.lower()
