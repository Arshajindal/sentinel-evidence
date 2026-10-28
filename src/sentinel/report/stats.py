"""`sentinel stats --run N` (§9.8, ADR-039).

Per-check counts and flag rates from the run's findings, the cascade ratios
the run saw (snapshotted in `check_run.counts`), and wall time per stage: the
check run's own, and the ingest, resolve and extract runs recorded for its
corpus before it started.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import Connection, Text, case, cast, func, or_, select

from sentinel.models import check_run, finding, stage_run


class CheckStats(BaseModel):
    check_id: str
    subjects: int                  # papers or trials the check ran on
    applicable: int                # subjects with at least one result that isn't not_applicable
    flagged_subjects: int
    flag_rate: float | None        # flagged / applicable
    by_status: dict[str, int]      # every finding, per-item and summary
    flags_by_severity: dict[str, int]
    seconds: float | None


class StageStats(BaseModel):
    stage: str
    source: str | None
    corpus_label: str | None
    runs: int
    by_status: dict[str, int]
    seconds: float                 # finished runs only
    items: int


class RunStats(BaseModel):
    run_id: int
    status: str
    corpus_label: str | None
    config_hash: str
    started_at: datetime
    finished_at: datetime | None
    seconds: float | None
    papers: int | None
    trials: int | None
    checks: list[CheckStats]
    load_seconds: float | None
    cascade: dict[str, dict[str, int]] | None
    stages: list[StageStats]


def load_run_stats(conn: Connection, run_id: int) -> RunStats | None:
    run = conn.execute(select(check_run).where(check_run.c.id == run_id)).mappings().one_or_none()
    if run is None:
        return None
    counts = run["counts"] or {}
    seconds = counts.get("seconds") or {}

    subject = func.coalesce(cast(finding.c.paper_id, Text), finding.c.nct_id)
    per_subject = (
        select(finding.c.check_id, subject.label("subject"),
               func.bool_or(finding.c.status == "flagged").label("flagged"),
               func.bool_and(finding.c.status == "not_applicable").label("na"))
        .where(finding.c.run_id == run_id)
        .group_by(finding.c.check_id, subject)
        .subquery()
    )
    subjects = {r.check_id: r for r in conn.execute(
        select(per_subject.c.check_id, func.count().label("n"),
               func.count().filter(~per_subject.c.na).label("applicable"),
               func.count().filter(per_subject.c.flagged).label("flagged"))
        .group_by(per_subject.c.check_id))}
    statuses: dict[str, dict[str, int]] = {}
    severities: dict[str, dict[str, int]] = {}
    for check_id, status, severity, n in conn.execute(
        select(finding.c.check_id, finding.c.status, finding.c.severity, func.count())
        .where(finding.c.run_id == run_id)
        .group_by(finding.c.check_id, finding.c.status, finding.c.severity)
    ):
        by_status = statuses.setdefault(check_id, {})
        by_status[status] = by_status.get(status, 0) + n
        if status == "flagged":
            sev = severities.setdefault(check_id, {})
            sev[severity] = sev.get(severity, 0) + n

    checks = []
    for check_id in run["checks"]:
        s = subjects.get(check_id)
        n, applicable, flagged = (s.n, s.applicable, s.flagged) if s else (0, 0, 0)
        checks.append(CheckStats(
            check_id=check_id, subjects=n, applicable=applicable, flagged_subjects=flagged,
            flag_rate=flagged / applicable if applicable else None,
            by_status=statuses.get(check_id, {}), flags_by_severity=severities.get(check_id, {}),
            seconds=seconds.get(check_id)))

    finished = run["finished_at"]
    return RunStats(
        run_id=run_id, status=run["status"], corpus_label=run["corpus_label"],
        config_hash=run["config_hash"], started_at=run["started_at"], finished_at=finished,
        seconds=(finished - run["started_at"]).total_seconds() if finished else None,
        papers=counts.get("papers"), trials=counts.get("trials"), checks=checks,
        load_seconds=seconds.get("load"), cascade=counts.get("cascade"),
        stages=_stages(conn, run["corpus_label"], run["started_at"]),
    )


def _stages(conn: Connection, corpus_label: str | None, before: datetime) -> list[StageStats]:
    """Stage runs that fed this check run: its corpus's, and corpus-wide ones
    such as the Retraction Watch load, started before the check run."""
    elapsed = func.extract("epoch", stage_run.c.finished_at - stage_run.c.started_at)
    corpus = or_(stage_run.c.corpus_label.is_(None), stage_run.c.corpus_label == corpus_label)
    groups: dict[tuple, StageStats] = {}
    for row in conn.execute(
        select(stage_run.c.stage, stage_run.c.source, stage_run.c.corpus_label,
               stage_run.c.status, func.count().label("runs"),
               func.coalesce(func.sum(case((stage_run.c.finished_at.is_not(None), elapsed))), 0)
               .label("seconds"),
               func.coalesce(func.sum(stage_run.c.n_items), 0).label("items"))
        .where(corpus, stage_run.c.started_at < before)
        .group_by(stage_run.c.stage, stage_run.c.source, stage_run.c.corpus_label,
                  stage_run.c.status)
    ):
        key = (row.stage, row.source, row.corpus_label)
        g = groups.setdefault(key, StageStats(stage=row.stage, source=row.source,
                                              corpus_label=row.corpus_label, runs=0,
                                              by_status={}, seconds=0.0, items=0))
        g.runs += row.runs
        g.by_status[row.status] = row.runs
        g.seconds += float(row.seconds)
        g.items += int(row.items)
    order = {"ingest": 0, "resolve": 1, "extract": 2}
    return sorted(groups.values(), key=lambda g: (order.get(g.stage, 9), g.source or ""))


def render_stats_json(stats: RunStats) -> str:
    return json.dumps(stats.model_dump(mode="json"), indent=2)


def _ratio(part: int, whole: int) -> str:
    return f"{part:,} ({100 * part / whole:.1f}%)" if whole else f"{part:,}"


def _duration(seconds: float | None) -> str:
    if seconds is None:
        return "not recorded"
    if seconds < 60:
        return f"{seconds:.1f} s"
    minutes, secs = divmod(round(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d} min" if hours else f"{minutes} min {secs:02d} s"


def render_stats_text(stats: RunStats) -> str:
    lines = [
        f"Run {stats.run_id} | {stats.status} | corpus {stats.corpus_label or '-'} | "
        f"config {stats.config_hash[:12]}",
        f"Started {stats.started_at:%Y-%m-%d %H:%M}; wall time {_duration(stats.seconds)}"
        + (f"; {stats.papers:,} papers" if stats.papers else "")
        + (f"; {stats.trials:,} trials" if stats.trials else ""),
        "",
        "Checks",
    ]
    for c in stats.checks:
        rate = f"{100 * c.flag_rate:.1f}%" if c.flag_rate is not None else "-"
        sev = ", ".join(f"{n} {s}" for s, n in sorted(c.flags_by_severity.items())) or "none"
        status = ", ".join(f"{n:,} {s}" for s, n in sorted(c.by_status.items()))
        lines += [
            f"  {c.check_id}: {c.flagged_subjects:,} of {c.applicable:,} applicable subjects flagged "
            f"({rate}); {c.subjects:,} checked; time {_duration(c.seconds)}",
            f"       findings: {status or 'none'}; flags by severity: {sev}",
        ]
    if stats.load_seconds is not None:
        lines.append(f"  loading contexts: {_duration(stats.load_seconds)}")

    lines += ["", "Resolution cascade (as the run saw it)"]
    if stats.cascade is None:
        lines.append("  not recorded for this run (no paper checks, or run before ADR-039)")
    else:
        by_status = stats.cascade.get("by_status", {})
        total = sum(by_status.values())
        resolved = by_status.get("resolved", 0)
        lines.append(f"  {total:,} references: " + ", ".join(
            f"{s} {_ratio(n, total)}" for s, n in sorted(by_status.items(), key=lambda x: -x[1])))
        for name, label in (("by_method", "resolved by stage"), ("by_pass", "title matches found in")):
            part = stats.cascade.get(name, {})
            if part:
                lines.append(f"  {label}: " + ", ".join(
                    f"{m} {_ratio(n, resolved)}" for m, n in sorted(part.items(), key=lambda x: -x[1])))

    lines += ["", "Stages before this run"]
    if not stats.stages:
        lines.append("  none recorded (stage timing starts with ADR-039)")
    for g in stats.stages:
        name = g.stage + (f" {g.source}" if g.source else "")
        runs = ", ".join(f"{n} {s}" for s, n in sorted(g.by_status.items()))
        lines.append(f"  {name}: {_duration(g.seconds)} over {g.runs} run(s) ({runs}); "
                     f"{g.items:,} items" + ("" if g.corpus_label else " | corpus-wide"))
    return "\n".join(lines)
