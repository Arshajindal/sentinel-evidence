"""Passage embeddings (S2.4, ADR-047). Tests never download a model: a fake
encoder makes each vector from its passage, so a vector stored against the
wrong passage would show."""

from __future__ import annotations

import subprocess
import sys

import pytest
from pydantic import ValidationError
from sqlalchemy import delete, insert, select

from sentinel.models import (
    EMBEDDING_DIM,
    corpus,
    corpus_paper,
    ingest_watermark,
    paper,
    passage,
    passage_embedding,
    reference,
)
from sentinel.retrieve.embed import PassageEmbedder, PassageEmbedding
from sentinel.retrieve.encoders import model_tag
from tests.conftest import ROOT, requires_db

VER = "1.0.0/150-300-1"


class FakeEncoder:
    name = "fake/encoder@000000000000"
    dim = EMBEDDING_DIM

    def __init__(self) -> None:
        self.batches: list[list[tuple[str, str]]] = []

    def lengths(self, pairs):
        return [len(title) + len(text) for title, text in pairs]

    def encode(self, pairs):
        self.batches.append(list(pairs))
        # Number 0 identifies the passage text; the rest are padding.
        return [[float(len(text))] + [0.5] * (EMBEDDING_DIM - 1) for _, text in pairs]


def test_model_tag_names_the_weights():
    assert model_tag("ncbi/MedCPT-Article-Encoder", "d05a736da4bb84ee4057b7f7999485be6ed85465") == \
        "ncbi/MedCPT-Article-Encoder@d05a736da4bb"


def test_embedding_records_need_the_right_shape_and_finite_numbers():
    ok = [0.1] * EMBEDDING_DIM
    assert PassageEmbedding(passage_id=1, model="m", embedding=ok).embedding == ok
    for bad in ([0.1] * 10, [float("nan")] + [0.1] * (EMBEDDING_DIM - 1)):
        with pytest.raises(ValidationError):
            PassageEmbedding(passage_id=1, model="m", embedding=bad)


def test_an_encoder_of_another_width_is_refused():
    enc = FakeEncoder()
    enc.dim = 384
    with pytest.raises(ValueError, match="384"):
        PassageEmbedder(None, enc, label="x", splitter_ver=VER)


def test_vectors_return_in_input_order_though_batches_are_sorted_by_length():
    enc = FakeEncoder()
    e = PassageEmbedder(None, enc, label="x", splitter_ver=VER, batch_size=2)
    pairs = [("T", "a" * n) for n in (50, 10, 40, 20, 30)]
    vectors = e._encode(pairs)
    assert [v[0] for v in vectors] == [50.0, 10.0, 40.0, 20.0, 30.0]
    assert [[len(t) for _, t in b] for b in enc.batches] == [[10, 20], [30, 40], [50]]


def test_importing_the_encoders_does_not_load_torch():
    code = "import sys, sentinel.retrieve.encoders, sentinel.retrieve.embed; print('torch' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT)
    assert out.stdout.strip() == "False", out.stderr


# --- against the test database ------------------------------------------------------

LABEL = "test-s24"
TITLES = ("s24-corpus", "s24-cited-a", "s24-cited-b")


@pytest.fixture
def s24_engine(db_engine):
    with db_engine.connect() as conn:
        if conn.execute(select(paper.c.id).where(paper.c.title.not_in(TITLES))).first():
            pytest.skip("paper holds other data; not a disposable database")
    _clear(db_engine)
    with db_engine.begin() as conn:
        ids = {t: conn.execute(insert(paper).values(title=t, title_norm=t, source="test", raw={})
                               .returning(paper.c.id)).scalar_one() for t in TITLES}
        conn.execute(insert(corpus).values(label=LABEL, source="t", query="t", seed=1, hit_count=1, n=1))
        conn.execute(insert(corpus_paper).values(label=LABEL, ordinal=1, source="PMC", source_id="x",
                                                 pmcid="PMC1", paper_id=ids["s24-corpus"]))
        conn.execute(insert(reference), [
            {"citing_paper_id": ids["s24-corpus"], "ordinal": i, "resolved_paper_id": ids[t],
             "resolution_status": "resolved"} for i, t in enumerate(TITLES[1:], start=1)])
        # Passage texts of distinct lengths, so the fake vector identifies each.
        rows = [{"paper_id": ids[t], "splitter_ver": VER, "ordinal": k, "section": "results",
                 "text": "w" * (10 * n + k)} for n, t in enumerate(TITLES) for k in range(1, 4)]
        rows.append({"paper_id": ids["s24-cited-a"], "splitter_ver": "0.9.0/old", "ordinal": 1,
                     "section": "results", "text": "older split"})
        conn.execute(insert(passage), rows)
    yield db_engine
    _clear(db_engine)


def _clear(engine) -> None:
    with engine.begin() as conn:
        ids = select(paper.c.id).where(paper.c.title.in_(TITLES))
        conn.execute(delete(reference).where(reference.c.citing_paper_id.in_(ids)))
        conn.execute(delete(paper).where(paper.c.title.in_(TITLES)))
        conn.execute(delete(corpus).where(corpus.c.label == LABEL))
        conn.execute(delete(ingest_watermark).where(ingest_watermark.c.source.like(f"embed:{LABEL}:%")))


def _stored(engine) -> dict[str, float]:
    with engine.connect() as conn:
        rows = conn.execute(select(passage.c.text, passage_embedding.c.embedding, passage_embedding.c.model)
                            .join(passage_embedding, passage_embedding.c.passage_id == passage.c.id)).all()
    assert {r.model for r in rows} <= {FakeEncoder.name}
    return {r.text: float(r.embedding[0]) for r in rows}


@requires_db
def test_cited_passages_are_embedded_once_each_against_the_right_passage(s24_engine):
    enc = FakeEncoder()
    embedder = PassageEmbedder(s24_engine, enc, label=LABEL, splitter_ver=VER, batch_size=2, chunk=4)
    assert embedder.count_pending() == 6  # the two cited works; not the corpus paper or the old split
    stats = embedder.run(s24_engine, batch_size=4)
    assert (stats.seen, stats.inserted) == (6, 6)
    stored = _stored(s24_engine)
    assert len(stored) == 6 and all(v == len(t) for t, v in stored.items())
    assert all(title in ("s24-cited-a", "s24-cited-b") for b in enc.batches for title, _ in b)

    again = PassageEmbedder(s24_engine, FakeEncoder(), label=LABEL, splitter_ver=VER).run(s24_engine)
    assert (again.seen, again.inserted) == (0, 0)  # nothing left without a vector


@requires_db
def test_a_killed_run_resumes_and_all_adds_the_corpus_papers(s24_engine):
    first = PassageEmbedder(s24_engine, FakeEncoder(), label=LABEL, splitter_ver=VER, chunk=2)
    assert first.run(s24_engine, limit=2, batch_size=2).inserted == 2
    rest = PassageEmbedder(s24_engine, FakeEncoder(), label=LABEL, splitter_ver=VER, chunk=2)
    assert rest.run(s24_engine, resume=True, batch_size=2).inserted == 4
    everything = PassageEmbedder(s24_engine, FakeEncoder(), label=LABEL, splitter_ver=VER, scope="all")
    assert everything.run(s24_engine).inserted == 3  # the corpus paper's own passages
    assert len(_stored(s24_engine)) == 9
