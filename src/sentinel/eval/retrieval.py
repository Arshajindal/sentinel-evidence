"""The retrieval ablation (§10.1, §10.3, §14, S2.6, ADR-050, ADR-055).

Two query sets:
- **SciFact** (ADR-049): the external set's claims that have evidence, with
  their gold passages pooled across the abstracts they cite. Every query
  searches the same pool: all SciFact passages, SciFact's standard
  abstract-retrieval task (87% of its abstracts are one passage, so
  searching inside one would be trivial).
- **Synthetic** (ADR-051): the intact pairs, an abstract sentence and the
  body passages that repeat its numbers. Each query searches only its own
  source paper's body, since the abstract holds the claim itself. That is
  C09's task: find the passages of a cited work that bear on a claim.

Each search keeps every stage's ranking, so one search scores four rows:
BM25, dense, fusion, fusion plus rerank. Dense search runs once by dot
product and once by cosine (ADR-048).

Measures, per query then averaged: nDCG@10 with binary relevance (§14's
selection measure for retrieval) and Recall@50. Fusion plus rerank reorders
the fused top 50, so its Recall@50 equals fusion's. Splits are reported
separately: choices are made on `val`, `test` confirms them (§14).
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from sqlalchemy import Engine, select

from sentinel.models import eval_item, eval_item_gold_passage


@dataclass(frozen=True)
class Query:
    claim_id: int
    claim: str
    split: str
    gold: frozenset[int]
    paper_ids: tuple[int, ...] | None = None  # this query's own pool; None means the set's shared one


def ndcg_at(ranking: Sequence[int], gold: frozenset[int], k: int = 10) -> float:
    dcg = sum(1.0 / math.log2(r + 1) for r, item in enumerate(ranking[:k], start=1) if item in gold)
    ideal = sum(1.0 / math.log2(r + 1) for r in range(1, min(len(gold), k) + 1))
    return dcg / ideal if ideal else 0.0


def recall_at(ranking: Sequence[int], gold: frozenset[int], k: int = 50) -> float:
    return len(gold.intersection(ranking[:k])) / len(gold) if gold else 0.0


def load_queries(engine: Engine, *, dataset: str, splits: Sequence[str]) -> list[Query]:
    """One query per claim with gold passages, in claim ID order."""
    with engine.connect() as conn:
        rows = conn.execute(
            select(eval_item.c.origin["claim_id"].astext.label("claim_id"), eval_item.c.claim_text,
                   eval_item.c.split, eval_item_gold_passage.c.passage_id)
            .join(eval_item_gold_passage, eval_item_gold_passage.c.eval_item_id == eval_item.c.id)
            .where(eval_item.c.set_name == "external",
                   eval_item.c.origin["dataset"].astext == dataset,
                   eval_item.c.split.in_(list(splits)))).all()
    claims: dict[int, tuple[str, str]] = {}
    gold: dict[int, set[int]] = defaultdict(set)
    for r in rows:
        claims[int(r.claim_id)] = (r.claim_text, r.split)
        gold[int(r.claim_id)].add(r.passage_id)
    return [Query(cid, claims[cid][0], claims[cid][1], frozenset(gold[cid])) for cid in sorted(claims)]


SYNTHETIC = "synthetic-rules"


def load_synthetic_queries(engine: Engine, *, splits: Sequence[str], corpus: str,
                           splitter_ver: str) -> list[Query]:
    """One query per intact synthetic pair (ADR-051), keyed by its item ID,
    searching only its source paper. Corrupted items are left out: their
    gold is the same passages, under a changed claim."""
    with engine.connect() as conn:
        rows = conn.execute(
            select(eval_item.c.id, eval_item.c.claim_text, eval_item.c.split,
                   eval_item.c.origin["claim_paper_id"].astext.label("paper_id"),
                   eval_item_gold_passage.c.passage_id)
            .join(eval_item_gold_passage, eval_item_gold_passage.c.eval_item_id == eval_item.c.id)
            .where(eval_item.c.set_name == "synthetic", eval_item.c.corruption_type == "none",
                   eval_item.c.origin["dataset"].astext == SYNTHETIC,
                   eval_item.c.origin["corpus"].astext == corpus,
                   eval_item.c.origin["splitter_ver"].astext == splitter_ver,
                   eval_item.c.split.in_(list(splits)))).all()
    items: dict[int, tuple[str, str, int]] = {}
    gold: dict[int, set[int]] = defaultdict(set)
    for r in rows:
        items[r.id] = (r.claim_text, r.split, int(r.paper_id))
        gold[r.id].add(r.passage_id)
    return [Query(i, items[i][0], items[i][1], frozenset(gold[i]), paper_ids=(items[i][2],))
            for i in sorted(items)]


def score(rankings: dict[str, dict[int, list[int]]], queries: Sequence[Query]
          ) -> dict[str, dict[str, dict[str, float]]]:
    """{row: {split: {"ndcg@10", "recall@50", "queries"}}} from each row's
    ranking per claim. A row missing a claim's ranking scores it zero."""
    out: dict[str, dict[str, dict[str, float]]] = {}
    for row, by_claim in rankings.items():
        per_split: dict[str, list[tuple[float, float]]] = defaultdict(list)
        for q in queries:
            ranking = by_claim.get(q.claim_id, [])
            per_split[q.split].append((ndcg_at(ranking, q.gold), recall_at(ranking, q.gold)))
        out[row] = {split: {"ndcg@10": sum(n for n, _ in v) / len(v),
                            "recall@50": sum(r for _, r in v) / len(v),
                            "queries": float(len(v))}
                    for split, v in sorted(per_split.items())}
    return out


BOOTSTRAP_RESAMPLES = 10_000


@dataclass(frozen=True)
class Paired:
    """Row a against row b over the same claims."""

    diff: float  # mean(a) - mean(b)
    lo: float  # 95% interval of the difference
    hi: float
    gain: float  # mean(a) / mean(b) - 1, the form of §10.3's "15–30%"
    gain_lo: float
    gain_hi: float
    better: int  # claims where a scored higher
    worse: int
    claims: int


def per_claim(by_claim: dict[int, list[int]], queries: Sequence[Query],
              measure: Callable[[Sequence[int], frozenset[int]], float] = ndcg_at) -> list[float]:
    """One row's measure for each query, in the queries' order."""
    return [measure(by_claim.get(q.claim_id, []), q.gold) for q in queries]


def paired_bootstrap(a: Sequence[float], b: Sequence[float], *, seed: int,
                     resamples: int = BOOTSTRAP_RESAMPLES) -> Paired:
    """Mean difference and relative gain of `a` over `b`, each with a 95%
    percentile interval. Claims are resampled with replacement and both rows
    are read at the same claims, so the comparison stays paired."""
    import numpy as np

    x, y = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    if x.shape != y.shape or x.size == 0:
        raise ValueError(f"need two equal, non-empty lists of scores; got {x.size} and {y.size}")
    idx = np.random.default_rng(seed).integers(0, x.size, size=(resamples, x.size))
    mx, my = x[idx].mean(axis=1), y[idx].mean(axis=1)
    lo, hi = np.percentile(mx - my, [2.5, 97.5])
    with np.errstate(divide="ignore", invalid="ignore"):
        gains = mx / my - 1.0
    gain_lo, gain_hi = np.nanpercentile(gains, [2.5, 97.5]) if np.isfinite(gains).any() else (math.nan,) * 2
    return Paired(diff=float(x.mean() - y.mean()), lo=float(lo), hi=float(hi),
                  gain=float(x.mean() / y.mean() - 1.0) if y.mean() else math.nan,
                  gain_lo=float(gain_lo), gain_hi=float(gain_hi),
                  better=int((x > y).sum()), worse=int((x < y).sum()), claims=int(x.size))


def short_rankings(rankings: dict[str, dict[int, list[int]]], depth: int,
                   pool: dict[int, int] | None = None) -> dict[str, int]:
    """Per row, how many claims got fewer results than they should: `depth`,
    or the claim's pool size from `pool` when that is smaller. Rows with
    none are left out. A short list means a stage lost candidates: the first
    run's cosine rows were all short, cut by the HNSW index (ADR-050)."""
    pool = pool or {}
    counts = {row: sum(len(r) < min(depth, pool.get(cid, depth)) for cid, r in by_claim.items())
              for row, by_claim in rankings.items()}
    return {row: n for row, n in counts.items() if n}


class CachingCrossEncoder:
    """Remembers (query, document) scores, so the dot and cosine runs don't
    rescore the passages their fused pools share."""

    def __init__(self, inner) -> None:
        self.inner = inner
        self.name = inner.name
        self._cache: dict[tuple[str, str], float] = {}
        self.scored = 0

    def score(self, query: str, documents: Sequence[str]) -> list[float]:
        todo = [d for d in dict.fromkeys(documents) if (query, d) not in self._cache]
        if todo:
            for d, s in zip(todo, self.inner.score(query, todo), strict=True):
                self._cache[(query, d)] = s
            self.scored += len(todo)
        return [self._cache[(query, d)] for d in documents]


def run_ablation(search: Callable[[Query, str], object], queries: Sequence[Query],
                 metrics: Sequence[str] = ("dot", "cosine"),
                 progress: Callable[[int, int], None] | None = None) -> dict[str, dict[int, list[int]]]:
    """Rankings per row for every query. `search(query, metric)` returns a
    SearchResult. BM25 doesn't depend on the dense metric, so it's kept once."""
    rankings: dict[str, dict[int, list[int]]] = defaultdict(dict)
    done, total = 0, len(queries) * len(metrics)
    for metric in metrics:
        for q in queries:
            result = search(q, metric)
            if "bm25" not in rankings or q.claim_id not in rankings["bm25"]:
                rankings["bm25"][q.claim_id] = [i for i, _ in result.bm25]
            rankings[f"dense/{metric}"][q.claim_id] = [i for i, _ in result.dense]
            rankings[f"fusion/{metric}"][q.claim_id] = [i for i, _ in result.fused]
            rankings[f"fusion+rerank/{metric}"][q.claim_id] = [i for i, _ in result.reranked]
            done += 1
            if progress:
                progress(done, total)
    return dict(rankings)


def log_to_mlflow(scores: dict[str, dict[str, dict[str, float]]], params: dict[str, object], *,
                  tracking_uri: str, experiment: str, artifact: str | None = None) -> list[str]:
    """One MLflow run per row, with every setting that shaped it as params
    and each split's measures as metrics. Returns the run IDs."""
    import sys

    import mlflow

    # MLflow prints an emoji at the end of a run; a cp1252 Windows console
    # can't encode it and the process crashed after logging (failure log,
    # 2026-10-31). Replace what can't be printed instead.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment(experiment)
    run_ids = []
    for row, by_split in scores.items():
        stage, _, metric = row.partition("/")
        with mlflow.start_run(run_name=row) as run:
            mlflow.log_params({**params, "row": stage, "dense_metric": metric or "n/a"})
            for split, m in by_split.items():
                mlflow.log_metrics({f"{split}_ndcg_at_10": m["ndcg@10"],
                                    f"{split}_recall_at_50": m["recall@50"],
                                    f"{split}_queries": m["queries"]})
            if artifact:
                mlflow.log_artifact(artifact)
            run_ids.append(run.info.run_id)
    return run_ids
