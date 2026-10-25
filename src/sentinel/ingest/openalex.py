"""OpenAlex lookup client, metered (ADR-032).

Endpoints, confirmed live on 2026-10-25:
  GET {base}/works/doi:{doi}                     one work; free; 404 if unknown
  GET {base}/works?filter=title.search:...       candidate search; $0.001 each

OpenAlex charges against a daily dollar budget: about $0.10 without a key,
$1 with a free one. Every response reports its cost and what's left in
X-RateLimit-* headers. Searches refuse to start once either this client's
budget or the server's is spent, and raise rather than return nothing, so an
exhausted budget can never make a reference look unresolvable. The API key
travels in the Authorization header so it never appears in a logged URL.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import httpx
from sqlalchemy import Engine

from sentinel.ingest.base import LookupClient
from sentinel.ingest.http import RateLimitedHTTP
from sentinel.ingest.papers import StoredPaper, upsert_paper
from sentinel.log import get_logger
from sentinel.resolve.normalize import normalize_doi, normalize_pmid, normalize_surname, normalize_title
from sentinel.schemas import PaperRecord

log = get_logger(__name__)

SEARCH_COST_USD = 0.001
DROPPED_KEYS = frozenset({"authorships", "institutions", "corresponding_author_ids",
                          "corresponding_institution_ids"})
_PMCID_RE = re.compile(r"(PMC\d+)", re.IGNORECASE)


class OpenAlexBudgetExhausted(RuntimeError):
    """Today's OpenAlex budget is spent; searching must wait, not fail silently."""


@dataclass
class Budget:
    limit_usd: float
    spent_usd: float = 0.0
    server_remaining_usd: float | None = None

    def can_spend(self, cost: float) -> bool:
        if self.spent_usd + cost > self.limit_usd + 1e-9:
            return False
        return self.server_remaining_usd is None or self.server_remaining_usd + 1e-9 >= cost

    def record(self, response: httpx.Response) -> None:
        cost = _header_float(response, "X-RateLimit-Cost-USD")
        if cost:
            self.spent_usd += cost
        remaining = _header_float(response, "X-RateLimit-Remaining-USD")
        if remaining is not None:
            self.server_remaining_usd = remaining


class OpenAlexClient(LookupClient):
    source = "openalex"

    def __init__(self, http: RateLimitedHTTP, *, base_url: str, api_key: str = "",
                 daily_budget_usd: float) -> None:
        super().__init__(http)
        self.base_url = base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.budget = Budget(limit_usd=daily_budget_usd)

    def _get(self, url: str, params: dict | None = None) -> httpx.Response:
        response = self.http.get(url, params=params, headers=self.headers)
        self.budget.record(response)
        return response

    def work_by_doi(self, doi: str) -> dict | None:
        response = self._get(f"{self.base_url}/works/doi:{doi}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    def search_title(self, title: str, *, year: int | None = None, rows: int = 5) -> list[dict]:
        if not self.budget.can_spend(SEARCH_COST_USD):
            log.warning("openalex_budget_exhausted", spent_usd=round(self.budget.spent_usd, 4),
                        server_remaining_usd=self.budget.server_remaining_usd)
            raise OpenAlexBudgetExhausted(
                f"OpenAlex budget spent (this run ${self.budget.spent_usd:.3f} of "
                f"${self.budget.limit_usd:.2f}; server reports "
                f"{self.budget.server_remaining_usd} left)")
        # Commas and colons are filter syntax; strip them from the title.
        clean = " ".join(re.sub(r"[,:|]", " ", title).split())
        filters = [f"title.search:{clean}"]
        if year is not None:
            filters.append(f"publication_year:{year - 1}-{year + 1}")
        response = self._get(f"{self.base_url}/works",
                             params={"filter": ",".join(filters), "per-page": rows})
        response.raise_for_status()
        return response.json()["results"]

    def normalise(self, raw: dict) -> PaperRecord:
        authors = [
            {"full_name": (a.get("author") or {}).get("display_name")}
            for a in raw.get("authorships") or []
            if (a.get("author") or {}).get("display_name")
        ]
        title = raw.get("display_name") or raw.get("title")
        ids = raw.get("ids") or {}
        primary = raw.get("primary_location") or {}
        return PaperRecord(
            doi=normalize_doi(raw.get("doi")),
            pmid=normalize_pmid((ids.get("pmid") or "").rsplit("/", 1)[-1]),
            pmcid=_pmcid(ids.get("pmcid")),
            title=title or "",
            title_norm=normalize_title(title),
            container=(primary.get("source") or {}).get("display_name"),
            year=raw.get("publication_year"),
            # OpenAlex gives only display names. The last token is a guess at
            # the surname; a wrong guess fails stage-4 matching, never fakes one.
            first_author=normalize_surname(authors[0]["full_name"].split()[-1]) if authors else None,
            authors=authors,
            source=self.source,
            is_oa=(raw.get("open_access") or {}).get("is_oa"),
            license=primary.get("license"),
            raw={k: v for k, v in raw.items() if k not in DROPPED_KEYS},
        )

    def upsert(self, conn, record: PaperRecord) -> StoredPaper:
        return upsert_paper(conn, record)

    def lookup(self, engine: Engine, doi: str) -> StoredPaper | None:
        doi_norm = normalize_doi(doi)
        if doi_norm is None:
            raise ValueError(f"not a DOI: {doi!r}")
        raw = self.work_by_doi(doi_norm)
        if raw is None:
            log.info("openalex_not_found", doi=doi_norm)
            return None
        with engine.begin() as conn:
            stored = self.upsert(conn, self.normalise(raw))
        log.info("openalex_stored", doi=doi_norm, paper_id=stored.paper_id, action=stored.action)
        return stored

    def search_and_store(self, engine: Engine, title: str, *, year: int | None = None,
                         rows: int = 5) -> list[StoredPaper]:
        stored: list[StoredPaper] = []
        results = self.search_title(title, year=year, rows=rows)
        with engine.begin() as conn:
            for item in results:
                try:
                    record = self.normalise(item)
                except ValueError:
                    log.info("openalex_candidate_skipped", id=item.get("id"))
                    continue
                stored.append(self.upsert(conn, record))
        log.info("openalex_search", candidates=len(stored),
                 spent_usd=round(self.budget.spent_usd, 4))
        return stored


def _pmcid(value: str | None) -> str | None:
    match = _PMCID_RE.search(value or "")
    return match.group(1).upper() if match else None


def _header_float(response: httpx.Response, name: str) -> float | None:
    try:
        return float(response.headers[name])
    except (KeyError, ValueError):
        return None
