"""Command-line entry point (sentinel-project.md §9.8)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Optional

import typer

from sentinel.config import Settings, get_settings
from sentinel.db import get_engine
from sentinel.ingest.base import Harvester
from sentinel.ingest.retraction_watch import RetractionWatchHarvester
from sentinel.log import configure_logging

app = typer.Typer(no_args_is_help=True, add_completion=False)

HARVESTERS: dict[str, Callable[[Settings], Harvester]] = {
    "retraction-watch": lambda s: RetractionWatchHarvester(s.retraction_watch_csv_path),
}


@app.callback()
def main() -> None:
    """Sentinel: audit biomedical papers and trials for integrity problems."""
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)


@app.command()
def ingest(
    source: Annotated[str, typer.Argument(help=f"One of: {', '.join(HARVESTERS)}")],
    limit: Annotated[Optional[int], typer.Option(help="Stop after N records")] = None,
    resume: Annotated[bool, typer.Option(help="Continue from the stored cursor")] = False,
) -> None:
    """Harvest a source into the database."""
    if source not in HARVESTERS:
        raise typer.BadParameter(f"unknown source {source!r}; choose from {list(HARVESTERS)}")
    settings = get_settings()
    stats = HARVESTERS[source](settings).run(
        get_engine(), resume=resume, limit=limit, batch_size=settings.ingest_batch_size
    )
    typer.echo(
        f"{source}: seen {stats.seen}, inserted {stats.inserted}, "
        f"updated {stats.updated}, unchanged {stats.unchanged}, failed {stats.failed}"
    )


if __name__ == "__main__":
    app()
