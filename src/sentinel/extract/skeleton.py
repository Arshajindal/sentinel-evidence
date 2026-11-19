"""The claim skeleton and the rules that make one valid (§10.1 item 6, ADR-053).

A skeleton says what a citing sentence attributes to one citation: what kind
of claim it is and, for a finding, its population, intervention, comparator,
outcome, effect, sample size and direction.

The gateway's strict JSON schema already fixes the shape, so validity is
mostly about grounding. Every text field must be copied from the context,
the statement from the citing sentence itself, and a sample size must be a
number written there. A paraphrased or invented span fails, and that failure
rate is S2.7's metric.

The prompt asks for the statement without the citation marker, so from 1.1.0
a statement may leave out any of the sentence's citations (ADR-056). 1.0.0
wanted one exact run of the sentence and rejected those faithful copies.

Context text is untrusted data from papers. Nothing here acts on it; it is
only compared against.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Kind = Literal["finding", "background", "method", "other"]
Direction = Literal["increase", "decrease", "no_effect", "unspecified"]

# Fields that only a finding fills, in the order they're reported.
FINDING_FIELDS = ("population", "intervention", "comparator", "outcome", "effect", "n", "direction")
# Text fields copied from anywhere in the context.
SPAN_FIELDS = ("population", "intervention", "comparator", "outcome", "effect")
# Longest span accepted. A real field is a phrase; a span this long is the
# model copying whole sentences.
MAX_SPAN_CHARS = 400


class ClaimSkeleton(BaseModel):
    """What the model returns. Every field is required, so the JSON schema
    sent to the gateway lists them all (strict mode); "not stated" is null."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Kind
    statement: str
    population: str | None
    intervention: str | None
    comparator: str | None
    outcome: str | None
    effect: str | None
    n: int | None
    direction: Direction | None


def response_schema() -> dict:
    """The JSON schema for the gateway's `response_format` (strict). The
    class docstring is for readers of this code, not for the model."""
    schema = ClaimSkeleton.model_json_schema()
    schema.pop("description", None)
    return schema


_DASHES = dict.fromkeys(map(ord, "‐‑‒–—―−﹘﹣－"), "-")
_QUOTES = {ord("‘"): "'", ord("’"): "'", ord("“"): '"', ord("”"): '"'}
_EDGE = " \t\n.,;:"


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold().translate(_DASHES).translate(_QUOTES)


def norm(text: str) -> str:
    """Compare text the way a reader would: Unicode-compatible forms, case,
    dashes, quotes and runs of whitespace don't matter."""
    return " ".join(_fold(text).split()).strip(_EDGE)


def _squash(text: str) -> str:
    return "".join(_fold(text).split())


def statement_in_sentence(statement: str, sentence: str,
                          cites: Sequence[tuple[int, int]] = ()) -> bool:
    """True when the statement is a run of the sentence's words, with any of
    the citation groups at `cites` (offsets into the sentence) left out:
    "Halse et al. noted" is in "Halse et al. [60] noted". When a marker is
    left out, spacing is ignored, since the marker's spaces stay behind."""
    if norm(statement) in norm(sentence):
        return True  # 1.0.0's rule
    if not cites:
        return False
    text, skips, pos = "", {}, 0
    for start, end in sorted(cites):
        if start < pos:
            continue  # overlapping spans: keep the first
        text += _squash(sentence[pos:start])
        mark = _squash(sentence[start:end])
        if mark:
            skips[len(text)] = len(text) + len(mark)
        text += mark
        pos = end
    text += _squash(sentence[pos:])
    return _reads_off(_squash(norm(statement)), text, skips)


def _reads_off(target: str, text: str, skips: dict[int, int]) -> bool:
    """Whether `target` reads off `text` from some position onwards, where
    the block starting at a key of `skips` may be jumped to its end."""
    def reach(positions: Iterable[int]) -> set[int]:
        out, stack = set(), list(positions)
        while stack:
            p = stack.pop()
            if p not in out:
                out.add(p)
                if p in skips:
                    stack.append(skips[p])
        return out

    states = reach(range(len(text) + 1))
    for ch in target:
        states = reach(p + 1 for p in states if p < len(text) and text[p] == ch)
        if not states:
            return False
    return bool(target)


_NUMBER = re.compile(r"\d(?:[\d,]*\d)?")


def numbers_in(text: str) -> set[int]:
    """Whole numbers written in digits, with thousands separators allowed
    ("1,234"). Digits inside a decimal count too ("0.5" gives 0 and 5), which
    errs towards accepting a sample size, never towards rejecting one."""
    return {int(m.group().replace(",", "")) for m in _NUMBER.finditer(text)}


def grounding_errors(skeleton: ClaimSkeleton, *, sentence: str, context: str,
                     cites: Sequence[tuple[int, int]] = ()) -> list[str]:
    """Rule codes the skeleton breaks, empty if it is valid. Codes, not
    messages, so they can be counted and logged without quoting paper text.
    `cites` are the sentence's citation groups, which the statement may
    leave out; without them it must be one exact run of the sentence (1.0.0)."""
    errors: list[str] = []
    ctx = norm(context)
    stmt = norm(skeleton.statement)
    if not stmt:
        errors.append("statement_empty")
    elif not statement_in_sentence(skeleton.statement, sentence, cites):
        errors.append("statement_not_in_sentence")

    for field in SPAN_FIELDS:
        value = getattr(skeleton, field)
        if value is None:
            continue
        if not norm(value):
            errors.append(f"{field}_empty")
        elif len(value) > MAX_SPAN_CHARS:
            errors.append(f"{field}_too_long")
        elif norm(value) not in ctx:
            errors.append(f"{field}_not_in_context")

    if skeleton.n is not None and (skeleton.n <= 0 or skeleton.n not in numbers_in(context)):
        errors.append("n_not_in_context")

    if skeleton.kind == "finding":
        if skeleton.direction is None:
            errors.append("finding_without_direction")
    elif any(getattr(skeleton, f) is not None for f in FINDING_FIELDS):
        errors.append("fields_set_for_non_finding")
    return errors


# An invalid answer is kept for re-checking, up to this many characters;
# MAX_TOKENS answer tokens fit well inside it.
MAX_ANSWER_CHARS = 20_000


class ClaimRecord(BaseModel):
    """One row of `claim`. A valid row holds its skeleton. An invalid one
    holds none; from 1.1.0 its last answer is kept as text in `answer`, with
    the rules it broke in `errors`, and is never read as a skeleton."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    citation_context_id: int
    skeleton: ClaimSkeleton | None
    extractor: str
    extractor_ver: str
    valid: bool
    answer: str | None = Field(default=None, max_length=MAX_ANSWER_CHARS)
    errors: list[str] | None = None

    @model_validator(mode="after")
    def _skeleton_iff_valid(self) -> ClaimRecord:
        if self.valid != (self.skeleton is not None):
            raise ValueError("a valid claim has a skeleton and an invalid one has none")
        if self.valid and (self.answer is not None or self.errors):
            raise ValueError("a valid claim keeps no answer or errors beside its skeleton")
        return self

    def row(self) -> dict:
        return {"citation_context_id": self.citation_context_id,
                "skeleton": self.skeleton.model_dump() if self.skeleton else None,
                "extractor": self.extractor, "extractor_ver": self.extractor_ver,
                "valid": self.valid, "answer": self.answer, "errors": self.errors}
