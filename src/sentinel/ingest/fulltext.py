"""Full text and abstracts for a corpus and the works it cites (§10.1 item 1, ADR-045).

Each work is looked up in Europe PMC by DOI, else by PMID, 25 to a query.
An open-access record (isOpenAccess Y, with a PMCID) has its JATS XML
fetched into SENTINEL_FULLTEXT_CACHE_DIR, the cache S1.12 started (ADR-037),
and the work counts as full text when that XML has body paragraphs. Nothing
is fetched for a PMC record that isn't open access: Europe PMC serves no XML
for it. Every other work keeps its abstract: Europe PMC's, else the one in
its stored Crossref or OpenAlex record.

The works are the corpus's papers plus every work their references resolved
to, in paper ID order; the cursor is "<label>:<last paper id>". Results go
to `text_availability`. The source's metadata in `paper` is left alone
(ADR-032), except `fulltext_status`, which is only ever raised.
"""

from __future__ import annotations

import html
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, Engine, case, func, literal_column, select, union, update
from sqlalchemy.dialects.postgresql import insert

from sentinel.ingest.base import Harvester, UpsertCounts
from sentinel.ingest.europepmc import EuropePMC
from sentinel.ingest.fulltext_stats import body_paragraphs
from sentinel.ingest.http import RetryableHTTPError
from sentinel.log import get_logger
from sentinel.models import corpus_paper, paper, reference, text_availability
from sentinel.resolve.normalize import normalize_doi
from sentinel.schemas import TextAvailability

log = get_logger(__name__)

LOOKUP_BATCH = 25
# The availability facts kept from a Europe PMC core record; the abstract has
# its own column and the rest of the record isn't needed.
RAW_KEYS = ("id", "source", "pmid", "pmcid", "doi", "isOpenAccess", "inEPMC", "inPMC",
            "hasPDF", "license", "fullTextIdList", "firstPublicationDate")
_RANK = {"none": 0, "abstract": 1, "full": 2}
_INLINE = re.compile(r"</?(?:[a-z]+:)?(?:i|b|em|strong|sup|sub|sc|span|a|u|italic|bold)\b[^>]*>",
                     re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")
# Markup some sources escape (&lt;em&gt;), so it reappears after unescaping.
# Only known HTML tag names: abstracts also hold real "<" ("18.5<BMI<25").
_ESCAPED_TAG = re.compile(r"</?(?:i|b|em|strong|sup|sub|span|u|br|p|div|h[1-6]|li|ul|ol)\b[^<>]*>",
                          re.IGNORECASE)
# Text a source puts where it has no abstract; not an abstract.
PLACEHOLDER_ABSTRACTS = frozenset({
    "abstract is missing", "no abstract available", "no abstract", "abstract not available",
    "[figure: see text]", "supplemental data file", "n/a",
})


@dataclass(frozen=True)
class Work:
    paper_id: int
    doi: str | None
    pmid: str | None


def work_ids(label: str):
    """IDs of the corpus's papers and every work their references resolved to."""
    members = select(corpus_paper.c.paper_id.label("id")).where(
        corpus_paper.c.label == label, corpus_paper.c.paper_id.is_not(None))
    cited = (select(reference.c.resolved_paper_id.label("id"))
             .join(corpus_paper, corpus_paper.c.paper_id == reference.c.citing_paper_id)
             .where(corpus_paper.c.label == label, reference.c.resolved_paper_id.is_not(None)))
    return union(members, cited)


def load_works(engine: Engine, label: str) -> list[Work]:
    """The corpus's papers and every work their references resolved to."""
    ids = work_ids(label).subquery()
    with engine.connect() as conn:
        rows = conn.execute(
            select(paper.c.id, paper.c.doi, paper.c.pmid)
            .join(ids, ids.c.id == paper.c.id).order_by(paper.c.id)
        ).all()
    return [Work(r.id, r.doi, r.pmid) for r in rows]


class FulltextHarvester(Harvester):
    """Looks up each work's text in Europe PMC; resumable from `ingest_watermark`."""

    def __init__(self, engine: Engine, epmc: EuropePMC, *, label: str, cache_dir: str | Path,
                 max_consecutive_failures: int = 5, works: list[Work] | None = None) -> None:
        self.source = f"fulltext:{label}"
        self.engine = engine
        self.epmc = epmc
        self.label = label
        self.cache = Path(cache_dir)
        self.max_consecutive_failures = max_consecutive_failures
        self._consecutive_failures = 0
        self.works = works if works is not None else load_works(engine, label)
        if not self.works:
            raise ValueError(f"corpus {label!r} has no harvested papers")
        self.fetch_failed: set[str] = set()

    def fetch(self, cursor: str | None) -> Iterator[tuple[dict, str | None]]:
        done = 0
        if cursor:
            cursor_label, _, last = cursor.rpartition(":")
            if cursor_label == self.label and last.isdigit():
                done = int(last)
        todo = [w for w in self.works if w.paper_id > done]
        for i in range(0, len(todo), LOOKUP_BATCH):
            chunk = todo[i:i + LOOKUP_BATCH]
            found = self._lookup(chunk)
            stored = self._stored_abstracts([w.paper_id for w in chunk])
            for w in chunk:
                key, core = found.get(w.paper_id, (None, None))
                xml, error = self._xml(core["pmcid"]) if wants_xml(core) else (None, None)
                yield {"paper_id": w.paper_id, "lookup_key": key, "core": core, "xml": xml,
                       "xml_error": error, "stored": stored.get(w.paper_id, {})}, \
                    f"{self.label}:{w.paper_id}"

    def _lookup(self, works: list[Work]) -> dict[int, tuple[str | None, dict | None]]:
        """paper_id -> (lookup key, best core record or None). DOI first; a
        work the DOI query misses is tried again by PMID."""
        out: dict[int, tuple[str | None, dict | None]] = {}
        by_doi = [w for w in works if w.doi]
        if by_doi:
            records = self.epmc.core_by_dois([w.doi for w in by_doi])
            for w in by_doi:
                out[w.paper_id] = (f"doi:{w.doi}", best_record(
                    [r for r in records if normalize_doi(r.get("doi")) == w.doi]))
        by_pmid = [w for w in works if w.pmid and out.get(w.paper_id, (None, None))[1] is None]
        if by_pmid:
            records = self.epmc.core_by_pmids([w.pmid for w in by_pmid])
            for w in by_pmid:
                out[w.paper_id] = (f"pmid:{w.pmid}", best_record(
                    [r for r in records if r.get("pmid") == w.pmid and r.get("source") == "MED"]))
        for w in works:
            out.setdefault(w.paper_id, (None, None))
        return out

    def _stored_abstracts(self, paper_ids: list[int]) -> dict[int, dict]:
        """Abstracts already in stored Crossref/OpenAlex records, for the fallback."""
        with self.engine.connect() as conn:
            rows = conn.execute(select(
                paper.c.id, paper.c.source,
                paper.c.raw["abstract"].astext.label("crossref"),
                paper.c.raw["abstract_inverted_index"].label("openalex"),
            ).where(paper.c.id.in_(paper_ids))).all()
        return {r.id: {"crossref": r.crossref if r.source == "crossref" else None,
                       "openalex": r.openalex if r.source == "openalex" else None} for r in rows}

    def _xml(self, pmcid: str) -> tuple[bytes | None, str | None]:
        """Cached or fetched XML. One record's persistent 5xx is recorded and
        skipped; several in a row look like an outage, so the run stops."""
        path = self.cache / f"{pmcid}.xml"
        if path.exists():
            return path.read_bytes(), None
        try:
            text = self.epmc.fulltext_xml(pmcid)
        except RetryableHTTPError as exc:
            self._consecutive_failures += 1
            self.fetch_failed.add(pmcid)
            log.error("fulltext_failed", pmcid=pmcid, status=exc.response.status_code,
                      consecutive=self._consecutive_failures)
            if self._consecutive_failures >= self.max_consecutive_failures:
                raise
            return None, f"http_{exc.response.status_code}"
        self._consecutive_failures = 0
        if text is None:
            return None, "not_found"
        data = text.encode("utf-8")
        self.cache.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return data, None

    def record_key(self, raw: dict) -> str | None:
        return str(raw.get("paper_id"))

    def normalise(self, raw: dict) -> TextAvailability:
        return text_availability_record(raw)

    def upsert(self, conn: Connection, records: list[TextAvailability]) -> UpsertCounts:
        counts = UpsertCounts()
        for r in records:
            values = r.model_dump()
            stmt = insert(text_availability).values(**values)
            inserted = conn.execute(stmt.on_conflict_do_update(
                index_elements=[text_availability.c.paper_id],
                set_={**{c: stmt.excluded[c] for c in values if c != "paper_id"},
                      "checked_at": func.now()},
            ).returning(literal_column("(xmax = 0)"))).scalar_one()
            if inserted:
                counts.inserted += 1
            else:
                counts.updated += 1
            # Raised, never lowered: a corpus paper whose body S1.12 examined
            # stays 'full' even if a later fetch fails (C03 relies on it).
            conn.execute(update(paper).where(
                paper.c.id == r.paper_id,
                case({k: v for k, v in _RANK.items()}, value=paper.c.fulltext_status, else_=0)
                < _RANK[r.fulltext],
            ).values(fulltext_status=r.fulltext))
        return counts


def wants_xml(core: dict | None) -> bool:
    return bool(core and core.get("isOpenAccess") == "Y" and core.get("pmcid"))


def best_record(records: list[dict]) -> dict | None:
    """Of several records for one work, prefer open access with a PMCID, then
    any PMCID, then PubMed's; ties go to the lowest Europe PMC ID."""
    if not records:
        return None
    return min(records, key=lambda r: (
        not wants_xml(r), not r.get("pmcid"), r.get("source") != "MED", str(r.get("id"))))


def text_availability_record(raw: dict) -> TextAvailability:
    """A fetched work -> validated row. Pure."""
    core = raw.get("core")
    xml = raw.get("xml")
    has_body = xml is not None and bool(body_paragraphs(xml))
    error = raw.get("xml_error") or ("no_body" if xml is not None and not has_body else None)
    abstract, abstract_source = pick_abstract(core, raw.get("stored") or {})
    return TextAvailability(
        paper_id=raw["paper_id"],
        source="europepmc",
        lookup_key=raw.get("lookup_key"),
        found=core is not None,
        pmcid=core.get("pmcid") if core else None,
        is_oa=core.get("isOpenAccess") == "Y" if core else None,
        in_pmc=core.get("inPMC") == "Y" if core else None,
        license=core.get("license") if core else None,
        fulltext="full" if has_body else ("abstract" if abstract else "none"),
        fulltext_error=error,
        abstract=abstract,
        abstract_source=abstract_source,
        raw={k: core[k] for k in RAW_KEYS if k in core} if core else None,
    )


def pick_abstract(core: dict | None, stored: dict) -> tuple[str | None, str | None]:
    """Europe PMC's abstract, else the stored Crossref one, else OpenAlex's."""
    for source, text in (("europepmc", clean_markup((core or {}).get("abstractText"))),
                         ("crossref", clean_markup(stored.get("crossref"))),
                         ("openalex", from_inverted_index(stored.get("openalex")))):
        if text:
            return text, source
    return None, None


def clean_markup(value: str | None) -> str | None:
    """HTML or JATS markup -> plain text. Inline tags join their text; other
    tags (headings, paragraphs) become a space."""
    if not value:
        return None
    return finish_cleaning(html.unescape(_TAG.sub(" ", _INLINE.sub("", value))))


def finish_cleaning(text: str) -> str | None:
    """The step after unescaping: markup that was escaped in the source, then
    placeholders. Safe on text that is already clean, unlike clean_markup."""
    text = " ".join(_ESCAPED_TAG.sub(lambda m: "" if _INLINE.fullmatch(m.group(0)) else " ",
                                     text).split())
    if text.lower().rstrip(".") in PLACEHOLDER_ABSTRACTS:
        return None
    return text or None


def from_inverted_index(index: dict[str, list[int]] | None) -> str | None:
    """OpenAlex's abstract_inverted_index -> the abstract's words in order."""
    if not index:
        return None
    words: dict[int, str] = {}
    for word, positions in index.items():
        for pos in positions:
            words[pos] = word
    return finish_cleaning(" ".join(words[i] for i in sorted(words)))


# --- coverage (Q2) ---------------------------------------------------------------


def coverage(conn: Connection, label: str) -> dict[str, Any]:
    """What text is held for a corpus and the works it cites (Q2)."""
    members = (select(corpus_paper.c.paper_id).where(
        corpus_paper.c.label == label, corpus_paper.c.paper_id.is_not(None))).subquery()
    refs = (select(reference.c.id, reference.c.resolution_status, reference.c.resolved_paper_id)
            .join(members, members.c.paper_id == reference.c.citing_paper_id)).subquery()
    cited = select(refs.c.resolved_paper_id.label("id")).where(
        refs.c.resolved_paper_id.is_not(None)).distinct().subquery()

    held = func.coalesce(text_availability.c.fulltext, "unchecked").label("held")

    def by_fulltext(ids) -> dict[str, int]:
        rows = conn.execute(
            select(held, func.count())
            .select_from(ids.outerjoin(text_availability,
                                       text_availability.c.paper_id == list(ids.c)[0]))
            .group_by(held)).all()
        return {k: n for k, n in rows}

    ref_rows = conn.execute(
        select(refs.c.resolution_status, held, func.count())
        .select_from(refs.outerjoin(text_availability,
                                    text_availability.c.paper_id == refs.c.resolved_paper_id))
        .group_by(refs.c.resolution_status, held)).all()
    references: dict[str, dict[str, int]] = {}
    for status, fulltext, n in ref_rows:
        references.setdefault(status, {})
        if status == "resolved":
            references[status][fulltext] = n
        else:
            references[status]["total"] = references[status].get("total", 0) + n

    licence = func.coalesce(text_availability.c.license, "unstated").label("licence")
    licences = conn.execute(
        select(licence, func.count())
        .select_from(cited.join(text_availability, text_availability.c.paper_id == cited.c.id))
        .where(text_availability.c.fulltext == "full")
        .group_by(licence).order_by(func.count().desc(), licence)).all()

    resolved = references.get("resolved", {})
    n_resolved = sum(resolved.values())
    n_refs = n_resolved + sum(v.get("total", 0) for k, v in references.items() if k != "resolved")
    full = resolved.get("full", 0)
    return {
        "corpus": label,
        "corpus_papers": by_fulltext(members),
        "cited_works": by_fulltext(cited),
        "references": {"total": n_refs, "resolved": n_resolved, "by_cited_text": resolved,
                       "unresolved_or_ambiguous": n_refs - n_resolved},
        "share_full_of_resolved_references": full / n_resolved if n_resolved else None,
        "share_full_of_all_references": full / n_refs if n_refs else None,
        "cited_full_text_licences": {k: n for k, n in licences},
    }


def render_coverage(c: dict[str, Any]) -> str:
    def pct(x: float | None) -> str:
        return "n/a" if x is None else f"{100 * x:.1f}%"

    def counts(d: dict[str, int]) -> str:
        return ", ".join(f"{k} {d.get(k, 0):,}" for k in ("full", "abstract", "none", "unchecked")
                         if d.get(k) or k != "unchecked")

    r = c["references"]
    lines = [
        f"Corpus {c['corpus']}",
        f"  corpus papers: {counts(c['corpus_papers'])}",
        f"  cited works ({sum(c['cited_works'].values()):,}): {counts(c['cited_works'])}",
        f"  references: {r['total']:,}; resolved {r['resolved']:,}; "
        f"cited text {counts(r['by_cited_text'])}",
        f"  cited source has full text (Q2): {pct(c['share_full_of_resolved_references'])} "
        f"of resolved references, {pct(c['share_full_of_all_references'])} of all references",
        "  licences of cited full text: " + (", ".join(
            f"{k} {n:,}" for k, n in c["cited_full_text_licences"].items()) or "none"),
    ]
    return "\n".join(lines)
