"""Statistic extraction and C03 statistical consistency (sentinel-project.md §9.7;
ADR-009, ADR-037). Pure: no database, no network.

Expected p-values were computed independently with scipy for each case
(two-tailed for t, r, z; upper tail for F and chi-square).
"""

from __future__ import annotations

import pytest

from sentinel.checks.c03_statcheck import C03StatCheck, assess, checkable, p_value
from sentinel.checks.context import PaperContext, StatTestContext
from sentinel.ingest.fulltext_stats import body_paragraphs, extract
from sentinel.parse.statistics import find_statistics

C03 = C03StatCheck(alpha=0.05, allow_one_tailed=True)


def _one(text: str):
    found = find_statistics(text)
    assert len(found) == 1, (text, found)
    return found[0]


def _assess(text: str, **kw):
    return assess(_one(text), alpha=kw.get("alpha", 0.05),
                  allow_one_tailed=kw.get("allow_one_tailed", True))


# --- the five §9.7 patterns ----------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "kind", "stat", "df1", "df2", "op", "p"),
    [
        ("t(58) = 2.10, p = .03", "t", 2.10, 58, None, "=", 0.03),
        ("F(2, 45) = 3.21, p < .05", "F", 3.21, 2, 45, "<", 0.05),
        ("χ2(1, N = 120) = 4.50, p = .034", "chi2", 4.50, 1, None, "=", 0.034),
        ("r(28) = .45, p < .01", "r", 0.45, 28, None, "<", 0.01),
        ("z = 2.10, p = .036", "z", 2.10, None, None, "=", 0.036),
    ],
)
def test_five_patterns_parse(text, kind, stat, df1, df2, op, p):
    t = _one(text)
    assert (t.test_type, t.statistic, t.df1, t.df2, t.p_op, t.reported_p) == (kind, stat, df1, df2, op, p)


@pytest.mark.parametrize(
    ("text", "computed", "consistent", "one_tailed", "inconsistent", "gross"),
    [
        # §9.7's first example is itself inconsistent: t(58) = 2.10 gives p = .040.
        ("t(58) = 2.10, p = .03", 0.0401, False, False, True, False),
        ("F(2, 45) = 3.21, p < .05", 0.0498, True, False, False, False),
        ("χ2(1, N = 120) = 4.50, p = .034", 0.0339, True, False, False, False),
        # Two-tailed p = .013 isn't < .01; one-tailed .0063 is: not flagged (ADR-009).
        ("r(28) = .45, p < .01", 0.0126, False, True, False, False),
        ("z = 2.10, p = .036", 0.0357, True, False, False, False),
    ],
)
def test_five_patterns_assess(text, computed, consistent, one_tailed, inconsistent, gross):
    a = _assess(text)
    assert a.computed_p == pytest.approx(computed, abs=1e-4)
    assert (a.consistent, a.one_tailed_ok, a.inconsistent, a.gross) == (
        consistent, one_tailed, inconsistent, gross)


# --- real statistics from corpus papers ------------------------------------------------
# Each was read in its sentence (2026-10-26) to confirm the p belongs to the statistic.

REAL = [
    # (paper id, as printed, outcome)
    (20, "χ2(3) = 2.2, p = 0.552", "inconsistent"),
    (20, "χ2(3) = 5.60, p = 0.905", "inconsistent"),      # Friedman test; computed .133
    (20, "χ2(3) = 0.97; p = 0.809", "consistent"),
    (264, "F(2,36) = 5.12, p = .008", "inconsistent"),     # computed .011
    (264, "F(2,34) = 3.82, p = .025", "inconsistent"),
    (264, "F(2,34) = 6.59, p = .002", "inconsistent"),
    (264, "F(4,34) = 2.86, p = .030", "inconsistent"),
    (434, "t (168.810) = 0.018, p = 0.86", "inconsistent"),  # computed .986
    (434, "F(1,74) = 0.876, p = 0.38", "inconsistent"),
    (434, "t (37) = 1.34, p = 0.17", "inconsistent"),
    (434, "F(1,74) = 1.39, p = 0.41", "inconsistent"),     # computed .242
    (434, "t (40) = -4.87, p < 0.001", "consistent"),
    (434, "t (145.24) = -3.51, p < 0.001", "consistent"),
    (528, "t(14) = -0.323, p > .1", "consistent"),
    (528, "F(1,29)=7.397, p<.05", "consistent"),
    (528, "F(1,29)=100.191, p<.001", "consistent"),
    (528, "F(2,56)=86.760, p<.001", "consistent"),
    (528, "F(1,29)=9.944, p<.01", "consistent"),
    (448, "t(65.6) = 0.11, p = 0.91", "consistent"),
    (448, "F(1, 73.9) = 4.95, p = 0.029", "consistent"),
    (244, "t(50) = -0.08, p = 0.9348", "consistent"),      # only via the statistic's rounding
    (231, "χ2(4) = 21.01, p = 0.0003", "consistent"),
]


@pytest.mark.parametrize(("paper", "text", "outcome"), REAL, ids=[r[1] for r in REAL])
def test_real_statistics(paper, text, outcome):
    a = _assess(text)
    got = ("consistent" if a.consistent else "one_tailed" if a.one_tailed_ok
           else "gross" if a.gross else "inconsistent")
    assert got == outcome, f"paper {paper}: computed {a.computed_p:.4g}"


def test_real_rank_test_z_values_are_not_rechecked():
    # Paper 20: the p-values come from the exact Wilcoxon V test, not from "Z".
    sentence = ("Post-hoc analysis with Wilcoxon signed-rank tests showed that FSS-7 scores decreased "
                "compared to baseline both at the week (V = 198, Z = 0.777, p = 0.002) and month "
                "(V = 142, Z = 0.535, p = 0.047) time point in the real stimulation group.")
    assert find_statistics(sentence) == []


# --- variants the parser must accept or refuse ----------------------------------------


@pytest.mark.parametrize("text", [
    "t (57.3) = −2·10, P = 0·04",       # Welch df, Unicode minus, middle-dot decimals, capital P
    "t(58)=2.10,p=.03",                  # no spaces
    "F(1,224) = 9.4, p < .01",
    "χ²(2) = 15.5; p < 0.001",           # superscript two, semicolon
    "X2(3) = 7.81, p = .05",
    "chi-square(1) = 3.84, p = .05",
    "Z = 1.96, two-tailed p = .05",
    "t(30) = 2.04, p ≤ .05",
])
def test_variants_parse(text):
    assert len(find_statistics(text)) == 1


@pytest.mark.parametrize("text", [
    "T = 12, p = .03",                   # Wilcoxon T
    "f = 3.2, p = .04",                  # a frequency
    "t(58) = 2.10",                      # no p-value: nothing to recheck
    "mean 3.2 (SD 1.1), p = .03",
    "Mann-Whitney U = 34, Z = -2.1, p = .03",
    "Wilcoxon signed-rank tests (V = 198, Z = 0.777, p = 0.002)",
])
def test_not_parsed(text):
    assert find_statistics(text) == []


def test_several_in_one_sentence_in_order():
    found = find_statistics("Effects of A (F(1, 30) = 5.2, p = .03) and B (F(1, 30) = 1.1, p = .30).")
    assert [t.statistic for t in found] == [5.2, 1.1]


# --- the comparison rules -------------------------------------------------------------


def test_reported_precision_is_respected():
    # t(30) = 2.04 gives .0502; reported to two decimals that is .05.
    assert _assess("t(30) = 2.04, p = .05").consistent
    assert _assess("t(30) = 2.04, p = .050").consistent   # .0502 rounds to .050
    assert not _assess("t(30) = 2.04, p = .0400").consistent


def test_statistic_rounding_is_respected():
    # Reported 2.0 stands for [1.95, 2.05]: p from .060 down to .049, so .05 is reachable.
    assert _assess("t(30) = 2.0, p = .05").consistent


def test_operator_is_respected():
    assert _assess("t(58) = 2.10, p < .05").consistent
    assert not _assess("t(58) = 2.10, p > .05").consistent


def test_one_tailed_rule():
    a = _assess("t(20) = 1.80, p = .04")  # two-tailed .087, one-tailed .043
    assert (a.consistent, a.one_tailed_ok, a.inconsistent) == (False, True, False)
    off = _assess("t(20) = 1.80, p = .04", allow_one_tailed=False)
    assert (off.one_tailed_ok, off.inconsistent, off.gross) == (False, True, True)


@pytest.mark.parametrize("text", ["F(1, 20) = 3.0, p = .049", "χ2(1) = 3.0, p = .042"])
def test_f_and_chi2_are_never_halved(text):
    a = _assess(text)  # both are one-tailed already; halving would hide errors
    assert (a.one_tailed_ok, a.inconsistent) == (False, True)


def test_gross_needs_the_alpha_boundary_between_them():
    assert _assess("t(20) = 1.20, p = .03").gross          # computed .244 vs reported .03
    assert not _assess("t(58) = 2.10, p = .03").gross       # .040 and .03 both below .05
    assert _assess("Z = 0.777, p = 0.002").gross


@pytest.mark.parametrize("text", [
    "χ²(4)=115.235, p<.000",          # paper 528: "p < .000" means "rounds to .000"
    "t(64) = 3.921, p = 0.001",       # paper 728: computed .0002, printed at the floor
    "F(1, 127) = 16.853, p = 0.001",  # paper 728: computed .0001
])
def test_reporting_conventions_count_as_consistent(text):
    assert _assess(text).consistent


@pytest.mark.parametrize(("text", "computed"), [
    ("F(1, 112) = 0.64, p = 0.524", 0.425),   # paper 946: likely a digit swap
    ("t(29)=13.942, p>.001", 0.0),            # paper 528: wrong direction
    ("F (1,37) = 7.14, p = 0.013", 0.0111),   # paper 570
    ("t(102) = 2.410, p = 0.017", 0.0177),    # paper 728: .0177 doesn't round to .017
])
def test_real_discrepancies_stay_flagged(text, computed):
    a = _assess(text)
    assert a.inconsistent and a.computed_p == pytest.approx(computed, abs=1e-3)


def test_floor_convention_only_applies_to_the_smallest_value():
    assert not _assess("t(64) = 3.921, p = 0.002").consistent  # .002 isn't a floor


def test_p_equals_zero_reported_to_three_decimals():
    assert _assess("t(100) = 6.5, p = .000").consistent     # true p ~ 3e-9 rounds to .000


@pytest.mark.parametrize(("text", "why"), [
    ("r(10) = 1.2, p = .01", "correlation of 1 or more"),
    ("t(0) = 2.0, p = .05", "missing or zero degrees of freedom"),
])
def test_uncheckable(text, why):
    assert checkable(_one(text)) == why


def test_p_value_functions():
    t = _one("r(28) = .45, p < .01")
    assert p_value(t, 0.45) == pytest.approx(0.0126, abs=1e-4)
    assert p_value(_one("z = 1.96, p = .05"), 1.96) == pytest.approx(0.05, abs=1e-3)


# --- extraction from JATS ----------------------------------------------------------------

XML = b"""<article><front><article-meta><abstract><p>Main result t(58) = 2.10, p = .03.</p></abstract>
</article-meta></front><body>
<sec><title>Results</title><p>Group effect (F(2, 45) = 3.21, p &lt; .05).</p>
<sec><title>Secondary</title><p>Correlation r(28) = .45, p &lt; .01.</p></sec>
<table-wrap><table><tr><td>t(10) = 9.9, p = .9</td></tr></table></table-wrap></sec>
<sec><title>Methods</title><p>No tests here.</p></sec></body>
<back><ref-list><ref><mixed-citation>Smith J. t(5) = 1.0, p = .5. J. 2000.</mixed-citation></ref></ref-list></back>
</article>"""


def test_body_paragraphs_skip_abstract_tables_and_references():
    paras = body_paragraphs(XML)
    assert [s for s, _ in paras] == ["Results", "Results", "Methods"]  # top-level section names
    assert not any("9.9" in p or "1.0, p" in p for _, p in paras)


def test_extract_rows():
    rows = extract(XML, alpha=0.05, allow_one_tailed=True)
    assert [(r.test_type, r.location, r.reported_p_op) for r in rows] == [
        ("F", "Results", "<"), ("r", "Results", "<")]
    assert rows[1].one_tailed_ok is True and rows[1].inconsistent is False


# --- C03 findings -------------------------------------------------------------------------


def _ctx(*raws, status="full") -> PaperContext:
    return PaperContext(paper_id=7, fulltext_status=status, stat_tests=tuple(
        StatTestContext(stat_test_id=i, location="Results", raw_string=r) for i, r in enumerate(raws, 1)))


def test_findings_flag_each_inconsistency_plus_summary():
    findings = C03.run(_ctx("F(2, 45) = 3.21, p < .05", "t(58) = 2.10, p = .03",
                            "t(20) = 1.20, p = .03", "r(28) = .45, p < .01"))
    flags = [f for f in findings if f.status == "flagged"]
    assert [(f.severity, f.evidence["raw_string"]) for f in flags] == [
        ("low", "t(58) = 2.10, p = .03"), ("high", "t(20) = 1.20, p = .03")]
    assert flags[0].summary == 'Reported "t(58) = 2.10, p = .03", but the statistic gives p = 0.0401.'
    assert flags[1].summary.endswith("on the other side of 0.05.")
    assert {"computed_p", "computed_p_range", "location", "tails", "alpha"} <= set(flags[0].evidence)
    summary = [f for f in findings if f.status != "flagged"][0]
    assert summary.status == "clean"
    assert summary.summary == ("4 reported tests rechecked: 1 consistent; 1 consistent only as "
                               "one-tailed; 1 inconsistent; 1 inconsistent across p = 0.05.")


def test_paper_without_tests_is_not_applicable():
    [f] = C03.run(_ctx())
    assert (f.status, f.summary) == ("not_applicable",
                                     "No recheckable test statistics found in the body text.")


def test_paper_not_examined_is_insufficient_data():
    [f] = C03.run(_ctx("t(58) = 2.10, p = .03", status="abstract"))
    assert (f.status, f.summary) == ("insufficient_data", "Full text not examined for statistics.")


def test_alpha_and_one_tailed_come_from_config():
    ctx = _ctx("t(20) = 1.80, p = .04")
    assert [f.status for f in C03StatCheck(alpha=0.05, allow_one_tailed=True).run(ctx)] == ["clean"]
    strict = C03StatCheck(alpha=0.05, allow_one_tailed=False).run(ctx)
    assert [f.status for f in strict] == ["flagged", "clean"]


def test_wording_never_overstates():
    for f in C03.run(_ctx("t(20) = 1.20, p = .03")):
        assert not any(w in f.summary.lower() for w in ("fabricat", "fraud", "manipulat", "error"))
