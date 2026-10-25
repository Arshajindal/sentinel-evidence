"""Storing works fetched by lookup clients (§9.3: no lookup is paid for twice).

A work is matched to an existing row by DOI, PMID or PMCID. The first source
to store a work owns its row: a later lookup from another source reuses it
and doesn't overwrite it, so a corpus paper's Europe PMC record stays
authoritative. Rows from the same source are refreshed in place. If the
identifiers point at two different rows, nothing is written or merged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import Connection, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from sentinel.log import get_logger
from sentinel.models import paper
from sentinel.schemas import PaperRecord

log = get_logger(__name__)


@dataclass(frozen=True)
class StoredPaper:
    paper_id: int | None
    action: Literal["inserted", "updated", "existing", "conflict"]


def upsert_paper(conn: Connection, record: PaperRecord) -> StoredPaper:
    keys = [(paper.c.doi, record.doi), (paper.c.pmid, record.pmid), (paper.c.pmcid, record.pmcid)]
    matches = [col == value for col, value in keys if value is not None]
    existing = conn.execute(
        select(paper.c.id, paper.c.source).where(or_(*matches))
    ).all() if matches else []

    if len({row.id for row in existing}) > 1:
        log.warning("paper_identifier_conflict", doi=record.doi, pmid=record.pmid,
                    pmcid=record.pmcid, rows=sorted(row.id for row in existing))
        return StoredPaper(None, "conflict")

    values = record.model_dump()
    if existing:
        row = existing[0]
        if row.source != record.source:
            return StoredPaper(row.id, "existing")
        # Identifiers aren't rewritten: they matched, and changing one could
        # collide with another row's unique key.
        conn.execute(update(paper).where(paper.c.id == row.id).values(
            **{k: v for k, v in values.items() if k not in ("doi", "pmid", "pmcid")},
            updated_at=func.now(),
        ))
        return StoredPaper(row.id, "updated")

    paper_id = conn.execute(insert(paper).values(**values).returning(paper.c.id)).scalar_one()
    return StoredPaper(paper_id, "inserted")
