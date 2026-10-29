"""C04 GRIM (sentinel-project.md §9.7; ADR-021, ADR-038). Pure arithmetic.

The corpus has no extracted means in Phase 1 (ADR-038), so C04 is proven by
constructed fixtures, as ADR-021 anticipated. Every applicability condition
is tested in both directions, and the arithmetic is checked against a brute
force over every possible whole-number total.
"""

from __future__ import annotations

import random

import pytest

from sentinel.checks.c04_grim import C04Grim, applicable, decimals_of, grim_consistent
from sentinel.checks.context import MeanContext, PaperContext

C04 = C04Grim(max_n=100, min_decimals=2, epsilon=1e-9)
GATE = dict(max_n=100, min_decimals=2)


def _mean(text="3.45", n=20, integer_scale=True, items=1, label="pain score (0-10)") -> MeanContext:
    return MeanContext(label=label, mean_text=text, n=n, integer_scale=integer_scale, items=items)


# --- applicability: every condition in both directions -----------------------------------


def test_applies_when_every_condition_holds():
    assert applicable(_mean(), **GATE) is None


@pytest.mark.parametrize(("integer_scale", "applies"), [(True, True), (False, False), (None, False)])
def test_integer_scale_condition(integer_scale, applies):
    result = applicable(_mean(integer_scale=integer_scale), **GATE)
    assert (result is None) is applies
    if not applies:
        assert result[0] == "not_integer_scale"  # unknown counts as no


@pytest.mark.parametrize(("n", "code"), [(20, None), (None, "n_unknown"), (0, "n_unknown")])
def test_n_known_condition(n, code):
    result = applicable(_mean(n=n), **GATE)
    assert (result[0] if result else None) == code


@pytest.mark.parametrize(("n", "code"), [(99, None), (100, "every_mean_possible"), (101, "n_above_max")])
def test_n_limit_condition(n, code):
    # At 2 decimals N = 100 already allows every mean; above max_n the gate says so first.
    result = applicable(_mean(text="3.45", n=n), **GATE)
    assert (result[0] if result else None) == code


def test_max_n_comes_from_config():
    assert applicable(_mean(text="3.456", n=150), max_n=100, min_decimals=2)[0] == "n_above_max"
    assert applicable(_mean(text="3.456", n=150), max_n=200, min_decimals=2) is None


@pytest.mark.parametrize(("text", "code"), [("3.45", None), ("3.456", None), ("3.4", "too_few_decimals"),
                                           ("3", "too_few_decimals")])
def test_decimals_condition(text, code):
    result = applicable(_mean(text=text, n=20), **GATE)
    assert (result[0] if result else None) == code


def test_min_decimals_comes_from_config():
    assert applicable(_mean(text="3.4", n=8), max_n=100, min_decimals=1) is None


@pytest.mark.parametrize(("n", "items", "code"), [(30, 3, None), (40, 3, "every_mean_possible"),
                                                  (20, 0, "items_unknown")])
def test_items_condition(n, items, code):
    result = applicable(_mean(n=n, items=items), **GATE)
    assert (result[0] if result else None) == code


# --- the arithmetic ----------------------------------------------------------------------


@pytest.mark.parametrize(("text", "n", "items", "consistent"), [
    ("3.45", 20, 1, True),    # 69 / 20 = 3.45
    ("3.45", 18, 1, False),   # 62/18 = 3.444, 63/18 = 3.5: neither prints as 3.45
    ("3.45", 10, 1, False),   # 3.40 or 3.50
    ("5.19", 28, 1, False),   # 145/28 = 5.179, 146/28 = 5.214
    ("5.18", 28, 1, True),    # 145/28 = 5.1786 -> 5.18
    ("3.47", 10, 3, True),    # denominator 30: 104/30 = 3.4667 -> 3.47
    ("3.45", 10, 3, False),   # 103/30 = 3.433, 104/30 = 3.467
    ("2.50", 4, 1, True),     # 10 / 4
    ("-1.25", 8, 1, True),    # integer scales can be negative: -10 / 8
    ("0.33", 3, 1, True),     # 1 / 3 = 0.333 -> 0.33
    ("0.34", 3, 1, False),
])
def test_grim_cases(text, n, items, consistent):
    assert grim_consistent(text, n * items, 1e-9)[0] is consistent


def test_rounding_boundary_is_never_misjudged():
    # 7 / 8 = 0.875 prints as 0.88 (half up) or 0.87 (half even); both are consistent.
    assert grim_consistent("0.88", 8, 1e-9)[0]
    assert grim_consistent("0.87", 8, 1e-9)[0]


def _brute_force(text: str, denominator: int) -> bool:
    mean = float(text)
    tol = 0.5 * 10 ** -decimals_of(text)
    lo, hi = int((mean - 1) * denominator) - 2, int((mean + 1) * denominator) + 2
    return any(abs(t / denominator - mean) <= tol + 1e-9 for t in range(lo, hi))


def test_matches_brute_force_on_many_cases():
    rng = random.Random(20260922)
    for _ in range(2000):
        n = rng.randint(2, 99)
        text = f"{rng.uniform(0, 10):.2f}"
        assert grim_consistent(text, n, 1e-9)[0] == _brute_force(text, n), (text, n)


# --- findings ----------------------------------------------------------------------------


def test_findings_flag_each_impossible_mean_plus_summary():
    ctx = PaperContext(paper_id=3, means=(
        _mean("3.45", 20), _mean("3.45", 18, label="fatigue score"),
        _mean("3.45", 20, integer_scale=None), _mean("4.1", 20)))
    findings = C04.run(ctx)
    flags = [f for f in findings if f.status == "flagged"]
    assert len(flags) == 1
    f = flags[0]
    assert (f.severity, f.check_id) == ("medium", "C04")
    assert f.summary == ("Mean 3.45 for fatigue score can't arise from 18 whole-number values: "
                         "no total divided by 18 rounds to it.")
    assert [t["total"] for t in f.evidence["totals_tried"]] == [61, 62, 63, 64]
    summary = [x for x in findings if x.status != "flagged"][0]
    assert (summary.status, summary.summary) == (
        "clean", "2 means checked: 1 possible; 1 not possible from the stated N (flagged separately).")
    assert summary.evidence["not_applicable"] == {"not_integer_scale": 1, "too_few_decimals": 1}


def test_corpus_paper_without_means_is_not_applicable():
    [f] = C04.run(PaperContext(paper_id=1))
    assert (f.status, f.summary) == ("not_applicable", "No means available to check.")


def test_no_applicable_means_is_not_applicable():
    [f] = C04.run(PaperContext(paper_id=1, means=(_mean(n=500), _mean(integer_scale=False))))
    assert f.status == "not_applicable"
    assert f.summary == "None of 2 means meets GRIM's conditions."


def test_wording_never_overstates():
    for f in C04.run(PaperContext(paper_id=1, means=(_mean("3.45", 18),))):
        assert not any(w in f.summary.lower() for w in ("fabricat", "fraud", "invent", "fake"))
