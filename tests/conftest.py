"""Shared test setup. Tests never read the developer's `.env` or call live APIs."""

from __future__ import annotations

import os

import pytest

# A complete set of the settings ADR-022 requires. Values are arbitrary but
# valid; tests that care about a value set it explicitly.
REQUIRED_ENV = {
    "SENTINEL_RANDOM_SEED": "1",
    "SENTINEL_DATABASE_URL": "postgresql+psycopg://u:p@localhost:5432/unused",
    "SENTINEL_CONTACT_EMAIL": "test@example.org",
    "SENTINEL_USER_AGENT": "sentinel-tests",
    "SENTINEL_STRIP_PERSONAL_FIELDS": "true",
    "SENTINEL_CHECKS_ENABLED": "C01,C02,C03,C04,C05",
    "SENTINEL_RESOLVE_TRIGRAM_MIN": "0.85",
    "SENTINEL_RESOLVE_TRIGRAM_STRICT": "0.92",
    "SENTINEL_RESOLVE_YEAR_TOLERANCE": "1",
    "SENTINEL_RESOLVE_AMBIGUOUS_MARGIN": "0.02",
    "SENTINEL_C02_GRACE_MONTHS": "6",
    "SENTINEL_C03_ALPHA": "0.05",
    "SENTINEL_C03_ALLOW_ONE_TAILED": "true",
    "SENTINEL_C04_MAX_N": "100",
    "SENTINEL_C04_MIN_DECIMALS": "2",
    "SENTINEL_C04_EPSILON": "1e-9",
    "SENTINEL_C05_GRACE_MONTHS": "12",
}


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Remove every SENTINEL_ variable inherited from the shell."""
    for key in list(os.environ):
        if key.startswith("SENTINEL_"):
            monkeypatch.delenv(key)
    return monkeypatch
