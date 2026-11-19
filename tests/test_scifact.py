"""SciFact as the external evaluation set (S2.6a, ADR-049).

`fixtures/scifact/` is cut from the real release (claims CC BY 4.0, abstracts
ODC-By 1.0): claim 47 has evidence in one cited abstract and none in the
other; claim 2 is contradicted; claim 0 has no evidence; dev claim 5 rests on
the same evidence sentence as train claim 2, so SciFact's own split puts one
abstract in both train and dev.
"""

from __future__ import annotations

import hashlib
import io
import json
import tarfile

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import delete, func, select

from sentinel.eval.scifact import (
    EvalItemRecord,
    assign_splits,
    fetch,
    items_from_claims,
    load,
    read_jsonl,
)
from sentinel.fulltext.passages import PassageParams
from sentinel.models import eval_item, eval_item_gold_passage, paper, passage
from tests.conftest import FIXTURES, requires_db

SF = FIXTURES / "scifact"
PARAMS = PassageParams(150, 300, 1)


def _items():
    return (items_from_claims(read_jsonl(SF / "claims_train.jsonl"), "train")
            + items_from_claims(read_jsonl(SF / "claims_dev.jsonl"), "dev"))


def test_one_item_per_claim_and_cited_abstract():
    got = {(i.claim_id, i.doc_id): (i.label, i.sentences, i.original_split) for i in _items()}
    assert got == {
        (47, 26996935): ("supported", (6,), "train"),
        (47, 3512154): ("insufficient", (), "train"),  # cited, but no evidence in it
        (2, 13734012): ("contradicted", (4,), "train"),
        (0, 31715818): ("insufficient", (), "train"),
        (5, 13734012): ("supported", (4,), "dev"),
    }


def test_mixed_labels_in_one_abstract_are_refused():
    claim = {"id": 1, "claim": "x", "cited_doc_ids": [9], "evidence": {"9": [
        {"label": "SUPPORT", "sentences": [0]}, {"label": "CONTRADICT", "sentences": [1]}]}}
    with pytest.raises(ValueError, match="mixed"):
        items_from_claims([claim], "train")


def test_claims_sharing_an_abstract_share_a_split():
    items = _items()
    for seed in (1, 2, 20260922):
        splits = assign_splits(items, seed)
        assert splits[(2, 13734012)] == splits[(5, 13734012)]  # train claim 2, dev claim 5
        assert splits[(47, 26996935)] == splits[(47, 3512154)]  # one claim, two abstracts
        assert set(splits.values()) <= {"train", "val", "test"}
    assert assign_splits(items, 7) == assign_splits(items, 7)


def test_supported_items_need_gold_and_insufficient_ones_have_none():
    base = dict(set_name="external", split="train", claim_text="c", source_paper_id=1,
                license="L", origin={})
    with pytest.raises(ValidationError):
        EvalItemRecord(**base, label="supported", gold_passage_ids=[])
    with pytest.raises(ValidationError):
        EvalItemRecord(**base, label="insufficient", gold_passage_ids=[3])


# --- fetch -------------------------------------------------------------------------


def _tarball(extra: tuple[str, bytes] | None = None) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name in ("corpus.jsonl", "claims_train.jsonl", "claims_dev.jsonl"):
            tar.add(SF / name, arcname=f"data/{name}")
        if extra:
            info = tarfile.TarInfo(extra[0])
            info.size = len(extra[1])
            tar.addfile(info, io.BytesIO(extra[1]))
    return buf.getvalue()


def _client(body: bytes, calls: list) -> httpx.Client:
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, content=body)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_checks_the_checksum_and_downloads_once(tmp_path):
    body, calls = _tarball(), []
    sha = hashlib.sha256(body).hexdigest()
    data = fetch("https://scifact.test/data.tar.gz", sha, tmp_path, client=_client(body, calls))
    assert (data / "corpus.jsonl").exists() and len(calls) == 1
    fetch("https://scifact.test/data.tar.gz", sha, tmp_path, client=_client(body, calls))
    assert len(calls) == 1  # already downloaded and checked
    with pytest.raises(ValueError, match="SHA-256"):
        fetch("https://scifact.test/data.tar.gz", "0" * 64, tmp_path, client=_client(body, calls))


def test_fetch_refuses_an_archive_that_writes_outside_its_folder(tmp_path):
    body = _tarball(extra=("../outside.txt", b"x"))
    with pytest.raises(tarfile.OutsideDestinationError):
        fetch("https://scifact.test/x", hashlib.sha256(body).hexdigest(), tmp_path / "sf",
              client=_client(body, []))
    assert not (tmp_path / "outside.txt").exists()


# --- load, against the test database ------------------------------------------------


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
def test_load_stores_abstracts_items_and_exact_gold_passages(sf_engine):
    result = load(sf_engine, SF, seed=1, params=PARAMS, release_sha256="abc")
    assert (result.papers_added, result.items_added, result.unmapped_sentences) == (4, 5, 0)
    corpus = {d["doc_id"]: d for d in read_jsonl(SF / "corpus.jsonl")}
    with sf_engine.connect() as conn:
        rows = conn.execute(
            select(eval_item.c.label, eval_item.c.origin, eval_item.c.license, passage.c.text)
            .outerjoin(eval_item_gold_passage, eval_item_gold_passage.c.eval_item_id == eval_item.c.id)
            .outerjoin(passage, passage.c.id == eval_item_gold_passage.c.passage_id)).all()
        sections = set(conn.execute(select(passage.c.section).join(paper, paper.c.id == passage.c.paper_id)
                                    .where(paper.c.source == "scifact")).scalars())
    assert sections == {"abstract"}
    for label, origin, licence, text in rows:
        assert "CC-BY-4.0" in licence and origin["dataset"] == "scifact"
        if label == "insufficient":
            assert text is None
        else:  # the gold passage holds the evidence sentence, word for word
            sentence = corpus[origin["doc_id"]]["abstract"][origin["evidence_sentences"][0]]
            assert " ".join(sentence.split()) in text

    again = load(sf_engine, SF, seed=1, params=PARAMS, release_sha256="abc")
    assert (again.papers_added, again.passages_added, again.items_added) == (0, 0, 0)
    with sf_engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(eval_item)).scalar_one() == 5
