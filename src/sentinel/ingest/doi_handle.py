"""doi.org handle API: does a DOI exist at any registration agency? (§9.5)

GET {base}/{doi}, confirmed live on 2026-10-25: 200 with responseCode 1 when
the DOI is registered (Crossref, DataCite, ...), 404 with responseCode 100
when it isn't. Asked before C01 flags a reference whose DOI Crossref doesn't
know. It returns no metadata, so nothing is stored.
"""

from __future__ import annotations

from sentinel.ingest.http import RateLimitedHTTP
from sentinel.log import get_logger
from sentinel.resolve.normalize import normalize_doi

log = get_logger(__name__)


class DoiHandleClient:
    source = "doi_handle"

    def __init__(self, http: RateLimitedHTTP, *, base_url: str) -> None:
        self.http = http
        self.base_url = base_url.rstrip("/")

    def exists(self, doi: str) -> bool:
        doi_norm = normalize_doi(doi)
        if doi_norm is None:
            raise ValueError(f"not a DOI: {doi!r}")
        response = self.http.get(f"{self.base_url}/{doi_norm}")
        if response.status_code == 404:
            return False
        response.raise_for_status()
        found = response.json().get("responseCode") == 1
        log.info("doi_handle_checked", doi=doi_norm, exists=found)
        return found
