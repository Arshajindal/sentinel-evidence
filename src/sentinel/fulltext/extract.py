"""Passages and citation contexts into the database (S2.3, ADR-046).

Both walk their works in paper ID order and resume from `ingest_watermark`.
A work already done under the current version is left alone, so a re-run
writes nothing and nothing is deleted: rows from an older version stay,
which keeps evaluation labels that point at them valid (ADR-044).

- Passages: every corpus paper and cited work that has text
  (`text_availability.fulltext` full or abstract). A full-text work's
  passages come from its abstract and its cached JATS body; an abstract-only
  work's from its abstract.
- Contexts: every corpus paper whose JATS XML is cached, linked to its
  stored references through the XML ref ids.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import Connection, Engine, insert, select

from sentinel.fulltext import body
from sentinel.fulltext.contexts import EXTRACTOR_VERSION, ContextStats, RefInfo, extract
from sentinel.fulltext.passages import PassageParams, make_passages
from sentinel.ingest.base import Harvester, UpsertCounts
from sentinel.ingest.fulltext import work_ids
from sentinel.models import (
    citation_context,
    citation_context_reference,
    corpus_paper,
    paper,
    passage,
    reference,
    text_availability,
)
from sentinel.schemas import (
    CitationContextRecord,
    PaperContexts,
    PaperPassages,
    PassageRecord,
)

CHUNK = 200
_LABEL = re.compile(r"\s*\[?\(?(\d+)[.\])]?\s*")


def _resume_after(cursor: str | None, label: str) -> int:
    if cursor:
        cursor_label, _, last = cursor.rpartition(":")
        if cursor_label == label and last.isdigit():
            return int(last)
    return 0


class PassageExtractor(Harvester):
    def __init__(self, engine: Engine, *, label: str, cache_dir: str | Path,
                 params: PassageParams) -> None:
        self.source = f"passages:{label}"
        self.engine = engine
        self.label = label
        self.cache = Path(cache_dir)
        self.params = params
        self.version = params.version()
        ids = work_ids(label).subquery()
        with engine.connect() as conn:
            self.works = conn.execute(
                select(text_availability.c.paper_id, text_availability.c.fulltext,
                       text_availability.c.pmcid)
                .join(ids, ids.c.id == text_availability.c.paper_id)
                .where(text_availability.c.fulltext != "none")
                .order_by(text_availability.c.paper_id)).all()
        if not self.works:
            raise ValueError(f"corpus {label!r} has no works with text; run `sentinel ingest fulltext`")
        self.missing_xml: set[str] = set()

    def fetch(self, cursor: str | None) -> Iterator[tuple[dict, str | None]]:
        done = _resume_after(cursor, self.label)
        todo = [w for w in self.works if w.paper_id > done]
        for i in range(0, len(todo), CHUNK):
            chunk = todo[i:i + CHUNK]
            with self.engine.connect() as conn:
                abstracts = dict(conn.execute(
                    select(text_availability.c.paper_id, text_availability.c.abstract)
                    .where(text_availability.c.paper_id.in_([w.paper_id for w in chunk]))).all())
            for w in chunk:
                xml = None
                if w.fulltext == "full":
                    path = self.cache / f"{w.pmcid}.xml"
                    if path.exists():
                        xml = path.read_bytes()
                    else:
                        self.missing_xml.add(w.pmcid)
                yield {"paper_id": w.paper_id, "abstract": abstracts.get(w.paper_id), "xml": xml}, \
                    f"{self.label}:{w.paper_id}"

    def record_key(self, raw: dict) -> str | None:
        return str(raw.get("paper_id"))

    def normalise(self, raw: dict) -> PaperPassages:
        paras = body.paragraphs(body.parse(raw["xml"])) if raw.get("xml") else []
        texts = make_passages(raw.get("abstract"), paras, self.params)
        return PaperPassages(paper_id=raw["paper_id"], splitter_ver=self.version, passages=[
            PassageRecord(ordinal=i, section=t.section, text=t.text)
            for i, t in enumerate(texts, start=1)])

    def upsert(self, conn: Connection, records: list[PaperPassages]) -> UpsertCounts:
        counts = UpsertCounts()
        done = set(conn.execute(select(passage.c.paper_id).distinct().where(
            passage.c.paper_id.in_([r.paper_id for r in records]),
            passage.c.splitter_ver == self.version)).scalars())
        rows = []
        for r in records:
            if r.paper_id in done or not r.passages:
                counts.unchanged += 1
                continue
            rows += [{"paper_id": r.paper_id, "splitter_ver": r.splitter_ver, **p.model_dump()}
                     for p in r.passages]
            counts.inserted += 1
        if rows:
            conn.execute(insert(passage), rows)
        return counts


class ContextExtractor(Harvester):
    def __init__(self, engine: Engine, *, label: str, cache_dir: str | Path) -> None:
        self.source = f"contexts:{label}"
        self.engine = engine
        self.label = label
        self.cache = Path(cache_dir)
        with engine.connect() as conn:
            self.papers = conn.execute(
                select(paper.c.id, paper.c.pmcid)
                .join(corpus_paper, corpus_paper.c.paper_id == paper.c.id)
                .where(corpus_paper.c.label == label).order_by(paper.c.id)).all()
        if not self.papers:
            raise ValueError(f"corpus {label!r} has no harvested papers")
        self.stats = ContextStats()
        self.missing_xml: set[str] = set()

    def fetch(self, cursor: str | None) -> Iterator[tuple[dict, str | None]]:
        done = _resume_after(cursor, self.label)
        for p in self.papers:
            if p.id <= done:
                continue
            path = self.cache / f"{p.pmcid}.xml"
            if not path.exists():
                self.missing_xml.add(p.pmcid)
            with self.engine.connect() as conn:
                refs = conn.execute(select(
                    reference.c.id, reference.c.ordinal, reference.c.raw["id"].astext.label("rid"),
                    reference.c.raw["label"].astext.label("label"),
                ).where(reference.c.citing_paper_id == p.id)).all()
            yield {"paper_id": p.id, "xml": path.read_bytes() if path.exists() else None,
                   "refs": {r.rid: RefInfo(r.id, r.ordinal, numeric_label(r.label))
                            for r in refs if r.rid}}, f"{self.label}:{p.id}"

    def record_key(self, raw: dict) -> str | None:
        return str(raw.get("paper_id"))

    def normalise(self, raw: dict) -> PaperContexts:
        paras = body.paragraphs(body.parse(raw["xml"])) if raw.get("xml") else []
        found = extract(paras, raw["refs"], self.stats)
        return PaperContexts(citing_paper_id=raw["paper_id"], extractor_ver=EXTRACTOR_VERSION,
                             contexts=[CitationContextRecord(
                                 ordinal=c.ordinal, section=c.section, marker=c.marker,
                                 sentence=c.sentence, context_text=c.context_text,
                                 reference_ids=list(c.reference_ids),
                                 sentence_cites=[list(x) for x in c.sentence_cites])
                                 for c in found])

    def upsert(self, conn: Connection, records: list[PaperContexts]) -> UpsertCounts:
        counts = UpsertCounts()
        done = set(conn.execute(select(citation_context.c.citing_paper_id).distinct().where(
            citation_context.c.citing_paper_id.in_([r.citing_paper_id for r in records]),
            citation_context.c.extractor_ver == EXTRACTOR_VERSION)).scalars())
        for r in records:
            if r.citing_paper_id in done or not r.contexts:
                counts.unchanged += 1
                continue
            ids = conn.execute(
                insert(citation_context).returning(citation_context.c.id,
                                                   sort_by_parameter_order=True),
                [{"citing_paper_id": r.citing_paper_id, "extractor_ver": r.extractor_ver,
                  **c.model_dump(exclude={"reference_ids"})} for c in r.contexts],
            ).scalars().all()
            conn.execute(insert(citation_context_reference), [
                {"citation_context_id": cid, "reference_id": ref}
                for cid, c in zip(ids, r.contexts, strict=True) for ref in c.reference_ids])
            counts.inserted += 1
        return counts


def numeric_label(label: str | None) -> int | None:
    """"18." / "[18]" / "18" -> 18; anything else (author-year) -> None."""
    m = _LABEL.fullmatch(label or "")
    return int(m.group(1)) if m else None


def context_coverage(conn: Connection, label: str) -> dict[str, int]:
    """How many of a corpus's references have at least one citation context
    from the current context extractor."""
    refs = (select(reference.c.id)
            .join(corpus_paper, corpus_paper.c.paper_id == reference.c.citing_paper_id)
            .where(corpus_paper.c.label == label)).subquery()
    total = conn.execute(select(refs.c.id)).all()
    with_ctx = conn.execute(
        select(citation_context_reference.c.reference_id).distinct()
        .join(refs, refs.c.id == citation_context_reference.c.reference_id)
        .join(citation_context, citation_context.c.id == citation_context_reference.c.citation_context_id)
        .where(citation_context.c.extractor_ver == EXTRACTOR_VERSION)).all()
    return {"references": len(total), "with_context": len(with_ctx)}
