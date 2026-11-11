"""The retrieval ablation's measures and harness (S2.6, ADR-050)."""

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select

from sentinel.eval.retrieval import (
    CachingCrossEncoder,
    Query,
    load_queries,
    ndcg_at,
    paired_bootstrap,
    per_claim,
    recall_at,
    run_ablation,
    score,
    short_rankings,
)
from sentinel.eval.scifact import load
from sentinel.fulltext.passages import PassageParams
from sentinel.models import eval_item, paper
from tests.conftest import FIXTURES, requires_db


def test_ndcg_at_10_with_binary_gains():
    gold = frozenset({7, 9})
    assert ndcg_at([7, 9, 1], gold) == pytest.approx(1.0)  # both gold at the top
    ideal = 1 + 1 / math.log2(3)
    assert ndcg_at([1, 7, 2, 9], gold) == pytest.approx((1 / math.log2(3) + 1 / math.log2(5)) / ideal)
    assert ndcg_at([1] * 10 + [7], gold) == 0.0  # gold below rank 10 counts nothing
    assert ndcg_at([], gold) == 0.0


def test_recall_at_50():
    gold = frozenset({1, 2, 3, 4})
    assert recall_at([1, 9, 2], gold) == 0.5
    assert recall_at(list(range(100, 150)) + [1], gold) == 0.0  # rank 51 is outside
    assert recall_at([1], frozenset()) == 0.0


def test_scores_average_per_split_and_missing_rankings_score_zero():
    queries = [Query(1, "a", "val", frozenset({10})), Query(2, "b", "val", frozenset({20})),
               Query(3, "c", "test", frozenset({30}))]
    out = score({"bm25": {1: [10], 2: [99], 3: [30]}, "dense/dot": {1: [10]}}, queries)
    assert out["bm25"]["val"] == {"ndcg@10": 0.5, "recall@50": 0.5, "queries": 2.0}
    assert out["bm25"]["test"]["ndcg@10"] == 1.0
    assert out["dense/dot"]["test"]["recall@50"] == 0.0  # no ranking for claim 3


def test_paired_bootstrap_difference_gain_and_counts():
    b = [0.2, 0.4, 0.6, 0.8]
    same = paired_bootstrap(b, b, seed=1, resamples=200)
    assert (same.diff, same.lo, same.hi, same.gain) == (0.0, 0.0, 0.0, 0.0)
    assert (same.better, same.worse, same.claims) == (0, 0, 4)
    shifted = paired_bootstrap([v + 0.1 for v in b], b, seed=1, resamples=200)
    # A constant shift has no spread to resample: the interval is the shift.
    assert (shifted.diff, shifted.lo, shifted.hi) == pytest.approx((0.1, 0.1, 0.1))
    assert shifted.gain == pytest.approx(0.6 / 0.5 - 1) and shifted.better == 4
    assert shifted.gain_lo <= shifted.gain <= shifted.gain_hi


def test_paired_bootstrap_is_seeded_and_refuses_unpaired_input():
    a, b = [0.9, 0.1, 0.5, 0.7, 0.3], [0.2, 0.3, 0.5, 0.1, 0.6]
    assert paired_bootstrap(a, b, seed=7, resamples=500) == paired_bootstrap(a, b, seed=7, resamples=500)
    first = paired_bootstrap(a, b, seed=7, resamples=500)
    assert first.lo < first.diff < first.hi
    assert (first.better, first.worse) == (2, 2)  # 0.9>0.2, 0.7>0.1; 0.1<0.3, 0.3<0.6; one tie
    with pytest.raises(ValueError):
        paired_bootstrap([1.0], [1.0, 2.0], seed=7)
    with pytest.raises(ValueError):
        paired_bootstrap([], [], seed=7)


def test_per_claim_follows_the_queries_order():
    queries = [Query(2, "b", "val", frozenset({20})), Query(1, "a", "val", frozenset({10}))]
    assert per_claim({1: [10], 2: [99, 20]}, queries) == pytest.approx([1 / math.log2(3), 1.0])
    assert per_claim({}, queries, recall_at) == [0.0, 0.0]


def test_short_rankings_counts_claims_below_depth_per_row():
    rankings = {"bm25": {1: [1, 2, 3], 2: [4, 5, 6]}, "dense/cosine": {1: [1], 2: [], 3: [1, 2, 3]}}
    assert short_rankings(rankings, depth=3) == {"dense/cosine": 2}
    assert short_rankings(rankings, depth=1) == {"dense/cosine": 1}


def test_caching_cross_encoder_scores_each_pair_once():
    calls = []

    class Inner:
        name = "fake@0"

        def score(self, query, documents):
            calls.append(list(documents))
            return [float(len(d)) for d in documents]

    cached = CachingCrossEncoder(Inner())
    assert cached.score("q", ["aa", "b", "aa"]) == [2.0, 1.0, 2.0]
    assert cached.score("q", ["b", "ccc"]) == [1.0, 3.0]
    assert calls == [["aa", "b"], ["ccc"]] and cached.scored == 3


def test_run_ablation_builds_a_row_per_stage_and_metric():
    def search(claim, metric):
        base = 1 if metric == "dot" else 2
        return SimpleNamespace(bm25=[(5, 1.0)], dense=[(base, 0.9)], fused=[(base, 0.1), (5, 0.1)],
                               reranked=[(5, 3.0), (base, 1.0)])

    rankings = run_ablation(search, [Query(1, "c", "val", frozenset({5}))])
    assert set(rankings) == {"bm25", "dense/dot", "dense/cosine", "fusion/dot", "fusion/cosine",
                             "fusion+rerank/dot", "fusion+rerank/cosine"}
    assert rankings["dense/cosine"][1] == [2] and rankings["fusion+rerank/dot"][1] == [5, 1]


@pytest.fixture
def sf_engine(db_engine):
    with db_engine.connect() as conn:
        if conn.execute(select(paper.c.id).where(paper.c.source != "scifact")).first():
            pytest.skip("paper holds other data; not a disposable database")
    _clear(db_engine)
    yield db_engine
    _clear(db_engine)


def _clear(engine) -> None:
    with engine.begin() as conn:
        conn.execute(delete(eval_item).where(eval_item.c.origin["dataset"].astext == "scifact"))
        conn.execute(delete(paper).where(paper.c.source == "scifact"))


@requires_db
def test_queries_are_claims_with_evidence_and_their_gold_passages(sf_engine):
    load(sf_engine, FIXTURES / "scifact", seed=1, params=PassageParams(150, 300, 1), release_sha256="x")
    queries = load_queries(sf_engine, dataset="scifact", splits=["train", "val", "test"])
    assert [q.claim_id for q in queries] == [2, 5, 47]  # claim 0 has no evidence
    # Claims 2 and 5 rest on sentence 4 of abstract 13734012, which is the
    # overlap between its two passages, so both passages are gold.
    assert [len(q.gold) for q in queries] == [2, 2, 1]
    assert queries[0].gold == queries[1].gold
    assert load_queries(sf_engine, dataset="scifact", splits=["nonexistent"]) == []
