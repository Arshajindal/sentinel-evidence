"""Runtime configuration, loaded from the environment (SENTINEL_ prefix).

ADR-022: settings that change a check's findings have no default and fail
startup if unset. Operational settings (batching, timeouts, pool size, log
level, endpoint URLs, ...) may default, since they change speed, not answers.
`config_hash` is computed over the finding-affecting group only.
"""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

PREFIX = "SENTINEL_"


class _OnlySentinelKeys(PydanticBaseSettingsSource):
    """The `.env` source, keeping only SENTINEL_ keys.

    `.env` is shared with other tools (e.g. TEST_DATABASE_URL for the test
    suite). Their keys are ignored; an unknown SENTINEL_ key still reaches
    validation, so a typo like SENTINEL_LOG_LEVL fails at startup.
    """

    def __init__(self, settings_cls: type[BaseSettings], inner: PydanticBaseSettingsSource) -> None:
        super().__init__(settings_cls)
        self.inner = inner

    def get_field_value(self, field, field_name):  # values come from __call__
        return None, field_name, False

    def __call__(self) -> dict:
        prefix = PREFIX.lower()
        return {k: v for k, v in self.inner().items()
                if k in self.settings_cls.model_fields or k.lower().startswith(prefix)}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix=PREFIX,
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
    )

    @classmethod
    def settings_customise_sources(cls, settings_cls, init_settings, env_settings,
                                   dotenv_settings, file_secret_settings):
        return (init_settings, env_settings, _OnlySentinelKeys(settings_cls, dotenv_settings),
                file_secret_settings)

    # --- Core ------------------------------------------------------------
    env: str = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "console"] = "json"
    random_seed: int  # no default — ADR-022

    # --- Database ----------------------------------------------------------
    database_url: str  # no default — ADR-022
    db_pool_size: int = 10
    db_max_overflow: int = 5
    db_statement_timeout_ms: int = 60_000
    db_echo: bool = False

    # --- Polite pool identity ------------------------------------------------
    contact_email: str  # no default — ADR-022
    user_agent: str  # no default — ADR-022

    # --- Optional credentials ---------------------------------------------
    crossref_plus_token: str = ""
    ncbi_api_key: str = ""
    openalex_api_key: str = ""

    # --- Source endpoints ---------------------------------------------------
    crossref_base_url: str = "https://api.crossref.org"
    openalex_base_url: str = "https://api.openalex.org"
    europepmc_base_url: str = "https://www.ebi.ac.uk/europepmc/webservices/rest"
    europepmc_annotations_base_url: str = "https://www.ebi.ac.uk/europepmc/annotations_api"
    clinicaltrials_base_url: str = "https://clinicaltrials.gov/api/v2"
    ncbi_eutils_base_url: str = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    doi_handle_base_url: str = "https://doi.org/api/handles"

    # --- HTTP client ---------------------------------------------------------
    http_timeout_s: int = 30
    http_max_retries: int = 5
    http_backoff_base_s: float = 1.0
    http_max_connections: int = 20

    # --- Per-source rate limits (requests/second, token bucket) --------------
    rate_crossref_rps: float = 5
    rate_openalex_rps: float = 10
    rate_europepmc_rps: float = 5
    rate_clinicaltrials_rps: float = 5
    rate_ncbi_rps: float = 3
    rate_doi_handle_rps: float = 5
    # OpenAlex is metered (ADR-032). Operational: exhausting it stops searches
    # with an error, it never changes a finding.
    openalex_daily_budget_usd: float = 0.10

    # --- Local data files (ADR-020; Crossref/OpenAlex are API-only) ----------
    data_dir: str = "./data"
    fulltext_cache_dir: str = "./data/fulltext"  # JATS XML of corpus papers (ADR-037)
    pmc_oa_snapshot_path: str = "./data/pmc-oa/"
    retraction_watch_csv_path: str = (
        "./data/retraction-watch-data/retraction_watch.csv"
    )
    # Bulk zip is for the 10,000-paper run only (ADR-019 as amended by ADR-023).
    clinicaltrials_bulk_url: str = (
        "https://clinicaltrials.gov/api/v2/studies/download?format=json.zip"
    )
    clinicaltrials_snapshot_path: str = "./data/clinicaltrials/"
    clinicaltrials_oas_url: str = "https://clinicaltrials.gov/api/oas/v2"

    # --- Privacy filtering (ADR-018, ADR-025) ----------------------------------
    strip_personal_fields: bool  # no default — ADR-022
    trial_drop_paths: str = (
        "protocolSection.contactsLocationsModule.centralContacts,"
        "protocolSection.contactsLocationsModule.overallOfficials,"
        "protocolSection.contactsLocationsModule.locations[].contacts,"
        "protocolSection.sponsorCollaboratorsModule.responsibleParty,"
        "resultsSection.moreInfoModule.pointOfContact"
    )

    # --- Pipeline batching and concurrency ------------------------------------
    ingest_batch_size: int = 1000
    ingest_max_concurrency: int = 8
    resolve_batch_size: int = 500
    check_batch_size: int = 200

    # --- Entity resolution cascade (§9.5) — feeds config_hash ----------------
    resolve_trigram_min: float  # no default — ADR-022
    resolve_trigram_strict: float  # no default — ADR-022
    resolve_year_tolerance: int  # no default — ADR-022
    resolve_ambiguous_margin: float  # no default — ADR-022

    # --- Check parameters — feed config_hash ----------------------------------
    checks_enabled: str  # comma-separated check ids, e.g. "C01,C02,C03,C04,C05"

    c01_near_match_min: float  # no default — ADR-022 / ADR-035
    c01_same_title_min: float  # no default — ADR-022 / ADR-035
    c02_grace_months: int  # no default — ADR-022 / ADR-017
    c03_alpha: float  # no default — ADR-022
    c03_allow_one_tailed: bool  # no default — ADR-022 / ADR-009
    c04_max_n: int  # no default — ADR-022
    c04_min_decimals: int  # no default — ADR-022
    c04_epsilon: float  # no default — ADR-022
    c05_grace_months: int  # no default — ADR-022

    # --- Corpus selection (§9.2, ADR-028) — defines the corpus, no default -----
    corpus_query: str  # no default — ADR-022

    # --- Runs and output -------------------------------------------------------
    default_corpus_label: str = "dev-sample"
    export_dir: str = "./out"

    @property
    def trial_drop_paths_list(self) -> list[str]:
        return [p.strip() for p in self.trial_drop_paths.split(",") if p.strip()]

    @property
    def checks_enabled_list(self) -> list[str]:
        return [c.strip() for c in self.checks_enabled.split(",") if c.strip()]

    @property
    def config_hash(self) -> str:
        """SHA-256 over the finding-affecting settings only (ADR-022)."""
        fields = {
            "random_seed": self.random_seed,
            "checks_enabled": self.checks_enabled_list,
            "resolve_trigram_min": self.resolve_trigram_min,
            "resolve_trigram_strict": self.resolve_trigram_strict,
            "resolve_year_tolerance": self.resolve_year_tolerance,
            "resolve_ambiguous_margin": self.resolve_ambiguous_margin,
            "strip_personal_fields": self.strip_personal_fields,
            "c01_near_match_min": self.c01_near_match_min,
            "c01_same_title_min": self.c01_same_title_min,
            "c02_grace_months": self.c02_grace_months,
            "c03_alpha": self.c03_alpha,
            "c03_allow_one_tailed": self.c03_allow_one_tailed,
            "c04_max_n": self.c04_max_n,
            "c04_min_decimals": self.c04_min_decimals,
            "c04_epsilon": self.c04_epsilon,
            "c05_grace_months": self.c05_grace_months,
        }
        payload = json.dumps(fields, sort_keys=True).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
