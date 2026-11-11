"""Passage retrieval: BM25, dense search, fusion, rerank (S2.5, ADR-048).

BM25 is the real bm25s library over a few constructed passages. Encoders are
fakes, so no model is downloaded: the query "vector" and the cross-encoder
score are simple functions of the text, chosen so each stage's effect shows.
"""

from __future__ import annotations

import pytest
from sqlalchemy import delete, insert, select

from sentinel.models import EMBEDDING_DIM, paper, passage, passage_embedding
from sentinel.retrieve.bm25 import BM25Index
from sentinel.retrieve.dense import dense_search
from sentinel.retrieve.encoders import document_text
from sentinel.retrieve.fusion import rrf
from sentinel.retrieve.search import RetrieveParams, Retriever
from tests.conftest import requires_db

VER = "1.0.0/150-300-1"
MODEL = "fake/article@000000000000"


# --- fusion ----------------------------------------------------------------------


def test_rrf_sums_reciprocal_ranks_and_breaks_ties_by_id():
    fused = rrf([[10, 20, 30], [20, 40]], k=60)
    # 20 is in both lists; 40 (2nd in its list, 1/62) beats 30 (3rd, 1/63).
    assert [i for i, _ in fused] == [20, 10, 40, 30]
    assert fused[0][1] == pytest.approx(1 / 62 + 1 / 61)
    assert [i for i, _ in rrf([[2], [1]], k=60)] == [1, 2]  # equal scores: lower id first
    assert rrf([], k=60) == []


# --- BM25 --------------------------------------------------------------------------

DOCS = {
    1: ("Aspirin trial", "Aspirin reduced the risk of stroke in older adults."),
    2: ("Statin trial", "Statins lowered LDL cholesterol and cardiovascular events."),
    3: ("Aspirin bleeding", "Aspirin increased major bleeding compared with placebo."),
    4: ("Exercise", "Exercise improved fitness; no drug was given."),
}


def _bm25(**kw) -> BM25Index:
    return BM25Index.from_documents(list(DOCS), [document_text(t, x) for t, x in DOCS.values()],
                                    k1=1.5, b=0.75, manifest={"corpus": "t", "scope": "cited",
                                                              "splitter_ver": VER}, **kw)


def test_bm25_ranks_by_shared_stemmed_terms_and_skips_non_matches():
    hits = _bm25().search("aspirin strokes", n=10)
    assert [i for i, _ in hits] == [1, 3]  # "strokes" stems to "stroke"; 2 and 4 share nothing
    assert hits[0][1] > hits[1][1] > 0


def test_bm25_can_search_only_some_passages():
    assert [i for i, _ in _bm25().search("aspirin", n=10, passage_ids=[3, 4])] == [3]
    assert _bm25().search("aspirin", n=10, passage_ids=[99]) == []
    assert _bm25().search("the of and", n=10) == []  # only stopwords


def test_bm25_index_round_trips_and_refuses_other_settings(tmp_path):
    index = _bm25()
    index.save(tmp_path / "idx")
    expect = {"corpus": "t", "scope": "cited", "splitter_ver": VER, "k1": 1.5, "b": 0.75}
    loaded = BM25Index.load(tmp_path / "idx", expect=expect)
    assert loaded.search("aspirin stroke", n=5) == index.search("aspirin stroke", n=5)
    with pytest.raises(ValueError, match="k1"):
        BM25Index.load(tmp_path / "idx", expect={**expect, "k1": 1.2})
    with pytest.raises(FileNotFoundError):
        BM25Index.load(tmp_path / "missing", expect=expect)


# --- dense search and the pipeline, against the test database -----------------------

TITLES = ("s25-a", "s25-b")


def _vec(x: float, y: float) -> list[float]:
    return [x, y] + [0.0] * (EMBEDDING_DIM - 2)


@pytest.fixture
def s25_engine(db_engine):
    with db_engine.connect() as conn:
        if conn.execute(select(paper.c.id).where(paper.c.title.not_in(TITLES))).first():
            pytest.skip("paper holds other data; not a disposable database")
    _clear(db_engine)
    with db_engine.begin() as conn:
        a, b = (conn.execute(insert(paper).values(title=t, title_norm=t, source="test", raw={})
                             .returning(paper.c.id)).scalar_one() for t in TITLES)
        ids = {}
        for key, (pid, text, vec) in {
            # Against the query (1, 0): "long" has the larger inner product,
            # "aligned" the larger cosine. Dot and cosine disagree on purpose.
            "aligned": (a, "Aspirin reduced stroke.", _vec(1.0, 0.0)),
            "long": (a, "Aspirin and bleeding.", _vec(3.0, 4.0)),
            "other": (b, "Statins and cholesterol.", _vec(0.0, 1.0)),
        }.items():
            ids[key] = conn.execute(insert(passage).values(
                paper_id=pid, splitter_ver=VER, ordinal=len(ids) + 1, section="results", text=text,
            ).returning(passage.c.id)).scalar_one()
            conn.execute(insert(passage_embedding).values(passage_id=ids[key], model=MODEL, embedding=vec))
        conn.execute(insert(passage_embedding).values(passage_id=ids["other"], model="another@0",
                                                      embedding=_vec(1.0, 0.0)))
    yield db_engine, ids, (a, b)
    _clear(db_engine)


def _clear(engine) -> None:
    with engine.begin() as conn:
        conn.execute(delete(paper).where(paper.c.title.in_(TITLES)))


@requires_db
def test_dense_search_by_dot_and_by_cosine(s25_engine):
    engine, ids, (a, b) = s25_engine
    with engine.connect() as conn:
        def search(metric, **kw):
            return dense_search(conn, _vec(1.0, 0.0), model=MODEL, splitter_ver=VER, metric=metric, n=5, **kw)
        dot, cos = search("dot"), search("cosine")
        only_b = search("dot", paper_ids=[b])
    assert [i for i, _ in dot] == [ids["long"], ids["aligned"], ids["other"]]
    assert [s for _, s in dot] == pytest.approx([3.0, 1.0, 0.0])
    assert [i for i, _ in cos] == [ids["aligned"], ids["long"], ids["other"]]
    assert [s for _, s in cos] == pytest.approx([1.0, 0.6, 0.0])
    assert [i for i, _ in only_b] == [ids["other"]]  # and only this model's vector of it


class FakeQueryEncoder:
    name = "fake/query@0"

    def encode(self, texts):
        return [_vec(1.0, 0.0) for _ in texts]


class FakeCrossEncoder:
    """Scores a document by how many of the claim's words it contains."""
    name = "fake/cross@0"

    def __init__(self):
        self.calls = []

    def score(self, query, documents):
        self.calls.append((query, list(documents)))
        words = set(query.lower().split())
        return [float(sum(w.strip(".,") in words for w in d.lower().split())) for d in documents]


@requires_db
def test_pipeline_fuses_bm25_and_dense_then_reranks(s25_engine):
    engine, ids, (a, b) = s25_engine
    bm25 = BM25Index.from_documents(
        [ids["aligned"], ids["long"], ids["other"]],
        ["s25-a. Aspirin reduced stroke.", "s25-a. Aspirin and bleeding.", "s25-b. Statins and cholesterol."],
        k1=1.5, b=0.75)
    cross = FakeCrossEncoder()
    params = RetrieveParams(splitter_ver=VER, article_model=MODEL, metric="dot", rrf_k=60, depth=50, keep=2)
    result = Retriever(engine, bm25, FakeQueryEncoder(), cross, params).search("aspirin reduced stroke")

    assert [i for i, _ in result.bm25] == [ids["aligned"], ids["long"]]
    assert [i for i, _ in result.dense] == [ids["long"], ids["aligned"], ids["other"]]
    # Fused: both lists hold "aligned" and "long"; equal scores, lower id first.
    assert [i for i, _ in result.fused][:2] == sorted([ids["aligned"], ids["long"]])
    # The cross-encoder sees "title. text" and puts the passage sharing most words first.
    assert cross.calls[0][1][0].startswith("s25-a. ")
    assert [h.passage_id for h in result.hits] == [ids["aligned"], ids["long"]]
    first = result.hits[0]
    assert (first.bm25_rank, first.dense_rank, first.rerank_score) == (1, 2, 3.0)
    assert set(result.seconds) == {"bm25", "dense", "rerank"}

    only_b = Retriever(engine, bm25, FakeQueryEncoder(), None, params).search("aspirin", paper_ids=[b])
    assert only_b.bm25 == [] and [h.passage_id for h in only_b.hits] == [ids["other"]]
    assert only_b.hits[0].rerank_score is None
