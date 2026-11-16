"""Metadata features for the tabular baseline (§10.1 item 8, S2.9, ADR-057).

One row per citation that C09 will judge: a citation context (the current
context extractor) and one reference it cites. A context citing "[3-5]" gives
three rows.

The features are metadata only. Nothing reads the words of the citing
sentence or the cited work, and nothing comes from a model or a check's
verdict, so the baseline measures how far "who cites what, where and when"
gets without reading anything:
- the citing paper: year, how many corpus papers share its venue, how many
  references it has, and its trial's sponsor class;
- the cited work: year, how many of the corpus's cited references share its
  venue, whether it was matched, what text is held for it, and its most
  serious notice
  (retraction, expression of concern, correction);
- the pair: the year gap, how many authors the two share (self-citation),
  the section the citation sits in, and how many references the same
  citation cites.

Venues are counts, not categories: with a few hundred labels, a category per
journal would be noise. Every category has a fixed vocabulary plus "unknown",
so the encoding never depends on which rows are labelled. Pure apart from
`build_features`, which only reads.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Sequence
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Connection, func, select

from sentinel.fulltext.contexts import EXTRACTOR_VERSION as CONTEXT_VERSION
from sentinel.models import (
    citation_context,
    citation_context_reference,
    corpus_paper,
    paper,
    paper_trial_link,
    reference,
    retraction_notice,
    text_availability,
    trial,
)

# Bump on any change to what a feature means or how it is encoded.
FEATURES_VER = "1.0.0"

SECTIONS = ("introduction", "methods", "results", "discussion", "other")
RESOLUTIONS = ("resolved", "unresolved", "ambiguous")
TEXT_HELD = ("full", "abstract", "none")
# Most serious first: a work with several notices takes the first that applies.
NOTICES = ("retraction", "expression_of_concern", "correction", "none")
# ClinicalTrials.gov's lead sponsor classes, plus "none" (no linked trial)
# and "multiple" (linked trials disagree).
SPONSORS = ("INDUSTRY", "NIH", "FED", "OTHER_GOV", "NETWORK", "INDIV", "OTHER", "AMBIG", "UNKNOWN",
            "none", "multiple")
CATEGORIES: dict[str, tuple[str, ...]] = {
    "section": SECTIONS, "cited_resolution": RESOLUTIONS, "cited_text": TEXT_HELD,
    "cited_notice": NOTICES, "citing_sponsor": SPONSORS,
}
NUMERIC = ("citing_year", "citing_venue_papers", "citing_references", "cited_year", "year_gap",
           "cited_venue_citations", "shared_authors", "co_cited")


class CitationFeatures(BaseModel):
    """One citation's metadata. None means unknown, never zero."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    citation_context_id: int
    reference_id: int
    citing_paper_id: int
    cited_paper_id: int | None
    citing_year: int | None = Field(default=None, ge=1800, le=2100)
    citing_venue_papers: int | None = Field(default=None, ge=1)
    citing_references: int = Field(ge=1)
    citing_sponsor: Literal[SPONSORS]  # type: ignore[valid-type]
    cited_year: int | None = Field(default=None, ge=1500, le=2100)
    year_gap: int | None = None
    cited_venue_citations: int | None = Field(default=None, ge=1)
    cited_resolution: Literal[RESOLUTIONS]  # type: ignore[valid-type]
    cited_text: Literal[TEXT_HELD] | None  # type: ignore[valid-type]
    cited_notice: Literal[NOTICES]  # type: ignore[valid-type]
    shared_authors: int | None = Field(default=None, ge=0)
    section: Literal[SECTIONS] | None  # type: ignore[valid-type]
    co_cited: int = Field(ge=1)


# --- authors ----------------------------------------------------------------------

def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    return re.sub(r"[^a-z]", "", "".join(c for c in text if not unicodedata.combining(c)).lower())


def author_keys(authors: Iterable[dict] | None) -> set[tuple[str, str]]:
    """(surname, first initial) for each author, from any source's shape:
    Europe PMC gives last_name and initials ("Chan", "JM"), Crossref
    last_name and a full name that starts with the given name, OpenAlex only
    a full name. Accents, case, spaces and hyphens are ignored."""
    keys = set()
    for a in authors or ():
        full = (a.get("full_name") or "").strip()
        last = (a.get("last_name") or "").strip()
        initials = (a.get("initials") or "").strip()
        if not last and full:
            last = full.split()[-1]
        if initials:
            first = initials[0]
        elif full and last and full.lower().startswith(last.lower()):
            rest = full[len(last):].strip()  # "Chan JM"
            first = rest[:1]
        else:
            first = full[:1]
        key = (_fold(last), _fold(first)[:1])
        if key[0]:
            keys.add(key)
    return keys


def shared_authors(a: Iterable[dict] | None, b: Iterable[dict] | None) -> int | None:
    """How many authors two lists share; None when either is empty. A count,
    not a flag: one shared "Lee J" may be two people, three shared names
    rarely are."""
    ka, kb = author_keys(a), author_keys(b)
    if not ka or not kb:
        return None
    return len(ka & kb)


def _venue(text: str | None) -> str | None:
    key = re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()
    return key or None


# --- building the table --------------------------------------------------------------

def build_features(conn: Connection, *, label: str) -> list[CitationFeatures]:
    """Every citation of a corpus's papers, from the current contexts."""
    in_corpus = select(corpus_paper.c.paper_id).where(corpus_paper.c.label == label)
    cited = paper.alias("cited")
    pairs = conn.execute(
        select(citation_context.c.id.label("ctx"), citation_context.c.section,
               citation_context.c.citing_paper_id.label("citing"),
               reference.c.id.label("ref"), reference.c.resolution_status, reference.c.parsed_year,
               reference.c.parsed_container, reference.c.parsed_doi, reference.c.parsed_pmid,
               reference.c.resolved_paper_id.label("cited"), cited.c.year.label("cited_year"),
               cited.c.container.label("cited_container"), cited.c.authors.label("cited_authors"),
               cited.c.doi.label("cited_doi"), cited.c.pmid.label("cited_pmid"))
        .join(citation_context_reference,
              citation_context_reference.c.citation_context_id == citation_context.c.id)
        .join(reference, reference.c.id == citation_context_reference.c.reference_id)
        .outerjoin(cited, cited.c.id == reference.c.resolved_paper_id)
        .where(citation_context.c.extractor_ver == CONTEXT_VERSION,
               citation_context.c.citing_paper_id.in_(in_corpus))
        .order_by(citation_context.c.id, reference.c.id)).all()
    if not pairs:
        return []
    citing_ids = sorted({p.citing for p in pairs})
    citing = {r.id: r for r in conn.execute(
        select(paper.c.id, paper.c.year, paper.c.container, paper.c.authors)
        .where(paper.c.id.in_(citing_ids)))}
    n_refs = dict(conn.execute(select(reference.c.citing_paper_id, func.count())
                               .where(reference.c.citing_paper_id.in_(citing_ids))
                               .group_by(reference.c.citing_paper_id)).all())
    sponsors: dict[int, set[str]] = {}
    for pid, cls in conn.execute(select(paper_trial_link.c.paper_id, trial.c.sponsor_class)
                                 .join(trial, trial.c.nct_id == paper_trial_link.c.nct_id)
                                 .where(paper_trial_link.c.paper_id.in_(citing_ids))):
        sponsors.setdefault(pid, set()).add(cls or "UNKNOWN")
    cited_ids = sorted({p.cited for p in pairs if p.cited})
    held = dict(conn.execute(select(text_availability.c.paper_id, text_availability.c.fulltext)
                             .where(text_availability.c.paper_id.in_(cited_ids))).all())
    notices = _notices(conn, pairs)
    co_cited = Counter(p.ctx for p in pairs)

    venue_papers = Counter(_venue(c.container) for c in citing.values())
    cited_venues = {p.ref: _venue(p.cited_container if p.cited else p.parsed_container) for p in pairs}
    venue_cites = Counter(cited_venues.values())

    rows = []
    for p in pairs:
        c = citing[p.citing]
        cited_year = p.cited_year if p.cited else p.parsed_year
        found = sponsors.get(p.citing, set())
        rows.append(CitationFeatures(
            citation_context_id=p.ctx, reference_id=p.ref, citing_paper_id=p.citing,
            cited_paper_id=p.cited, citing_year=c.year,
            citing_venue_papers=venue_papers[_venue(c.container)] if _venue(c.container) else None,
            citing_references=n_refs[p.citing],
            citing_sponsor="none" if not found else next(iter(found)) if len(found) == 1 else "multiple",
            cited_year=cited_year,
            year_gap=c.year - cited_year if c.year and cited_year else None,
            cited_venue_citations=venue_cites[cited_venues[p.ref]] if cited_venues[p.ref] else None,
            cited_resolution=p.resolution_status if p.resolution_status in RESOLUTIONS else "unresolved",
            cited_text=held.get(p.cited) if held.get(p.cited) in TEXT_HELD else None,
            cited_notice=notices.get(p.ref, "none"),
            shared_authors=shared_authors(c.authors, p.cited_authors) if p.cited else None,
            section=p.section if p.section in SECTIONS else None,
            co_cited=co_cited[p.ctx]))
    return rows


def _notices(conn: Connection, pairs: Sequence) -> dict[int, str]:
    """The most serious notice about each cited work, matched on DOI or PMID
    (the reference's own or its matched work's), as C02 matches them."""
    dois: dict[str, set[int]] = {}
    pmids: dict[str, set[int]] = {}
    for p in pairs:
        for doi in {p.parsed_doi, p.cited_doi} - {None}:
            dois.setdefault(doi, set()).add(p.ref)
        for pmid in {p.parsed_pmid, p.cited_pmid} - {None}:
            pmids.setdefault(pmid, set()).add(p.ref)
    out: dict[int, str] = {}
    rank = {kind: i for i, kind in enumerate(NOTICES)}
    all_d, all_p = list(dois), list(pmids)
    for start in range(0, max(len(all_d), len(all_p)), 5000):
        chunk_d, chunk_p = all_d[start:start + 5000], all_p[start:start + 5000]
        for n in conn.execute(select(retraction_notice.c.retracted_doi, retraction_notice.c.retracted_pmid,
                                     retraction_notice.c.notice_type)
                              .where(retraction_notice.c.retracted_doi.in_(chunk_d)
                                     | retraction_notice.c.retracted_pmid.in_(chunk_p))):
            if n.notice_type not in rank:
                continue  # a reinstatement says nothing against the work
            for ref in dois.get(n.retracted_doi, set()) | pmids.get(n.retracted_pmid, set()):
                if rank[n.notice_type] < rank[out.get(ref, "none")]:
                    out[ref] = n.notice_type
    return out


# --- encoding -------------------------------------------------------------------------

def feature_names() -> list[str]:
    """Column names of `to_matrix`: the numbers, then one column per category
    value, "unknown" included."""
    names = list(NUMERIC)
    for name, values in CATEGORIES.items():
        names += [f"{name}={v}" for v in values] + [f"{name}=unknown"]
    return names


def feature_group(column: str) -> str:
    """The feature a column belongs to: "section=methods" -> "section"."""
    return column.split("=", 1)[0]


def to_matrix(rows: Sequence[CitationFeatures]) -> np.ndarray:
    """Float matrix in `feature_names()` order: NaN for an unknown number,
    one-hot categories with an explicit unknown column."""
    names = feature_names()
    index = {n: i for i, n in enumerate(names)}
    x = np.full((len(rows), len(names)), np.nan, dtype=float)
    for i, r in enumerate(rows):
        for name in NUMERIC:
            value = getattr(r, name)
            if value is not None:
                x[i, index[name]] = float(value)
        for name, values in CATEGORIES.items():
            value = getattr(r, name)
            for v in values:
                x[i, index[f"{name}={v}"]] = 0.0
            x[i, index[f"{name}=unknown"]] = 0.0
            x[i, index[f"{name}={value}" if value in values else f"{name}=unknown"]] = 1.0
    return x


def describe(rows: Sequence[CitationFeatures]) -> dict:
    """How complete each feature is and how it is spread, for the report."""
    n = len(rows)
    out: dict = {"rows": n, "citing_papers": len({r.citing_paper_id for r in rows}),
                 "contexts": len({r.citation_context_id for r in rows}), "numeric": {}, "categories": {}}
    for name in NUMERIC:
        values = [float(getattr(r, name)) for r in rows if getattr(r, name) is not None]
        q = np.percentile(values, [5, 50, 95]).tolist() if values else [None] * 3
        out["numeric"][name] = {"known": len(values), "p5": q[0], "median": q[1], "p95": q[2],
                                "mean": float(np.mean(values)) if values else None}
    for name, values in CATEGORIES.items():
        counts = Counter(getattr(r, name) if getattr(r, name) in values else "unknown" for r in rows)
        out["categories"][name] = dict(counts.most_common())
    return out
