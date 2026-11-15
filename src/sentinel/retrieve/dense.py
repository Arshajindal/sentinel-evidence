"""Dense search over stored passage vectors with pgvector (§10.1 item 5, ADR-048, ADR-050).

`dot` ranks by inner product, which MedCPT was trained for; `cosine` by
cosine similarity. Scores are similarities, higher is better; ties go to the
lower passage ID.

The search is exact. Every candidate's distance is computed in a
materialised CTE and sorted there, so an HNSW index can't serve the ORDER
BY. It used to: the cosine query was answered by the HNSW index, which
hands over only its 40 nearest vectors (`hnsw.ef_search`) before the model,
splitter and paper filters run. With the search restricted to some papers
(C09, and the S2.6 ablation), those 40 were mostly other papers' passages,
and every cosine search in the ablation came back short, some empty
(ADR-050). Restricted to a few cited works, exact search reads only their
rows; over the whole index it reads every vector of the model.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from sqlalchemy import Connection, func, select

from sentinel.models import passage, passage_embedding

Metric = Literal["dot", "cosine"]


def dense_search(conn: Connection, vector: Sequence[float], *, model: str, splitter_ver: str,
                 metric: Metric, n: int, paper_ids: Sequence[int] | None = None,
                 skip_sections: Sequence[str] = ()) -> list[tuple[int, float]]:
    """The `n` nearest passages, exactly. `skip_sections` leaves out passages
    of those sections; a passage with no section is never skipped."""
    col = passage_embedding.c.embedding
    # pgvector's <#> is the negative inner product and <=> the cosine distance:
    # both sort ascending, best first.
    distance = (col.max_inner_product(list(vector)) if metric == "dot"
                else col.cosine_distance(list(vector))).label("distance")
    scored = (select(passage.c.id, distance)
              .join(passage, passage.c.id == passage_embedding.c.passage_id)
              .where(passage_embedding.c.model == model, passage.c.splitter_ver == splitter_ver))
    if paper_ids is not None:
        scored = scored.where(passage.c.paper_id.in_(list(paper_ids)))
    if skip_sections:
        scored = scored.where(func.coalesce(passage.c.section, "").not_in(list(skip_sections)))
    # MATERIALIZED fences the CTE off from the outer ORDER BY ... LIMIT, so
    # the planner can't turn the pair into an approximate index scan.
    candidates = scored.cte("candidates").prefix_with("MATERIALIZED")
    rows = conn.execute(select(candidates.c.id, candidates.c.distance)
                        .order_by(candidates.c.distance, candidates.c.id).limit(n)).all()
    return [(r.id, -float(r.distance) if metric == "dot" else 1.0 - float(r.distance))
            for r in rows]
