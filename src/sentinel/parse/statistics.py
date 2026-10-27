"""Reported null-hypothesis tests in text (sentinel-project.md §9.7, C03).

Finds a test statistic followed directly by its p-value:

  t(58) = 2.10, p = .03            F(2, 45) = 3.21, p < .05
  χ2(1, N = 120) = 4.50, p = .034  r(28) = .45, p < .01      z = 2.10, p = .036

Tolerant of spacing, case in "p", Unicode minus signs and multiplication-dot
decimals ("2·10"), and of χ written as χ², χ2, X2 or chi-square. A statistic
without an adjacent p-value is skipped: there is nothing to recheck. For
precision, `t` and `r` must be lowercase and `F` uppercase ("T" is often a
Wilcoxon statistic, "f" a frequency), and a z reported alongside a rank test
(Wilcoxon, Mann-Whitney, Kruskal-Wallis, Friedman, or a U/V/W/T/H statistic
in the same sentence) is skipped: its p usually comes from the exact rank
distribution, not from z, and the "Z" is sometimes an effect size (ADR-037).
Pure: text in, matches out.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

TestType = Literal["t", "F", "chi2", "r", "z"]

_NUM = r"[-+]?(?:\d+(?:\.\d+)?|\.\d+)"
_P = (r"\s*[,;]\s*(?:two[- ]tailed\s*,?\s*)?(?:p|P)(?:\s*-?\s*value)?\s*"
      r"(?P<op><=|>=|≤|≥|<|>|=)\s*(?P<p>0?\.\d+|0|1(?:\.0+)?)")
_PATTERNS: list[tuple[TestType, re.Pattern]] = [
    ("t", re.compile(rf"(?<![A-Za-z])t\s*\(\s*(?P<df1>\d+(?:\.\d+)?)\s*\)\s*=\s*(?P<stat>{_NUM}){_P}")),
    ("F", re.compile(rf"(?<![A-Za-z])F\s*\(\s*(?P<df1>\d+(?:\.\d+)?)\s*,\s*(?P<df2>\d+(?:\.\d+)?)\s*\)"
                     rf"\s*=\s*(?P<stat>{_NUM}){_P}")),
    ("chi2", re.compile(rf"(?:χ\s*²|χ\s*2|X\s*2|[Cc]hi[- ]?squared?)\s*\(\s*(?P<df1>\d+)"
                        rf"(?:\s*,\s*[Nn]\s*=\s*[\d,]+)?\s*\)\s*=\s*(?P<stat>{_NUM}){_P}")),
    ("r", re.compile(rf"(?<![A-Za-z])r\s*\(\s*(?P<df1>\d+)\s*\)\s*=\s*(?P<stat>{_NUM}){_P}")),
    ("z", re.compile(rf"(?<![A-Za-z])[zZ]\s*=\s*(?P<stat>{_NUM}){_P}")),
]


_RANK_TEST = re.compile(
    r"(?i)\b(?:wilcoxon|mann[- –]?whitney|kruskal|friedman|signed[- ]rank|rank[- ]sum)"
    r"|(?<![A-Za-z])[UVWTH]\s*=\s*\d")


def _rank_test_context(s: str, start: int) -> bool:
    """Is a rank test named earlier in the same sentence (within 200 chars)?"""
    window = s[max(0, start - 200):start]
    cut = max(window.rfind(". "), window.rfind("? "), window.rfind("! "))
    return bool(_RANK_TEST.search(window[cut + 1:] if cut >= 0 else window))


@dataclass(frozen=True)
class ReportedTest:
    test_type: TestType
    statistic: float
    stat_decimals: int
    df1: float | None
    df2: float | None
    reported_p: float
    p_op: Literal["=", "<", ">"]
    p_inclusive: bool          # ≤ / ≥
    p_decimals: int
    raw_string: str


def normalise_text(text: str) -> str:
    """Unicode minus signs to '-', '·' between digits to '.', thin and
    non-breaking spaces to plain spaces."""
    text = re.sub(r"[−‒–—‐‑﹣－]", "-", text)
    text = re.sub(r"(?<=\d)[·⋅]\s?(?=\d)", ".", text)
    text = re.sub(r"[     ]", " ", text)
    return text


def _decimals(number: str) -> int:
    return len(number.split(".", 1)[1]) if "." in number else 0


def find_statistics(text: str) -> list[ReportedTest]:
    """Every reported test with its p-value, in text order, without overlaps."""
    if not text:
        return []
    s = normalise_text(text)
    found: list[tuple[int, int, ReportedTest]] = []
    for test_type, pattern in _PATTERNS:
        for m in pattern.finditer(s):
            if test_type == "z" and _rank_test_context(s, m.start()):
                continue
            op_raw = m.group("op")
            op = {"≤": "<", "<=": "<", "≥": ">", ">=": ">"}.get(op_raw, op_raw)
            stat_text, p_text = m.group("stat"), m.group("p")
            df1 = m.groupdict().get("df1")
            df2 = m.groupdict().get("df2")
            found.append((m.start(), m.end(), ReportedTest(
                test_type=test_type,
                statistic=float(stat_text),
                stat_decimals=_decimals(stat_text),
                df1=float(df1) if df1 else None,
                df2=float(df2) if df2 else None,
                reported_p=float(p_text),
                p_op=op,
                p_inclusive=op_raw in ("≤", "≥", "<=", ">="),
                p_decimals=_decimals(p_text),
                raw_string=m.group(0).strip(),
            )))
    found.sort(key=lambda x: (x[0], -x[1]))
    out, last_end = [], -1
    for start, end, test in found:
        if start >= last_end:
            out.append(test)
            last_end = end
    return out
