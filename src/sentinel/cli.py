"""Command-line entry point (sentinel-project.md §9.8)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Optional

import typer

from sentinel.config import Settings, get_settings
from sentinel.db import get_engine
from sentinel.ingest.base import Harvester
from sentinel.ingest.clinicaltrials import ClinicalTrialsClient
from sentinel.ingest.http import RateLimitedHTTP, build_http_client
from sentinel.ingest.retraction_watch import RetractionWatchHarvester
from sentinel.log import configure_logging

app = typer.Typer(no_args_is_help=True, add_completion=False)

HARVESTERS: dict[str, Callable[[Settings], Harvester]] = {
    "retraction-watch": lambda s: RetractionWatchHarvester(s.retraction_watch_csv_path),
}
LOOKUPS = ("clinicaltrials",)


@app.callback()
def main() -> None:
    """Sentinel: audit biomedical papers and trials for integrity problems."""
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)


@app.command()
def ingest(
    source: Annotated[
        str, typer.Argument(help=f"One of: {', '.join([*HARVESTERS, *LOOKUPS])}")
    ],
    limit: Annotated[Optional[int], typer.Option(help="Stop after N records")] = None,
    resume: Annotated[bool, typer.Option(help="Continue from the stored cursor")] = False,
    nct: Annotated[
        Optional[list[str]], typer.Option(help="NCT ID to fetch (clinicaltrials; repeatable)")
    ] = None,
) -> None:
    """Harvest a source, or fetch records by ID from a lookup source."""
    settings = get_settings()
    if source == "clinicaltrials":
        if not nct:
            raise typer.BadParameter("clinicaltrials needs at least one --nct")
        _fetch_trials(settings, nct)
        return
    if source not in HARVESTERS:
        raise typer.BadParameter(
            f"unknown source {source!r}; choose from {[*HARVESTERS, *LOOKUPS]}"
        )
    stats = HARVESTERS[source](settings).run(
        get_engine(), resume=resume, limit=limit, batch_size=settings.ingest_batch_size
    )
    typer.echo(
        f"{source}: seen {stats.seen}, inserted {stats.inserted}, "
        f"updated {stats.updated}, unchanged {stats.unchanged}, failed {stats.failed}"
    )


def _fetch_trials(settings: Settings, nct_ids: list[str]) -> None:
    with build_http_client(settings) as client:
        http = RateLimitedHTTP(
            client,
            source="clinicaltrials",
            rate_per_s=settings.rate_clinicaltrials_rps,
            max_retries=settings.http_max_retries,
            backoff_base_s=settings.http_backoff_base_s,
        )
        trials = ClinicalTrialsClient(
            http,
            base_url=settings.clinicaltrials_base_url,
            drop_paths=settings.trial_drop_paths_list,
            strip_personal_fields=settings.strip_personal_fields,
        )
        for nct_id in nct_ids:
            record = trials.lookup(get_engine(), nct_id)
            if record is None:
                typer.echo(f"{nct_id}: not found")
            else:
                typer.echo(f"{record.nct_id}: stored, {len(record.outcomes)} outcomes")


if __name__ == "__main__":
    app()
