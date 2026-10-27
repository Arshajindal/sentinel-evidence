"""Reported tests from corpus papers' body text, into `stat_test` (S1.12; ADR-037).

Full-text JATS XML is cached on disk (SENTINEL_FULLTEXT_CACHE_DIR, gitignored)
and fetched only when missing, so re-extraction needs no network and Phase 2
can reuse it. Only body paragraphs are read: tables, figure captions, the
abstract (it repeats the results) and the reference list are skipped. No body
text is stored in the database, only the matched test strings and the section
they came from.

A paper's rows are derived data: re-extraction replaces them in one
transaction. The paper's `fulltext_status` becomes 'full' once its body has
been examined, which is how C03 tells "no tests reported" from "not looked".
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from lxml import etree
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Connection, Engine, delete, insert, select, update

from sentinel.checks.c03_statcheck import assess, checkable
from sentinel.ingest.base import Harvester, UpsertCounts
from sentinel.ingest.europepmc import EuropePMC
from sentinel.ingest.http import RetryableHTTPError
from sentinel.log import get_logger
from sentinel.models import corpus_paper, paper, stat_test
from sentinel.parse.statistics import find_statistics

log = get_logger(__name__)

_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False)
_SKIP = frozenset({"table-wrap", "table", "fig", "ref-list", "abstract", "sub-article", "response",
                   "back", "front", "fn-group", "app-group"})


class StatRow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    location: str | None
    raw_string: str
    test_type: str
    statistic: float
    df1: float | None
    df2: float | None
    reported_p: float
    reported_p_op: str
    computed_p: float | None
    one_tailed_ok: bool | None
    inconsistent: bool | None
    gross: bool | None


class PaperStats(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    paper_id: int
    has_body: bool
    rows: list[StatRow]


def body_paragraphs(xml: str | bytes) -> list[tuple[str | None, str]]:
    """(section title, paragraph text) for every body paragraph."""
    data = xml.encode("utf-8") if isinstance(xml, str) else xml
    root = etree.fromstring(data, parser=_PARSER)
    body = root.find(".//body")
    if body is None:
        return []
    out = []
    for p in body.iter("p"):
        ancestors = list(p.iterancestors())
        if any(a.tag in _SKIP for a in ancestors):
            continue
        secs = [a for a in ancestors if a.tag == "sec"]
        title = None
        if secs:
            top = secs[-1].find("title")  # the top-level section names the location
            title = " ".join("".join(top.itertext()).split()) if top is not None else None
        out.append((title, " ".join("".join(p.itertext()).split())))
    return out


def extract(xml: str | bytes, *, alpha: float, allow_one_tailed: bool) -> list[StatRow]:
    rows = []
    for section, text in body_paragraphs(xml):
        for test in find_statistics(text):
            computed = None if checkable(test) else assess(test, alpha=alpha,
                                                           allow_one_tailed=allow_one_tailed)
            op = test.p_op + ("=" if test.p_inclusive else "") if test.p_op != "=" else "="
            rows.append(StatRow(
                location=section, raw_string=test.raw_string, test_type=test.test_type,
                statistic=test.statistic, df1=test.df1, df2=test.df2,
                reported_p=test.reported_p, reported_p_op=op,
                computed_p=computed.computed_p if computed else None,
                one_tailed_ok=computed.one_tailed_ok if computed else None,
                inconsistent=computed.inconsistent if computed else None,
                gross=computed.gross if computed else None,
            ))
    return rows


class StatExtractor(Harvester):
    """Walks a corpus's papers; resumable from `ingest_watermark`."""

    def __init__(self, engine: Engine, epmc: EuropePMC, *, label: str, cache_dir: str | Path,
                 alpha: float, allow_one_tailed: bool) -> None:
        self.source = f"stats:{label}"
        self.epmc = epmc
        self.label = label
        self.cache = Path(cache_dir)
        self.alpha = alpha
        self.allow_one_tailed = allow_one_tailed
        with engine.connect() as conn:
            self.members = conn.execute(
                select(corpus_paper.c.ordinal, corpus_paper.c.paper_id, corpus_paper.c.pmcid)
                .where(corpus_paper.c.label == label, corpus_paper.c.paper_id.is_not(None))
                .order_by(corpus_paper.c.ordinal)).mappings().all()
        if not self.members:
            raise ValueError(f"corpus {label!r} has no harvested papers")
        self.no_fulltext: set[str] = set()

    def _xml(self, pmcid: str) -> bytes | None:
        path = self.cache / f"{pmcid}.xml"
        if path.exists():
            return path.read_bytes()
        try:
            text = self.epmc.fulltext_xml(pmcid)
        except RetryableHTTPError as exc:
            log.error("fulltext_failed", pmcid=pmcid, status=exc.response.status_code)
            return None
        if text is None:
            return None
        self.cache.mkdir(parents=True, exist_ok=True)
        data = text.encode("utf-8")
        path.write_bytes(data)
        return data

    def fetch(self, cursor: str | None) -> Iterator[tuple[dict, str | None]]:
        done = 0
        if cursor:
            label, _, last = cursor.rpartition(":")
            if label == self.label and last.isdigit():
                done = int(last)
        for m in self.members:
            if m["ordinal"] <= done:
                continue
            yield {"paper_id": m["paper_id"], "pmcid": m["pmcid"],
                   "xml": self._xml(m["pmcid"])}, f"{self.label}:{m['ordinal']}"

    def record_key(self, raw: dict) -> str | None:
        return raw.get("pmcid")

    def normalise(self, raw: dict) -> PaperStats:
        xml = raw.get("xml")
        if xml is None:
            self.no_fulltext.add(raw["pmcid"])
            return PaperStats(paper_id=raw["paper_id"], has_body=False, rows=[])
        rows = extract(xml, alpha=self.alpha, allow_one_tailed=self.allow_one_tailed)
        has_body = bool(body_paragraphs(xml))
        return PaperStats(paper_id=raw["paper_id"], has_body=has_body, rows=rows)

    def upsert(self, conn: Connection, records: list[PaperStats]) -> UpsertCounts:
        counts = UpsertCounts()
        for r in records:
            conn.execute(delete(stat_test).where(stat_test.c.paper_id == r.paper_id))
            if r.rows:
                conn.execute(insert(stat_test), [{"paper_id": r.paper_id, **row.model_dump()}
                                                 for row in r.rows])
            if r.has_body:
                conn.execute(update(paper).where(paper.c.id == r.paper_id)
                             .values(fulltext_status="full"))
            counts.inserted += len(r.rows)
        return counts
