"""Crossref lookup client (sentinel-project.md §5, §9.5).

Endpoints, confirmed live on 2026-10-25:
  GET {base}/works/{doi}                       one work; 404 if Crossref doesn't know it
  GET {base}/works?query.bibliographic=...     candidate search, ranked by score

`mailto` puts requests in the polite pool (10 rps, 3 concurrent, against 5
rps and 1 in the public pool). Crossref is the primary candidate source
(ADR-032).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Engine

from sentinel.ingest.base import LookupClient
from sentinel.ingest.http import RateLimitedHTTP
from sentinel.ingest.papers import StoredPaper, upsert_paper
from sentinel.log import get_logger
from sentinel.resolve.normalize import normalize_doi, normalize_surname, normalize_title
from sentinel.schemas import PaperRecord

log = get_logger(__name__)

# Author names go to paper.authors; ORCIDs and affiliations aren't kept.
DROPPED_KEYS = frozenset({"author", "editor", "translator", "chair"})


class CrossrefClient(LookupClient):
    source = "crossref"

    def __init__(self, http: RateLimitedHTTP, *, base_url: str, mailto: str) -> None:
        super().__init__(http)
        self.base_url = base_url.rstrip("/")
        self.mailto = mailto

    def work_by_doi(self, doi: str) -> dict | None:
        response = self.http.get(f"{self.base_url}/works/{doi}", params={"mailto": self.mailto})
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()["message"]

    def search(self, query: str, *, rows: int = 5) -> list[dict]:
        """Candidates for a free-text citation, best first."""
        response = self.http.get(f"{self.base_url}/works", params={
            "query.bibliographic": query, "rows": rows, "mailto": self.mailto,
        })
        response.raise_for_status()
        return response.json()["message"]["items"]

    def normalise(self, raw: dict) -> PaperRecord:
        authors = [
            {k: v for k, v in {
                "last_name": a.get("family"),
                "full_name": " ".join(p for p in (a.get("given"), a.get("family")) if p) or None,
                "collective_name": a.get("name"),
            }.items() if v}
            for a in raw.get("author") or []
        ]
        first = next((a for a in authors if a.get("last_name")), None)
        title = _first(raw.get("title"))
        return PaperRecord(
            doi=normalize_doi(raw.get("DOI")),
            title=title or "",
            title_norm=normalize_title(title),
            container=_first(raw.get("container-title")),
            year=_year(raw),
            first_author=normalize_surname(first["last_name"]) if first else None,
            authors=authors,
            source=self.source,
            license=_first([lic.get("URL") for lic in raw.get("license") or []]),
            raw={k: v for k, v in raw.items() if k not in DROPPED_KEYS},
        )

    def upsert(self, conn, record: PaperRecord) -> StoredPaper:
        return upsert_paper(conn, record)

    def lookup(self, engine: Engine, doi: str) -> StoredPaper | None:
        """Fetch one DOI and store it. None if Crossref doesn't know the DOI."""
        doi_norm = normalize_doi(doi)
        if doi_norm is None:
            raise ValueError(f"not a DOI: {doi!r}")
        raw = self.work_by_doi(doi_norm)
        if raw is None:
            log.info("crossref_not_found", doi=doi_norm)
            return None
        with engine.begin() as conn:
            stored = self.upsert(conn, self.normalise(raw))
        log.info("crossref_stored", doi=doi_norm, paper_id=stored.paper_id, action=stored.action)
        return stored

    def search_and_store(self, engine: Engine, query: str, *, rows: int = 5) -> list[StoredPaper]:
        """Search, store each usable candidate, and return them best first.

        Candidates without a title or DOI can't be matched later and are
        skipped (logged), not stored.
        """
        stored: list[StoredPaper] = []
        with engine.begin() as conn:
            for item in self.search(query, rows=rows):
                try:
                    record = self.normalise(item)
                except ValueError:
                    log.info("crossref_candidate_skipped", doi=item.get("DOI"))
                    continue
                if record.doi is None:
                    continue
                stored.append(self.upsert(conn, record))
        return stored


def _first(values: list[Any] | None) -> Any:
    return next((v for v in values or [] if v), None)


def _year(raw: dict) -> int | None:
    for key in ("issued", "published-print", "published-online", "created"):
        parts = (raw.get(key) or {}).get("date-parts") or [[]]
        if parts and parts[0] and parts[0][0]:
            return int(parts[0][0])
    return None
