"""Runs checks over stored data and records the results.

One `check_run` row per invocation, carrying `config_hash` (ADR-022). Every
finding is validated before it is written, and evidence is kept for clean
results too (ADR-015).

`counts` also keeps each check's wall time and a snapshot of how the run's
references were resolved, since re-resolution later would overwrite the
cascade ratios this run saw (§9.8, ADR-039).
"""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import Connection, Engine, func, insert, select, update

from sentinel.checks.base import Check
from sentinel.checks.context import corpus_paper_ids, load_paper_contexts, load_trial_contexts
from sentinel.log import get_logger
from sentinel.models import check_run, finding, reference
from sentinel.schemas import Finding

log = get_logger(__name__)


@dataclass
class RunResult:
    run_id: int
    counts: dict[str, dict[str, int]]


def run_checks(
    engine: Engine,
    checks: list[Check],
    *,
    config_hash: str,
    corpus_label: str | None,
    nct_ids: list[str] | None = None,
    paper_ids: list[int] | None = None,
    limit: int | None = None,
    paper_batch: int = 100,
) -> RunResult:
    """Run trial checks over stored trials and paper checks over the papers of
    `corpus_label` (or `paper_ids`), recording one check_run.

    Paper checks with nothing to run on raise instead of recording an empty
    run: a mistyped corpus label must not look like a clean corpus.
    """
    if any(c.subject == "paper" for c in checks) and paper_ids is None:
        if corpus_label is None:
            raise ValueError("paper checks need a corpus label or paper IDs")
        with engine.connect() as conn:
            paper_ids = corpus_paper_ids(conn, corpus_label, limit)
        if not paper_ids:
            raise ValueError(f"corpus {corpus_label!r} has no harvested papers")
    with engine.begin() as conn:
        run_id = conn.execute(
            insert(check_run)
            .values(
                config_hash=config_hash,
                checks=[c.check_id for c in checks],
                corpus_label=corpus_label,
            )
            .returning(check_run.c.id)
        ).scalar_one()
    log.info("check_run_start", run_id=run_id, checks=[c.check_id for c in checks])

    try:
        counts: dict[str, Counter] = {c.check_id: Counter() for c in checks}
        seconds: Counter = Counter()
        trial_checks = [c for c in checks if c.subject == "trial"]
        paper_checks = [c for c in checks if c.subject == "paper"]
        n_trials = n_papers = 0

        def timed(check: Check, ctx) -> list[Finding]:
            start = time.perf_counter()
            try:
                return check.run(ctx)
            finally:
                seconds[check.check_id] += time.perf_counter() - start

        def record(conn, findings: list[Finding]) -> None:
            for f in findings:
                counts[f.check_id][f.status] += 1
            if findings:
                conn.execute(insert(finding), [{**f.model_dump(), "run_id": run_id} for f in findings])

        if trial_checks:
            with engine.begin() as conn:
                start = time.perf_counter()
                contexts = load_trial_contexts(conn, nct_ids, limit)
                seconds["load"] += time.perf_counter() - start
                n_trials = len(contexts)
                for ctx in contexts:
                    record(conn, [f for check in trial_checks for f in timed(check, ctx)])

        cascade = None
        if paper_checks:
            for i in range(0, len(paper_ids), paper_batch):
                with engine.begin() as conn:
                    start = time.perf_counter()
                    contexts = load_paper_contexts(conn, paper_ids[i:i + paper_batch])
                    seconds["load"] += time.perf_counter() - start
                    for ctx in contexts:
                        record(conn, [f for check in paper_checks for f in timed(check, ctx)])
            n_papers = len(paper_ids)
            with engine.connect() as conn:
                cascade = cascade_snapshot(conn, paper_ids)

        result_counts = {k: dict(v) for k, v in counts.items()}
        _finish(engine, run_id, "done",
                {**result_counts, "trials": n_trials, "papers": n_papers,
                 "seconds": {k: round(v, 3) for k, v in seconds.items()}, "cascade": cascade})
    except Exception:
        _finish(engine, run_id, "failed", None)
        log.exception("check_run_failed", run_id=run_id)
        raise

    log.info("check_run_done", run_id=run_id, trials=n_trials, papers=n_papers, counts=result_counts)
    return RunResult(run_id=run_id, counts=result_counts)


def cascade_snapshot(conn: Connection, paper_ids: list[int]) -> dict[str, dict[str, int]]:
    """How the papers' references stand in the cascade (§9.5): counts by
    status, by the method that resolved them, and by the candidate pass that
    found a title match (stored works, or fetched from Crossref/OpenAlex)."""
    last_pass = reference.c.resolution_evidence["stages"][-1]["pass"].astext
    by_status: Counter = Counter()
    by_method: Counter = Counter()
    by_pass: Counter = Counter()
    for status, method, pass_, n in conn.execute(
        select(reference.c.resolution_status, reference.c.resolution_method, last_pass,
               func.count())
        .where(reference.c.citing_paper_id.in_(paper_ids))
        .group_by(reference.c.resolution_status, reference.c.resolution_method, last_pass)
    ):
        by_status[status] += n
        if status == "resolved":
            by_method[method] += n
            if pass_:
                by_pass[pass_] += n
    return {"by_status": dict(by_status), "by_method": dict(by_method), "by_pass": dict(by_pass)}


def _finish(engine: Engine, run_id: int, status: str, counts: dict | None) -> None:
    with engine.begin() as conn:
        conn.execute(
            update(check_run)
            .where(check_run.c.id == run_id)
            .values(status=status, finished_at=func.now(), counts=counts)
        )
