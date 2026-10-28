"""Records when each pipeline stage ran, for `sentinel stats` (§9.8, ADR-039).

Wrap a stage in `record_stage`: a `stage_run` row is written at the start and
closed at the end with its status and counts, so a killed or failed run still
shows when it started. Check runs keep their own timing in `check_run`.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Engine, func, insert, update

from sentinel.log import get_logger
from sentinel.models import stage_run

log = get_logger(__name__)

STAGES = ("ingest", "resolve", "extract")


@dataclass
class StageHandle:
    """Filled in by the stage while it runs; written when it ends."""

    run_id: int
    items: int | None = None
    counts: dict[str, Any] = field(default_factory=dict)
    # Set to "stopped" when the stage ends early on purpose (e.g. the
    # OpenAlex budget ran out); otherwise an exception means "failed".
    status: str | None = None


@contextmanager
def record_stage(engine: Engine, stage: str, *, source: str | None = None,
                 corpus_label: str | None = None) -> Iterator[StageHandle]:
    if stage not in STAGES:
        raise ValueError(f"unknown stage {stage!r}; choose from {STAGES}")
    with engine.begin() as conn:
        run_id = conn.execute(
            insert(stage_run)
            .values(stage=stage, source=source, corpus_label=corpus_label)
            .returning(stage_run.c.id)
        ).scalar_one()
    handle = StageHandle(run_id=run_id)
    try:
        yield handle
    except BaseException:
        _close(engine, handle, handle.status or "failed")
        raise
    _close(engine, handle, handle.status or "done")


def _close(engine: Engine, handle: StageHandle, status: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            update(stage_run)
            .where(stage_run.c.id == handle.run_id)
            .values(status=status, finished_at=func.now(), n_items=handle.items,
                    counts=handle.counts or None)
        )
    log.info("stage_done", stage_run_id=handle.run_id, status=status, items=handle.items)
