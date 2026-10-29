"""Inputs to checks (sentinel-project.md §9.6).

Everything a check needs is loaded here first, so `Check.run()` never touches
the network or the database. Later sessions add fields without changing the
check interface.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Connection, select

from sentinel.models import (
    corpus_paper,
    paper,
    reference,
    retraction_notice,
    stat_test,
    trial,
    trial_outcome,
)
from sentinel.schemas import TrialOutcome, TrialRecord


class TrialContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    trial: TrialRecord
    # The day the registry record was fetched. Date-based checks measure to
    # here, which keeps them pure and reproducible.
    fetched_on: date


class NoticeContext(BaseModel):
    """A Retraction Watch notice about a cited work (C02)."""

    model_config = ConfigDict(frozen=True)

    notice_type: str                  # retraction|correction|expression_of_concern|reinstatement
    notice_date: date | None
    notice_doi: str | None
    reason: str | None
    source: str
    source_record_id: str | None
    matched_on: str                   # doi | pmid


class ReferenceContext(BaseModel):
    """One entry of a paper's bibliography with its resolution result (§9.5)."""

    model_config = ConfigDict(frozen=True)

    reference_id: int
    ordinal: int | None
    raw_string: str | None
    citation_tag: str | None          # element-citation | mixed-citation
    publication_type: str | None      # from the XML, when given (journal, book, web...)
    parsed_doi: str | None
    parsed_pmid: str | None
    parsed_title: str | None
    parsed_first_author: str | None
    parsed_year: int | None
    parsed_container: str | None
    resolution_status: str
    resolution_method: str | None
    resolved_paper_id: int | None
    resolution_evidence: dict[str, Any] | None
    # Notices about the cited work, matched by the reference's own DOI/PMID
    # or its resolved paper's (C02).
    notices: tuple[NoticeContext, ...] = ()


class StatTestContext(BaseModel):
    """A reported test found in the paper's body text (C03)."""

    model_config = ConfigDict(frozen=True)

    stat_test_id: int
    location: str | None
    raw_string: str


class MeanContext(BaseModel):
    """A reported mean for C04 (GRIM). Nothing extracts these from the corpus
    in Phase 1 (ADR-038); fixtures supply them."""

    model_config = ConfigDict(frozen=True)

    label: str                      # what was measured
    mean_text: str                  # as printed, e.g. "3.45": its decimals matter
    n: int | None                   # number of participants the mean is over
    integer_scale: bool | None      # each value a whole number? None = unknown
    items: int = 1                  # items averaged per participant (1 = single measure)
    source: str | None = None       # where it was read


class PaperContext(BaseModel):
    """A paper and what is known about it."""

    model_config = ConfigDict(frozen=True)

    paper_id: int
    # When the paper first appeared (Europe PMC firstPublicationDate); C02's
    # timing bands measure from here.
    publication_date: date | None = None
    # "full" once the body text has been examined for statistics (C03).
    fulltext_status: str = "none"
    references: tuple[ReferenceContext, ...] = ()
    stat_tests: tuple[StatTestContext, ...] = ()
    means: tuple[MeanContext, ...] = ()
    trials: tuple[TrialContext, ...] = ()


def load_paper_contexts(conn: Connection, paper_ids: list[int]) -> list[PaperContext]:
    """Papers with their references (in bibliography order) and the notices
    about each cited work."""
    if not paper_ids:
        return []
    cited = paper.alias("cited")
    rows = conn.execute(
        select(reference, cited.c.doi.label("cited_doi"), cited.c.pmid.label("cited_pmid"))
        .outerjoin(cited, cited.c.id == reference.c.resolved_paper_id)
        .where(reference.c.citing_paper_id.in_(paper_ids))
        .order_by(reference.c.citing_paper_id, reference.c.ordinal, reference.c.id)
    ).mappings().all()
    notices = _notices_for(conn, rows)
    meta = {pid: (_iso_date((raw or {}).get("firstPublicationDate")), status)
            for pid, raw, status in conn.execute(
                select(paper.c.id, paper.c.raw, paper.c.fulltext_status).where(paper.c.id.in_(paper_ids)))}
    tests: dict[int, list[StatTestContext]] = {}
    for st in conn.execute(select(stat_test.c.id, stat_test.c.paper_id, stat_test.c.location,
                                  stat_test.c.raw_string)
                           .where(stat_test.c.paper_id.in_(paper_ids)).order_by(stat_test.c.id)):
        tests.setdefault(st.paper_id, []).append(
            StatTestContext(stat_test_id=st.id, location=st.location, raw_string=st.raw_string))

    refs: dict[int, list[ReferenceContext]] = {pid: [] for pid in paper_ids}
    for r in rows:
        raw = r["raw"] or {}
        refs[r["citing_paper_id"]].append(ReferenceContext(
            reference_id=r["id"], ordinal=r["ordinal"], raw_string=r["raw_string"],
            citation_tag=raw.get("citation_tag"), publication_type=raw.get("publication_type"),
            parsed_doi=r["parsed_doi"], parsed_pmid=r["parsed_pmid"],
            parsed_title=r["parsed_title"], parsed_first_author=r["parsed_first_author"],
            parsed_year=r["parsed_year"], parsed_container=r["parsed_container"],
            resolution_status=r["resolution_status"], resolution_method=r["resolution_method"],
            resolved_paper_id=r["resolved_paper_id"],
            resolution_evidence=r["resolution_evidence"],
            notices=tuple(notices.get(r["id"], ())),
        ))
    return [PaperContext(paper_id=pid, publication_date=meta.get(pid, (None, None))[0],
                         fulltext_status=meta.get(pid, (None, "none"))[1] or "none",
                         references=tuple(refs[pid]), stat_tests=tuple(tests.get(pid, ())))
            for pid in paper_ids]


def _notices_for(conn: Connection, rows) -> dict[int, list[NoticeContext]]:
    """Notices per reference ID, matched on DOI or PMID (the reference's own
    or its resolved paper's). A notice found both ways is listed once."""
    dois: dict[str, set[int]] = {}
    pmids: dict[str, set[int]] = {}
    for r in rows:
        for value in {r["parsed_doi"], r["cited_doi"]} - {None}:
            dois.setdefault(value, set()).add(r["id"])
        for value in {r["parsed_pmid"], r["cited_pmid"]} - {None}:
            pmids.setdefault(value, set()).add(r["id"])
    if not dois and not pmids:
        return {}
    found = conn.execute(select(retraction_notice).where(
        retraction_notice.c.retracted_doi.in_(list(dois))
        | retraction_notice.c.retracted_pmid.in_(list(pmids)))).mappings()
    out: dict[int, dict[int, NoticeContext]] = {}
    for n in found:
        for matched_on, ref_ids in (("doi", dois.get(n["retracted_doi"], set())),
                                    ("pmid", pmids.get(n["retracted_pmid"], set()))):
            for ref_id in ref_ids:
                out.setdefault(ref_id, {}).setdefault(n["id"], NoticeContext(
                    notice_type=n["notice_type"], notice_date=n["notice_date"],
                    notice_doi=n["notice_doi"], reason=n["reason"], source=n["source"],
                    source_record_id=n["source_record_id"], matched_on=matched_on))
    return {ref_id: sorted(by_id.values(), key=lambda x: (x.notice_date or date.max, x.notice_type))
            for ref_id, by_id in out.items()}


def _iso_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def corpus_paper_ids(conn: Connection, label: str, limit: int | None = None) -> list[int]:
    query = (select(corpus_paper.c.paper_id)
             .where(corpus_paper.c.label == label, corpus_paper.c.paper_id.is_not(None))
             .order_by(corpus_paper.c.ordinal))
    if limit is not None:
        query = query.limit(limit)
    return list(conn.execute(query).scalars())


def load_trial_contexts(
    conn: Connection, nct_ids: list[str] | None = None, limit: int | None = None
) -> list[TrialContext]:
    query = select(trial).order_by(trial.c.nct_id)
    if nct_ids is not None:
        query = query.where(trial.c.nct_id.in_(nct_ids))
    if limit is not None:
        query = query.limit(limit)
    trials = conn.execute(query).mappings().all()
    if not trials:
        return []

    outcomes: dict[str, list[TrialOutcome]] = {}
    rows = conn.execute(
        select(trial_outcome)
        .where(trial_outcome.c.nct_id.in_([t["nct_id"] for t in trials]))
        .order_by(trial_outcome.c.version_no, trial_outcome.c.id)
    ).mappings()
    for row in rows:
        outcomes.setdefault(row["nct_id"], []).append(
            TrialOutcome.model_validate(
                {k: row[k] for k in TrialOutcome.model_fields}
            )
        )

    contexts = []
    for t in trials:
        record = TrialRecord.model_validate(
            {
                **{k: t[k] for k in TrialRecord.model_fields if k != "outcomes"},
                "outcomes": outcomes.get(t["nct_id"], []),
            }
        )
        contexts.append(TrialContext(trial=record, fetched_on=t["ingested_at"].date()))
    return contexts
