"""Pydantic models for all structured data. Nothing reaches the database
without passing through one of these first.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

NoticeType = Literal[
    "retraction", "correction", "expression_of_concern", "reinstatement"
]


class RetractionNotice(BaseModel):
    """One row of `retraction_notice`. DOIs and PMIDs are already normalised."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str = Field(min_length=1)
    source_record_id: str = Field(min_length=1)
    retracted_doi: str | None = Field(default=None, pattern=r"^10\.\d{4,9}/\S+$")
    retracted_pmid: str | None = Field(default=None, pattern=r"^[1-9]\d*$")
    notice_doi: str | None = Field(default=None, pattern=r"^10\.\d{4,9}/\S+$")
    notice_type: NoticeType
    notice_date: date | None = None
    reason: str | None = None
    raw: dict[str, Any]


class TrialOutcome(BaseModel):
    """One row of `trial_outcome`. version_no 0 is the current record (§8)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version_no: int = Field(ge=0)
    version_date: date | None = None
    outcome_type: Literal["primary", "secondary", "other"]
    measure: str = Field(min_length=1)
    description: str | None = None
    time_frame: str | None = None


class TrialRecord(BaseModel):
    """One row of `trial` plus its outcomes. `raw` has ADR-018 paths removed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    nct_id: str = Field(pattern=r"^NCT\d{8}$")
    brief_title: str | None = None
    overall_status: str | None = None
    study_type: str | None = None
    phase: str | None = None
    enrollment: int | None = Field(default=None, ge=0)
    start_date: date | None = None
    primary_completion_date: date | None = None
    completion_date: date | None = None
    results_first_posted: date | None = None
    lead_sponsor: str | None = None
    sponsor_class: str | None = None
    raw: dict[str, Any]
    outcomes: list[TrialOutcome]


class PaperRecord(BaseModel):
    """One row of `paper`. Identifiers and names are already normalised."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    doi: str | None = Field(default=None, pattern=r"^10\.\d{4,9}/\S+$")
    pmid: str | None = Field(default=None, pattern=r"^[1-9]\d*$")
    pmcid: str | None = Field(default=None, pattern=r"^PMC\d+$")
    title: str = Field(min_length=1)
    title_norm: str = Field(min_length=1)
    container: str | None = None
    year: int | None = Field(default=None, ge=1800, le=2100)
    first_author: str | None = None
    authors: list[dict[str, Any]] = []
    source: str = Field(min_length=1)
    is_oa: bool | None = None
    license: str | None = None
    fulltext_status: Literal["none", "abstract", "full"] = "none"
    raw: dict[str, Any]


class ReferenceRecord(BaseModel):
    """One row of `reference`, without the citing paper's ID."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ordinal: int = Field(ge=1)
    raw_string: str | None = None
    parsed_doi: str | None = Field(default=None, pattern=r"^10\.\d{4,9}/\S+$")
    parsed_pmid: str | None = Field(default=None, pattern=r"^[1-9]\d*$")
    parsed_title: str | None = None
    parsed_first_author: str | None = None
    parsed_year: int | None = Field(default=None, ge=1000, le=2100)
    parsed_container: str | None = None
    raw: dict[str, Any]


class CorpusPaperRecord(BaseModel):
    """A harvested corpus paper: the paper, its references and its own trials."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    label: str
    ordinal: int
    paper: PaperRecord
    references: list[ReferenceRecord]
    nct_ids: list[Annotated[str, StringConstraints(pattern=r"^NCT\d{8}$")]] = Field(
        default_factory=list
    )


Severity = Literal["info", "low", "medium", "high"]
FindingStatus = Literal["flagged", "clean", "not_applicable", "insufficient_data"]

# Ground rule 3: an unmatched reference "could not be verified".
FORBIDDEN_WORDS = ("fabricated",)


class Finding(BaseModel):
    """One row of `finding`, before it is attached to a run.

    About exactly one subject, a paper or a trial (§8). Evidence is always
    populated, including for clean results (ADR-015).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    paper_id: int | None = None
    nct_id: str | None = Field(default=None, pattern=r"^NCT\d{8}$")
    check_id: str = Field(pattern=r"^C\d{2}$")
    check_ver: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    severity: Severity
    status: FindingStatus
    summary: str = Field(min_length=1)
    evidence: dict[str, Any] = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self) -> Finding:
        if (self.paper_id is None) == (self.nct_id is None):
            raise ValueError("a finding is about exactly one of paper_id, nct_id")
        lowered = self.summary.lower()
        for word in FORBIDDEN_WORDS:
            if word in lowered:
                raise ValueError(f"finding summary must not say {word!r}")
        return self
