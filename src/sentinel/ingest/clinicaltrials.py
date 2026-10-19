"""ClinicalTrials.gov lookup client: one study at a time (ADR-023).

Endpoint: GET {SENTINEL_CLINICALTRIALS_BASE_URL}/studies/{nctId}, confirmed
against the live API v2.0.5. Unknown IDs return 404; an ID listed as an alias
returns a 301 to the real study, so the stored nct_id comes from the body.

Personal data: every path in SENTINEL_TRIAL_DROP_PATHS is removed in
normalise(), before any field is read or anything is written (ADR-018,
ADR-025).
"""

from __future__ import annotations

import calendar
import copy
import re
from datetime import date
from typing import Any

from sqlalchemy import Connection, Engine, delete, func
from sqlalchemy.dialects.postgresql import insert

from sentinel.ingest.base import LookupClient
from sentinel.ingest.http import RateLimitedHTTP
from sentinel.log import get_logger
from sentinel.models import trial, trial_outcome
from sentinel.schemas import TrialOutcome, TrialRecord

log = get_logger(__name__)

NCT_RE = re.compile(r"^NCT\d{8}$")

OUTCOME_KEYS = {
    "primaryOutcomes": "primary",
    "secondaryOutcomes": "secondary",
    "otherOutcomes": "other",
}


class ClinicalTrialsClient(LookupClient):
    source = "clinicaltrials"

    def __init__(
        self,
        http: RateLimitedHTTP,
        *,
        base_url: str,
        drop_paths: list[str],
        strip_personal_fields: bool,
    ) -> None:
        if not strip_personal_fields:
            raise ValueError(
                "SENTINEL_STRIP_PERSONAL_FIELDS is false; storing personal data "
                "from trial records is not supported (ADR-018)"
            )
        super().__init__(http)
        self.base_url = base_url.rstrip("/")
        self.drop_paths = drop_paths

    def fetch_study(self, nct_id: str) -> dict | None:
        """The raw study JSON, or None if the registry has no such study."""
        nct_id = normalise_nct_id(nct_id)
        response = self.http.get(f"{self.base_url}/studies/{nct_id}", params={"format": "json"})
        if response.status_code == 404:
            log.info("trial_not_found", source=self.source, nct_id=nct_id)
            return None
        response.raise_for_status()
        return response.json()

    def normalise(self, raw: dict) -> TrialRecord:
        stripped = drop_paths(raw, self.drop_paths)
        protocol = stripped.get("protocolSection") or {}
        ident = protocol.get("identificationModule") or {}
        status = protocol.get("statusModule") or {}
        design = protocol.get("designModule") or {}
        sponsor = (protocol.get("sponsorCollaboratorsModule") or {}).get("leadSponsor") or {}

        version_date = _struct_date(status, "lastUpdatePostDateStruct")
        phases = design.get("phases") or []
        return TrialRecord(
            nct_id=ident.get("nctId"),
            brief_title=ident.get("briefTitle"),
            overall_status=status.get("overallStatus"),
            study_type=design.get("studyType"),
            phase=",".join(phases) or None,
            enrollment=(design.get("enrollmentInfo") or {}).get("count"),
            start_date=_struct_date(status, "startDateStruct"),
            primary_completion_date=_struct_date(status, "primaryCompletionDateStruct"),
            completion_date=_struct_date(status, "completionDateStruct"),
            results_first_posted=_struct_date(status, "resultsFirstPostDateStruct"),
            lead_sponsor=sponsor.get("name"),
            sponsor_class=sponsor.get("class"),
            raw=stripped,
            outcomes=_outcomes(protocol.get("outcomesModule") or {}, version_date, ident.get("nctId")),
        )

    def upsert(self, conn: Connection, record: TrialRecord) -> None:
        """Upsert the trial and replace its version-0 outcomes.

        Version 0 is defined as the current record as last fetched (§8), so it
        is replaced wholesale; registry history (versions 1..n) is never touched.
        """
        values = record.model_dump(exclude={"outcomes"})
        stmt = insert(trial).values(**values)
        conn.execute(
            stmt.on_conflict_do_update(
                index_elements=[trial.c.nct_id],
                set_={**{c: stmt.excluded[c] for c in values if c != "nct_id"},
                      "ingested_at": func.now()},
            )
        )
        conn.execute(
            delete(trial_outcome).where(
                trial_outcome.c.nct_id == record.nct_id, trial_outcome.c.version_no == 0
            )
        )
        if record.outcomes:
            conn.execute(
                insert(trial_outcome),
                [{"nct_id": record.nct_id, **o.model_dump()} for o in record.outcomes],
            )

    def lookup(self, engine: Engine, nct_id: str) -> TrialRecord | None:
        """Fetch, normalise and store one study. Returns None if not found."""
        raw = self.fetch_study(nct_id)
        if raw is None:
            return None
        record = self.normalise(raw)
        with engine.begin() as conn:
            self.upsert(conn, record)
        log.info(
            "trial_stored",
            source=self.source,
            nct_id=record.nct_id,
            requested=normalise_nct_id(nct_id),
            outcomes=len(record.outcomes),
        )
        return record


def normalise_nct_id(value: str) -> str:
    nct_id = value.strip().upper()
    if not NCT_RE.match(nct_id):
        raise ValueError(f"not an NCT ID: {value!r}")
    return nct_id


def drop_paths(raw: dict, paths: list[str]) -> dict:
    """A deep copy of `raw` with each dotted path removed.

    `a.b[].c` removes `c` from every element of the list at `a.b`.
    """
    result = copy.deepcopy(raw)
    for path in paths:
        _drop(result, path.split("."))
    return result


def _drop(node: Any, segments: list[str]) -> None:
    if not isinstance(node, dict) or not segments:
        return
    head, rest = segments[0], segments[1:]
    if head.endswith("[]"):
        items = node.get(head[:-2])
        if isinstance(items, list):
            for item in items:
                _drop(item, rest)
    elif not rest:
        node.pop(head, None)
    else:
        _drop(node.get(head), rest)


def parse_registry_date(value: str | None) -> date | None:
    """Registry dates are YYYY-MM-DD, YYYY-MM or YYYY.

    Partial dates resolve to the latest day they could mean (ADR-026), so a
    date-based check can never fire early.
    """
    if not value:
        return None
    parts = value.strip().split("-")
    if not all(p.isdigit() for p in parts) or len(parts) not in (1, 2, 3):
        raise ValueError(f"unrecognised registry date {value!r}")
    year = int(parts[0])
    if len(parts) == 3:
        return date(year, int(parts[1]), int(parts[2]))
    month = int(parts[1]) if len(parts) == 2 else 12
    return date(year, month, calendar.monthrange(year, month)[1])


def _struct_date(status: dict, key: str) -> date | None:
    return parse_registry_date((status.get(key) or {}).get("date"))


def _outcomes(module: dict, version_date: date | None, nct_id: str | None) -> list[TrialOutcome]:
    outcomes: list[TrialOutcome] = []
    seen: set[tuple[str, str]] = set()
    duplicates = 0
    for key, outcome_type in OUTCOME_KEYS.items():
        for item in module.get(key) or []:
            measure = (item.get("measure") or "").strip()
            # (type, measure) is unique per version in trial_outcome. Keep the
            # first; the full list stays in raw.
            if (outcome_type, measure) in seen:
                duplicates += 1
                continue
            seen.add((outcome_type, measure))
            outcomes.append(
                TrialOutcome(
                    version_no=0,
                    version_date=version_date,
                    outcome_type=outcome_type,
                    measure=measure,
                    description=item.get("description"),
                    time_frame=item.get("timeFrame"),
                )
            )
    if duplicates:
        log.warning("duplicate_outcome_measures", nct_id=nct_id, skipped=duplicates)
    return outcomes
