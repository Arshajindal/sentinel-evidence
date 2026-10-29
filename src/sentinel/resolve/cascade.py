"""Entity resolution: matching each reference to a known work (§9.5).

Stages run in order and stop at the first that finds exactly one match:

  1 DOI exact                                         1.00  (fetched if not stored)
  2 PMID exact                                        1.00  (fetched if not stored)
  3 normalised title exact                            0.95
 3b title equal but for a subtitle, first author, year 0.85
  4 title trigram >= min, first author, year +/- tol  0.70-0.90, scaled by similarity
  5 title trigram >= strict alone                     0.60, weak: for review

Two or more candidates tied at a stage give `ambiguous`, and nothing is
picked. Stages 3-5 run on stored works first; if none matches, candidates are
fetched (Crossref search, then OpenAlex within budget) and the stages re-run.

Precision guards beyond §9.5 (ADR-034); both can only cost recall:
- at stages 3 and 5, a candidate whose year or first author contradicts the
  reference is rejected;
- stage 5 needs a title of at least four words; trigram similarity on very
  short titles is noisy.

Stage 3b (ADR-041) is for a reference that gives a title's subtitle when the
record doesn't, or the reverse. A subtitle can be all that tells two works in
a series apart, so the first author and year must agree, not merely not
contradict.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any, Literal

from sqlalchemy import Engine, func, select, update

from sentinel.log import get_logger
from sentinel.models import corpus_paper, paper, reference
from sentinel.parse.references import PARSER_VERSION, parse_reference
from sentinel.resolve.candidates import Candidate, CandidateSource, LookupLog
from sentinel.resolve.normalize import main_title, normalize_title

log = get_logger(__name__)

Status = Literal["resolved", "unresolved", "ambiguous"]
STAGE5_MIN_WORDS = 4


@dataclass(frozen=True)
class Thresholds:
    trigram_min: float
    trigram_strict: float
    year_tolerance: int
    ambiguous_margin: float


@dataclass(frozen=True)
class RefFields:
    reference_id: int
    citing_paper_id: int
    doi: str | None
    pmid: str | None
    title: str | None
    first_author: str | None
    year: int | None
    container: str | None = None
    raw_string: str | None = None


@dataclass
class Resolution:
    status: Status
    method: str | None = None
    score: float | None = None
    paper_id: int | None = None
    evidence: dict[str, Any] = field(default_factory=dict)


# --- pure matching (stages 3-5) ---------------------------------------------------


def author_tokens(name: str | None) -> set[str]:
    return set(name.split()) if name else set()


def authors_match(a: str | None, b: str | None) -> bool:
    ta, tb = author_tokens(a), author_tokens(b)
    return bool(ta and tb) and (ta <= tb or tb <= ta)


def authors_overlap(a: str | None, b: str | None) -> bool:
    return bool(author_tokens(a) & author_tokens(b))


def contradicts(ref: RefFields, c: Candidate, year_tolerance: int) -> str | None:
    """Why a candidate can't be this work, or None if nothing contradicts."""
    if ref.year is not None and c.year is not None and abs(ref.year - c.year) > year_tolerance:
        return f"year {c.year} vs {ref.year}"
    ta, tb = author_tokens(ref.first_author), author_tokens(c.first_author)
    if ta and tb and not (ta & tb):
        return f"first author {c.first_author!r} vs {ref.first_author!r}"
    return None


def match_title(ref: RefFields, candidates: list[Candidate], th: Thresholds) -> Resolution | None:
    """Stages 3-5 over the given candidates. None means no stage matched."""
    title_norm = normalize_title(ref.title)
    if not title_norm:
        return None

    # Stage 3: exact normalised title, nothing contradicting.
    exact = [c for c in candidates
             if c.title_norm == title_norm and contradicts(ref, c, th.year_tolerance) is None]
    exact_ids = sorted({c.paper_id for c in exact})
    if len(exact_ids) > 1:
        return Resolution("ambiguous", "title_exact", evidence={"tied": exact_ids})
    if exact_ids:
        return Resolution("resolved", "title_exact", 0.95, exact_ids[0])

    # Stage 3b: the same title but for a subtitle, same first author and year.
    if ref.first_author and ref.year is not None:
        ref_main = main_title(ref.title)
        partial_ids = sorted({
            c.paper_id for c in candidates
            if ((ref_main and c.title_norm == ref_main) or main_title(c.title) == title_norm)
            and authors_match(ref.first_author, c.first_author)
            and c.year is not None and abs(c.year - ref.year) <= th.year_tolerance})
        if len(partial_ids) > 1:
            return Resolution("ambiguous", "title_main", evidence={"tied": partial_ids})
        if partial_ids:
            return Resolution("resolved", "title_main", 0.85, partial_ids[0])

    # Stage 4: similar title, same first author, year within tolerance.
    if ref.first_author and ref.year is not None:
        span = 1.0 - th.trigram_min
        scored = sorted(
            ((0.70 + 0.20 * (c.similarity - th.trigram_min) / span if span > 0 else 0.90, c)
             for c in candidates
             if c.similarity >= th.trigram_min and authors_match(ref.first_author, c.first_author)
             and c.year is not None and abs(c.year - ref.year) <= th.year_tolerance),
            key=lambda sc: (-sc[0], sc[1].paper_id))
        if scored:
            best_score, best = scored[0]
            tied = sorted({c.paper_id for s, c in scored if best_score - s <= th.ambiguous_margin})
            if len(tied) > 1:
                return Resolution("ambiguous", "title_author_year", evidence={"tied": tied})
            return Resolution("resolved", "title_author_year", round(best_score, 4), best.paper_id)

    # Stage 5: very similar title alone, for longer titles, nothing contradicting.
    if len(title_norm.split()) >= STAGE5_MIN_WORDS:
        strong = sorted(
            (c for c in candidates
             if c.similarity >= th.trigram_strict and contradicts(ref, c, th.year_tolerance) is None),
            key=lambda c: (-c.similarity, c.paper_id))
        if strong:
            tied = sorted({c.paper_id for c in strong
                           if strong[0].similarity - c.similarity <= th.ambiguous_margin})
            if len(tied) > 1:
                return Resolution("ambiguous", "title_only", evidence={"tied": tied})
            return Resolution("resolved", "title_only", 0.60, strong[0].paper_id)
    return None


def near_misses(ref: RefFields, candidates: list[Candidate], th: Thresholds, n: int = 3) -> list[dict]:
    """The top `n` candidates by similarity, plus every candidate sharing the
    reference's first author (C01 needs those to tell 'exists under a variant
    title' from 'not found'). At most 8."""
    picked = list(candidates[:n])
    picked += [c for c in candidates[n:] if authors_overlap(ref.first_author, c.first_author)]
    out = []
    for c in picked[:8]:
        why = contradicts(ref, c, th.year_tolerance)
        if why is None:
            why = (f"similarity {c.similarity:.3f} below {th.trigram_min}"
                   if c.similarity < th.trigram_min else "author or year missing or not matching")
        out.append({"paper_id": c.paper_id, "similarity": round(c.similarity, 4),
                    "year": c.year, "first_author": c.first_author, "rejected": why})
    return out


def search_query(ref: RefFields) -> str:
    """A citation-like string for Crossref's bibliographic search."""
    parts = [ref.first_author, ref.title, ref.container, str(ref.year) if ref.year else None]
    return " ".join(p for p in parts if p)


# --- the cascade ---------------------------------------------------------------------


class Resolver:
    def __init__(self, source: CandidateSource, thresholds: Thresholds) -> None:
        self.source = source
        self.th = thresholds

    def resolve(self, ref: RefFields) -> Resolution:
        lookups = LookupLog()
        stages: list[dict] = []
        result = self._run(ref, lookups, stages)
        result.evidence = {**result.evidence, "stages": stages, "lookups": lookups.as_dict()}
        return result

    def _title_agreement(self, ref: RefFields, paper_id: int) -> dict:
        """How well the cited title agrees with the work an identifier points to.

        Evidence only: an identifier match stands (§9.5). A low value means the
        cited DOI/PMID names a differently titled work, which C01 can report.
        """
        cited = normalize_title(ref.title)
        matched = self.source.title_norm_of(paper_id)
        if not cited or not matched:
            return {}
        return {"title_agreement": round(SequenceMatcher(None, cited, matched).ratio(), 3)}

    def _run(self, ref: RefFields, lookups: LookupLog, stages: list[dict]) -> Resolution:
        if ref.doi:
            paper_id = self.source.paper_by("doi", ref.doi) or self.source.fetch_doi(ref.doi, lookups)
            if paper_id:
                stages.append({"stage": 1, "outcome": "matched"})
                return Resolution("resolved", "doi", 1.0, paper_id,
                                  evidence=self._title_agreement(ref, paper_id))
            stages.append({"stage": 1, "outcome": "doi_not_found",
                           "doi_org_registered": self.source.doi_registered(ref.doi, lookups)})
        else:
            stages.append({"stage": 1, "outcome": "no_doi"})

        if ref.pmid:
            paper_id = self.source.paper_by("pmid", ref.pmid) or self.source.fetch_pmid(ref.pmid, lookups)
            stages.append({"stage": 2, "outcome": "matched" if paper_id else "pmid_not_found"})
            if paper_id:
                return Resolution("resolved", "pmid", 1.0, paper_id,
                                  evidence=self._title_agreement(ref, paper_id))
        else:
            stages.append({"stage": 2, "outcome": "no_pmid"})

        title_norm = normalize_title(ref.title)
        if not title_norm:
            stages.append({"stage": "3-5", "outcome": "no_title"})
            return Resolution("unresolved", evidence={"reason": "nothing_to_match_on"})
        main_norm = main_title(ref.title)

        candidates = self.source.local_title_candidates(title_norm, ref.citing_paper_id,
                                                        main_norm=main_norm)
        found = match_title(ref, candidates, self.th)
        stages.append({"stage": "3-5", "pass": "local", "outcome": found.status if found else "no_match"})
        if found:
            return found

        self.source.search_crossref(search_query(ref), lookups)
        candidates = self.source.local_title_candidates(title_norm, ref.citing_paper_id,
                                                        main_norm=main_norm)
        found = match_title(ref, candidates, self.th)
        stages.append({"stage": "3-5", "pass": "crossref", "outcome": found.status if found else "no_match"})
        if found:
            return found

        self.source.search_openalex(ref.title, ref.year, lookups)
        candidates = self.source.local_title_candidates(title_norm, ref.citing_paper_id,
                                                        main_norm=main_norm)
        found = match_title(ref, candidates, self.th)
        stages.append({"stage": "3-5", "pass": "openalex", "outcome": found.status if found else "no_match"})
        if found:
            return found
        return Resolution("unresolved", evidence={"reason": "no_candidate_matched",
                                                  "near_misses": near_misses(ref, candidates, self.th)})


# --- running over stored references ----------------------------------------------------


@dataclass
class RunStats:
    seen: int = 0
    parsed: int = 0
    by_status: dict[str, int] = field(default_factory=dict)
    by_method: dict[str, int] = field(default_factory=dict)


def select_references(engine: Engine, *, corpus: str, retry: bool = False,
                      sample: int | None = None, seed: int | None = None,
                      limit: int | None = None, structured: bool = False) -> list[int]:
    """Reference IDs to resolve. A sample is deterministic for a given seed.

    `structured` keeps only references from structured reference lists (JATS
    element-citation), the population §9.10's resolution target is defined on.
    """
    query = (select(reference.c.id)
             .join(corpus_paper, corpus_paper.c.paper_id == reference.c.citing_paper_id)
             .where(corpus_paper.c.label == corpus))
    if structured:
        query = query.where(reference.c.raw["citation_tag"].astext == "element-citation")
    if sample is not None:
        if seed is None:
            raise ValueError("a sample needs a seed")
        order = func.md5(func.concat(reference.c.id, ":", str(seed)))
        with engine.connect() as conn:
            ids = conn.execute(query.order_by(order).limit(sample)).scalars().all()
        if not retry:
            with engine.connect() as conn:
                pending = set(conn.execute(select(reference.c.id).where(
                    reference.c.id.in_(ids), reference.c.resolution_status == "pending")).scalars())
            ids = [i for i in ids if i in pending]
        return ids
    if not retry:
        query = query.where(reference.c.resolution_status == "pending")
    query = query.order_by(reference.c.id)
    if limit is not None:
        query = query.limit(limit)
    with engine.connect() as conn:
        return conn.execute(query).scalars().all()


def resolve_references(engine: Engine, resolver: Resolver, reference_ids: list[int],
                       *, batch_size: int = 50) -> RunStats:
    """Parse untagged references, run the cascade, and record each result.

    Each batch commits on its own, so a stopped run (e.g. an exhausted
    OpenAlex budget) keeps what it finished and resumes from `pending`.
    """
    stats = RunStats()
    for i in range(0, len(reference_ids), batch_size):
        batch = reference_ids[i:i + batch_size]
        with engine.connect() as conn:
            rows = conn.execute(
                select(reference, paper.c.year.label("citing_year"))
                .join(paper, paper.c.id == reference.c.citing_paper_id)
                .where(reference.c.id.in_(batch)).order_by(reference.c.id)
            ).mappings().all()

        updates = []
        for row in rows:
            fields, parsed_now = _fields(row)
            result = resolver.resolve(fields)
            evidence = {**result.evidence, "parser": PARSER_VERSION if parsed_now else None}
            values = {
                "resolved_paper_id": result.paper_id,
                "resolution_method": result.method,
                "resolution_score": result.score,
                "resolution_status": result.status,
                "resolved_at": datetime.now(timezone.utc),
                "resolution_evidence": evidence,
            }
            if parsed_now:
                stats.parsed += 1
                values.update(parsed_doi=fields.doi, parsed_pmid=fields.pmid,
                              parsed_title=fields.title, parsed_first_author=fields.first_author,
                              parsed_year=fields.year, parsed_container=fields.container)
            updates.append((row["id"], values))
            stats.seen += 1
            stats.by_status[result.status] = stats.by_status.get(result.status, 0) + 1
            if result.method:
                stats.by_method[result.method] = stats.by_method.get(result.method, 0) + 1

        with engine.begin() as conn:
            for ref_id, values in updates:
                conn.execute(update(reference).where(reference.c.id == ref_id).values(**values))
        log.info("resolve_batch", done=stats.seen, total=len(reference_ids),
                 by_status=stats.by_status,
                 openalex_spent_usd=round(resolver.source.openalex.budget.spent_usd, 4))
    return stats


def summarise(engine: Engine, reference_ids: list[int]) -> dict[str, dict[str, int]]:
    """Status and method counts over a set of references (e.g. the sample)."""
    with engine.connect() as conn:
        rows = conn.execute(
            select(reference.c.resolution_status, reference.c.resolution_method, func.count())
            .where(reference.c.id.in_(reference_ids))
            .group_by(reference.c.resolution_status, reference.c.resolution_method)
        ).all()
    by_status: dict[str, int] = {}
    by_method: dict[str, int] = {}
    for status, method, n in rows:
        by_status[status] = by_status.get(status, 0) + n
        if method:
            by_method[method] = by_method.get(method, 0) + n
    return {"by_status": by_status, "by_method": by_method}


def _fields(row) -> tuple[RefFields, bool]:
    """Fields to match on. Untagged references are parsed from their raw string
    now, and re-parsed when their fields came from an older parser version.
    Tag-derived fields are never overwritten."""
    keys = ("parsed_doi", "parsed_pmid", "parsed_title", "parsed_first_author", "parsed_year")
    parsed_by = (row["resolution_evidence"] or {}).get("parser")
    from_tags = any(row[k] is not None for k in keys) and parsed_by is None
    if from_tags or not row["raw_string"] or parsed_by == PARSER_VERSION:
        return RefFields(row["id"], row["citing_paper_id"], row["parsed_doi"], row["parsed_pmid"],
                         row["parsed_title"], row["parsed_first_author"], row["parsed_year"],
                         row["parsed_container"], row["raw_string"]), False
    p = parse_reference(row["raw_string"], max_year=(row["citing_year"] or 2100) + 1)
    return RefFields(row["id"], row["citing_paper_id"], p.doi, p.pmid, p.title, p.first_author,
                     p.year, p.container, row["raw_string"]), True
