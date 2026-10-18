"""Harvester contract (sentinel-project.md §9.3).

A harvester walks a source and saves everything it finds. It resumes from
`ingest_watermark.cursor`, so a run killed at record 40,000 restarts there.
Each batch's upsert and its watermark update commit in one transaction, so
the cursor never runs ahead of what was written.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from itertools import islice

from pydantic import BaseModel, ValidationError
from sqlalchemy import Connection, Engine, select
from sqlalchemy.dialects.postgresql import insert

from sentinel.log import get_logger
from sentinel.models import ingest_watermark

log = get_logger(__name__)


@dataclass
class UpsertCounts:
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0


@dataclass
class RunStats:
    seen: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    failed: int = 0
    batches: int = 0
    cursor: str | None = field(default=None)

    def add(self, counts: UpsertCounts) -> None:
        self.inserted += counts.inserted
        self.updated += counts.updated
        self.unchanged += counts.unchanged


class Harvester(ABC):
    source: str

    @abstractmethod
    def fetch(self, cursor: str | None) -> Iterator[tuple[dict, str | None]]:
        """Yield (raw_record, next_cursor). Must resume from cursor."""

    @abstractmethod
    def normalise(self, raw: dict) -> BaseModel:
        """Raw payload -> validated Pydantic model. Never write unvalidated data."""

    @abstractmethod
    def upsert(self, conn: Connection, records: list[BaseModel]) -> UpsertCounts:
        """Idempotent upsert on the natural key. Never deletes."""

    def record_key(self, raw: dict) -> str | None:
        """Identifier logged when a record fails validation. Never log content."""
        return None

    def run(
        self,
        engine: Engine,
        *,
        resume: bool = False,
        limit: int | None = None,
        batch_size: int = 1000,
    ) -> RunStats:
        # rows_seen is a running total across all runs; the cursor is used only
        # when resuming.
        stored_cursor, rows_before = self._read_watermark(engine)
        cursor = stored_cursor if resume else None
        stats = RunStats(cursor=cursor)
        log.info("harvest_start", source=self.source, resume=resume, cursor=cursor)

        stream = self.fetch(cursor)
        if limit is not None:
            stream = islice(stream, limit)

        while batch := list(islice(stream, batch_size)):
            records: list[BaseModel] = []
            failed = 0
            for raw, _ in batch:
                try:
                    records.append(self.normalise(raw))
                except (ValidationError, ValueError) as exc:
                    failed += 1
                    log.warning(
                        "record_invalid",
                        source=self.source,
                        record=self.record_key(raw),
                        errors=_error_summary(exc),
                    )
            next_cursor = batch[-1][1]
            with engine.begin() as conn:
                counts = self.upsert(conn, records) if records else UpsertCounts()
                self._write_cursor(
                    conn, next_cursor, rows_seen=rows_before + stats.seen + len(batch)
                )

            stats.seen += len(batch)
            stats.failed += failed
            stats.batches += 1
            stats.cursor = next_cursor
            stats.add(counts)
            log.info(
                "harvest_batch",
                source=self.source,
                seen=len(batch),
                written=counts.inserted + counts.updated,
                inserted=counts.inserted,
                updated=counts.updated,
                skipped=counts.unchanged,
                failed=failed,
                cursor=next_cursor,
            )

        log.info(
            "harvest_done",
            source=self.source,
            seen=stats.seen,
            inserted=stats.inserted,
            updated=stats.updated,
            skipped=stats.unchanged,
            failed=stats.failed,
            cursor=stats.cursor,
        )
        return stats

    def _read_watermark(self, engine: Engine) -> tuple[str | None, int]:
        with engine.connect() as conn:
            row = conn.execute(
                select(ingest_watermark.c.cursor, ingest_watermark.c.rows_seen).where(
                    ingest_watermark.c.source == self.source
                )
            ).one_or_none()
        return (row.cursor, row.rows_seen) if row else (None, 0)

    def _write_cursor(self, conn: Connection, cursor: str | None, *, rows_seen: int) -> None:
        stmt = insert(ingest_watermark).values(
            source=self.source,
            cursor=cursor,
            last_run_at=datetime.now(timezone.utc),
            rows_seen=rows_seen,
        )
        conn.execute(
            stmt.on_conflict_do_update(
                index_elements=[ingest_watermark.c.source],
                set_={
                    "cursor": stmt.excluded.cursor,
                    "last_run_at": stmt.excluded.last_run_at,
                    "rows_seen": stmt.excluded.rows_seen,
                },
            )
        )


def _error_summary(exc: Exception) -> list[str] | str:
    if isinstance(exc, ValidationError):
        return [f"{'.'.join(map(str, e['loc']))}: {e['type']}" for e in exc.errors()]
    return type(exc).__name__
