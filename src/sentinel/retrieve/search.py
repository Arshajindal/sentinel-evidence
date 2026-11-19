"""Passages for a claim: BM25 and dense search, fused, then reranked (§10.1 item 5, ADR-048).

Each first-stage retriever returns `depth` passages; reciprocal rank fusion
merges them and keeps the top `depth`; the cross-encoder scores those and
the best `keep` are the answer. With `paper_ids` the search covers only
those works' passages (C09: the works a citation cites); without, the whole
index. Every stage's ranks and scores, and its time, are kept on the result,
so the ablation in S2.6 can score each stage from one search.

Claim and passage text are data: they are scored, never followed.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy import Engine, func, select

from sentinel.models import paper, passage
from sentinel.retrieve.bm25 import BM25Index
from sentinel.retrieve.dense import Metric, dense_search
from sentinel.retrieve.encoders import CrossEncoder, QueryEncoder, document_text
from sentinel.retrieve.fusion import rrf


@dataclass(frozen=True)
class RetrieveParams:
    splitter_ver: str
    article_model: str  # whose passage vectors to search
    metric: Metric
    rrf_k: int
    depth: int
    keep: int


@dataclass(frozen=True)
class Hit:
    passage_id: int
    paper_id: int
    section: str | None
    text: str
    bm25_rank: int | None
    dense_rank: int | None
    fused_rank: int
    rerank_score: float | None


@dataclass
class SearchResult:
    bm25: list[tuple[int, float]]
    dense: list[tuple[int, float]]
    fused: list[tuple[int, float]]
    reranked: list[tuple[int, float]]  # the fused pool in cross-encoder order; empty if not reranked
    hits: list[Hit]  # the final `keep`, best first
    seconds: dict[str, float] = field(default_factory=dict)


class Retriever:
    def __init__(self, engine: Engine, bm25: BM25Index, query_encoder: QueryEncoder,
                 cross_encoder: CrossEncoder | None, params: RetrieveParams) -> None:
        self.engine = engine
        self.bm25 = bm25
        self.query_encoder = query_encoder
        self.cross_encoder = cross_encoder
        self.params = params

    def search(self, claim: str, *, paper_ids: Sequence[int] | None = None,
               skip_sections: Sequence[str] = (), rerank: bool = True) -> SearchResult:
        """`skip_sections` leaves out passages of those sections, such as the
        abstract a synthetic claim was copied from (ADR-051). It needs
        `paper_ids`."""
        p = self.params
        seconds: dict[str, float] = {}
        if skip_sections and paper_ids is None:
            raise ValueError("skip_sections needs paper_ids")
        candidates = self._passage_ids(paper_ids, skip_sections) if paper_ids is not None else None

        t = time.monotonic()
        bm25 = self.bm25.search(claim, n=p.depth, passage_ids=candidates)
        seconds["bm25"] = time.monotonic() - t

        t = time.monotonic()
        vector = self.query_encoder.encode([claim])[0]
        with self.engine.connect() as conn:
            dense = dense_search(conn, vector, model=p.article_model, splitter_ver=p.splitter_ver,
                                 metric=p.metric, n=p.depth, paper_ids=paper_ids,
                                 skip_sections=skip_sections)
        seconds["dense"] = time.monotonic() - t

        fused = rrf([[i for i, _ in bm25], [i for i, _ in dense]], p.rrf_k)[:p.depth]
        rows = self._passages([i for i, _ in fused])
        order = [i for i, _ in fused]
        scores: dict[int, float] = {}
        if rerank and self.cross_encoder is not None and order:
            t = time.monotonic()
            values = self.cross_encoder.score(
                claim, [document_text(rows[i]["title"], rows[i]["text"]) for i in order])
            scores = dict(zip(order, values, strict=True))
            seconds["rerank"] = time.monotonic() - t
            # Best cross-encoder score first; ties keep the fused order.
            order = sorted(order, key=lambda i: -scores[i])

        bm25_rank = {i: r for r, (i, _) in enumerate(bm25, start=1)}
        dense_rank = {i: r for r, (i, _) in enumerate(dense, start=1)}
        fused_rank = {i: r for r, (i, _) in enumerate(fused, start=1)}
        hits = [Hit(passage_id=i, paper_id=rows[i]["paper_id"], section=rows[i]["section"],
                    text=rows[i]["text"], bm25_rank=bm25_rank.get(i), dense_rank=dense_rank.get(i),
                    fused_rank=fused_rank[i], rerank_score=scores.get(i))
                for i in order[:p.keep]]
        reranked = [(i, scores[i]) for i in order] if scores else []
        return SearchResult(bm25=bm25, dense=dense, fused=fused, reranked=reranked, hits=hits,
                            seconds=seconds)

    def _passage_ids(self, paper_ids: Sequence[int], skip_sections: Sequence[str] = ()) -> list[int]:
        stmt = select(passage.c.id).where(passage.c.paper_id.in_(list(paper_ids)),
                                          passage.c.splitter_ver == self.params.splitter_ver)
        if skip_sections:
            stmt = stmt.where(func.coalesce(passage.c.section, "").not_in(list(skip_sections)))
        with self.engine.connect() as conn:
            return list(conn.execute(stmt).scalars())

    def _passages(self, ids: list[int]) -> dict[int, dict]:
        if not ids:
            return {}
        with self.engine.connect() as conn:
            rows = conn.execute(select(passage.c.id, passage.c.paper_id, passage.c.section,
                                       passage.c.text, paper.c.title)
                                .join(paper, paper.c.id == passage.c.paper_id)
                                .where(passage.c.id.in_(ids))).mappings().all()
        return {r["id"]: dict(r) for r in rows}
