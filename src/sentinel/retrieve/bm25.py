"""BM25 over passages, with the bm25s library (§10.1 item 5, ADR-048).

Postgres's ts_rank is not BM25, so the index is bm25s's, built from the
passages in the database and saved under SENTINEL_BM25_INDEX_DIR with the
passage ID of every row and a manifest of how it was built. Loading checks
the manifest against the settings, so an index built with other parameters
or another splitter version is refused rather than silently used.

Each passage is indexed as "title. text". Tokens: lower-cased words of two
or more characters, English stopwords removed, Snowball English stems
(TOKENIZER_VERSION). Scoring a subset (one cited work's passages) uses the
whole index's statistics, so a term rare in the corpus stays rare.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

import bm25s
import numpy as np
import Stemmer
from sqlalchemy import Engine, select

from sentinel.models import paper, passage
from sentinel.retrieve.encoders import document_text

TOKENIZER_VERSION = "1.0.0"
_STEMMER = Stemmer.Stemmer("english")


def tokenize(texts: list[str], *, return_ids: bool = True):
    return bm25s.tokenize(texts, stopwords="en", stemmer=_STEMMER, return_ids=return_ids,
                          show_progress=False)


def index_dir(root: str | Path, label: str, scope: str, splitter_ver: str) -> Path:
    return Path(root) / f"{label}-{scope}-{splitter_ver.replace('/', '_')}"


class BM25Index:
    def __init__(self, retriever: bm25s.BM25, passage_ids: np.ndarray, manifest: dict) -> None:
        self.retriever = retriever
        self.passage_ids = passage_ids
        self.manifest = manifest
        self._position = {int(pid): i for i, pid in enumerate(passage_ids)}

    @classmethod
    def from_documents(cls, passage_ids: Sequence[int], documents: list[str], *, k1: float,
                       b: float, manifest: dict | None = None) -> BM25Index:
        retriever = bm25s.BM25(k1=k1, b=b)
        retriever.index(tokenize(documents), show_progress=False)
        return cls(retriever, np.asarray(passage_ids, dtype=np.int64),
                   {**(manifest or {}), "k1": k1, "b": b, "tokenizer": TOKENIZER_VERSION,
                    "documents": len(documents)})

    @classmethod
    def build(cls, engine: Engine, paper_ids_query, *, splitter_ver: str, k1: float,
              b: float, manifest: dict) -> BM25Index:
        ids = paper_ids_query.subquery()
        with engine.connect() as conn:
            rows = conn.execute(
                select(passage.c.id, paper.c.title, passage.c.text)
                .join(paper, paper.c.id == passage.c.paper_id)
                .join(ids, ids.c.id == passage.c.paper_id)
                .where(passage.c.splitter_ver == splitter_ver)
                .order_by(passage.c.id)).all()
        if not rows:
            raise ValueError(f"no passages of splitter version {splitter_ver!r} in scope")
        return cls.from_documents([r.id for r in rows],
                                  [document_text(r.title, r.text) for r in rows], k1=k1, b=b,
                                  manifest={**manifest, "splitter_ver": splitter_ver,
                                            "built_at": datetime.now(timezone.utc).isoformat()})

    def save(self, directory: str | Path) -> None:
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        self.retriever.save(str(directory), show_progress=False)
        np.save(directory / "passage_ids.npy", self.passage_ids)
        (directory / "manifest.json").write_text(json.dumps(self.manifest, indent=2), "utf-8")

    @classmethod
    def load(cls, directory: str | Path, *, expect: dict) -> BM25Index:
        """Refuses an index whose manifest disagrees with `expect`."""
        directory = Path(directory)
        if not (directory / "manifest.json").exists():
            raise FileNotFoundError(f"no BM25 index at {directory}; run `sentinel retrieve build-bm25`")
        manifest = json.loads((directory / "manifest.json").read_text("utf-8"))
        wrong = {k: (manifest.get(k), v) for k, v in {**expect, "tokenizer": TOKENIZER_VERSION}.items()
                 if manifest.get(k) != v}
        if wrong:
            raise ValueError(f"BM25 index at {directory} was built differently "
                             f"(index, settings): {wrong}; rebuild it")
        retriever = bm25s.BM25.load(str(directory), show_progress=False)
        return cls(retriever, np.load(directory / "passage_ids.npy"), manifest)

    def search(self, query: str, *, n: int,
               passage_ids: Sequence[int] | None = None) -> list[tuple[int, float]]:
        """Top `n` (passage ID, score), best first, among `passage_ids` if given.
        Passages that share no term with the query aren't returned."""
        tokens = tokenize([query], return_ids=False)[0]
        if not tokens:
            return []
        scores = self.retriever.get_scores(tokens)
        if passage_ids is None:
            positions = np.flatnonzero(scores > 0)
        else:
            positions = np.array([self._position[p] for p in passage_ids if p in self._position],
                                 dtype=np.int64)
            positions = positions[scores[positions] > 0] if positions.size else positions
        hits = [(int(self.passage_ids[i]), float(scores[i])) for i in positions]
        return sorted(hits, key=lambda h: (-h[1], h[0]))[:n]
