"""Inputs to checks (sentinel-project.md §9.6).

Everything a check needs is loaded here first, so `Check.run()` never touches
the network or the database. Later sessions add fields without changing the
check interface.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Connection, select

from sentinel.models import corpus_paper, reference, trial, trial_outcome
from sentinel.schemas import TrialOutcome, TrialRecord


class TrialContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    trial: TrialRecord
    # The day the registry record was fetched. Date-based checks measure to
    # here, which keeps them pure and reproducible.
    fetched_on: date


class ReferenceContext(BaseModel):
    """One entry of a paper's bibliography with its resolution result (§9.5)."""

    model_config = ConfigDict(frozen=True)

    reference_id: int
    ordinal: int | None
    raw_string: str | None
    citation_tag: str | None          # element-citation | mixed-citation
    publication_type: str | None      # from the XML, when given (journal, book, web...)
    parsed_doi: str | None
    parsed_pmid: str | None
    parsed_title: str | None
    parsed_first_author: str | None
    parsed_year: int | None
    parsed_container: str | None
    resolution_status: str
    resolution_method: str | None
    resolved_paper_id: int | None
    resolution_evidence: dict[str, Any] | None


class PaperContext(BaseModel):
    """A paper and what is known about it. Statistical tests are added in S1.12."""

    model_config = ConfigDict(frozen=True)

    paper_id: int
    references: tuple[ReferenceContext, ...] = ()
    trials: tuple[TrialContext, ...] = ()


def load_paper_contexts(conn: Connection, paper_ids: list[int]) -> list[PaperContext]:
    """Papers with their references (in bibliography order)."""
    if not paper_ids:
        return []
    refs: dict[int, list[ReferenceContext]] = {pid: [] for pid in paper_ids}
    rows = conn.execute(
        select(reference)
        .where(reference.c.citing_paper_id.in_(paper_ids))
        .order_by(reference.c.citing_paper_id, reference.c.ordinal, reference.c.id)
    ).mappings()
    for r in rows:
        raw = r["raw"] or {}
        refs[r["citing_paper_id"]].append(ReferenceContext(
            reference_id=r["id"], ordinal=r["ordinal"], raw_string=r["raw_string"],
            citation_tag=raw.get("citation_tag"), publication_type=raw.get("publication_type"),
            parsed_doi=r["parsed_doi"], parsed_pmid=r["parsed_pmid"],
            parsed_title=r["parsed_title"], parsed_first_author=r["parsed_first_author"],
            parsed_year=r["parsed_year"], parsed_container=r["parsed_container"],
            resolution_status=r["resolution_status"], resolution_method=r["resolution_method"],
            resolved_paper_id=r["resolved_paper_id"],
            resolution_evidence=r["resolution_evidence"],
        ))
    return [PaperContext(paper_id=pid, references=tuple(refs[pid])) for pid in paper_ids]


def corpus_paper_ids(conn: Connection, label: str, limit: int | None = None) -> list[int]:
    query = (select(corpus_paper.c.paper_id)
             .where(corpus_paper.c.label == label, corpus_paper.c.paper_id.is_not(None))
             .order_by(corpus_paper.c.ordinal))
    if limit is not None:
        query = query.limit(limit)
    return list(conn.execute(query).scalars())


def load_trial_contexts(
    conn: Connection, nct_ids: list[str] | None = None, limit: int | None = None
) -> list[TrialContext]:
    query = select(trial).order_by(trial.c.nct_id)
    if nct_ids is not None:
        query = query.where(trial.c.nct_id.in_(nct_ids))
    if limit is not None:
        query = query.limit(limit)
    trials = conn.execute(query).mappings().all()
    if not trials:
        return []

    outcomes: dict[str, list[TrialOutcome]] = {}
    rows = conn.execute(
        select(trial_outcome)
        .where(trial_outcome.c.nct_id.in_([t["nct_id"] for t in trials]))
        .order_by(trial_outcome.c.version_no, trial_outcome.c.id)
    ).mappings()
    for row in rows:
        outcomes.setdefault(row["nct_id"], []).append(
            TrialOutcome.model_validate(
                {k: row[k] for k in TrialOutcome.model_fields}
            )
        )

    contexts = []
    for t in trials:
        record = TrialRecord.model_validate(
            {
                **{k: t[k] for k in TrialRecord.model_fields if k != "outcomes"},
                "outcomes": outcomes.get(t["nct_id"], []),
            }
        )
        contexts.append(TrialContext(trial=record, fetched_on=t["ingested_at"].date()))
    return contexts
