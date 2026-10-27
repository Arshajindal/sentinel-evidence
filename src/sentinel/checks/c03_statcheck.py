"""C03 — statistical consistency (sentinel-project.md §9.7; ADR-009, ADR-037).

Each reported test's p-value is recomputed from its statistic and degrees of
freedom (two-tailed for t, r and z; F and chi-square are one-tailed by
definition). The comparison respects precision on both sides: a statistic
reported as 2.10 could be anything in [2.095, 2.105], which gives a range of
p-values, and a p reported as .03 stands for anything that rounds to .03.

  consistent        the reported p can arise from the reported statistic
  one-tailed only   consistent once halved (t, r, z): not flagged (ADR-009)
  inconsistent      neither                                   -> low
  gross             inconsistent, and the two p-values fall on opposite
                    sides of alpha                              -> high
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import Any

from scipy import stats

from sentinel.checks.base import Check
from sentinel.checks.context import PaperContext
from sentinel.parse.statistics import ReportedTest, find_statistics
from sentinel.schemas import Finding

TWO_TAILED = frozenset({"t", "r", "z"})


@dataclass(frozen=True)
class Assessment:
    computed_p: float            # from the reported statistic as printed
    p_range: tuple[float, float]  # over the statistic's rounding interval
    consistent: bool
    one_tailed_ok: bool
    inconsistent: bool
    gross: bool


def p_value(test: ReportedTest, statistic: float) -> float:
    """The test's p-value at `statistic` (two-tailed for t, r, z)."""
    x = abs(statistic)
    if test.test_type == "t":
        return float(2 * stats.t.sf(x, test.df1))
    if test.test_type == "z":
        return float(2 * stats.norm.sf(x))
    if test.test_type == "r":
        if x >= 1:
            return 0.0
        df = test.df1
        return float(2 * stats.t.sf(x * math.sqrt(df / (1 - x * x)), df))
    if test.test_type == "F":
        return float(stats.f.sf(x, test.df1, test.df2))
    if test.test_type == "chi2":
        return float(stats.chi2.sf(x, test.df1))
    raise ValueError(f"unknown test {test.test_type}")


def checkable(test: ReportedTest) -> str | None:
    """Why a test can't be rechecked, or None if it can."""
    if test.test_type in ("t", "r", "chi2") and not (test.df1 and test.df1 > 0):
        return "missing or zero degrees of freedom"
    if test.test_type == "F" and not (test.df1 and test.df2 and test.df1 > 0 and test.df2 > 0):
        return "missing or zero degrees of freedom"
    if test.test_type in ("F", "chi2") and test.statistic < 0:
        return "negative F or chi-square"
    if test.test_type == "r" and abs(test.statistic) >= 1:
        return "correlation of 1 or more"
    if not 0 <= test.reported_p <= 1:
        return "p outside [0, 1]"
    return None


def _matches(lo: float, hi: float, test: ReportedTest) -> bool:
    """Can a p in [lo, hi] be what was printed? Two common reporting conventions
    count as consistent (ADR-037): the smallest value at the stated precision
    printed as a floor (p = .001 for any p below it), and "p < .000" for
    "rounds to .000". Truncation is not accepted: at two decimals it would let
    .03 stand for anything up to .0399."""
    eps = 1e-12
    p, k = test.reported_p, test.p_decimals
    unit = 10 ** -k
    if test.p_op == "=":
        rounded = lo <= p + unit / 2 + eps and hi >= p - unit / 2 - eps
        floor = k > 0 and math.isclose(p, unit) and lo < p
        return rounded or floor
    if test.p_op == "<":
        if p == 0:  # "p < .000": below what rounds to zero at that precision
            p = 0.5 * 10 ** -max(k, 3)
        return lo <= p + eps if test.p_inclusive else lo < p
    return hi >= p - eps if test.p_inclusive else hi > p


def assess(test: ReportedTest, *, alpha: float, allow_one_tailed: bool) -> Assessment:
    half = 0.5 * 10 ** -test.stat_decimals
    x = abs(test.statistic)
    upper = x + half
    if test.test_type == "r":
        upper = min(upper, 1 - 1e-12)
    lo, hi = p_value(test, upper), p_value(test, max(x - half, 0.0))
    point = p_value(test, x)
    consistent = _matches(lo, hi, test)
    one_tailed = (not consistent and allow_one_tailed and test.test_type in TWO_TAILED
                  and _matches(lo / 2, hi / 2, test))
    inconsistent = not consistent and not one_tailed
    gross = False
    if inconsistent:
        p = test.reported_p
        if test.p_op == "<":
            reported_sig: bool | None = p <= alpha
        elif test.p_op == "=":
            reported_sig = p < alpha
        else:  # "p > x": says "not significant" only if x is at least alpha
            reported_sig = False if p >= alpha else None
        gross = reported_sig is not None and reported_sig != (point < alpha)
    return Assessment(point, (lo, hi), consistent, one_tailed, inconsistent, gross)


class C03StatCheck(Check):
    check_id = "C03"
    check_ver = "1.0.0"
    subject = "paper"

    def __init__(self, *, alpha: float, allow_one_tailed: bool) -> None:
        self.alpha = alpha
        self.allow_one_tailed = allow_one_tailed

    def run(self, ctx: PaperContext) -> list[Finding]:
        if ctx.fulltext_status != "full":
            return [self._summary(ctx, "insufficient_data", "Full text not examined for statistics.", {})]
        findings: list[Finding] = []
        tally: Counter = Counter()
        for st in ctx.stat_tests:
            parsed = find_statistics(st.raw_string)
            if not parsed:
                tally["unparsed"] += 1
                continue
            test = parsed[0]
            why_not = checkable(test)
            if why_not:
                tally["not_checkable"] += 1
                continue
            a = assess(test, alpha=self.alpha, allow_one_tailed=self.allow_one_tailed)
            if a.consistent:
                tally["consistent"] += 1
            elif a.one_tailed_ok:
                tally["one_tailed_only"] += 1
            else:
                tally["gross" if a.gross else "inconsistent"] += 1
                findings.append(self._flag(ctx, st, test, a))
        checked = sum(tally[k] for k in ("consistent", "one_tailed_only", "inconsistent", "gross"))
        if not ctx.stat_tests or checked == 0:
            findings.append(self._summary(ctx, "not_applicable",
                                          "No recheckable test statistics found in the body text.",
                                          dict(tally)))
            return findings
        parts = [f"{tally['consistent']} consistent"]
        if tally["one_tailed_only"]:
            parts.append(f"{tally['one_tailed_only']} consistent only as one-tailed")
        if tally["inconsistent"]:
            parts.append(f"{tally['inconsistent']} inconsistent")
        if tally["gross"]:
            parts.append(f"{tally['gross']} inconsistent across p = {self.alpha:g}")
        findings.append(self._summary(ctx, "clean",
                                      f"{checked} reported tests rechecked: " + "; ".join(parts) + ".",
                                      dict(tally)))
        return findings

    def _flag(self, ctx: PaperContext, st, test: ReportedTest, a: Assessment) -> Finding:
        # The paper's own wording, so a reviewer can find it in the text.
        if a.gross:
            severity = "high"
            summary = (f"Reported \"{st.raw_string}\", but the statistic gives "
                       f"p = {a.computed_p:.3g}, on the other side of {self.alpha:g}.")
        else:
            severity = "low"
            summary = f"Reported \"{st.raw_string}\", but the statistic gives p = {a.computed_p:.3g}."
        return Finding(
            paper_id=ctx.paper_id, check_id=self.check_id, check_ver=self.check_ver,
            severity=severity, status="flagged", summary=summary,
            evidence={
                "stat_test_id": st.stat_test_id, "location": st.location, "raw_string": st.raw_string,
                "test_type": test.test_type, "statistic": test.statistic,
                "statistic_decimals": test.stat_decimals, "df1": test.df1, "df2": test.df2,
                "reported_p": test.reported_p, "reported_p_op": test.p_op,
                "reported_p_inclusive": test.p_inclusive, "reported_p_decimals": test.p_decimals,
                "computed_p": a.computed_p, "computed_p_range": list(a.p_range),
                "tails": 2 if test.test_type in TWO_TAILED else 1,
                "one_tailed_checked": self.allow_one_tailed and test.test_type in TWO_TAILED,
                "gross": a.gross, "alpha": self.alpha,
            },
        )

    def _summary(self, ctx: PaperContext, status: str, summary: str, tally: dict[str, Any]) -> Finding:
        return Finding(paper_id=ctx.paper_id, check_id=self.check_id, check_ver=self.check_ver,
                       severity="info", status=status, summary=summary,
                       evidence={"stat_tests": len(ctx.stat_tests), "counts": tally,
                                 "alpha": self.alpha, "allow_one_tailed": self.allow_one_tailed,
                                 "fulltext_status": ctx.fulltext_status})
