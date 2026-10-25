"""Command-line entry point (sentinel-project.md §9.8)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Optional

import httpx
import typer

from sentinel.checks.registry import build_checks
from sentinel.checks.runner import run_checks
from sentinel.config import Settings, get_settings
from sentinel.db import get_engine
from sentinel.ingest.base import Harvester
from sentinel.ingest.clinicaltrials import ClinicalTrialsClient, normalise_nct_id
from sentinel.ingest.europepmc import EuropePMC, EuropePMCHarvester, select_corpus
from sentinel.ingest.http import RateLimitedHTTP, RetryableHTTPError, build_http_client
from sentinel.ingest.retraction_watch import RetractionWatchHarvester
from sentinel.log import configure_logging, get_logger
from sentinel.report.card import detect_subject, load_trial_card, render_json, render_text

app = typer.Typer(no_args_is_help=True, add_completion=False)
corpus_app = typer.Typer(no_args_is_help=True, help="Select and inspect working corpora.")
app.add_typer(corpus_app, name="corpus")
log = get_logger(__name__)

HARVESTERS: dict[str, Callable[[Settings], Harvester]] = {
    "retraction-watch": lambda s: RetractionWatchHarvester(s.retraction_watch_csv_path),
}
LOOKUPS = ("clinicaltrials",)
CORPUS_SOURCES = ("europepmc",)
ALL_SOURCES = [*HARVESTERS, *LOOKUPS, *CORPUS_SOURCES]


def _rate_limited(settings: Settings, client: httpx.Client, source: str, rps: float) -> RateLimitedHTTP:
    return RateLimitedHTTP(
        client,
        source=source,
        rate_per_s=rps,
        max_retries=settings.http_max_retries,
        backoff_base_s=settings.http_backoff_base_s,
    )


def _trials_client(settings: Settings, client: httpx.Client) -> ClinicalTrialsClient:
    return ClinicalTrialsClient(
        _rate_limited(settings, client, "clinicaltrials", settings.rate_clinicaltrials_rps),
        base_url=settings.clinicaltrials_base_url,
        drop_paths=settings.trial_drop_paths_list,
        strip_personal_fields=settings.strip_personal_fields,
    )


def _europepmc(settings: Settings, client: httpx.Client) -> EuropePMC:
    return EuropePMC(
        _rate_limited(settings, client, "europepmc", settings.rate_europepmc_rps),
        base_url=settings.europepmc_base_url,
        annotations_url=settings.europepmc_annotations_base_url,
    )


@app.callback()
def main() -> None:
    """Sentinel: audit biomedical papers and trials for integrity problems."""
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)


@corpus_app.command("select")
def corpus_select(
    n: Annotated[int, typer.Option(help="Papers to sample")] = 1000,
    label: Annotated[Optional[str], typer.Option(help="Corpus label")] = None,
) -> None:
    """Sample N papers from SENTINEL_CORPUS_QUERY with SENTINEL_RANDOM_SEED."""
    settings = get_settings()
    label = label or settings.default_corpus_label
    with build_http_client(settings) as client:
        try:
            sel = select_corpus(
                get_engine(), _europepmc(settings, client),
                query=settings.corpus_query, seed=settings.random_seed, n=n, label=label,
            )
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"corpus {sel.label}: sampled {sel.n} of {sel.hit_count} hits "
               f"(seed {settings.random_seed})")


@app.command()
def ingest(
    source: Annotated[str, typer.Argument(help=f"One of: {', '.join(ALL_SOURCES)}")],
    limit: Annotated[Optional[int], typer.Option(help="Stop after N records")] = None,
    resume: Annotated[bool, typer.Option(help="Continue from the stored cursor")] = False,
    nct: Annotated[
        Optional[list[str]], typer.Option(help="NCT ID to fetch (clinicaltrials; repeatable)")
    ] = None,
    corpus: Annotated[
        Optional[str], typer.Option(help="Corpus label to harvest (europepmc)")
    ] = None,
) -> None:
    """Harvest a source, or fetch records by ID from a lookup source."""
    settings = get_settings()
    if source == "clinicaltrials":
        if not nct:
            raise typer.BadParameter("clinicaltrials needs at least one --nct")
        _fetch_trials(settings, nct)
        return
    if source == "europepmc":
        label = corpus or settings.default_corpus_label
        with build_http_client(settings) as client:
            try:
                harvester = EuropePMCHarvester(
                    get_engine(), _europepmc(settings, client),
                    _trials_client(settings, client), label=label,
                )
            except ValueError as exc:
                raise typer.BadParameter(str(exc)) from exc
            # Small batches: each one waits on ~25 network calls.
            stats = harvester.run(get_engine(), resume=resume, limit=limit, batch_size=25)
        _echo_stats(source, stats)
        if harvester.not_found:
            typer.echo(f"trials not in the registry (not linked): {sorted(harvester.not_found)}")
        if harvester.no_fulltext:
            typer.echo(f"no full text, stored without references: {sorted(harvester.no_fulltext)}")
        if harvester.fulltext_failed:
            typer.echo(f"full text failed upstream, stored without references "
                       f"(re-run to retry): {sorted(harvester.fulltext_failed)}")
        return
    if source not in HARVESTERS:
        raise typer.BadParameter(f"unknown source {source!r}; choose from {ALL_SOURCES}")
    stats = HARVESTERS[source](settings).run(
        get_engine(), resume=resume, limit=limit, batch_size=settings.ingest_batch_size
    )
    _echo_stats(source, stats)


def _echo_stats(source: str, stats) -> None:
    typer.echo(
        f"{source}: seen {stats.seen}, inserted {stats.inserted}, "
        f"updated {stats.updated}, unchanged {stats.unchanged}, failed {stats.failed}"
    )


def _fetch_trials(settings: Settings, raw_ids: list[str]) -> None:
    """Fetch each study. One failure is logged and skipped, not fatal to the
    rest; the command exits non-zero if any study failed."""
    nct_ids, bad = [], []
    for value in raw_ids:
        try:
            nct_ids.append(normalise_nct_id(value))
        except ValueError:
            bad.append(value)
    if bad:
        raise typer.BadParameter(f"not NCT IDs: {bad}", param_hint="--nct")

    failed = 0
    with build_http_client(settings) as client:
        trials = _trials_client(settings, client)
        for nct_id in nct_ids:
            try:
                record = trials.lookup(get_engine(), nct_id)
            except (httpx.HTTPError, RetryableHTTPError, ValueError) as exc:
                # ValueError covers pydantic ValidationError and bad dates.
                failed += 1
                log.error("trial_failed", nct_id=nct_id, error=type(exc).__name__)
                typer.echo(f"{nct_id}: failed ({type(exc).__name__})")
                continue
            if record is None:
                typer.echo(f"{nct_id}: not found")
            else:
                typer.echo(f"{record.nct_id}: stored, {len(record.outcomes)} outcomes")
    if failed:
        raise typer.Exit(code=1)


@app.command()
def check(
    checks: Annotated[
        Optional[str], typer.Option(help="Comma-separated check IDs, e.g. C05")
    ] = None,
    limit: Annotated[Optional[int], typer.Option(help="Stop after N subjects")] = None,
    nct: Annotated[
        Optional[list[str]], typer.Option(help="Only these trials (repeatable)")
    ] = None,
) -> None:
    """Run checks over stored data and record a check_run."""
    settings = get_settings()
    requested = [c.strip().upper() for c in checks.split(",")] if checks else None
    try:
        built = build_checks(settings, requested)
        nct_ids = [normalise_nct_id(n) for n in nct] if nct else None
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if not built:
        raise typer.BadParameter("no implemented checks are enabled")
    result = run_checks(
        get_engine(),
        built,
        config_hash=settings.config_hash,
        corpus_label=settings.default_corpus_label,
        nct_ids=nct_ids,
        limit=limit,
    )
    typer.echo(f"run {result.run_id}: {result.counts}")


@app.command()
def report(
    subject: Annotated[str, typer.Argument(help="NCT ID, DOI or paper ID")],
    format: Annotated[str, typer.Option(help="text or json")] = "text",
) -> None:
    """Print the report card for a trial (papers arrive in later sessions)."""
    if format not in ("text", "json"):
        raise typer.BadParameter("format must be text or json", param_hint="--format")
    try:
        kind, value = detect_subject(subject)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if kind != "trial":
        typer.echo(f"Report cards for papers aren't built yet ({kind} {value}).", err=True)
        raise typer.Exit(code=2)
    with get_engine().connect() as conn:
        card = load_trial_card(conn, value)
    if card is None:
        typer.echo(f"{value} is not in the database. Fetch it with "
                   f"`sentinel ingest clinicaltrials --nct {value}`.", err=True)
        raise typer.Exit(code=1)
    typer.echo(render_json(card) if format == "json" else render_text(card))


if __name__ == "__main__":
    app()
