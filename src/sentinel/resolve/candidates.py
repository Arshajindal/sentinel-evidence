"""Fetching candidate works from Crossref and OpenAlex (§9.5, ADR-032).

Every fetched work is stored in `paper` (first source owns the row), so a
later run finds it locally and no lookup is paid for twice. Crossref is the
search source; OpenAlex search is a fallback within its daily budget, and an
exhausted budget raises rather than returning nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx
from sqlalchemy import Engine, func, or_, select, text

from sentinel.ingest.crossref import CrossrefClient
from sentinel.ingest.doi_handle import DoiHandleClient
from sentinel.ingest.openalex import OpenAlexClient
from sentinel.models import paper
from sentinel.resolve.normalize import MAIN_TITLE_MIN_WORDS

# Near-misses down to this similarity are recorded as evidence. It never
# decides a match; the matching thresholds come from config.
NEAR_MISS_FLOOR = 0.6


@dataclass(frozen=True)
class Candidate:
    paper_id: int
    title_norm: str
    year: int | None
    first_author: str | None
    similarity: float
    title: str | None = None  # as stored, with its punctuation: a subtitle starts at the colon


@dataclass
class LookupLog:
    crossref_doi: int = 0
    openalex_doi: int = 0
    openalex_pmid: int = 0
    crossref_search: int = 0
    openalex_search: int = 0
    doi_handle: int = 0
    found: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    registered_untitled: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v}


class CandidateSource:
    def __init__(self, engine: Engine, crossref: CrossrefClient, openalex: OpenAlexClient,
                 doi_handle: DoiHandleClient | None = None) -> None:
        self.engine = engine
        self.crossref = crossref
        self.openalex = openalex
        self.doi_handle = doi_handle

    def doi_registered(self, doi: str, log: LookupLog) -> bool | None:
        """Is the DOI registered at any agency (doi.org)? None if not asked.

        §9.5: before C01 flags a reference whose DOI Crossref doesn't know,
        doi.org is asked; the DOI may be DataCite's or another agency's.
        """
        if log.registered_untitled:
            return True  # a source returned a record for it
        if self.doi_handle is None:
            return None
        log.doi_handle += 1
        return self.doi_handle.exists(doi)

    def title_norm_of(self, paper_id: int) -> str | None:
        with self.engine.connect() as conn:
            return conn.execute(select(paper.c.title_norm).where(paper.c.id == paper_id)).scalar_one_or_none()

    def paper_by(self, column: str, value: str) -> int | None:
        with self.engine.connect() as conn:
            return conn.execute(select(paper.c.id).where(paper.c[column] == value)).scalar_one_or_none()

    def fetch_doi(self, doi: str, log: LookupLog) -> int | None:
        """Crossref, then OpenAlex (free). The stored paper's ID, or None.

        A record a source has but can't be stored (no title) still proves the
        DOI is registered; that's recorded for C01, and the other source tried.
        """
        log.crossref_doi += 1
        stored = self.crossref.lookup(self.engine, doi)
        if stored is None or stored.paper_id is None:
            if stored is not None and stored.action == "unusable":
                log.registered_untitled.append(f"crossref:{doi}")
            log.openalex_doi += 1
            stored = self.openalex.lookup(self.engine, doi)
            if stored is not None and stored.action == "unusable":
                log.registered_untitled.append(f"openalex:{doi}")
        if stored is not None and stored.paper_id is not None:
            log.found.append(f"doi:{doi}")
            return stored.paper_id
        return None

    def fetch_pmid(self, pmid: str, log: LookupLog) -> int | None:
        log.openalex_pmid += 1
        stored = self.openalex.lookup_pmid(self.engine, pmid)
        if stored is not None and stored.action == "unusable":
            log.registered_untitled.append(f"openalex:pmid:{pmid}")
        if stored is not None and stored.paper_id is not None:
            log.found.append(f"pmid:{pmid}")
            return stored.paper_id
        return None

    def search_crossref(self, query: str, log: LookupLog) -> None:
        log.crossref_search += 1
        self._rejectable("crossref", log, self.crossref.search_and_store, self.engine, query, rows=5)

    def search_openalex(self, title: str, year: int | None, log: LookupLog) -> None:
        log.openalex_search += 1  # raises OpenAlexBudgetExhausted when spent
        self._rejectable("openalex", log, self.openalex.search_and_store, self.engine, title,
                         year=year, rows=5)

    @staticmethod
    def _rejectable(source: str, log: LookupLog, fn, *args, **kwargs) -> None:
        """A 4xx means the service won't accept this one query; that reference
        gets no candidates from it (recorded), and the run carries on. 5xx after
        retries still raises: that's an outage, not this reference."""
        try:
            fn(*args, **kwargs)
        except httpx.HTTPStatusError as exc:
            if not 400 <= exc.response.status_code < 500:
                raise
            log.rejected.append(f"{source}:{exc.response.status_code}")

    def local_title_candidates(self, title_norm: str, exclude_paper_id: int,
                               limit: int = 10, main_norm: str = "") -> list[Candidate]:
        """Works with a similar title, plus works whose title is this one with a
        subtitle added (`title_norm` + more) or removed (`main_norm`, the
        reference's title before its colon). A title and its extension can score
        below the trigram floor, so those are looked up directly."""
        with self.engine.begin() as conn:
            conn.execute(text("SELECT set_config('pg_trgm.similarity_threshold', :t, true)"),
                         {"t": str(NEAR_MISS_FLOOR)})
            sim = func.similarity(paper.c.title_norm, title_norm).label("sim")
            columns = (paper.c.id, paper.c.title_norm, paper.c.year, paper.c.first_author, sim,
                       paper.c.title)
            rows = conn.execute(
                select(*columns)
                .where(paper.c.title_norm.op("%")(title_norm), paper.c.id != exclude_paper_id)
                .order_by(sim.desc(), paper.c.id)
                .limit(limit)
            ).all()
            if len(title_norm.split()) >= MAIN_TITLE_MIN_WORDS:
                extended = [paper.c.title_norm.startswith(title_norm + " ", autoescape=True)]
                if main_norm:
                    extended.append(paper.c.title_norm == main_norm)
                rows += conn.execute(
                    select(*columns)
                    .where(or_(*extended), paper.c.id != exclude_paper_id)
                    .order_by(paper.c.id)
                    .limit(limit)
                ).all()
        unique = {r.id: r for r in rows}
        return [Candidate(r.id, r.title_norm, r.year, r.first_author, float(r.sim), r.title)
                for r in sorted(unique.values(), key=lambda r: (-r.sim, r.id))]
