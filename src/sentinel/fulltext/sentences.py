"""Rule-based sentence boundaries for biomedical prose (ADR-046).

A boundary is a full stop, question or exclamation mark, then whitespace,
then a capital, digit, bracket or quote. It is not a boundary after a known
abbreviation ("et al.", "e.g.", "Fig.", "vs.") or a single initial ("J.
Smith"). A citation that follows the punctuation ("in adults.12,13 Next")
belongs to the sentence before it. When unsure, sentences are left joined:
a context one sentence too long is better than one cut mid-claim.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

_END = re.compile(r"[.!?][\"”’)\]]*")
_START = re.compile(r"\s+[\"“‘(\[]?[A-Z0-9]")
_GLUE = re.compile(r"[,;–\-\[\]()]{0,2}")
# Words that end in a full stop without ending a sentence. Units ("h",
# "min") are left out: they end sentences as often as not.
ABBREVIATIONS = frozenset({
    "al", "e.g", "i.e", "eg", "ie", "fig", "figs", "vs", "eq", "eqs", "ref", "refs", "no", "nos",
    "approx", "ca", "cf", "dr", "prof", "vol", "pp", "resp", "suppl", "sp", "spp", "u.s", "u.k",
    "st", "mr", "mrs", "ms", "inc", "ltd", "dept", "univ", "table", "tab", "sect", "ch", "ed",
    "eds", "jr", "sr", "corp",
})


def split(text: str, protected: Sequence[tuple[int, int]] = ()) -> list[tuple[int, int]]:
    """Sentence spans (start, end) covering `text`, whitespace trimmed.
    `protected` spans (the citations) never contain a boundary, and a
    citation right after the punctuation stays with the sentence before."""
    protected = sorted(protected)
    bounds = []
    for m in _END.finditer(text):
        if _inside(m.start(), protected) or _not_an_end(text, m.start()):
            continue
        end = _after_citations(text, m.end(), protected)
        if _START.match(text, end):
            bounds.append(end)
    spans, start = [], 0
    for end in bounds:
        if end > start:
            spans.append((start, end))
            start = end
    if start < len(text):
        spans.append((start, len(text)))
    return [t for s, e in spans if (t := _trim(text, s, e))[1] > t[0]]


def _not_an_end(text: str, i: int) -> bool:
    word = re.search(r"(\S+)\s*$", text[:i])  # "countries 20) ." has a space first
    if not word:
        return True
    token = word.group(1).lstrip("([\"“").lower()
    if token in ABBREVIATIONS:
        return True
    return len(token) == 1 and token.isalpha() and text[i] == "."  # an initial


def _inside(i: int, spans: Sequence[tuple[int, int]]) -> bool:
    return any(s <= i < e for s, e in spans)


def _after_citations(text: str, i: int, spans: Sequence[tuple[int, int]]) -> int:
    """Move past citations that start right where the sentence ends, with at
    most two joining characters (",", "–", brackets) before each."""
    for s, e in spans:
        if s >= i and _GLUE.fullmatch(text, i, s):
            i = e
    return i


def _trim(text: str, s: int, e: int) -> tuple[int, int]:
    while s < e and text[s].isspace():
        s += 1
    while e > s and text[e - 1].isspace():
        e -= 1
    return s, e
