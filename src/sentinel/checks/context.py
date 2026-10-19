"""Inputs to checks (sentinel-project.md §9.6).

Everything a check needs is loaded here first, so `Check.run()` never touches
the network or the database. Later sessions add fields without changing the
check interface.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Connection, select

from sentinel.models import trial, trial_outcome
from sentinel.schemas import TrialOutcome, TrialRecord


class TrialContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    trial: TrialRecord
    # The day the registry record was fetched. Date-based checks measure to
    # here, which keeps them pure and reproducible.
    fetched_on: date


class PaperContext(BaseModel):
    """A paper and what is known about it. References, resolution results and
    statistical tests are added in S1.6 onward."""

    model_config = ConfigDict(frozen=True)

    paper_id: int
    trials: tuple[TrialContext, ...] = ()


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
