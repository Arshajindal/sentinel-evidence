"""C02 — retraction and concern status (sentinel-project.md §9.7; ADR-017, ADR-036).

For each cited work with a Retraction Watch notice, the band depends on when
the citing paper appeared relative to the retraction. G = C02_GRACE_MONTHS:

  published > G whole months after the retraction    high   cited after retraction
  published 0..G months after                         low    retracted while in press
  published on or before the retraction date          low    cited before retraction
  retraction marked 'Retract and Replace' or
    'Withdrawn as Out of Date', any timing            low    a current version exists
  expression of concern, any timing                   medium
  retraction later reversed by a reinstatement        not flagged
  citing date or retraction date unknown              insufficient_data

Corrections are listed in the summary's evidence but never flagged.
"""

from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Any

from sentinel.checks.base import Check
from sentinel.checks.c05_results_reporting import months_between
from sentinel.checks.context import NoticeContext, PaperContext, ReferenceContext
from sentinel.schemas import Finding

REPLACED_REASONS = ("retract and replace", "withdrawn as out of date")


def _replaced(notice: NoticeContext) -> bool:
    reason = (notice.reason or "").lower()
    return any(r in reason for r in REPLACED_REASONS)


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


class C02Retraction(Check):
    check_id = "C02"
    check_ver = "1.0.0"
    subject = "paper"

    def __init__(self, grace_months: int) -> None:
        self.grace_months = grace_months

    def run(self, ctx: PaperContext) -> list[Finding]:
        findings: list[Finding] = []
        bands: Counter = Counter()
        listed: list[dict[str, Any]] = []
        checkable = 0
        for ref in ctx.references:
            if ref.parsed_doi or ref.parsed_pmid or ref.resolved_paper_id:
                checkable += 1
            if not ref.notices:
                continue
            band, f = self._assess(ctx, ref)
            bands[band] += 1
            listed.append({"reference_id": ref.reference_id, "ordinal": ref.ordinal, "band": band})
            if f is not None:
                findings.append(f)
        findings.append(self._summary(ctx, len(ctx.references), checkable, bands, listed))
        return findings

    def _assess(self, ctx: PaperContext, ref: ReferenceContext) -> tuple[str, Finding | None]:
        retractions = [n for n in ref.notices if n.notice_type == "retraction"]
        concerns = [n for n in ref.notices if n.notice_type == "expression_of_concern"]
        label = f"Reference {ref.ordinal}" if ref.ordinal is not None else "A reference"
        evidence: dict[str, Any] = {
            "reference_id": ref.reference_id,
            "ordinal": ref.ordinal,
            "raw_string": ref.raw_string,
            "notices": [{"type": n.notice_type, "date": _iso(n.notice_date), "reason": n.reason,
                         "notice_doi": n.notice_doi, "source": n.source,
                         "source_record_id": n.source_record_id, "matched_on": n.matched_on}
                        for n in ref.notices],
            "citing_publication_date": _iso(ctx.publication_date),
            "grace_months": self.grace_months,
        }

        def finding(band: str, status: str, severity: str, summary: str, **extra) -> tuple[str, Finding]:
            return band, Finding(paper_id=ctx.paper_id, check_id=self.check_id,
                                 check_ver=self.check_ver, severity=severity, status=status,
                                 summary=summary, evidence={**evidence, "band": band, **extra})

        if retractions:
            dated = [n for n in retractions if n.notice_date]
            first = min(dated, key=lambda n: n.notice_date) if dated else None
            reinstated = first is not None and any(
                n.notice_type == "reinstatement" and n.notice_date and n.notice_date >= first.notice_date
                for n in ref.notices)
            if reinstated:
                return "reinstated", None
            if all(_replaced(n) for n in retractions):
                when = f" ({_iso(first.notice_date)})" if first else ""
                return finding("replaced", "flagged", "low",
                               f"{label} cites a work that was retracted and replaced or withdrawn "
                               f"as out of date{when}; the current version should be checked.")
            if first is None:
                return finding("retraction_date_unknown", "insufficient_data", "info",
                               f"{label} cites a retracted work; the retraction date is unknown.")
            if ctx.publication_date is None:
                return finding("citing_date_unknown", "insufficient_data", "info",
                               f"{label} cites a work retracted on {_iso(first.notice_date)}; "
                               "this paper's publication date is unknown.")
            notice_date = first.notice_date
            if ctx.publication_date <= notice_date:
                return finding("before", "flagged", "low",
                               f"{label} was cited before its retraction ({_iso(notice_date)}); "
                               "downstream claims may need review.", gap_months=None)
            gap = months_between(notice_date, ctx.publication_date)
            if gap > self.grace_months:
                return finding("after_grace", "flagged", "high",
                               f"{label} cites a work retracted on {_iso(notice_date)}, "
                               f"{gap} months before this paper was published.", gap_months=gap)
            return finding("in_press", "flagged", "low",
                           f"{label} cites a work retracted on {_iso(notice_date)}, while this "
                           f"paper was in press ({gap} months before publication).", gap_months=gap)

        if concerns:
            first = min(concerns, key=lambda n: n.notice_date or date.max)
            when = f" ({_iso(first.notice_date)})" if first.notice_date else ""
            return finding("expression_of_concern", "flagged", "medium",
                           f"{label} cites a work with an expression of concern{when}.")
        if any(n.notice_type == "correction" for n in ref.notices):
            return "corrected", None
        return "other_notice", None

    def _summary(self, ctx: PaperContext, total: int, checkable: int, bands: Counter,
                 listed: list[dict[str, Any]]) -> Finding:
        evidence = {"references": total, "checkable": checkable, "bands": dict(bands),
                    "affected": listed, "grace_months": self.grace_months,
                    "citing_publication_date": _iso(ctx.publication_date)}
        if total == 0:
            status, summary = "insufficient_data", "No reference list available."
        elif checkable == 0:
            status, summary = "insufficient_data", "No reference has a DOI, PMID or match to check."
        else:
            status = "clean"
            words = {"after_grace": "cited after retraction", "in_press": "retracted while in press",
                     "before": "cited before retraction", "replaced": "retracted and replaced",
                     "expression_of_concern": "expression of concern", "reinstated": "reinstated",
                     "corrected": "corrected", "retraction_date_unknown": "retraction date unknown",
                     "citing_date_unknown": "publication date unknown"}
            parts = [f"{n} {words.get(b, b)}" for b, n in sorted(bands.items())]
            summary = (f"{checkable} of {total} references checked against Retraction Watch: "
                       + ("; ".join(parts) if parts else "no notices") + ".")
        return Finding(paper_id=ctx.paper_id, check_id=self.check_id, check_ver=self.check_ver,
                       severity="info", status=status, summary=summary, evidence=evidence)
