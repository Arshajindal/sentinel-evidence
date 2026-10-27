"""Identifier normalisation shared by ingestion and resolution (§9.5).

Returns None rather than guessing: a wrong identifier leads to a confident
wrong match downstream.
"""

from __future__ import annotations

import re
import unicodedata

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
    # A link or second value glued on ("10.1038/x,http://www.ncbi..."): keep the DOI.
    doi = re.split(r"\s|,\s*(?:https?:|www\.)", doi)[0]
    doi = doi.rstrip(".,;:")
    return doi if _DOI_RE.match(doi) else None


_TAG_RE = re.compile(r"<[^>]+>")
_NON_WORD_RE = re.compile(r"[\W_]+")
_INITIALS_RE = re.compile(r"^[A-Z]{1,4}$")


def _fold(text: str) -> str:
    """NFKD, drop combining marks, lowercase."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def normalize_title(value: str | None) -> str:
    """Title for exact and trigram matching (§9.5 stage 3).

    Markup removed, NFKD-folded, lowercased, punctuation replaced by spaces
    and whitespace collapsed. Words stay separated so trigrams stay meaningful.
    """
    if not value:
        return ""
    text = _fold(_TAG_RE.sub(" ", value))
    return " ".join(_NON_WORD_RE.sub(" ", text).split())


def normalize_surname(value: str | None) -> str | None:
    """Folded surname, for matching first authors. Blank is None."""
    if not value or not value.strip():
        return None
    return " ".join(_NON_WORD_RE.sub(" ", _fold(value)).split()) or None


def first_author_surname(author_string: str | None) -> str | None:
    """Surname of the first author in `Surname AB, Other CD, ...` form.

    A trailing all-caps token of up to four letters is taken as initials.
    """
    if not author_string:
        return None
    first = author_string.split(",")[0].strip().rstrip(".")
    tokens = first.split()
    if len(tokens) > 1 and _INITIALS_RE.match(tokens[-1]):
        tokens = tokens[:-1]
    return normalize_surname(" ".join(tokens))


def normalize_pmid(value: str | None) -> str | None:
    """A PMID is a positive integer. Blank and '0' (a common placeholder) are None."""
    if value is None:
        return None
    pmid = value.strip()
    if not pmid.isdigit():
        return None
    pmid = pmid.lstrip("0")
    return pmid or None
