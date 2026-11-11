"""Dense search over stored passage vectors with pgvector (§10.1 item 5, ADR-048).

`dot` ranks by inner product, which MedCPT was trained for; `cosine` by
cosine similarity, which the HNSW index from ADR-044 serves. Restricted to
some papers (C09: the works one citation cites), Postgres reads those few
rows exactly; over the whole index, cosine can use HNSW and dot is an exact
scan until S2.6 decides which index to keep. Scores are similarities, higher
is better; ties go to the lower passage ID.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from sqlalchemy import Connection, select

from sentinel.models import passage, passage_embedding

Metric = Literal["dot", "cosine"]


def dense_search(conn: Connection, vector: Sequence[float], *, model: str, splitter_ver: str,
                 metric: Metric, n: int,
                 paper_ids: Sequence[int] | None = None) -> list[tuple[int, float]]:
    col = passage_embedding.c.embedding
    # pgvector's <#> is the negative inner product and <=> the cosine distance:
    # both sort ascending, best first.
    distance = (col.max_inner_product(list(vector)) if metric == "dot"
                else col.cosine_distance(list(vector))).label("distance")
    stmt = (select(passage.c.id, distance)
            .join(passage, passage.c.id == passage_embedding.c.passage_id)
            .where(passage_embedding.c.model == model, passage.c.splitter_ver == splitter_ver))
    if paper_ids is not None:
        stmt = stmt.where(passage.c.paper_id.in_(list(paper_ids)))
    rows = conn.execute(stmt.order_by(distance, passage.c.id).limit(n)).all()
    return [(r.id, -float(r.distance) if metric == "dot" else 1.0 - float(r.distance))
            for r in rows]
