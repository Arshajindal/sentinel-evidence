"""C01 reference existence (sentinel-project.md §9.5, §9.7; ADR-035).

`references.json` holds 48 real corpus references with their stored
resolution trail, labelled by reading each one: whether it should be flagged,
and its class where that is clear-cut. Pure: no database, no network.
"""

from __future__ import annotations

import json

import pytest

from sentinel.checks.c01_existence import C01ReferenceExistence, classify
from sentinel.checks.context import PaperContext, ReferenceContext
from tests.conftest import FIXTURES

CASES = json.loads((FIXTURES / "c01" / "references.json").read_text("utf-8"))
PARAMS = dict(near_match_min=0.6, same_title_min=0.85, year_tolerance=1)
C01 = C01ReferenceExistence(**PARAMS)


def _ref(**kw) -> ReferenceContext:
    base = dict(reference_id=1, ordinal=7, raw_string="Smith J. A trial. J Med. 2019;1:1-9.",
                citation_tag="mixed-citation", publication_type=None, parsed_doi=None,
                parsed_pmid=None, parsed_title="A randomised trial of things",
                parsed_first_author="smith", parsed_year=2019, parsed_container="J Med",
                resolution_status="unresolved", resolution_method=None, resolved_paper_id=None,
                resolution_evidence={"reason": "no_candidate_matched", "stages": [],
                                     "near_misses": [], "lookups": {}})
    return ReferenceContext(**{**base, **kw})


def _cls(ref: ReferenceContext) -> str:
    return classify(ref, **PARAMS)[0]


# --- the real fixture -------------------------------------------------------------


@pytest.mark.parametrize("case", CASES, ids=[str(c["reference"]["reference_id"]) for c in CASES])
def test_real_references(case):
    ref = ReferenceContext(**case["reference"])
    cls, reason = classify(ref, **PARAMS)
    assert (cls == "journal_like") is case["expected_flag"], f"{case['note']}: {cls} ({reason})"
    if case["expected_class"]:
        assert cls == case["expected_class"], f"{case['note']}: {reason}"


def test_no_non_journal_reference_is_ever_flagged():
    nonjournal = [c for c in CASES if c["expected_class"] == "nonjournal"]
    assert len(nonjournal) >= 25
    for case in nonjournal:
        assert _cls(ReferenceContext(**case["reference"])) != "journal_like", case["note"]


def test_fixture_has_flag_cases_too():
    assert sum(c["expected_flag"] for c in CASES) == 3


# --- each rule ------------------------------------------------------------------------


def test_plausible_journal_article_with_no_record_is_flagged():
    assert _cls(_ref()) == "journal_like"


@pytest.mark.parametrize("status,cls", [("resolved", "resolved"), ("ambiguous", "ambiguous"),
                                        ("pending", "pending")])
def test_non_unresolved_statuses(status, cls):
    assert _cls(_ref(resolution_status=status)) == cls


def test_doi_registered_elsewhere_is_not_flagged():
    ev = {"stages": [{"stage": 1, "outcome": "doi_not_found", "doi_org_registered": True}]}
    assert _cls(_ref(parsed_doi="10.5061/x", resolution_evidence=ev)) == "doi_registered"
    ev = {"stages": [], "lookups": {"registered_untitled": ["crossref:10.1/x"]}}
    assert _cls(_ref(resolution_evidence=ev)) == "doi_registered"


def test_unregistered_doi_alone_does_not_stop_a_flag():
    ev = {"stages": [{"stage": 1, "outcome": "doi_not_found", "doi_org_registered": False}]}
    assert _cls(_ref(parsed_doi="10.9999/fake", resolution_evidence=ev)) == "journal_like"


@pytest.mark.parametrize("ptype", ["book", "web", "webpage", "other", "confproc", "thesis", "report"])
def test_structured_publication_type_decides(ptype):
    assert _cls(_ref(publication_type=ptype)) == "nonjournal"


def test_journal_publication_type_still_checked():
    assert _cls(_ref(publication_type="journal")) == "journal_like"


@pytest.mark.parametrize("raw", [
    "Smith J. A trial. In: Jones K, editors. Big Book. London: Press; 2019.",
    "Smith J. A trial. 2nd ed. New York: Wiley; 2019.",
    "Smith J. A trial [Internet]. 2019. Available from: https://example.org",
    "Smith J. A trial. Poster presented at the Annual Meeting; 2019.",
    "Smith J. A trial. PhD thesis, University of X; 2019.",
    "Smith J. A trial. medRxiv 2019. doi:10.1101/2019.01.01",
])
def test_text_cues_mark_non_journal(raw):
    assert _cls(_ref(raw_string=raw)) == "nonjournal"


def test_journal_name_itself_is_not_a_cue():
    # "Proceedings" and "Association" appear in real journal names.
    ref = _ref(raw_string="Smith J. A trial. Proceedings of the National Academy of Sciences. 2019;1:1.",
               parsed_container="Proceedings of the National Academy of Sciences")
    assert _cls(ref) == "journal_like"


def test_organisation_author_is_non_journal():
    assert _cls(_ref(parsed_first_author="world health organization")) == "nonjournal"


@pytest.mark.parametrize("near,cls", [
    ({"similarity": 0.64, "first_author": "smith", "year": 2019}, "near_match"),   # same author+year
    ({"similarity": 0.64, "first_author": "smith", "year": 2015}, "journal_like"),  # year too far
    ({"similarity": 0.55, "first_author": "smith", "year": 2019}, "journal_like"),  # too dissimilar
    ({"similarity": 0.88, "first_author": "kongo", "year": 2019}, "near_match"),   # same title, any author
    ({"similarity": 0.80, "first_author": "kongo", "year": 2019}, "journal_like"),
])
def test_near_match_rules(near, cls):
    ev = {"near_misses": [{"paper_id": 9, **near}]}
    assert _cls(_ref(resolution_evidence=ev)) == cls


def test_near_match_thresholds_come_from_config():
    ev = {"near_misses": [{"paper_id": 9, "similarity": 0.64, "first_author": "smith", "year": 2019}]}
    ref = _ref(resolution_evidence=ev)
    assert classify(ref, near_match_min=0.7, same_title_min=0.85, year_tolerance=1)[0] == "journal_like"


@pytest.mark.parametrize("missing", [
    dict(parsed_title=None), dict(parsed_title="Pain"), dict(parsed_first_author=None),
    dict(parsed_year=None), dict(parsed_container=None),
])
def test_incomplete_parse_is_unparseable_not_flagged(missing):
    assert _cls(_ref(**missing)) == "unparseable"


# --- findings ----------------------------------------------------------------------------


def test_findings_flag_per_reference_plus_summary():
    refs = (_ref(reference_id=1, ordinal=1, resolution_status="resolved", resolution_method="doi"),
            _ref(reference_id=2, ordinal=2),
            _ref(reference_id=3, ordinal=3, publication_type="book"),
            _ref(reference_id=4, ordinal=4, parsed_title=None))
    findings = C01.run(PaperContext(paper_id=42, references=refs))
    flags = [f for f in findings if f.status == "flagged"]
    summary = [f for f in findings if f.status != "flagged"]
    assert len(flags) == 1 and len(summary) == 1
    flag = flags[0]
    assert (flag.paper_id, flag.check_id, flag.severity) == (42, "C01", "medium")
    assert flag.summary == ("Reference 2 could not be verified: no matching record in "
                            "Crossref or OpenAlex.")
    assert flag.evidence["reference_id"] == 2 and flag.evidence["raw_string"]
    assert {"stages", "near_misses", "lookups", "parsed"} <= set(flag.evidence)
    s = summary[0]
    assert (s.status, s.severity) == ("clean", "info")
    assert s.evidence["counts"] == {"resolved": 1, "journal_like": 1, "nonjournal": 1, "unparseable": 1}
    assert "1 could not be verified (flagged separately)" in s.summary


def test_flag_mentions_unregistered_doi():
    ev = {"stages": [{"stage": 1, "outcome": "doi_not_found", "doi_org_registered": False}]}
    f = C01.run(PaperContext(paper_id=1, references=(
        _ref(parsed_doi="10.9999/x", resolution_evidence=ev),)))[0]
    assert f.summary.endswith("; its DOI is not registered at doi.org.")


@pytest.mark.parametrize("refs,summary", [
    ((), "No reference list available."),
    ((_ref(resolution_status="pending"),), "1 of 1 references not resolved yet."),
    ((_ref(parsed_title=None), _ref(reference_id=2, parsed_year=None)),
     "No reference could be parsed well enough to check."),
])
def test_summary_insufficient_data(refs, summary):
    findings = C01.run(PaperContext(paper_id=1, references=refs))
    assert [(f.status, f.summary) for f in findings] == [("insufficient_data", summary)]


def test_clean_paper_has_only_the_summary():
    refs = tuple(_ref(reference_id=i, ordinal=i, resolution_status="resolved",
                      resolution_method="doi") for i in range(1, 4))
    findings = C01.run(PaperContext(paper_id=1, references=refs))
    assert [(f.status, f.summary) for f in findings] == [("clean", "3 references checked: 3 resolved.")]


def test_wording_never_overstates():
    findings = C01.run(PaperContext(paper_id=1, references=(_ref(),)))
    for f in findings:
        text = f.summary.lower()
        assert "fabricat" not in text and "fake" not in text and "does not exist" not in text
