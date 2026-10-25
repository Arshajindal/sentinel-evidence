"""ADR-022: finding-affecting settings have no default; startup fails without them."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from sentinel.config import Settings
from tests.conftest import REQUIRED_ENV

ROOT = Path(__file__).resolve().parent.parent

# The settings ADR-022 says must be stated explicitly. Listed by hand so that
# adding a convenience default to any of them fails this suite.
EXPECTED_REQUIRED = {
    "random_seed",
    "database_url",
    "contact_email",
    "user_agent",
    "strip_personal_fields",
    "checks_enabled",
    "resolve_trigram_min",
    "resolve_trigram_strict",
    "resolve_year_tolerance",
    "resolve_ambiguous_margin",
    "c02_grace_months",
    "c03_alpha",
    "c03_allow_one_tailed",
    "c04_max_n",
    "c04_min_decimals",
    "c04_epsilon",
    "c05_grace_months",
    "corpus_query",
}


def _settings(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> Settings:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)


def test_required_fields_match_adr_022():
    required = {
        name for name, field in Settings.model_fields.items() if field.is_required()
    }
    assert required == EXPECTED_REQUIRED


def test_loads_with_all_required(clean_env):
    settings = _settings(clean_env, REQUIRED_ENV)
    assert settings.c05_grace_months == 12


@pytest.mark.parametrize("missing", sorted(REQUIRED_ENV))
def test_startup_fails_without_required_variable(clean_env, missing):
    env = {k: v for k, v in REQUIRED_ENV.items() if k != missing}
    with pytest.raises(ValidationError) as excinfo:
        _settings(clean_env, env)
    field = missing.removeprefix("SENTINEL_").lower()
    assert any(err["loc"] == (field,) for err in excinfo.value.errors())


def test_config_hash_ignores_operational_settings(clean_env):
    base = _settings(clean_env, REQUIRED_ENV).config_hash
    clean_env.setenv("SENTINEL_INGEST_BATCH_SIZE", "7")
    assert Settings(_env_file=None).config_hash == base


def test_config_hash_tracks_finding_settings(clean_env):
    base = _settings(clean_env, REQUIRED_ENV).config_hash
    clean_env.setenv("SENTINEL_C05_GRACE_MONTHS", "18")
    assert Settings(_env_file=None).config_hash != base


def test_env_example_lists_exactly_the_settings_fields():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    keys = set(re.findall(r"^SENTINEL_([A-Z0-9_]+)=", text, flags=re.MULTILINE))
    assert {k.lower() for k in keys} == set(Settings.model_fields)
