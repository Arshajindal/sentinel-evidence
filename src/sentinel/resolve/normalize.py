"""Identifier normalisation shared by ingestion and resolution (§9.5).

Returns None rather than guessing: a wrong identifier leads to a confident
wrong match downstream.
"""

from __future__ import annotations

import re

_DOI_PREFIXES = ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/",
                 "http://dx.doi.org/", "doi:")
_DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$")


def normalize_doi(value: str | None) -> str | None:
    """Lowercase, strip resolver prefixes and trailing punctuation.

    Anything that isn't then a bare `10.NNNN/suffix` DOI returns None.
    """
    if value is None:
        return None
    doi = value.strip().lower()
    for prefix in _DOI_PREFIXES:
        if doi.startswith(prefix):
            doi = doi[len(prefix):]
            break
    doi = doi.rstrip(".,;:")
    return doi if _DOI_RE.match(doi) else None


def normalize_pmid(value: str | None) -> str | None:
    """A PMID is a positive integer. Blank and '0' (a common placeholder) are None."""
    if value is None:
        return None
    pmid = value.strip()
    if not pmid.isdigit():
        return None
    pmid = pmid.lstrip("0")
    return pmid or None
