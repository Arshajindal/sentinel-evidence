"""`sentinel export --run N --format csv|json --out PATH` (§9.8).

One row per finding of the run, with the subject's identifiers. Summaries
and evidence can quote text from papers, which is untrusted: in CSV, a cell
that a spreadsheet would read as a formula gets a leading apostrophe. JSON
keeps values exactly as stored.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from sqlalchemy import Connection, select

from sentinel.models import check_run, finding, paper

COLUMNS = ["run_id", "finding_id", "check_id", "check_ver", "subject_type", "paper_id", "pmcid",
           "doi", "nct_id", "severity", "status", "summary", "evidence"]

# Leading characters spreadsheets treat as the start of a formula (OWASP CSV injection).
FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value: object) -> object:
    if isinstance(value, str) and value.startswith(FORMULA_START):
        return "'" + value
    return value


def run_status(conn: Connection, run_id: int) -> str | None:
    return conn.execute(select(check_run.c.status).where(check_run.c.id == run_id)).scalar_one_or_none()


def _rows(conn: Connection, run_id: int):
    for r in conn.execute(
        select(finding, paper.c.pmcid, paper.c.doi)
        .outerjoin(paper, paper.c.id == finding.c.paper_id)
        .where(finding.c.run_id == run_id)
        .order_by(finding.c.check_id, finding.c.paper_id, finding.c.nct_id, finding.c.id)
    ).mappings():
        yield dict(zip(COLUMNS, (
            run_id, r["id"], r["check_id"], r["check_ver"],
            "paper" if r["paper_id"] is not None else "trial",
            r["paper_id"], r["pmcid"], r["doi"], r["nct_id"], r["severity"], r["status"],
            r["summary"], r["evidence"],
        )))


def export_findings(conn: Connection, run_id: int, out: Path, fmt: str) -> int:
    """Write the run's findings to `out` as csv or json; returns the number of rows."""
    if fmt not in ("csv", "json"):
        raise ValueError(f"unknown format {fmt!r}; choose csv or json")
    out.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "json":
        rows = list(_rows(conn, run_id))
        out.write_text(json.dumps(rows, indent=1, ensure_ascii=False) + "\n", "utf-8")
        return len(rows)
    n = 0
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(COLUMNS)
        for row in _rows(conn, run_id):
            row["evidence"] = json.dumps(row["evidence"], ensure_ascii=False, sort_keys=True)
            writer.writerow([safe_cell(row[c]) for c in COLUMNS])
            n += 1
    return n
