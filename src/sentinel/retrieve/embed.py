"""Passage embeddings into `passage_embedding` (§10.1 item 4, S2.4, ADR-047).

Walks the passages of one splitter version that have no vector from this
model yet, in passage ID order, so a killed run resumes where it stopped and
a re-run does nothing. Each chunk is encoded in batches of similar token
length (less padding: 1.6x faster on CPU, measured) and written in passage ID
order in one transaction with its cursor.

The scope is the works a corpus cites (what C09 retrieves from) or, with
"all", the corpus papers too.
"""

from __future__ import annotations

import math
import time
from collections.abc import Iterator
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import Connection, Engine, exists, func, select
from sqlalchemy.dialects.postgresql import insert

from sentinel.ingest.base import Harvester, UpsertCounts
from sentinel.ingest.fulltext import work_ids
from sentinel.log import get_logger
from sentinel.models import EMBEDDING_DIM, corpus_paper, paper, passage, passage_embedding, reference
from sentinel.retrieve.encoders import ArticleEncoder

log = get_logger(__name__)

Scope = Literal["cited", "all", "scifact"]
SCOPES = ("cited", "all", "scifact")
CHUNK = 256


class PassageEmbedding(BaseModel):
    """One row of `passage_embedding`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    passage_id: int
    model: str
    embedding: list[float]

    @field_validator("embedding")
    @classmethod
    def _shape(cls, v: list[float]) -> list[float]:
        if len(v) != EMBEDDING_DIM:
            raise ValueError(f"expected {EMBEDDING_DIM} numbers, got {len(v)}")
        if not all(math.isfinite(x) for x in v):
            raise ValueError("embedding has a NaN or infinite value")
        return v


def scope_ids(label: str, scope: Scope):
    """Paper IDs whose passages are embedded or indexed: a corpus's cited
    works, those plus the corpus papers, or the SciFact abstracts (ADR-049),
    which belong to no corpus and ignore `label`."""
    if scope == "scifact":
        return select(paper.c.id).where(paper.c.source == "scifact")
    if scope == "all":
        return work_ids(label)
    return (select(reference.c.resolved_paper_id.label("id"))
            .join(corpus_paper, corpus_paper.c.paper_id == reference.c.citing_paper_id)
            .where(corpus_paper.c.label == label, reference.c.resolved_paper_id.is_not(None))
            .distinct())


class PassageEmbedder(Harvester):
    def __init__(self, engine: Engine, encoder: ArticleEncoder, *, label: str, splitter_ver: str,
                 scope: Scope = "cited", batch_size: int = 8, chunk: int = CHUNK) -> None:
        if encoder.dim != EMBEDDING_DIM:
            raise ValueError(f"{encoder.name} makes {encoder.dim}-number vectors; "
                             f"passage_embedding holds {EMBEDDING_DIM}")
        self.source = f"embed:{label}:{scope}"
        self.engine = engine
        self.encoder = encoder
        self.label = label
        self.splitter_ver = splitter_ver
        self.scope = scope
        self.batch_size = batch_size
        self.chunk = chunk

    def _pending(self, after: int, limit: int | None):
        ids = scope_ids(self.label, self.scope).subquery()
        done = exists().where(passage_embedding.c.passage_id == passage.c.id,
                              passage_embedding.c.model == self.encoder.name)
        stmt = (select(passage.c.id, passage.c.text, paper.c.title)
                .join(paper, paper.c.id == passage.c.paper_id)
                .join(ids, ids.c.id == passage.c.paper_id)
                .where(passage.c.splitter_ver == self.splitter_ver, passage.c.id > after, ~done)
                .order_by(passage.c.id))
        return stmt.limit(limit) if limit else stmt

    def count_pending(self) -> int:
        with self.engine.connect() as conn:
            return conn.execute(select(func.count()).select_from(
                self._pending(0, None).subquery())).scalar_one()

    def fetch(self, cursor: str | None) -> Iterator[tuple[dict, str | None]]:
        last = 0
        if cursor:
            cursor_label, _, tail = cursor.rpartition(":")
            if cursor_label == self.label and tail.isdigit():
                last = int(tail)
        while True:
            with self.engine.connect() as conn:
                rows = conn.execute(self._pending(last, self.chunk)).all()
            if not rows:
                return
            started = time.monotonic()
            vectors = self._encode([(r.title, r.text) for r in rows])
            log.info("embed_chunk", passages=len(rows), first=rows[0].id, last=rows[-1].id,
                     per_s=round(len(rows) / (time.monotonic() - started), 2))
            for r, v in zip(rows, vectors, strict=True):
                yield {"passage_id": r.id, "embedding": v}, f"{self.label}:{r.id}"
            last = rows[-1].id

    def _encode(self, pairs: list[tuple[str, str]]) -> list[list[float]]:
        """Vectors in input order, computed in batches of similar length."""
        lengths = self.encoder.lengths(pairs)
        order = sorted(range(len(pairs)), key=lambda i: lengths[i])
        out: list[list[float] | None] = [None] * len(pairs)
        for i in range(0, len(order), self.batch_size):
            idx = order[i:i + self.batch_size]
            for j, v in zip(idx, self.encoder.encode([pairs[j] for j in idx]), strict=True):
                out[j] = v
        return out  # type: ignore[return-value]

    def record_key(self, raw: dict) -> str | None:
        return str(raw.get("passage_id"))

    def normalise(self, raw: dict) -> PassageEmbedding:
        return PassageEmbedding(passage_id=raw["passage_id"], model=self.encoder.name,
                                embedding=raw["embedding"])

    def upsert(self, conn: Connection, records: list[PassageEmbedding]) -> UpsertCounts:
        # rowcount isn't reliable for a multi-row insert; count what was returned.
        written = len(conn.execute(
            insert(passage_embedding).on_conflict_do_nothing()
            .returning(passage_embedding.c.passage_id),
            [r.model_dump() for r in records]).all())
        return UpsertCounts(inserted=written, unchanged=len(records) - written)
