"""Freeze the fixture set's inputs from the database (sentinel-project.md §9.9).

    python tests/fixtures/set/freeze.py            # every item in manifest.json
    python tests/fixtures/set/freeze.py PMC7052980 # just these

For each manifest item this writes `papers/<PMCID>.json` or `trials/<NCT>.json`
with the exact context the checks receive (`load_paper_contexts` /
`load_trial_contexts`). A file's hand-written `expected` and `notes` are kept
on re-freeze, so the context can be refreshed and the test shows what moved.
New items get `expected: null` until someone checks them by hand.

Trial records keep only what C05 reads plus identifiers and outcomes: site
facilities, cities and everything else in `raw` are dropped (data
minimisation; ADR-018/ADR-025 paths are already gone at ingest).

This is a developer tool: it reads the database named by SENTINEL_DATABASE_URL.
Tests never run it.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

from sqlalchemy import select

from sentinel.checks.context import load_paper_contexts, load_trial_contexts
from sentinel.db import get_engine
from sentinel.models import paper

HERE = Path(__file__).resolve().parent


def trim_trial_raw(raw: dict) -> dict:
    ps = raw.get("protocolSection") or {}
    ident = ps.get("identificationModule") or {}
    design = ps.get("designModule") or {}
    locations = (ps.get("contactsLocationsModule") or {}).get("locations") or []
    out = {"protocolSection": {
        "identificationModule": {k: ident[k] for k in ("nctId", "briefTitle") if k in ident},
        "statusModule": ps.get("statusModule") or {},
        "oversightModule": ps.get("oversightModule") or {},
        "designModule": {k: design[k] for k in ("studyType", "phases") if k in design},
        "contactsLocationsModule": {"locations": [{"country": loc.get("country")} for loc in locations]},
    }}
    if "hasResults" in raw:
        out["hasResults"] = raw["hasResults"]
    return out


def _write(path: Path, item: dict, context: dict) -> None:
    old = json.loads(path.read_text("utf-8")) if path.exists() else {}
    doc = {
        "id": item["id"],
        "subject": item["subject"],
        "categories": item["categories"],
        "source": {**{k: v for k, v in item.items() if k in ("corpus", "paper_id")},
                   "frozen_on": date.today().isoformat()},
        "notes": old.get("notes", ""),
        "expected": old.get("expected"),
        "context": context,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=1, ensure_ascii=False, sort_keys=False) + "\n", "utf-8")
    print(f"froze {path.relative_to(HERE)}")


def main(only: set[str]) -> None:
    manifest = json.loads((HERE / "manifest.json").read_text("utf-8"))
    engine = get_engine()
    with engine.connect() as conn:
        for item in manifest:
            if only and item["id"] not in only:
                continue
            if item["subject"] == "paper":
                pid = conn.execute(select(paper.c.id).where(paper.c.pmcid == item["id"])).scalar_one()
                [ctx] = load_paper_contexts(conn, [pid])
                _write(HERE / "papers" / f"{item['id']}.json", {**item, "paper_id": pid},
                       ctx.model_dump(mode="json"))
            else:
                [ctx] = load_trial_contexts(conn, [item["id"]])
                context = ctx.model_dump(mode="json")
                context["trial"]["raw"] = trim_trial_raw(context["trial"]["raw"])
                _write(HERE / "trials" / f"{item['id']}.json", item, context)


if __name__ == "__main__":
    main(set(sys.argv[1:]))
