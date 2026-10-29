"""C05 — results reporting (sentinel-project.md §9.7).

A completed interventional trial (not Early Phase 1 or Phase 1 only) whose
primary completion is more than SENTINEL_C05_GRACE_MONTHS in the past, with no
results posted on ClinicalTrials.gov, is flagged `medium`.

Months are measured to the day the registry record was fetched, the last time
`results_first_posted` was observed. Wording never says "non-compliant" or
"violation": whether a trial had to post depends on facts Sentinel can't verify.

Trials outside FDAAA 801's plausible reach are out of scope (ADR-031):
- era: the statute covers trials initiated after 2007-09-27 or still ongoing
  on 2007-12-26; from registry dates, start > 2007-09-27 or completion
  >= 2007-12-26. Partial dates are read at their earliest possible day here,
  so a trial counts as covered only if every reading of its dates says so.
- reach: a US site, or the registry marks it an FDA-regulated drug or device.
  A trial with neither never had a duty to post on ClinicalTrials.gov.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sentinel.checks.base import Check
from sentinel.checks.context import TrialContext
from sentinel.schemas import Finding

EXCLUDED_PHASES = frozenset({"EARLY_PHASE1", "PHASE1"})
FDAAA_INITIATED_AFTER = date(2007, 9, 27)
FDAAA_ONGOING_ON = date(2007, 12, 26)


class C05ResultsReporting(Check):
    check_id = "C05"
    check_ver = "1.1.0"
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

        start = _earliest(t.raw, "startDateStruct")
        completion = _earliest(t.raw, "completionDateStruct")
        evidence["start_date_registered"] = _registered(t.raw, "startDateStruct")
        evidence["completion_date_registered"] = _registered(t.raw, "completionDateStruct")
        if start is None and completion is None:
            return finding("insufficient_data", "info",
                           "Start and completion dates not recorded; cannot tell whether "
                           "the trial falls in the results-reporting era.",
                           "era_dates_missing")
        covered = ((start is not None and start > FDAAA_INITIATED_AFTER)
                   or (completion is not None and completion >= FDAAA_ONGOING_ON))
        if not covered:
            return finding("not_applicable", "info",
                           "Trial started before 27 September 2007 and completed before "
                           "26 December 2007, before FDAAA results reporting; C05 does not apply.",
                           "before_fdaaa")

        oversight = (t.raw.get("protocolSection") or {}).get("oversightModule") or {}
        us_site = "United States" in evidence["countries"]
        fda_drug = oversight.get("isFdaRegulatedDrug") is True
        fda_device = oversight.get("isFdaRegulatedDevice") is True
        evidence.update(us_site=us_site, fda_regulated_drug=oversight.get("isFdaRegulatedDrug"),
                        fda_regulated_device=oversight.get("isFdaRegulatedDevice"))
        if not (us_site or fda_drug or fda_device):
            return finding("not_applicable", "info",
                           "No US site and not marked as an FDA-regulated product; "
                           "C05 does not apply.",
                           "outside_fdaaa_scope")

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


def _registered(raw: dict, key: str) -> str | None:
    """The date string exactly as the registry gave it (may be YYYY-MM)."""
    status = (raw.get("protocolSection") or {}).get("statusModule") or {}
    return (status.get(key) or {}).get("date")


def _earliest(raw: dict, key: str) -> date | None:
    """Earliest day a registry date could mean: YYYY-MM -> the 1st, YYYY -> 1 Jan."""
    value = _registered(raw, key)
    if not value:
        return None
    parts = [int(p) for p in value.split("-")]
    return date(parts[0], parts[1] if len(parts) > 1 else 1, parts[2] if len(parts) > 2 else 1)


def _countries(raw: dict) -> list[str]:
    locations = (
        (raw.get("protocolSection") or {}).get("contactsLocationsModule") or {}
    ).get("locations") or []
    return sorted({loc["country"] for loc in locations if loc.get("country")})


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value else None
