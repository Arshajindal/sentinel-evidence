"""Pydantic models for all structured data. Nothing reaches the database
without passing through one of these first.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

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
