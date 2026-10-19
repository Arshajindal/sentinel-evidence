"""C05 — results reporting (sentinel-project.md §9.7).

A completed interventional trial (not Early Phase 1 or Phase 1 only) whose
primary completion is more than SENTINEL_C05_GRACE_MONTHS in the past, with no
results posted on ClinicalTrials.gov, is flagged `medium`.

Months are measured to the day the registry record was fetched, the last time
`results_first_posted` was observed. Wording never says "non-compliant" or
"violation": whether a trial had to post depends on facts Sentinel can't verify.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sentinel.checks.base import Check
from sentinel.checks.context import TrialContext
from sentinel.schemas import Finding

EXCLUDED_PHASES = frozenset({"EARLY_PHASE1", "PHASE1"})


class C05ResultsReporting(Check):
    check_id = "C05"
    check_ver = "1.0.0"
    subject = "trial"

    def __init__(self, grace_months: int) -> None:
        self.grace_months = grace_months

    def run(self, ctx: TrialContext) -> list[Finding]:
        t = ctx.trial
        phases = sorted(t.phase.split(",")) if t.phase else []
        evidence: dict[str, Any] = {
            "study_type": t.study_type,
            "phase": phases,
            "overall_status": t.overall_status,
            "primary_completion_date": _iso(t.primary_completion_date),
            "results_first_posted": _iso(t.results_first_posted),
            "record_fetched_on": ctx.fetched_on.isoformat(),
            "grace_months": self.grace_months,
            "lead_sponsor": t.lead_sponsor,
            "sponsor_class": t.sponsor_class,
            "countries": _countries(t.raw),
        }

        def finding(status: str, severity: str, summary: str, reason: str) -> list[Finding]:
            return [
                Finding(
                    nct_id=t.nct_id,
                    check_id=self.check_id,
                    check_ver=self.check_ver,
                    severity=severity,
                    status=status,
                    summary=summary,
                    evidence={**evidence, "reason": reason},
                )
            ]

        if t.study_type != "INTERVENTIONAL":
            return finding("not_applicable", "info",
                           "Not an interventional study; C05 does not apply.",
                           "not_interventional")
        if not phases:
            return finding("insufficient_data", "info",
                           "Phase not recorded; cannot tell whether C05 applies.",
                           "phase_missing")
        if set(phases) <= EXCLUDED_PHASES:
            return finding("not_applicable", "info",
                           "Early Phase 1 or Phase 1 trial; C05 does not apply.",
                           "phase_excluded")
        if t.overall_status != "COMPLETED":
            return finding("not_applicable", "info",
                           f"Trial status is {t.overall_status}; C05 covers completed trials only.",
                           "not_completed")
        if t.primary_completion_date is None:
            return finding("insufficient_data", "info",
                           "Primary completion date not recorded.",
                           "primary_completion_missing")

        months = months_between(t.primary_completion_date, ctx.fetched_on)
        evidence["months_since_primary_completion"] = months

        if t.results_first_posted is not None:
            return finding("clean", "info",
                           "Results posted on ClinicalTrials.gov.",
                           "results_posted")
        if months < 0:
            return finding("clean", "info",
                           "Primary completion date is after the record was fetched.",
                           "primary_completion_after_fetch")
        if months <= self.grace_months:
            return finding("clean", "info",
                           f"Primary completion {_months(months)} ago, within the "
                           f"{self.grace_months}-month window; no results posted yet.",
                           "within_grace")
        return finding("flagged", "medium",
                       f"No results posted on ClinicalTrials.gov {_months(months)} "
                       "after primary completion.",
                       "no_results_after_grace")


def months_between(start: date, end: date) -> int:
    """Whole months from `start` to `end`; a partial month doesn't count."""
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        months -= 1
    return months


def _months(n: int) -> str:
    return f"{n} month" if n == 1 else f"{n} months"


def _countries(raw: dict) -> list[str]:
    locations = (
        (raw.get("protocolSection") or {}).get("contactsLocationsModule") or {}
    ).get("locations") or []
    return sorted({loc["country"] for loc in locations if loc.get("country")})


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value else None
