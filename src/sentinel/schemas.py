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
