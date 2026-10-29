"""Europe PMC: corpus selection and the corpus harvester (§9.2, §9.3).

Endpoints, confirmed live on 2026-10-18:
  {base}/search                      query; resultType idlist|core; cursorMark paging
  {base}/{pmcid}/fullTextXML         JATS full text; only the <ref-list> is read
  {annotations}/annotationsByArticleIds   text-mined accession numbers, 8 IDs per call

Reference lists come from the full text, not {base}/{source}/{id}/references:
that endpoint returns only references Europe PMC has matched, so unmatched
ones (the cases C01 exists for) never arrive (ADR-030).

Trial links (ADR-028): an NCT number is linked only when it is mentioned in
the Title, Abstract or Methods, where a paper names its own trial. Mentions
elsewhere usually cite someone else's trial.
"""

from __future__ import annotations

import random
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Connection, Engine, func, insert as core_insert, literal_column, select, update
from sqlalchemy.dialects.postgresql import insert

from sentinel.ingest.base import Harvester, UpsertCounts
from sentinel.ingest.clinicaltrials import ClinicalTrialsClient, NCT_RE
from sentinel.ingest.http import RateLimitedHTTP, RetryableHTTPError
from sentinel.ingest.jats import parse_ref_list
from sentinel.log import get_logger
from sentinel.models import corpus, corpus_paper, paper, paper_trial_link, reference, trial
from sentinel.resolve.normalize import (
    normalize_doi,
    normalize_pmid,
    normalize_surname,
    normalize_title,
)
from sentinel.schemas import CorpusPaperRecord, PaperRecord, ReferenceRecord

log = get_logger(__name__)

OWN_TRIAL_SECTIONS = frozenset({"Title", "Abstract", "Methods"})
CORE_BATCH = 25
ANNOTATION_BATCH = 8
PAGE_SIZE = 1000

# Not needed downstream; kept out of paper.raw to store only what's needed.
# Author names go to paper.authors; affiliations and author IDs are dropped.
DROPPED_CORE_KEYS = frozenset({"authorList", "authorIdList", "affiliation"})


class EuropePMC:
    """Thin client over the Europe PMC REST and Annotations APIs."""

    def __init__(self, http: RateLimitedHTTP, *, base_url: str, annotations_url: str) -> None:
        self.http = http
        self.base_url = base_url.rstrip("/")
        self.annotations_url = annotations_url.rstrip("/")

    def _json(self, url: str, params: dict[str, Any]) -> dict | list:
        response = self.http.get(url, params=params)
        response.raise_for_status()
        return response.json()

    def walk_ids(self, query: str) -> tuple[int, list[dict]]:
        """Every hit for `query` as {id, source, pmid, pmcid}, and the hit count."""
        cursor, results, hit_count = "*", [], 0
        while True:
            page = self._json(f"{self.base_url}/search", {
                "query": query, "format": "json", "resultType": "idlist",
                "pageSize": PAGE_SIZE, "cursorMark": cursor,
            })
            hit_count = page["hitCount"]
            batch = page["resultList"]["result"]
            results.extend(batch)
            next_cursor = page.get("nextCursorMark")
            if not batch or not next_cursor or next_cursor == cursor:
                return hit_count, results
            cursor = next_cursor

    def core_by_pmcid(self, pmcids: list[str]) -> dict[str, dict]:
        query = " OR ".join(f"PMCID:{p}" for p in pmcids)
        page = self._json(f"{self.base_url}/search", {
            "query": query, "format": "json", "resultType": "core",
            "pageSize": len(pmcids),
        })
        return {r["pmcid"]: r for r in page["resultList"]["result"] if r.get("pmcid")}

    def fulltext_xml(self, pmcid: str) -> str | None:
        """JATS XML for an open-access paper, or None if Europe PMC has none."""
        response = self.http.get(f"{self.base_url}/{pmcid}/fullTextXML")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.text

    def accession_annotations(self, pmcids: list[str]) -> dict[str, list[dict]]:
        articles = self._json(f"{self.annotations_url}/annotationsByArticleIds", {
            "articleIds": ",".join(f"PMC:{p}" for p in pmcids),
            "type": "Accession Numbers", "format": "JSON",
        })
        return {a.get("pmcid"): a.get("annotations") or [] for a in articles}


# --- corpus selection -------------------------------------------------------------


@dataclass
class Selection:
    label: str
    hit_count: int
    walked: int
    n: int


def select_corpus(
    engine: Engine, epmc: EuropePMC, *, query: str, seed: int, n: int, label: str
) -> Selection:
    """Sample `n` hits of `query` with `seed` and record them as corpus `label`.

    Hits are sorted by PMCID before sampling, so the result doesn't depend on
    the API's ordering. The hit set itself changes as Europe PMC indexes new
    papers, which is why the date is recorded with it.
    """
    with engine.connect() as conn:
        if conn.execute(select(corpus.c.label).where(corpus.c.label == label)).first():
            raise ValueError(f"corpus {label!r} already exists")

    hit_count, hits = epmc.walk_ids(query)
    by_pmcid = {h["pmcid"]: h for h in hits if h.get("pmcid")}
    chosen = sample_pmcids(list(by_pmcid), seed=seed, n=n)

    with engine.begin() as conn:
        conn.execute(core_insert(corpus).values(
            label=label, source="europepmc", query=query, seed=seed,
            hit_count=hit_count, n=n,
        ))
        conn.execute(core_insert(corpus_paper), [
            {"label": label, "ordinal": i, "source": by_pmcid[p]["source"],
             "source_id": by_pmcid[p]["id"], "pmcid": p}
            for i, p in enumerate(chosen, start=1)
        ])
    log.info("corpus_selected", label=label, hit_count=hit_count, walked=len(hits),
             unique_pmcids=len(by_pmcid), n=n, seed=seed)
    return Selection(label=label, hit_count=hit_count, walked=len(hits), n=n)


def sample_pmcids(pmcids: list[str], *, seed: int, n: int) -> list[str]:
    """`n` PMCIDs chosen with `seed`, independent of input order."""
    pool = sorted(set(pmcids), key=lambda p: int(p.removeprefix("PMC")))
    if len(pool) < n:
        raise ValueError(f"query has {len(pool)} usable hits, fewer than n={n}")
    return random.Random(seed).sample(pool, n)


# --- harvester ------------------------------------------------------------------------


class EuropePMCHarvester(Harvester):
    """Harvests a selected corpus: metadata, reference lists and trial links.

    The cursor is `<label>:<last ordinal done>`. Trials named by a batch are
    fetched from ClinicalTrials.gov before the batch is written, because
    paper_trial_link references trial.
    """

    def __init__(
        self,
        engine: Engine,
        epmc: EuropePMC,
        trials: ClinicalTrialsClient,
        *,
        label: str,
        max_consecutive_failures: int = 5,
        members: list[dict] | None = None,
    ) -> None:
        self.source = f"europepmc:{label}"
        self.epmc = epmc
        self.trials = trials
        self.label = label
        self.max_consecutive_failures = max_consecutive_failures
        self._consecutive_failures = 0
        if members is None:
            with engine.connect() as conn:
                members = conn.execute(
                    select(corpus_paper.c.ordinal, corpus_paper.c.source,
                           corpus_paper.c.source_id, corpus_paper.c.pmcid)
                    .where(corpus_paper.c.label == label)
                    .order_by(corpus_paper.c.ordinal)
                ).mappings().all()
        self.members = members
        if not self.members:
            raise ValueError(f"corpus {label!r} has no members; run `sentinel corpus select`")
        self.not_found: set[str] = set()
        self.no_fulltext: set[str] = set()
        self.fulltext_failed: set[str] = set()

    def fetch(self, cursor: str | None) -> Iterator[tuple[dict, str | None]]:
        done = 0
        if cursor:
            cursor_label, _, last = cursor.rpartition(":")
            if cursor_label == self.label and last.isdigit():
                done = int(last)
        todo = [m for m in self.members if m["ordinal"] > done]
        for i in range(0, len(todo), CORE_BATCH):
            chunk = todo[i:i + CORE_BATCH]
            pmcids = [m["pmcid"] for m in chunk]
            cores = self.epmc.core_by_pmcid(pmcids)
            annotations: dict[str, list[dict]] = {}
            for j in range(0, len(pmcids), ANNOTATION_BATCH):
                annotations.update(self.epmc.accession_annotations(pmcids[j:j + ANNOTATION_BATCH]))
            for m in chunk:
                yield {
                    "member": dict(m),
                    "core": cores.get(m["pmcid"]),
                    "fulltext_xml": self._fulltext(m["pmcid"]),
                    "annotations": annotations.get(m["pmcid"], []),
                }, f"{self.label}:{m['ordinal']}"

    def _fulltext(self, pmcid: str) -> str | None:
        """One paper's persistent 5xx (a broken record upstream) shouldn't stop
        the corpus: it's stored without references and a re-run can fill them.
        Several failures in a row look like an outage, so the run stops."""
        try:
            xml = self.epmc.fulltext_xml(pmcid)
        except RetryableHTTPError as exc:
            self._consecutive_failures += 1
            self.fulltext_failed.add(pmcid)
            log.error("fulltext_failed", pmcid=pmcid, status=exc.response.status_code,
                      consecutive=self._consecutive_failures)
            if self._consecutive_failures >= self.max_consecutive_failures:
                raise
            return None
        self._consecutive_failures = 0
        return xml

    def record_key(self, raw: dict) -> str | None:
        return (raw.get("member") or {}).get("pmcid")

    def normalise(self, raw: dict) -> CorpusPaperRecord:
        pmcid = raw["member"]["pmcid"]
        if raw.get("fulltext_xml") is None and pmcid not in self.fulltext_failed:
            # Stored without references rather than dropped; reported at the end.
            self.no_fulltext.add(pmcid)
            log.warning("fulltext_unavailable", pmcid=pmcid)
        return corpus_paper_record(raw, self.label)

    def before_upsert(self, engine: Engine, records: list[CorpusPaperRecord]) -> None:
        wanted = {n for r in records for n in r.nct_ids} - self.not_found
        if not wanted:
            return
        with engine.connect() as conn:
            have = set(conn.execute(select(trial.c.nct_id).where(trial.c.nct_id.in_(wanted))).scalars())
        for nct_id in sorted(wanted - have):
            if self.trials.lookup(engine, nct_id) is None:
                self.not_found.add(nct_id)

    def upsert(self, conn: Connection, records: list[CorpusPaperRecord]) -> UpsertCounts:
        counts = UpsertCounts()
        all_ncts = {n for r in records for n in r.nct_ids}
        known = set(conn.execute(
            select(trial.c.nct_id).where(trial.c.nct_id.in_(all_ncts))
        ).scalars()) if all_ncts else set()

        for record in records:
            values = record.paper.model_dump()
            stmt = insert(paper).values(**values)
            paper_id, inserted = conn.execute(
                stmt.on_conflict_do_update(
                    index_elements=[paper.c.pmcid],
                    set_={**{c: stmt.excluded[c] for c in values if c != "pmcid"},
                          "updated_at": stmt.excluded.ingested_at},
                ).returning(paper.c.id, _inserted())
            ).one()
            if inserted:
                counts.inserted += 1
            else:
                counts.updated += 1

            conn.execute(update(corpus_paper).where(
                corpus_paper.c.label == record.label, corpus_paper.c.ordinal == record.ordinal,
            ).values(paper_id=paper_id))

            if record.references:
                ref_stmt = insert(reference).values(
                    [{"citing_paper_id": paper_id, **r.model_dump()} for r in record.references]
                )
                # The text and the source entry refresh. A parsed field the XML
                # supplies replaces the stored one; one it doesn't supply keeps
                # what the reference parser filled in (re-harvesting must not
                # wipe it). Resolution results are left alone.
                parsed = [c for c in ReferenceRecord.model_fields if c.startswith("parsed_")]
                conn.execute(ref_stmt.on_conflict_do_update(
                    index_elements=[reference.c.citing_paper_id, reference.c.ordinal],
                    set_={"raw_string": ref_stmt.excluded.raw_string, "raw": ref_stmt.excluded.raw,
                          **{c: func.coalesce(ref_stmt.excluded[c], reference.c[c]) for c in parsed}},
                ))

            links = [n for n in record.nct_ids if n in known]
            if links:
                conn.execute(insert(paper_trial_link).values([
                    {"paper_id": paper_id, "nct_id": n, "link_source": "europepmc",
                     "confidence": 1.0} for n in links
                ]).on_conflict_do_nothing())
            missing = [n for n in record.nct_ids if n not in known]
            if missing:
                log.info("trial_link_skipped", pmcid=record.paper.pmcid, nct_ids=missing,
                         reason="not_in_registry")
        return counts


def corpus_paper_record(raw: dict, label: str) -> CorpusPaperRecord:
    """Raw harvest bundle -> validated record. Pure."""
    member = raw["member"]
    core = raw.get("core")
    if not core:
        raise ValueError(f"no metadata returned for {member['pmcid']}")
    xml = raw.get("fulltext_xml")
    return CorpusPaperRecord(
        label=label,
        ordinal=member["ordinal"],
        paper=_paper(core),
        references=parse_ref_list(xml) if xml is not None else [],
        nct_ids=own_trial_ids(raw.get("annotations") or []),
    )


def own_trial_ids(annotations: list[dict]) -> list[str]:
    """NCT numbers mentioned in the Title, Abstract or Methods (ADR-028)."""
    found: set[str] = set()
    for a in annotations:
        nct_id = (a.get("exact") or "").strip().upper()
        section = (a.get("section") or "").split(" (")[0].strip()
        if NCT_RE.match(nct_id) and section in OWN_TRIAL_SECTIONS:
            found.add(nct_id)
    return sorted(found)


def _inserted():
    return literal_column("(xmax = 0)").label("inserted")


def _paper(core: dict) -> PaperRecord:
    authors = [
        {k: v for k, v in {
            "full_name": a.get("fullName"),
            "last_name": a.get("lastName"),
            "initials": a.get("initials"),
            "collective_name": a.get("collectiveName"),
        }.items() if v}
        for a in (core.get("authorList") or {}).get("author") or []
    ]
    first = next((a for a in authors if a.get("last_name")), None)
    year = core.get("pubYear")
    journal = ((core.get("journalInfo") or {}).get("journal") or {})
    return PaperRecord(
        doi=normalize_doi(core.get("doi")),
        pmid=normalize_pmid(core.get("pmid")),
        pmcid=core.get("pmcid"),
        title=(core.get("title") or "").strip(),
        title_norm=normalize_title(core.get("title")),
        container=journal.get("title") or journal.get("medlineAbbreviation"),
        year=int(year) if year and str(year).isdigit() else None,
        first_author=normalize_surname(first["last_name"]) if first else None,
        authors=authors,
        source="europepmc",
        is_oa=core.get("isOpenAccess") == "Y",
        license=core.get("license"),
        fulltext_status="abstract" if core.get("abstractText") else "none",
        raw={k: v for k, v in core.items() if k not in DROPPED_CORE_KEYS},
    )


