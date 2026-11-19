"""Reciprocal rank fusion (§10.1 item 5): an item scores 1/(k + rank) in each
ranked list it appears in, summed. k = 60 in §10.1. Ties go to the lower
passage ID, so the order never depends on dictionary order. Pure."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence


def rrf(rankings: Sequence[Sequence[int]], k: int) -> list[tuple[int, float]]:
    scores: dict[int, float] = defaultdict(float)
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] += 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
