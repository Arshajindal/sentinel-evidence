"""Retraction Watch harvester (sentinel-project.md §5).

Source: the CSV in Crossref's GitLab repository
(`git clone https://gitlab.com/crossref/retraction-watch-data`), pointed at by
SENTINEL_RETRACTION_WATCH_CSV_PATH.

The cursor is `<file fingerprint>:<rows done>`. Resuming against a different
file (the CSV is regenerated every working day) starts again from row 0, and
the upsert makes that safe.
"""

from __future__ import annotations

import csv
import hashlib
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import Connection, literal_column, tuple_
from sqlalchemy.dialects.postgresql import insert

from sentinel.ingest.base import Harvester, UpsertCounts
from sentinel.log import get_logger
from sentinel.models import retraction_notice
from sentinel.resolve.normalize import normalize_doi, normalize_pmid
from sentinel.schemas import RetractionNotice

log = get_logger(__name__)

NOTICE_TYPES = {
    "Retraction": "retraction",
    "Correction": "correction",
    "Expression of concern": "expression_of_concern",
    "Reinstatement": "reinstatement",
}

# Not needed for matching (C02 uses DOI/PMID). Kept out of `raw` to store only
# what's needed. The trailing comma in the header yields an unnamed column.
DROPPED_COLUMNS = frozenset({"Author", "Institution", ""})

_UPDATABLE = (
    "retracted_doi",
    "retracted_pmid",
    "notice_doi",
    "notice_type",
    "notice_date",
    "reason",
    "raw",
)


class RetractionWatchHarvester(Harvester):
    source = "retraction_watch"

    def __init__(self, csv_path: str | Path) -> None:
        self.csv_path = Path(csv_path)

    def fetch(self, cursor: str | None) -> Iterator[tuple[dict, str | None]]:
        fingerprint = _fingerprint(self.csv_path)
        skip = 0
        if cursor:
            cursor_fp, _, done = cursor.rpartition(":")
            if cursor_fp == fingerprint and done.isdigit():
                skip = int(done)
            else:
                log.info("source_changed", source=self.source, restart_from=0)

        with self.csv_path.open(encoding="utf-8", newline="") as fh:
            for i, row in enumerate(csv.DictReader(fh)):
                if i < skip:
                    continue
                yield row, f"{fingerprint}:{i + 1}"

    def normalise(self, raw: dict) -> RetractionNotice:
        nature = (raw.get("RetractionNature") or "").strip()
        if nature not in NOTICE_TYPES:
            raise ValueError(f"unknown RetractionNature {nature!r}")
        return RetractionNotice(
            source=self.source,
            source_record_id=(raw.get("Record ID") or "").strip(),
            retracted_doi=normalize_doi(raw.get("OriginalPaperDOI")),
            retracted_pmid=normalize_pmid(raw.get("OriginalPaperPubMedID")),
            notice_doi=normalize_doi(raw.get("RetractionDOI")),
            notice_type=NOTICE_TYPES[nature],
            notice_date=_parse_date(raw.get("RetractionDate")),
            reason=(raw.get("Reason") or "").strip() or None,
            raw={k: v for k, v in raw.items() if k not in DROPPED_COLUMNS},
        )

    def record_key(self, raw: dict) -> str | None:
        return raw.get("Record ID")

    def upsert(self, conn: Connection, records: list[RetractionNotice]) -> UpsertCounts:
        # Last occurrence wins if a batch repeats a key; Postgres rejects a
        # single INSERT that touches the same row twice.
        rows = {r.source_record_id: r.model_dump() for r in records}
        stmt = insert(retraction_notice).values(list(rows.values()))
        changed = tuple_(*(retraction_notice.c[c] for c in _UPDATABLE)).is_distinct_from(
            tuple_(*(stmt.excluded[c] for c in _UPDATABLE))
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["source", "source_record_id"],
            set_={c: stmt.excluded[c] for c in _UPDATABLE},
            where=changed,
        ).returning(literal_column("(xmax = 0)").label("inserted"))
        returned = conn.execute(stmt).scalars().all()
        inserted = sum(1 for r in returned if r)
        return UpsertCounts(
            inserted=inserted,
            updated=len(returned) - inserted,
            unchanged=len(rows) - len(returned),
        )


def _parse_date(value: str | None) -> date | None:
    """Retraction Watch dates look like `3/6/2026 0:00` (month first)."""
    if not value or not value.strip():
        return None
    return datetime.strptime(value.strip(), "%m/%d/%Y %H:%M").date()


def _fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()[:16]
