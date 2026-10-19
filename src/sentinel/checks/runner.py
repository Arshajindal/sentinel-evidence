"""Runs checks over stored data and records the results.

One `check_run` row per invocation, carrying `config_hash` (ADR-022). Every
finding is validated before it is written, and evidence is kept for clean
results too (ADR-015).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from sqlalchemy import Engine, func, insert, update

from sentinel.checks.base import Check
from sentinel.checks.context import load_trial_contexts
from sentinel.log import get_logger
from sentinel.models import check_run, finding
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
    limit: int | None = None,
) -> RunResult:
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
        trial_checks = [c for c in checks if c.subject == "trial"]
        with engine.begin() as conn:
            contexts = load_trial_contexts(conn, nct_ids, limit) if trial_checks else []
            for ctx in contexts:
                findings: list[Finding] = []
                for check in trial_checks:
                    findings.extend(check.run(ctx))
                for f in findings:
                    counts[f.check_id][f.status] += 1
                if findings:
                    conn.execute(
                        insert(finding),
                        [{**f.model_dump(), "run_id": run_id} for f in findings],
                    )
        result_counts = {k: dict(v) for k, v in counts.items()}
        _finish(engine, run_id, "done", {**result_counts, "trials": len(contexts)})
    except Exception:
        _finish(engine, run_id, "failed", None)
        log.exception("check_run_failed", run_id=run_id)
        raise

    log.info("check_run_done", run_id=run_id, trials=len(contexts), counts=result_counts)
    return RunResult(run_id=run_id, counts=result_counts)


def _finish(engine: Engine, run_id: int, status: str, counts: dict | None) -> None:
    with engine.begin() as conn:
        conn.execute(
            update(check_run)
            .where(check_run.c.id == run_id)
            .values(status=status, finished_at=func.now(), counts=counts)
        )
