"""C04 — impossible means, GRIM (sentinel-project.md §9.7; ADR-021, ADR-038).

The mean of N whole-number values is a whole-number total divided by N. If no
whole-number total divided by N rounds to the printed mean, the mean can't
come from N such values. For a measure averaged over k items per participant
the denominator is N x k.

The check applies only when every condition holds; otherwise not_applicable,
never a guess:
  - the measure is known to be integer-valued (unknown counts as no);
  - N is known and at most C04_MAX_N;
  - the mean is printed to at least C04_MIN_DECIMALS decimals;
  - N x k is below 10^decimals (at or above it every mean is possible).

Totals on both sides of mean x N are tried, not only the nearest, so a mean
at a rounding boundary is never misjudged. A flag is `medium` and says the
mean can't arise from the stated data; it says nothing about why.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any

from sentinel.checks.base import Check
from sentinel.checks.context import MeanContext, PaperContext
from sentinel.schemas import Finding


def decimals_of(text: str) -> int:
    t = text.strip().replace("−", "-")
    return len(t.split(".", 1)[1]) if "." in t else 0


def applicable(m: MeanContext, *, max_n: int, min_decimals: int) -> tuple[str, str] | None:
    """(reason code, explanation) if GRIM can't be applied to this mean, else None."""
    if m.integer_scale is not True:
        return "not_integer_scale", "measure not known to be integer-valued"
    if m.n is None or m.n <= 0:
        return "n_unknown", "N unknown"
    if m.n > max_n:
        return "n_above_max", f"N {m.n} above {max_n}"
    d = decimals_of(m.mean_text)
    if d < min_decimals:
        return "too_few_decimals", f"mean printed to {d} decimals, fewer than {min_decimals}"
    if m.items < 1:
        return "items_unknown", "item count unknown"
    if m.n * m.items >= 10 ** d:
        return ("every_mean_possible",
                f"N x items ({m.n * m.items}) too large for {d} decimals: every mean is possible")
    return None


def grim_consistent(mean_text: str, denominator: int, epsilon: float) -> tuple[bool, list[dict]]:
    """Is there a whole-number total T with T / denominator printing as `mean_text`?

    Returns the answer and the nearest totals tried, for the evidence.
    """
    mean = float(mean_text.strip().replace("−", "-"))
    tolerance = 0.5 * 10 ** -decimals_of(mean_text)
    product = mean * denominator
    tried = []
    for total in sorted({math.floor(product) - 1, math.floor(product),
                         math.ceil(product), math.ceil(product) + 1}):
        value = total / denominator
        tried.append({"total": total, "mean": value, "distance": abs(value - mean)})
    consistent = any(t["distance"] <= tolerance + epsilon for t in tried)
    return consistent, tried


class C04Grim(Check):
    check_id = "C04"
    check_ver = "1.0.0"
    subject = "paper"

    def __init__(self, *, max_n: int, min_decimals: int, epsilon: float) -> None:
        self.max_n = max_n
        self.min_decimals = min_decimals
        self.epsilon = epsilon

    def run(self, ctx: PaperContext) -> list[Finding]:
        findings: list[Finding] = []
        reasons: Counter = Counter()
        checked = consistent = 0
        for m in ctx.means:
            why_not = applicable(m, max_n=self.max_n, min_decimals=self.min_decimals)
            if why_not:
                reasons[why_not[0]] += 1
                continue
            checked += 1
            ok, tried = grim_consistent(m.mean_text, m.n * m.items, self.epsilon)
            if ok:
                consistent += 1
                continue
            unit = "values" if m.items == 1 else f"values of a {m.items}-item average"
            findings.append(Finding(
                paper_id=ctx.paper_id, check_id=self.check_id, check_ver=self.check_ver,
                severity="medium", status="flagged",
                summary=(f"Mean {m.mean_text} for {m.label} can't arise from {m.n} whole-number "
                         f"{unit}: no total divided by {m.n * m.items} rounds to it."),
                evidence={"label": m.label, "mean_text": m.mean_text, "n": m.n, "items": m.items,
                          "denominator": m.n * m.items, "decimals": decimals_of(m.mean_text),
                          "tolerance": 0.5 * 10 ** -decimals_of(m.mean_text),
                          "totals_tried": tried, "source": m.source, "epsilon": self.epsilon},
            ))
        evidence: dict[str, Any] = {"means": len(ctx.means), "checked": checked,
                                    "consistent": consistent, "not_applicable": dict(reasons),
                                    "max_n": self.max_n, "min_decimals": self.min_decimals}
        if checked == 0:
            summary = ("No means available to check." if not ctx.means else
                       f"None of {len(ctx.means)} means meets GRIM's conditions.")
            findings.append(Finding(paper_id=ctx.paper_id, check_id=self.check_id,
                                    check_ver=self.check_ver, severity="info",
                                    status="not_applicable", summary=summary, evidence=evidence))
        else:
            impossible = checked - consistent
            findings.append(Finding(
                paper_id=ctx.paper_id, check_id=self.check_id, check_ver=self.check_ver,
                severity="info", status="clean",
                summary=(f"{checked} means checked: {consistent} possible"
                         + (f"; {impossible} not possible from the stated N (flagged separately)."
                            if impossible else ".")),
                evidence=evidence))
        return findings
