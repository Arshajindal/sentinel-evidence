"""Check ID -> check, built from settings. Only implemented checks appear."""

from __future__ import annotations

from collections.abc import Callable

from sentinel.checks.base import Check
from sentinel.checks.c05_results_reporting import C05ResultsReporting
from sentinel.config import Settings

REGISTRY: dict[str, Callable[[Settings], Check]] = {
    "C05": lambda s: C05ResultsReporting(grace_months=s.c05_grace_months),
}


def build_checks(settings: Settings, requested: list[str] | None = None) -> list[Check]:
    """Checks to run: `requested` (or all enabled), limited to enabled ones.

    Raises ValueError for a requested check that is disabled or not built yet.
    """
    enabled = settings.checks_enabled_list
    if requested is None:
        ids = [c for c in enabled if c in REGISTRY]
    else:
        disabled = [c for c in requested if c not in enabled]
        if disabled:
            raise ValueError(f"not in SENTINEL_CHECKS_ENABLED: {disabled}")
        missing = [c for c in requested if c not in REGISTRY]
        if missing:
            raise ValueError(f"not implemented yet: {missing}")
        ids = requested
    return [REGISTRY[c](settings) for c in ids]
