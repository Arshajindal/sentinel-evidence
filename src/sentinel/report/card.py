"""Report cards (sentinel-project.md §9.8).

A trial card shows the latest finding from each check, from the most recent
completed run that examined it. A paper card shows, for each check, all of its
findings from the latest completed run that examined the paper (C01 records
one finding per unverifiable reference plus a summary), followed by the cards
of its linked trials.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy import Connection, func, select

from sentinel.models import check_run, finding, paper, paper_trial_link, trial
from sentinel.resolve.normalize import normalize_doi

NCT_RE = re.compile(r"^NCT\d{8}$", re.IGNORECASE)
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}


def detect_subject(value: str) -> tuple[Literal["trial", "doi", "paper"], str | int]:
    """NCT + 8 digits is a trial, `10.` starts a DOI, an integer is a paper ID."""
    v = value.strip()
    if NCT_RE.match(v):
        return "trial", v.upper()
    if v.startswith("10."):
        return "doi", v
    if v.isdigit():
        return "paper", int(v)
    raise ValueError(f"not an NCT ID, DOI or paper ID: {value!r}")


class CardFinding(BaseModel):
    check_id: str
    check_ver: str
    severity: str
    status: str
    summary: str
    evidence: dict[str, Any]
    run_id: int
    run_started_at: str
    config_hash: str


class TrialCard(BaseModel):
    nct_id: str
    brief_title: str | None
    overall_status: str | None
    study_type: str | None
    phase: str | None
    lead_sponsor: str | None
    sponsor_class: str | None
    findings: list[CardFinding]


def load_trial_card(conn: Connection, nct_id: str) -> TrialCard | None:
    t = conn.execute(select(trial).where(trial.c.nct_id == nct_id)).mappings().one_or_none()
    if t is None:
        return None
    rows = conn.execute(
        select(finding, check_run.c.started_at, check_run.c.config_hash)
        .join(check_run, finding.c.run_id == check_run.c.id)
        .where(finding.c.nct_id == nct_id, check_run.c.status == "done")
        .order_by(finding.c.check_id, finding.c.run_id.desc(), finding.c.id)
        .distinct(finding.c.check_id)
    ).mappings()
    findings = [
        CardFinding(
            check_id=r["check_id"],
            check_ver=r["check_ver"],
            severity=r["severity"],
            status=r["status"],
            summary=r["summary"],
            evidence=r["evidence"],
            run_id=r["run_id"],
            run_started_at=r["started_at"].isoformat(),
            config_hash=r["config_hash"],
        )
        for r in rows
    ]
    findings.sort(key=lambda f: (SEVERITY_ORDER.get(f.severity, 9), f.check_id))
    return TrialCard(
        nct_id=t["nct_id"],
        brief_title=t["brief_title"],
        overall_status=t["overall_status"],
        study_type=t["study_type"],
        phase=t["phase"],
        lead_sponsor=t["lead_sponsor"],
        sponsor_class=t["sponsor_class"],
        findings=findings,
    )


def render_json(card: TrialCard) -> str:
    return json.dumps(card.model_dump(), indent=2, ensure_ascii=False)


def render_text(card: TrialCard) -> str:
    facts = [card.overall_status, card.study_type, card.phase]
    sponsor = card.lead_sponsor or "unknown sponsor"
    if card.sponsor_class:
        sponsor += f" ({card.sponsor_class})"
    lines = [
        f"{card.nct_id}: {card.brief_title or '(no title)'}",
        " | ".join([*(f for f in facts if f), sponsor]),
        "",
    ]
    if not card.findings:
        lines.append("No findings yet. Run `sentinel check` first.")
        return "\n".join(lines)
    for f in card.findings:
        lines.append(f"[{f.severity.upper()}] {f.check_id} {f.status}: {f.summary}")
        lines.append(
            f"    run {f.run_id} | {f.run_started_at[:10]} | check v{f.check_ver}"
            f" | config {f.config_hash[:8]}"
        )
        detail = _detail(f)
        if detail:
            lines.append(f"    {detail}")
    return "\n".join(lines)


class PaperCard(BaseModel):
    paper_id: int
    title: str
    doi: str | None
    pmcid: str | None
    year: int | None
    container: str | None
    findings: list[CardFinding]
    trials: list[TrialCard]


def _card_finding(r) -> CardFinding:
    return CardFinding(check_id=r["check_id"], check_ver=r["check_ver"], severity=r["severity"],
                       status=r["status"], summary=r["summary"], evidence=r["evidence"],
                       run_id=r["run_id"], run_started_at=r["started_at"].isoformat(),
                       config_hash=r["config_hash"])


def load_paper_card(conn: Connection, paper_id: int | None, *, doi: str | None = None) -> PaperCard | None:
    query = select(paper)
    if paper_id is not None:
        query = query.where(paper.c.id == paper_id)
    else:
        query = query.where(paper.c.doi == normalize_doi(doi))
    p = conn.execute(query).mappings().one_or_none()
    if p is None:
        return None
    latest = (select(finding.c.check_id, func.max(finding.c.run_id).label("run_id"))
              .join(check_run, finding.c.run_id == check_run.c.id)
              .where(finding.c.paper_id == p["id"], check_run.c.status == "done")
              .group_by(finding.c.check_id).subquery())
    rows = conn.execute(
        select(finding, check_run.c.started_at, check_run.c.config_hash)
        .join(check_run, finding.c.run_id == check_run.c.id)
        .join(latest, (finding.c.check_id == latest.c.check_id) & (finding.c.run_id == latest.c.run_id))
        .where(finding.c.paper_id == p["id"])
        .order_by(finding.c.id)
    ).mappings()
    findings = sorted((_card_finding(r) for r in rows),
                      key=lambda f: (SEVERITY_ORDER.get(f.severity, 9), f.check_id))
    nct_ids = conn.execute(select(paper_trial_link.c.nct_id)
                           .where(paper_trial_link.c.paper_id == p["id"])
                           .order_by(paper_trial_link.c.nct_id)).scalars().all()
    trials = [c for c in (load_trial_card(conn, n) for n in nct_ids) if c is not None]
    return PaperCard(paper_id=p["id"], title=p["title"], doi=p["doi"], pmcid=p["pmcid"],
                     year=p["year"], container=p["container"], findings=findings, trials=trials)


def render_paper_json(card: PaperCard) -> str:
    return json.dumps(card.model_dump(), indent=2, ensure_ascii=False)


def render_paper_text(card: PaperCard) -> str:
    ids = " | ".join(x for x in (card.doi and f"doi {card.doi}", card.pmcid,
                                 card.container, str(card.year) if card.year else None) if x)
    lines = [f"Paper {card.paper_id}: {card.title}", ids, ""]
    if not card.findings:
        lines.append("No findings yet. Run `sentinel check` first.")
    for f in card.findings:
        lines.append(f"[{f.severity.upper()}] {f.check_id} {f.status}: {f.summary}")
        lines.append(f"    run {f.run_id} | {f.run_started_at[:10]} | check v{f.check_ver}"
                     f" | config {f.config_hash[:8]}")
        if f.evidence.get("raw_string"):
            lines.append(f"    cited as: {f.evidence['raw_string'][:200]}")
    for t in card.trials:
        lines += ["", "Linked trial:", *("  " + line for line in render_text(t).splitlines())]
    return "\n".join(lines)


def _detail(f: CardFinding) -> str:
    keys = ("primary_completion_date", "results_first_posted", "record_fetched_on",
            "grace_months")
    parts = [f"{k.replace('_', ' ')} {f.evidence[k]}" for k in keys if f.evidence.get(k) is not None]
    return " | ".join(parts)
