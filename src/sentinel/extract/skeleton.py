"""The claim skeleton and the rules that make one valid (§10.1 item 6, ADR-053).

A skeleton says what a citing sentence attributes to one citation: what kind
of claim it is and, for a finding, its population, intervention, comparator,
outcome, effect, sample size and direction.

The gateway's strict JSON schema already fixes the shape, so validity is
mostly about grounding. Every text field must be copied from the context,
the statement from the citing sentence itself, and a sample size must be a
number written there. A paraphrased or invented span fails, and that failure
rate is S2.7's metric.

Context text is untrusted data from papers. Nothing here acts on it; it is
only compared against.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

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


def norm(text: str) -> str:
    """Compare text the way a reader would: Unicode-compatible forms, case,
    dashes, quotes and runs of whitespace don't matter."""
    text = unicodedata.normalize("NFKC", text).casefold().translate(_DASHES).translate(_QUOTES)
    return " ".join(text.split()).strip(_EDGE)


_NUMBER = re.compile(r"\d(?:[\d,]*\d)?")


def numbers_in(text: str) -> set[int]:
    """Whole numbers written in digits, with thousands separators allowed
    ("1,234"). Digits inside a decimal count too ("0.5" gives 0 and 5), which
    errs towards accepting a sample size, never towards rejecting one."""
    return {int(m.group().replace(",", "")) for m in _NUMBER.finditer(text)}


def grounding_errors(skeleton: ClaimSkeleton, *, sentence: str, context: str) -> list[str]:
    """Rule codes the skeleton breaks, empty if it is valid. Codes, not
    messages, so they can be counted and logged without quoting paper text."""
    errors: list[str] = []
    ctx = norm(context)
    stmt = norm(skeleton.statement)
    if not stmt:
        errors.append("statement_empty")
    elif stmt not in norm(sentence):
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


class ClaimRecord(BaseModel):
    """One row of `claim`. A valid row holds its skeleton; an invalid one
    holds none, so nothing that failed validation is ever stored."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    citation_context_id: int
    skeleton: ClaimSkeleton | None
    extractor: str
    extractor_ver: str
    valid: bool

    @model_validator(mode="after")
    def _skeleton_iff_valid(self) -> ClaimRecord:
        if self.valid != (self.skeleton is not None):
            raise ValueError("a valid claim has a skeleton and an invalid one has none")
        return self

    def row(self) -> dict:
        return {"citation_context_id": self.citation_context_id,
                "skeleton": self.skeleton.model_dump() if self.skeleton else None,
                "extractor": self.extractor, "extractor_ver": self.extractor_ver,
                "valid": self.valid}
