"""CLI behaviour that doesn't need a database or network."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from sentinel import cli
from sentinel.config import get_settings
from sentinel.ingest.clinicaltrials import ClinicalTrialsClient
from sentinel.ingest.http import RetryableHTTPError
from tests.conftest import REQUIRED_ENV

runner = CliRunner()


@pytest.fixture
def env(clean_env):
    for key, value in REQUIRED_ENV.items():
        clean_env.setenv(key, value)
    get_settings.cache_clear()
    yield clean_env
    get_settings.cache_clear()


def test_bad_nct_id_is_rejected_before_any_fetch(env, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(ClinicalTrialsClient, "lookup", lambda self, e, n: calls.append(n))
    result = runner.invoke(
        cli.app, ["ingest", "clinicaltrials", "--nct", "NCT02016378", "--nct", "BADID"]
    )
    assert result.exit_code == 2
    assert "BADID" in result.output
    assert calls == []


def test_one_failed_study_does_not_stop_the_rest(env, monkeypatch):
    calls: list[str] = []

    def lookup(self, engine, nct_id):
        calls.append(nct_id)
        if nct_id == "NCT00000002":
            import httpx

            raise RetryableHTTPError(
                httpx.Response(503, request=httpx.Request("GET", "https://x.test"))
            )
        return None

    monkeypatch.setattr(ClinicalTrialsClient, "lookup", lookup)
    monkeypatch.setattr(cli, "get_engine", lambda: None)
    result = runner.invoke(
        cli.app,
        ["ingest", "clinicaltrials", "--nct", "NCT00000001", "--nct", "NCT00000002",
         "--nct", "NCT00000003"],
    )
    assert calls == ["NCT00000001", "NCT00000002", "NCT00000003"]
    assert "NCT00000002: failed (RetryableHTTPError)" in result.output
    assert result.exit_code == 1


def test_invalid_log_level_fails_at_startup(env):
    from pydantic import ValidationError

    from sentinel.config import Settings

    env.setenv("SENTINEL_LOG_LEVEL", "VERBOSE")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
