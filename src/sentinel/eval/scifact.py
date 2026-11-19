"""SciFact as the external evaluation set (§10.1 item 7, Q6, ADR-049).

SciFact (Wadden et al., EMNLP 2020) pairs claims, rewritten from citing
sentences in biomedical papers, with abstracts whose sentences experts marked
as supporting or contradicting them. Claims and labels are CC BY 4.0, the
abstracts ODC-By 1.0 (the authors' LICENSE.md). Only the train and dev claims
are used: the test claims have no labels.

One evaluation item per (claim, cited abstract), which is C09's question:
- an abstract with evidence: `supported` or `contradicted` (SciFact never
  mixes labels within one abstract), with the passages holding the evidence
  sentences as gold;
- an abstract the citation named but where annotators found no evidence:
  `insufficient`, with no gold passage.

Splits are our own, by source paper (§10.2). SciFact's train and dev share
181 of dev's 283 abstracts, so its split would leak. Claims and abstracts
that share a link form groups (572, the largest 9 claims), and each group is
assigned train/val/test 60/20/20 by a hash seeded with SENTINEL_RANDOM_SEED.
SciFact's own split is kept in `origin` for comparison with published work.

Each abstract becomes a paper (source "scifact") and its sentences are cut
into passages by the corpus's window rules, from SciFact's own sentence list,
so every evidence sentence maps to a passage exactly.
"""

from __future__ import annotations

import hashlib
import json
import tarfile
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import httpx
from sqlalchemy import Engine, insert, select

from sentinel.eval.items import EvalItemRecord, hash_split
from sentinel.fulltext.passages import PassageParams, sentence_windows
from sentinel.log import get_logger
from sentinel.models import eval_item, eval_item_gold_passage, paper, passage
from sentinel.resolve.normalize import normalize_title
from sentinel.schemas import PaperRecord

log = get_logger(__name__)

DATASET = "scifact"
LICENSE = "claims and labels CC-BY-4.0; abstracts ODC-By-1.0"
LABELS = {"SUPPORT": "supported", "CONTRADICT": "contradicted"}


@dataclass(frozen=True)
class Item:
    claim_id: int
    claim: str
    doc_id: int
    label: Literal["supported", "contradicted", "insufficient"]
    sentences: tuple[int, ...]  # evidence sentence indexes in the abstract
    original_split: str


@dataclass
class Loaded:
    papers_added: int = 0
    passages_added: int = 0
    items_added: int = 0
    gold_links: int = 0
    by_split_label: dict[str, int] = field(default_factory=dict)
    unmapped_sentences: int = 0


# --- fetching ----------------------------------------------------------------------


def fetch(url: str, sha256: str, directory: Path, *, client: httpx.Client) -> Path:
    """Download (unless already there) and check the release, then unpack it
    with tarfile's "data" filter: no paths outside the target, no links or
    devices. Returns the folder holding corpus.jsonl and the claims files."""
    archive = directory / "download" / "data.tar.gz"
    if not archive.exists():
        archive.parent.mkdir(parents=True, exist_ok=True)
        response = client.get(url, follow_redirects=True)
        response.raise_for_status()
        archive.write_bytes(response.content)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if digest != sha256:
        raise ValueError(f"{archive} has SHA-256 {digest}, not the pinned {sha256}; "
                         "the release changed or the download is damaged")
    target = directory / "extracted"
    if not (target / "data" / "corpus.jsonl").exists():
        with tarfile.open(archive) as tar:
            tar.extractall(target, filter="data")
    return target / "data"


# --- parsing -----------------------------------------------------------------------


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def items_from_claims(claims: list[dict], original_split: str) -> list[Item]:
    out = []
    for c in claims:
        evidence = {int(k): v for k, v in c["evidence"].items()}
        for doc_id in sorted(evidence):
            rationales = evidence[doc_id]
            labels = {LABELS[r["label"]] for r in rationales}
            if len(labels) != 1:
                raise ValueError(f"claim {c['id']} has mixed labels in abstract {doc_id}")
            out.append(Item(c["id"], c["claim"], doc_id, labels.pop(),
                            tuple(sorted({s for r in rationales for s in r["sentences"]})),
                            original_split))
        for doc_id in sorted({int(d) for d in c["cited_doc_ids"]} - set(evidence)):
            out.append(Item(c["id"], c["claim"], doc_id, "insufficient", (), original_split))
    return out


def assign_splits(items: list[Item], seed: int) -> dict[tuple[int, int], str]:
    """(claim_id, doc_id) -> split. Claims and abstracts joined by any item
    land in the same split, so no abstract or claim text crosses splits."""
    parent: dict[tuple[str, int], tuple[str, int]] = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for it in items:
        parent[find(("c", it.claim_id))] = find(("d", it.doc_id))
    members: dict[tuple[str, int], list[int]] = defaultdict(list)
    for it in items:
        members[find(("d", it.doc_id))].append(it.doc_id)
    split_of_root = {root: hash_split(f"{seed}:{DATASET}:{min(docs)}") for root, docs in members.items()}
    return {(it.claim_id, it.doc_id): split_of_root[find(("d", it.doc_id))] for it in items}


# --- loading -----------------------------------------------------------------------


def load(engine: Engine, data_dir: Path, *, seed: int, params: PassageParams,
         release_sha256: str) -> Loaded:
    """Papers and passages for every abstract, then the items and their gold
    passages. Idempotent: abstracts already stored are reused, and items from
    this release are written only once."""
    corpus = {d["doc_id"]: d for d in read_jsonl(data_dir / "corpus.jsonl")}
    items = (items_from_claims(read_jsonl(data_dir / "claims_train.jsonl"), "train")
             + items_from_claims(read_jsonl(data_dir / "claims_dev.jsonl"), "dev"))
    splits = assign_splits(items, seed)
    version = params.version()
    result = Loaded()

    with engine.begin() as conn:
        stored = {int(doc): pid for pid, doc in conn.execute(
            select(paper.c.id, paper.c.raw["scifact_doc_id"].astext)
            .where(paper.c.source == DATASET)).all()}
        for doc_id, doc in corpus.items():
            if doc_id in stored:
                continue
            record = PaperRecord(title=doc["title"].strip(), title_norm=normalize_title(doc["title"]),
                                 source=DATASET, fulltext_status="abstract",
                                 raw={"scifact_doc_id": doc_id, "structured": doc["structured"]})
            stored[doc_id] = conn.execute(insert(paper).values(**record.model_dump())
                                          .returning(paper.c.id)).scalar_one()
            result.papers_added += 1

        have_passages = set(conn.execute(select(passage.c.paper_id).distinct().where(
            passage.c.paper_id.in_(list(stored.values())), passage.c.splitter_ver == version)).scalars())
        sentence_passages: dict[int, dict[int, list[int]]] = {}
        for doc_id, doc in corpus.items():
            pid = stored[doc_id]
            windows = sentence_windows([" ".join(s.split()) for s in doc["abstract"]], params)
            if pid not in have_passages:
                conn.execute(insert(passage), [
                    {"paper_id": pid, "splitter_ver": version, "ordinal": i, "section": "abstract",
                     "text": text} for i, (text, _) in enumerate(windows, start=1)])
                result.passages_added += len(windows)
            ids = list(conn.execute(select(passage.c.id).where(
                passage.c.paper_id == pid, passage.c.splitter_ver == version)
                .order_by(passage.c.ordinal)).scalars())
            by_sentence: dict[int, list[int]] = defaultdict(list)
            for passage_id, (_, sent_ids) in zip(ids, windows, strict=True):
                for s in sent_ids:
                    by_sentence[s].append(passage_id)
            sentence_passages[doc_id] = by_sentence

        already = conn.execute(select(eval_item.c.id).where(
            eval_item.c.origin["dataset"].astext == DATASET,
            eval_item.c.origin["release_sha256"].astext == release_sha256).limit(1)).first()
        if already:
            log.info("scifact_items_present", release_sha256=release_sha256)
            return result

        counts: dict[str, int] = defaultdict(int)
        for it in items:
            gold = sorted({p for s in it.sentences for p in sentence_passages[it.doc_id].get(s, [])})
            result.unmapped_sentences += sum(1 for s in it.sentences
                                             if s not in sentence_passages[it.doc_id])
            split = splits[(it.claim_id, it.doc_id)]
            record = EvalItemRecord(
                set_name="external", split=split, claim_text=it.claim,
                source_paper_id=stored[it.doc_id], label=it.label, license=LICENSE,
                origin={"dataset": DATASET, "release_sha256": release_sha256,
                        "claim_id": it.claim_id, "doc_id": it.doc_id,
                        "original_split": it.original_split,
                        "evidence_sentences": list(it.sentences)},
                gold_passage_ids=gold)
            item_id = conn.execute(insert(eval_item).values(
                **record.model_dump(exclude={"gold_passage_ids"})).returning(eval_item.c.id)).scalar_one()
            if gold:
                conn.execute(insert(eval_item_gold_passage),
                             [{"eval_item_id": item_id, "passage_id": p} for p in gold])
            result.items_added += 1
            result.gold_links += len(gold)
            counts[f"{split}/{it.label}"] += 1
        result.by_split_label = dict(sorted(counts.items()))
    return result
