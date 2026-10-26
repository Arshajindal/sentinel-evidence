"""C01 — reference existence (sentinel-project.md §9.5, §9.7; ADR-035).

Resolution has already run; C01 classifies each reference from what it
recorded. Only one class is flagged:

  resolved, ambiguous      the work exists (ambiguous: more than one record does)
  doi_registered           not in Crossref/OpenAlex, but doi.org knows the DOI
  nonjournal               book, chapter, website, guideline, report, thesis,
                           conference item, preprint, organisation author...
  near_match               a candidate looks like this work under a variant
                           title (same first author and year, or a near-identical
                           title in the same year)
  unparseable              not enough to attempt or judge resolution -> insufficient_data
  journal_like             a plausible journal article that matched nothing -> flagged
  pending                  not resolved yet

An unmatched reference "could not be verified". It is never called anything
stronger: it may be real and simply not indexed.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Literal

from sentinel.checks.base import Check
from sentinel.checks.context import PaperContext, ReferenceContext
from sentinel.schemas import Finding

RefClass = Literal["resolved", "ambiguous", "doi_registered", "nonjournal", "near_match",
                   "unparseable", "journal_like", "pending"]

JOURNAL_TYPES = frozenset({"journal", "article", "journal-article"})
# Signs a reference isn't a journal article. They can only stop a flag.
_NONJOURNAL = re.compile(
    r"(?i)(?:\bin:\s|\beds?\.|\beditors?\b|\bedition\b|\(ed\.?\s*\d|\b\d+(?:st|nd|rd|th)\s+ed\b"
    r"|\bpress\b|\bpublish(?:er|ers|ing)\b|\bverlag\b|\bisbn\b|\bthesis\b|\bdissertation\b"
    r"|\bph\.?d\b|\breport\b|\bguideline|\bguidance\b|\bmanual\b|\bhandbook\b|\btextbook\b"
    r"|fact\s*sheet|\bstatistics\b|package insert|prescribing information|data on file"
    r"|press release|\bweb\s?site\b|\bdashboard\b|\bsoftware\b|\bversion\s+\d|\bpreprint\b"
    r"|biorxiv|medrxiv|\barxiv\b|\bssrn\b|research square|\bconference\b|\bcongress\b"
    r"|\bsymposium\b|\bmeeting\b|\babstracts?\b|\bposter\b|presented at|\bproceedings\b"
    r"|personal communication|\bunpublished\b|clinicaltrials\.gov|\bNCT\d{8}\b|\[internet\]"
    r"|available (?:from|at|online)|\baccessed\b|retrieved from|https?://|\bwww\.)")
_ORG_WORDS = frozenset({
    "organization", "organisation", "association", "society", "group", "institute",
    "department", "ministry", "council", "committee", "foundation", "agency", "centre",
    "center", "office", "board", "team", "university", "consortium", "collaboration",
    "network", "government", "commission", "authority", "programme", "program", "unaids",
    "who", "fda", "ema", "cdc", "nice", "collaborators",
})
MIN_TITLE_WORDS = 3


def classify(ref: ReferenceContext, *, near_match_min: float, same_title_min: float,
             year_tolerance: int) -> tuple[RefClass, str]:
    """A reference's class and the reason, from stored fields only."""
    if ref.resolution_status == "resolved":
        return "resolved", f"matched by {ref.resolution_method}"
    if ref.resolution_status == "ambiguous":
        return "ambiguous", "more than one record matches"
    if ref.resolution_status == "pending":
        return "pending", "not resolved yet"

    evidence = ref.resolution_evidence or {}
    stages = evidence.get("stages") or []
    lookups = evidence.get("lookups") or {}
    if any(s.get("doi_org_registered") is True for s in stages) or lookups.get("registered_untitled"):
        return "doi_registered", "DOI is registered (doi.org), though not indexed in Crossref/OpenAlex"

    if ref.publication_type and ref.publication_type.lower() not in JOURNAL_TYPES:
        return "nonjournal", f"publication type '{ref.publication_type}'"
    text = ref.raw_string or ""
    if ref.parsed_container:
        text = text.replace(ref.parsed_container, " ")  # a journal's own name can't count
    cue = _NONJOURNAL.search(text)
    if cue:
        return "nonjournal", f"looks like a non-journal item ('{cue.group(0).strip()}')"
    if ref.parsed_first_author and set(ref.parsed_first_author.split()) & _ORG_WORDS:
        return "nonjournal", "organisation author"

    for m in evidence.get("near_misses") or []:
        sim, year, author = m.get("similarity") or 0, m.get("year"), m.get("first_author")
        same_year = (ref.parsed_year is not None and year is not None
                     and abs(ref.parsed_year - year) <= year_tolerance)
        same_author = bool(set((ref.parsed_first_author or "").split())
                           & set((author or "").split()))
        if same_year and (sim >= same_title_min or (same_author and sim >= near_match_min)):
            return "near_match", (f"candidate {m.get('paper_id')} ({author}, {year}, "
                                  f"similarity {sim}) is probably this work")

    title_words = len((ref.parsed_title or "").split())
    if (title_words < MIN_TITLE_WORDS or not ref.parsed_first_author
            or ref.parsed_year is None or not ref.parsed_container):
        missing = [name for name, ok in (
            ("title", title_words >= MIN_TITLE_WORDS), ("first author", ref.parsed_first_author),
            ("year", ref.parsed_year is not None), ("journal", ref.parsed_container)) if not ok]
        return "unparseable", "not enough parsed to judge: no " + ", ".join(missing)

    return "journal_like", "a journal article by its fields, with no matching record"


class C01ReferenceExistence(Check):
    check_id = "C01"
    check_ver = "1.0.0"
    subject = "paper"

    def __init__(self, *, near_match_min: float, same_title_min: float, year_tolerance: int) -> None:
        self.near_match_min = near_match_min
        self.same_title_min = same_title_min
        self.year_tolerance = year_tolerance

    def run(self, ctx: PaperContext) -> list[Finding]:
        classified = [(ref, *classify(ref, near_match_min=self.near_match_min,
                                      same_title_min=self.same_title_min,
                                      year_tolerance=self.year_tolerance))
                      for ref in ctx.references]
        findings = [self._flag(ctx.paper_id, ref, reason)
                    for ref, cls, reason in classified if cls == "journal_like"]
        findings.append(self._summary(ctx.paper_id, classified))
        return findings

    def _flag(self, paper_id: int, ref: ReferenceContext, reason: str) -> Finding:
        evidence = ref.resolution_evidence or {}
        label = f"Reference {ref.ordinal}" if ref.ordinal is not None else "A reference"
        doi_note = ""
        if ref.parsed_doi:
            doi_note = "; its DOI is not registered at doi.org"
        return Finding(
            paper_id=paper_id, check_id=self.check_id, check_ver=self.check_ver,
            severity="medium", status="flagged",
            summary=(f"{label} could not be verified: no matching record in Crossref "
                     f"or OpenAlex{doi_note}."),
            evidence={
                "reference_id": ref.reference_id,
                "ordinal": ref.ordinal,
                "raw_string": ref.raw_string,
                "parsed": {"doi": ref.parsed_doi, "pmid": ref.parsed_pmid,
                           "title": ref.parsed_title, "first_author": ref.parsed_first_author,
                           "year": ref.parsed_year, "container": ref.parsed_container},
                "class": "journal_like",
                "reason": reason,
                "stages": evidence.get("stages"),
                "near_misses": evidence.get("near_misses"),
                "lookups": evidence.get("lookups"),
                "parser": evidence.get("parser"),
            },
        )

    def _summary(self, paper_id: int, classified: list[tuple[ReferenceContext, str, str]]) -> Finding:
        counts = Counter(cls for _, cls, _ in classified)
        total = len(classified)
        by_class: dict[str, list[dict[str, Any]]] = {}
        for ref, cls, reason in classified:
            if cls != "resolved":
                by_class.setdefault(cls, []).append(
                    {"reference_id": ref.reference_id, "ordinal": ref.ordinal, "reason": reason})
        evidence = {"references": total, "counts": dict(counts), "not_resolved": by_class}

        if total == 0:
            status, summary = "insufficient_data", "No reference list available."
        elif counts["pending"]:
            status = "insufficient_data"
            summary = f"{counts['pending']} of {total} references not resolved yet."
        elif counts["unparseable"] == total:
            status, summary = "insufficient_data", "No reference could be parsed well enough to check."
        else:
            status = "clean"
            parts = [f"{counts['resolved']} resolved"]
            for cls, words in (("ambiguous", "matched more than one record"),
                               ("doi_registered", "registered DOI outside Crossref/OpenAlex"),
                               ("near_match", "probable match under a variant title"),
                               ("nonjournal", "non-journal"),
                               ("unparseable", "too incomplete to check"),
                               ("journal_like", "could not be verified (flagged separately)")):
                if counts[cls]:
                    parts.append(f"{counts[cls]} {words}")
            summary = f"{total} references checked: " + "; ".join(parts) + "."
        return Finding(paper_id=paper_id, check_id=self.check_id, check_ver=self.check_ver,
                       severity="info", status=status, summary=summary, evidence=evidence)
