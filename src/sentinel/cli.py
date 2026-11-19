"""Command-line entry point (sentinel-project.md §9.8).

Network clients, the resolver and the checks are imported inside the commands
that use them, so `sentinel report` doesn't pay for loading them: a report
card must take under five seconds (§9.10).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Optional

import typer
from sqlalchemy import select

from sentinel.config import Settings, get_settings
from sentinel.db import get_engine
from sentinel.log import configure_logging, get_logger
from sentinel.models import corpus as corpus_table
from sentinel.report.card import (
    detect_subject,
    load_paper_card,
    load_trial_card,
    render_json,
    render_paper_json,
    render_paper_text,
    render_text,
)
from sentinel.report.export import export_findings, run_status
from sentinel.report.stats import load_run_stats, render_stats_json, render_stats_text
from sentinel.resolve.normalize import normalize_doi
from sentinel.stages import StageHandle, record_stage

if TYPE_CHECKING:
    import httpx

    from sentinel.ingest.base import Harvester
    from sentinel.ingest.clinicaltrials import ClinicalTrialsClient
    from sentinel.ingest.europepmc import EuropePMC
    from sentinel.ingest.http import RateLimitedHTTP

app = typer.Typer(no_args_is_help=True, add_completion=False)
corpus_app = typer.Typer(no_args_is_help=True, help="Select and inspect working corpora.")
app.add_typer(corpus_app, name="corpus")
extract_app = typer.Typer(no_args_is_help=True, help="Extract structured data from full text.")
app.add_typer(extract_app, name="extract")
embed_app = typer.Typer(no_args_is_help=True, help="Embed passages for dense retrieval.")
app.add_typer(embed_app, name="embed")
retrieve_app = typer.Typer(no_args_is_help=True, help="Find the passages that bear on a claim.")
app.add_typer(retrieve_app, name="retrieve")
eval_app = typer.Typer(no_args_is_help=True, help="Evaluation sets.")
app.add_typer(eval_app, name="eval")
ml_app = typer.Typer(no_args_is_help=True, help="Trained models: the tabular baseline (ADR-057).")
app.add_typer(ml_app, name="ml")
log = get_logger(__name__)

def _retraction_watch(settings: Settings) -> Harvester:
    from sentinel.ingest.retraction_watch import RetractionWatchHarvester

    return RetractionWatchHarvester(settings.retraction_watch_csv_path)


HARVESTERS: dict[str, Callable[[Settings], Harvester]] = {
    "retraction-watch": _retraction_watch,
}
LOOKUPS = ("clinicaltrials", "crossref", "openalex", "doi-handle")
CORPUS_SOURCES = ("europepmc", "fulltext")
ALL_SOURCES = [*HARVESTERS, *LOOKUPS, *CORPUS_SOURCES]


def _rate_limited(settings: Settings, client: httpx.Client, source: str, rps: float) -> RateLimitedHTTP:
    from sentinel.ingest.http import RateLimitedHTTP

    return RateLimitedHTTP(
        client,
        source=source,
        rate_per_s=rps,
        max_retries=settings.http_max_retries,
        backoff_base_s=settings.http_backoff_base_s,
    )


def _trials_client(settings: Settings, client: httpx.Client) -> ClinicalTrialsClient:
    from sentinel.ingest.clinicaltrials import ClinicalTrialsClient

    return ClinicalTrialsClient(
        _rate_limited(settings, client, "clinicaltrials", settings.rate_clinicaltrials_rps),
        base_url=settings.clinicaltrials_base_url,
        drop_paths=settings.trial_drop_paths_list,
        strip_personal_fields=settings.strip_personal_fields,
    )


def _europepmc(settings: Settings, client: httpx.Client) -> EuropePMC:
    from sentinel.ingest.europepmc import EuropePMC

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
    from sentinel.ingest.europepmc import select_corpus
    from sentinel.ingest.http import build_http_client

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


@corpus_app.command("coverage")
def corpus_coverage(
    corpus: Annotated[Optional[str], typer.Option(help="Corpus label")] = None,
    fmt: Annotated[str, typer.Option("--format", help="text or json")] = "text",
) -> None:
    """What full text and abstracts are held for a corpus and its cited works (Q2)."""
    import json

    from sentinel.ingest.fulltext import coverage, render_coverage

    label = corpus or get_settings().default_corpus_label
    if label not in _corpus_labels():
        raise typer.BadParameter(f"unknown corpus {label!r}. Known corpora: {_corpus_labels()}")
    with get_engine().connect() as conn:
        result = coverage(conn, label)
    typer.echo(json.dumps(result, indent=2) if fmt == "json" else render_coverage(result))


@extract_app.command("passages")
def extract_passages(
    corpus: Annotated[Optional[str], typer.Option(help="Corpus label")] = None,
    limit: Annotated[Optional[int], typer.Option(help="Stop after N works")] = None,
    resume: Annotated[bool, typer.Option(help="Continue from the stored cursor")] = False,
) -> None:
    """Cut every corpus paper and cited work with text into passages (ADR-046)."""
    from sentinel.fulltext.extract import PassageExtractor
    from sentinel.fulltext.passages import PassageParams

    settings = get_settings()
    label = corpus or settings.default_corpus_label
    params = PassageParams(settings.passage_min_words, settings.passage_max_words,
                           settings.passage_overlap_sentences)
    try:
        extractor = PassageExtractor(get_engine(), label=label,
                                     cache_dir=settings.fulltext_cache_dir, params=params)
    except ValueError as exc:
        raise typer.BadParameter(f"{exc}. Known corpora: {_corpus_labels()}") from exc
    with record_stage(get_engine(), "extract", source="passages", corpus_label=label) as stage:
        stats = extractor.run(get_engine(), resume=resume, limit=limit, batch_size=200)
        _stage_counts(stage, stats)
    typer.echo(f"works {stats.seen}: {stats.inserted} split ({params.version()}), "
               f"{stats.unchanged} already split or empty, failed {stats.failed}")
    if extractor.missing_xml:
        typer.echo(f"full text marked but not cached: {len(extractor.missing_xml)}")


@extract_app.command("contexts")
def extract_contexts(
    corpus: Annotated[Optional[str], typer.Option(help="Corpus label")] = None,
    limit: Annotated[Optional[int], typer.Option(help="Stop after N papers")] = None,
    resume: Annotated[bool, typer.Option(help="Continue from the stored cursor")] = False,
) -> None:
    """Citation contexts from corpus papers' full text, linked to references (ADR-046)."""
    from sentinel.fulltext.extract import ContextExtractor, context_coverage

    settings = get_settings()
    label = corpus or settings.default_corpus_label
    try:
        extractor = ContextExtractor(get_engine(), label=label, cache_dir=settings.fulltext_cache_dir)
    except ValueError as exc:
        raise typer.BadParameter(f"{exc}. Known corpora: {_corpus_labels()}") from exc
    with record_stage(get_engine(), "extract", source="contexts", corpus_label=label) as stage:
        stats = extractor.run(get_engine(), resume=resume, limit=limit, batch_size=25)
        _stage_counts(stage, stats)
        s = extractor.stats
        stage.counts.update({"citations": s.citations, "groups": s.groups, "contexts": s.contexts,
                             "unknown_rids": s.unknown_rids, "unlinked_groups": s.unlinked_groups,
                             "ranges_expanded": s.ranges_expanded})
    s = extractor.stats
    typer.echo(f"papers {stats.seen}: {stats.inserted} with contexts, failed {stats.failed}")
    typer.echo(f"citations {s.citations:,} in {s.groups:,} groups -> {s.contexts:,} contexts; "
               f"{s.ranges_expanded:,} ranges expanded; {s.unknown_rids:,} citations point at no "
               f"stored reference ({s.unlinked_groups:,} groups unlinked)")
    with get_engine().connect() as conn:
        cov = context_coverage(conn, label)
    share = cov["with_context"] / cov["references"] if cov["references"] else 0
    typer.echo(f"references with a context: {cov['with_context']:,} of {cov['references']:,} "
               f"({100 * share:.1f}%)")
    if extractor.missing_xml:
        typer.echo(f"no cached XML: {sorted(extractor.missing_xml)}")


@embed_app.command("passages")
def embed_passages(
    corpus: Annotated[Optional[str], typer.Option(help="Corpus label")] = None,
    scope: Annotated[str, typer.Option(help="cited: works the corpus cites; all: corpus papers too; scifact: the external set")] = "cited",
    limit: Annotated[Optional[int], typer.Option(help="Stop after N passages")] = None,
    resume: Annotated[bool, typer.Option(help="Continue from the stored cursor")] = False,
) -> None:
    """MedCPT article vectors for passages that don't have one yet (ADR-047)."""
    from sentinel.fulltext.passages import PassageParams
    from sentinel.retrieve.embed import CHUNK, PassageEmbedder
    from sentinel.retrieve.encoders import MedCPTArticleEncoder

    settings = get_settings()
    label = _scope_label(settings, corpus, scope)
    version = PassageParams(settings.passage_min_words, settings.passage_max_words,
                            settings.passage_overlap_sentences).version()
    encoder = MedCPTArticleEncoder(settings.embed_article_model, settings.embed_article_revision)
    embedder = PassageEmbedder(get_engine(), encoder, label=label, splitter_ver=version,
                               scope=scope, batch_size=settings.embed_batch_size)
    pending = embedder.count_pending()
    typer.echo(f"{pending:,} passages ({version}, scope {scope}) need a vector from {encoder.name}")
    with record_stage(get_engine(), "embed", source=f"passages:{scope}", corpus_label=label) as stage:
        # One transaction per encoded chunk, so a killed run loses at most one chunk.
        stats = embedder.run(get_engine(), resume=resume, limit=limit, batch_size=CHUNK)
        _stage_counts(stage, stats)
        stage.counts["model"] = encoder.name
    typer.echo(f"passages {stats.seen:,}: {stats.inserted:,} embedded, failed {stats.failed}")


def _scope_label(settings: Settings, corpus: str | None, scope: str) -> str:
    """The label a scope's index and cursor are named by: the corpus for
    `cited` and `all`, "scifact" for the external set (ADR-049)."""
    from sentinel.retrieve.embed import SCOPES

    if scope not in SCOPES:
        raise typer.BadParameter(f"scope is one of {SCOPES}")
    if scope == "scifact":
        return "scifact"
    label = corpus or settings.default_corpus_label
    if label not in _corpus_labels():
        raise typer.BadParameter(f"unknown corpus {label!r}. Known corpora: {_corpus_labels()}")
    return label


def _passage_params(settings: Settings):
    from sentinel.fulltext.passages import PassageParams

    return PassageParams(settings.passage_min_words, settings.passage_max_words,
                         settings.passage_overlap_sentences)


def _splitter_version(settings: Settings) -> str:
    return _passage_params(settings).version()


def _bm25_expect(settings: Settings, label: str, scope: str) -> dict:
    return {"corpus": label, "scope": scope, "splitter_ver": _splitter_version(settings),
            "k1": settings.bm25_k1, "b": settings.bm25_b}


@retrieve_app.command("build-bm25")
def retrieve_build_bm25(
    corpus: Annotated[Optional[str], typer.Option(help="Corpus label")] = None,
    scope: Annotated[str, typer.Option(help="cited: works the corpus cites; all: corpus papers too; scifact: the external set")] = "cited",
) -> None:
    """Build and save the BM25 index over a corpus's passages (ADR-048)."""
    import time

    from sentinel.retrieve.bm25 import BM25Index, index_dir
    from sentinel.retrieve.embed import scope_ids

    settings = get_settings()
    label = _scope_label(settings, corpus, scope)
    expect = _bm25_expect(settings, label, scope)
    started = time.monotonic()
    index = BM25Index.build(get_engine(), scope_ids(label, scope), splitter_ver=expect["splitter_ver"],
                            k1=settings.bm25_k1, b=settings.bm25_b,
                            manifest={"corpus": label, "scope": scope})
    path = index_dir(settings.bm25_index_dir, label, scope, expect["splitter_ver"])
    index.save(path)
    typer.echo(f"BM25 index: {index.manifest['documents']:,} passages in "
               f"{time.monotonic() - started:.0f} s -> {path}")


@retrieve_app.command("search")
def retrieve_search(
    claim: Annotated[Optional[str], typer.Argument(help="Claim text (or use --context)")] = None,
    context: Annotated[Optional[int], typer.Option(help="A citation_context id: search the works it cites")] = None,
    paper: Annotated[Optional[list[int]], typer.Option(help="Search only this paper id (repeatable)")] = None,
    corpus: Annotated[Optional[str], typer.Option(help="Corpus label")] = None,
    scope: Annotated[str, typer.Option(help="Which BM25 index: cited, all or scifact")] = "cited",
    rerank: Annotated[bool, typer.Option(help="Rerank with the cross-encoder")] = True,
) -> None:
    """Debug: the top passages for a claim, with every stage's rank (ADR-048)."""
    from sqlalchemy import select as sql_select

    from sentinel.models import citation_context, citation_context_reference, reference
    from sentinel.retrieve.bm25 import BM25Index, index_dir
    from sentinel.retrieve.encoders import MedCPTCrossEncoder, MedCPTQueryEncoder, model_tag
    from sentinel.retrieve.search import RetrieveParams, Retriever

    settings = get_settings()
    label = _scope_label(settings, corpus, scope)
    paper_ids = list(paper) if paper else None
    if context is not None:
        with get_engine().connect() as conn:
            row = conn.execute(sql_select(citation_context.c.sentence)
                               .where(citation_context.c.id == context)).first()
            if row is None:
                raise typer.BadParameter(f"no citation_context {context}")
            claim = claim or row.sentence
            paper_ids = sorted(set(conn.execute(
                sql_select(reference.c.resolved_paper_id)
                .join(citation_context_reference,
                      citation_context_reference.c.reference_id == reference.c.id)
                .where(citation_context_reference.c.citation_context_id == context,
                       reference.c.resolved_paper_id.is_not(None))).scalars()))
            if not paper_ids:
                raise typer.BadParameter(f"context {context} cites no resolved work")
    if not claim:
        raise typer.BadParameter("give a claim or --context")
    expect = _bm25_expect(settings, label, scope)
    try:
        bm25 = BM25Index.load(index_dir(settings.bm25_index_dir, label, scope, expect["splitter_ver"]),
                              expect=expect)
    except (FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    params = RetrieveParams(
        splitter_ver=expect["splitter_ver"],
        article_model=model_tag(settings.embed_article_model, settings.embed_article_revision),
        metric=settings.retrieve_dense_metric, rrf_k=settings.retrieve_rrf_k,
        depth=settings.retrieve_depth, keep=settings.retrieve_keep)
    query_encoder = MedCPTQueryEncoder(settings.embed_query_model, settings.embed_query_revision)
    cross = MedCPTCrossEncoder(settings.rerank_model, settings.rerank_revision) if rerank else None
    result = Retriever(get_engine(), bm25, query_encoder, cross, params).search(
        claim, paper_ids=paper_ids, rerank=rerank)

    where = f"papers {paper_ids}" if paper_ids else "the whole index"
    typer.echo(f"Claim: {claim}\nSearched {where}: BM25 {len(result.bm25)}, dense {len(result.dense)} "
               f"({params.metric}), fused {len(result.fused)}; "
               + ", ".join(f"{k} {v:.2f} s" for k, v in result.seconds.items()))
    if not result.dense:
        typer.echo("  (no passage vectors here yet: is `sentinel embed passages` finished?)")
    for rank, h in enumerate(result.hits, start=1):
        score = f"rerank {h.rerank_score:.2f}" if h.rerank_score is not None else "not reranked"
        typer.echo(f"\n{rank}. passage {h.passage_id} (paper {h.paper_id}, {h.section}) {score}; "
                   f"BM25 #{h.bm25_rank or '-'}, dense #{h.dense_rank or '-'}, fused #{h.fused_rank}")
        typer.echo("   " + (h.text[:300] + ("..." if len(h.text) > 300 else "")))


@eval_app.command("retrieval")
def eval_retrieval(
    splits: Annotated[str, typer.Option(help="Comma-separated splits to score")] = "val,test",
    limit: Annotated[Optional[int], typer.Option(help="Score only the first N claims (a quick check)")] = None,
    query_set: Annotated[str, typer.Option("--set", help=(
        "scifact: all SciFact abstracts as one pool; synthetic: each intact pair searches "
        "its own source paper's body (ADR-055)"))] = "scifact",
    corpus: Annotated[Optional[str], typer.Option(help="Corpus label, for --set synthetic")] = None,
    mlflow: Annotated[bool, typer.Option(help="Log the rows to MLflow")] = True,
) -> None:
    """The four-row retrieval ablation, dot and cosine (S2.6, ADR-050, ADR-055)."""
    import json
    import time
    from datetime import datetime, timezone

    from sqlalchemy import select as sql_select

    from sqlalchemy import func

    from sentinel.eval.retrieval import (
        SYNTHETIC,
        CachingCrossEncoder,
        load_queries,
        load_synthetic_queries,
        log_to_mlflow,
        run_ablation,
        score,
        short_rankings,
    )
    from sentinel.models import paper as paper_table
    from sentinel.models import passage as passage_table
    from sentinel.retrieve.bm25 import TOKENIZER_VERSION, BM25Index, index_dir
    from sentinel.retrieve.encoders import MedCPTCrossEncoder, MedCPTQueryEncoder, model_tag
    from sentinel.retrieve.search import RetrieveParams, Retriever

    settings = get_settings()
    wanted = [s.strip() for s in splits.split(",") if s.strip()]
    engine = get_engine()
    if query_set not in ("scifact", "synthetic"):
        raise typer.BadParameter("--set is scifact or synthetic")
    synthetic = query_set == "synthetic"
    skip = ("abstract",) if synthetic else ()
    label = "scifact"
    if synthetic:
        label = _scope_label(settings, corpus, "cited")
        dataset = SYNTHETIC
        queries = load_synthetic_queries(engine, splits=wanted, corpus=label,
                                         splitter_ver=_splitter_version(settings))[:limit]
        if not queries:
            raise typer.BadParameter("no intact synthetic pairs; run `sentinel eval make-synthetic`")
        expect = _bm25_expect(settings, label, "cited")
        index = index_dir(settings.bm25_index_dir, label, "cited", expect["splitter_ver"])
        pool: list[int] = []
        with engine.connect() as conn:  # each query's own pool size, to tell a short list
            sizes = dict(conn.execute(
                sql_select(passage_table.c.paper_id, func.count())
                .where(passage_table.c.paper_id.in_({q.paper_ids[0] for q in queries}),
                       passage_table.c.splitter_ver == expect["splitter_ver"],
                       func.coalesce(passage_table.c.section, "").not_in(list(skip)))
                .group_by(passage_table.c.paper_id)).all())
        pool_sizes = {q.claim_id: sizes.get(q.paper_ids[0], 0) for q in queries}
    else:
        dataset = "scifact"
        queries = load_queries(engine, dataset="scifact", splits=wanted)[:limit]
        if not queries:
            raise typer.BadParameter("no SciFact claims with evidence; run `sentinel eval load-scifact`")
        with engine.connect() as conn:
            pool = list(conn.execute(sql_select(paper_table.c.id).where(paper_table.c.source == "scifact")).scalars())
        pool_sizes = None
        expect = _bm25_expect(settings, "scifact", "scifact")
        index = index_dir(settings.bm25_index_dir, "scifact", "scifact", expect["splitter_ver"])
    try:
        bm25 = BM25Index.load(index, expect=expect)
    except (FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    article = model_tag(settings.embed_article_model, settings.embed_article_revision)
    query_encoder = MedCPTQueryEncoder(settings.embed_query_model, settings.embed_query_revision)
    cross = CachingCrossEncoder(MedCPTCrossEncoder(settings.rerank_model, settings.rerank_revision))
    retrievers = {metric: Retriever(engine, bm25, query_encoder, cross, RetrieveParams(
        splitter_ver=expect["splitter_ver"], article_model=article, metric=metric,
        rrf_k=settings.retrieve_rrf_k, depth=settings.retrieve_depth, keep=settings.retrieve_keep))
        for metric in ("dot", "cosine")}

    if synthetic:
        typer.echo(f"{len(queries)} intact pairs ({', '.join(wanted)}), each against its own source "
                   f"paper's body: {sum(pool_sizes.values()) / len(queries):.1f} passages on average")
    else:
        typer.echo(f"{len(queries)} claims ({', '.join(wanted)}) against {len(pool):,} SciFact abstracts")
    started = time.monotonic()

    def progress(done: int, total: int) -> None:
        if done % 25 == 0 or done == total:
            log.info("ablation_progress", done=done, total=total, cross_pairs=cross.scored,
                     elapsed_s=round(time.monotonic() - started))

    rankings = run_ablation(
        lambda q, metric: retrievers[metric].search(
            q.claim, paper_ids=q.paper_ids if q.paper_ids is not None else pool, skip_sections=skip),
        queries, progress=progress)
    results = score(rankings, queries)
    elapsed = time.monotonic() - started
    short = short_rankings(rankings, settings.retrieve_depth, pool_sizes)

    params = {"dataset": dataset, "corpus": label,
              "dataset_sha256": "" if synthetic else settings.scifact_sha256,
              "pool": "own paper's body" if synthetic else "all SciFact abstracts",
              "splitter_ver": expect["splitter_ver"], "article_model": article,
              "query_model": model_tag(settings.embed_query_model, settings.embed_query_revision),
              "rerank_model": cross.name, "bm25_k1": settings.bm25_k1, "bm25_b": settings.bm25_b,
              "bm25_tokenizer": TOKENIZER_VERSION, "rrf_k": settings.retrieve_rrf_k,
              "depth": settings.retrieve_depth, "config_hash": settings.config_hash,
              "seed": settings.random_seed, "claims": len(queries)}
    out_dir = Path(settings.data_dir) / "evals"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = out_dir / f"retrieval-ablation-{'synthetic-' if synthetic else ''}{stamp}.json"
    out.write_text(json.dumps({"params": params, "scores": results, "seconds": elapsed,
                               "cross_pairs_scored": cross.scored, "short_rankings": short,
                               "rankings": {row: {str(k): v for k, v in by.items()}
                                            for row, by in rankings.items()}}, indent=1), "utf-8")

    typer.echo(f"\n{'row':26s}" + "".join(f"{s + ' nDCG@10':>16s}{s + ' R@50':>13s}" for s in wanted))
    for row, by_split in results.items():
        # A split with no claims prints "-", not a zero that reads like a result.
        typer.echo(f"{row:26s}" + "".join(
            f"{by_split[s]['ndcg@10']:16.3f}{by_split[s]['recall@50']:13.3f}" if s in by_split
            else f"{'-':>16s}{'-':>13s}" for s in wanted))
    typer.echo(f"\n{elapsed / 60:.1f} min; {cross.scored:,} cross-encoder pairs scored; results -> {out}")
    if short:
        # Fewer results than min(depth, pool): a stage lost candidates.
        log.warning("ablation_short_rankings", depth=settings.retrieve_depth, rows=short)
        typer.echo(f"WARNING: claims with fewer than {settings.retrieve_depth} results, by row: {short}")
    if mlflow:
        try:
            run_ids = log_to_mlflow(results, params, tracking_uri=settings.mlflow_tracking_uri,
                                    experiment="retrieval-ablation", artifact=str(out))
        except Exception as exc:  # the results are saved; a down server mustn't lose them
            typer.echo(f"MLflow logging failed ({type(exc).__name__}: {exc}); start it with "
                       f"`dotenv run -- mlflow server` and rerun, or log {out} by hand")
            raise typer.Exit(code=2) from exc
        typer.echo(f"logged {len(run_ids)} MLflow runs to experiment 'retrieval-ablation'")


@eval_app.command("compare")
def eval_compare(
    results: Annotated[Path, typer.Argument(help="A results file written by `sentinel eval retrieval`")],
    row_a: Annotated[str, typer.Argument(help="The row to test, e.g. fusion+rerank/dot")],
    row_b: Annotated[str, typer.Argument(help="The row to test it against, e.g. bm25")],
) -> None:
    """Paired bootstrap of two ablation rows' nDCG@10, per split (ADR-050)."""
    import json

    from sentinel.eval.retrieval import (
        BOOTSTRAP_RESAMPLES, SYNTHETIC, load_queries, load_synthetic_queries, paired_bootstrap, per_claim)

    settings = get_settings()
    data = json.loads(results.read_text("utf-8"))
    rankings = {row: {int(k): v for k, v in by.items()} for row, by in data["rankings"].items()}
    for row in (row_a, row_b):
        if row not in rankings:
            raise typer.BadParameter(f"no row {row!r}; the rows are {', '.join(rankings)}")
    splits = sorted({split for by_split in data["scores"].values() for split in by_split})
    scored = rankings[row_a].keys() & rankings[row_b].keys()
    params = data["params"]
    if params["dataset"] == SYNTHETIC:
        loaded = load_synthetic_queries(get_engine(), splits=splits, corpus=params["corpus"],
                                        splitter_ver=params["splitter_ver"])
    else:
        loaded = load_queries(get_engine(), dataset=params["dataset"], splits=splits)
    queries = [q for q in loaded if q.claim_id in scored]
    typer.echo(f"{row_a} against {row_b}, nDCG@10; {BOOTSTRAP_RESAMPLES:,} paired resamples, "
               f"seed {settings.random_seed}, 95% intervals")
    for split in splits:
        qs = [q for q in queries if q.split == split]
        a, b = per_claim(rankings[row_a], qs), per_claim(rankings[row_b], qs)
        p = paired_bootstrap(a, b, seed=settings.random_seed)
        typer.echo(f"  {split:5s} {p.claims} claims: {sum(a) / len(a):.3f} vs {sum(b) / len(b):.3f}; "
                   f"difference {p.diff:+.3f} [{p.lo:+.3f}, {p.hi:+.3f}]; "
                   f"gain {p.gain:+.1%} [{p.gain_lo:+.1%}, {p.gain_hi:+.1%}]; "
                   f"better on {p.better}, worse on {p.worse}")


@eval_app.command("load-scifact")
def eval_load_scifact() -> None:
    """Download SciFact, check it against the pinned SHA-256, and load it as
    the external evaluation set (Q6, ADR-049)."""
    from sentinel.eval.scifact import fetch, load
    from sentinel.ingest.http import build_http_client

    settings = get_settings()
    directory = Path(settings.data_dir) / "external" / "scifact"
    with build_http_client(settings) as client:
        try:
            data = fetch(settings.scifact_url, settings.scifact_sha256, directory, client=client)
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
    with record_stage(get_engine(), "ingest", source="scifact") as stage:
        result = load(get_engine(), data, seed=settings.random_seed,
                      params=_passage_params(settings), release_sha256=settings.scifact_sha256)
        stage.items = result.items_added
        stage.counts = {"papers": result.papers_added, "passages": result.passages_added,
                        "items": result.items_added, "gold_links": result.gold_links,
                        "unmapped_sentences": result.unmapped_sentences}
    typer.echo(f"SciFact: {result.papers_added:,} abstracts and {result.passages_added:,} passages added; "
               f"{result.items_added:,} items, {result.gold_links:,} gold passage links, "
               f"{result.unmapped_sentences} evidence sentences unmapped")
    for key, n in result.by_split_label.items():
        typer.echo(f"  {key}: {n:,}")


@eval_app.command("make-synthetic")
def eval_make_synthetic(
    corpus: Annotated[Optional[str], typer.Option(help="Corpus label")] = None,
) -> None:
    """Build the synthetic set: claim–passage pairs from the corpus's cited
    works with full text, corrupted by rule (S2.8, ADR-051)."""
    from sentinel.eval.synthetic import GENERATOR_VERSION, generate

    settings = get_settings()
    label = _scope_label(settings, corpus, "cited")
    version = _passage_params(settings).version()
    with record_stage(get_engine(), "extract", source="synthetic", corpus_label=label) as stage:
        result = generate(get_engine(), corpus=label, seed=settings.random_seed, splitter_ver=version)
        stage.items = result.items_added
        stage.counts = {"sources": result.sources, "adaptable": result.adaptable,
                        "papers_with_pairs": result.papers_with_pairs, "pairs": result.pairs,
                        "items": result.items_added, "gold_links": result.gold_links,
                        "by_type": result.by_type, "partners": result.partners,
                        "scifact_title_overlap": result.scifact_title_overlap}
    if result.already_present:
        typer.echo(f"The synthetic set (generator {GENERATOR_VERSION}, {version}) is already loaded for {label}.")
        return
    typer.echo(f"Synthetic set, generator {GENERATOR_VERSION}: {result.sources:,} cited works with full text, "
               f"{result.adaptable:,} with a licence that allows adapted text, {result.papers_with_pairs:,} "
               f"giving {result.pairs:,} pairs")
    typer.echo(f"{result.items_added:,} items, {result.gold_links:,} gold passage links; swapped sources: "
               + ", ".join(f"{k} {n:,}" for k, n in sorted(result.partners.items())))
    for key, n in result.by_type.items():
        typer.echo(f"  {key}: {n:,}")
    for key, n in result.by_split_label.items():
        typer.echo(f"  {key}: {n:,}")
    if result.scifact_title_overlap:
        typer.echo(f"{result.scifact_title_overlap} source papers share a title with a SciFact abstract")


@extract_app.command("stats")
def extract_stats(
    corpus: Annotated[Optional[str], typer.Option(help="Corpus label")] = None,
    limit: Annotated[Optional[int], typer.Option(help="Stop after N papers")] = None,
    resume: Annotated[bool, typer.Option(help="Continue from the stored cursor")] = False,
) -> None:
    """Find reported test statistics in corpus papers' body text (C03's input)."""
    from sentinel.ingest.fulltext_stats import StatExtractor
    from sentinel.ingest.http import build_http_client

    settings = get_settings()
    label = corpus or settings.default_corpus_label
    with build_http_client(settings) as client:
        try:
            extractor = StatExtractor(get_engine(), _europepmc(settings, client), label=label,
                                      cache_dir=settings.fulltext_cache_dir,
                                      alpha=settings.c03_alpha,
                                      allow_one_tailed=settings.c03_allow_one_tailed)
        except ValueError as exc:
            raise typer.BadParameter(f"{exc}. Known corpora: {_corpus_labels()}") from exc
        with record_stage(get_engine(), "extract", source="stats", corpus_label=label) as stage:
            stats = extractor.run(get_engine(), resume=resume, limit=limit, batch_size=25)
            _stage_counts(stage, stats)
    typer.echo(f"papers {stats.seen}: {stats.inserted} reported tests stored, failed {stats.failed}")
    if extractor.no_fulltext:
        typer.echo(f"no full text: {len(extractor.no_fulltext)} papers")


@extract_app.command("claims")
def extract_claims(
    model: Annotated[str, typer.Option(help="Gateway model ID, exactly as GET /v1/models lists it")],
    corpus: Annotated[Optional[str], typer.Option(help="Corpus label")] = None,
    sample: Annotated[Optional[int], typer.Option(
        help="Only N contexts, in an order fixed by SENTINEL_RANDOM_SEED (the same N for every model)")] = None,
    limit: Annotated[Optional[int], typer.Option(help="Stop after N contexts")] = None,
    batch: Annotated[bool, typer.Option(
        help="Use the gateway's batch API, which runs on spare capacity (for large runs)")] = False,
    mlflow: Annotated[bool, typer.Option(help="Log the run's rates to MLflow")] = True,
) -> None:
    """Claim skeletons for citation contexts from a gateway model (S2.7, ADR-053, ADR-054).

    Contexts that already have a claim from this model and extractor version
    are skipped, so rerunning resumes a stopped run."""
    import httpx

    from sentinel.extract.claims import (
        CHUNK, EXTRACTOR_VER, MAX_REPAIRS, MAX_TOKENS, ClaimExtractor, log_to_mlflow, stored_summary)
    from sentinel.extract.llm import BatchClient, ChatClient

    settings = get_settings()
    label = corpus or settings.default_corpus_label
    if label not in _corpus_labels():
        raise typer.BadParameter(f"unknown corpus {label!r}. Known corpora: {_corpus_labels()}")
    # No From header: the key identifies the caller, and the contact email is
    # only for the public APIs' polite pools.
    client = httpx.Client(timeout=settings.llm_timeout_s, headers={"User-Agent": settings.user_agent},
                          limits=httpx.Limits(max_connections=2 * settings.llm_workers))
    with client:
        try:
            http = _rate_limited(settings, client, "llm", settings.rate_llm_rps)
            gateway = dict(base_url=settings.llm_base_url, api_key=settings.llm_api_key)
            extractor = ClaimExtractor(
                get_engine(), None if batch else ChatClient(http, **gateway),
                batch=BatchClient(http, **gateway) if batch else None, label=label, model=model,
                seed=settings.random_seed, sample=sample, workers=settings.llm_workers)
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(f"{len(extractor.pending_ids()):,} contexts need a claim from {extractor.extractor} "
                   f"{EXTRACTOR_VER}" + (f" (sample of {sample:,})" if sample else "")
                   + (" by batch" if batch else ""))
        with record_stage(get_engine(), "extract", source=f"claims:{model}", corpus_label=label) as stage:
            stats = extractor.run(get_engine(), limit=limit, batch_size=CHUNK)
            _stage_counts(stage, stats)
            s = extractor.stats
            stage.counts.update({"model": model, "extractor_ver": EXTRACTOR_VER, "sample": sample,
                                 "valid_first": s.valid_first, "valid_repaired": s.valid_repaired,
                                 "invalid": s.invalid, "rejected": s.rejected, "calls": s.calls,
                                 "deployments": dict(s.deployments), "batch": batch,
                                 "batches": s.batches, "unanswered": s.unanswered,
                                 "energy_wh": round(s.energy_wh, 3), "co2_g": round(s.co2_g, 3)})
    s = extractor.stats
    r = s.rates()
    typer.echo(f"contexts {s.contexts:,}: {s.valid_first:,} valid at once, {s.valid_repaired:,} after "
               f"repair, {s.invalid:,} still invalid, {s.rejected:,} refused by the gateway")
    if s.unanswered:
        typer.echo(f"{s.unanswered:,} contexts got no answer from the gateway; run again to ask them")
    typer.echo(f"validation failure rate: {100 * r['failure_rate_first']:.1f}% on the first answer, "
               f"{100 * r['failure_rate_final']:.1f}% after up to {MAX_REPAIRS} repairs")
    timing = f"in {s.batches} batches" if batch else f"{r['seconds_per_call']:.1f} s each"
    typer.echo(f"{s.calls:,} calls, {timing}; "
               f"{r['prompt_tokens_per_context']:.0f} prompt and "
               f"{r['completion_tokens_per_context']:.0f} answer tokens per context; "
               f"{s.truncated} answers cut off")
    if s.energy_wh:
        typer.echo(f"the gateway's estimate: {s.energy_wh:,.1f} Wh, {s.co2_g:,.1f} g CO2")
    if s.first_errors:
        typer.echo("first-answer errors: " + ", ".join(f"{k} {v}" for k, v in s.first_errors.most_common()))
    if s.final_errors:
        typer.echo("errors left: " + ", ".join(f"{k} {v}" for k, v in s.final_errors.most_common()))
    typer.echo("kinds: " + ", ".join(f"{k} {v}" for k, v in s.kinds.most_common()))
    with get_engine().connect() as conn:
        stored = stored_summary(conn, label=label, extractor=extractor.extractor)
    typer.echo(f"stored for {extractor.extractor} {EXTRACTOR_VER}: {stored['valid']:,} valid, "
               f"{stored['invalid']:,} invalid")
    if mlflow and s.contexts:
        params = {"model": model, "extractor_ver": EXTRACTOR_VER, "corpus": label,
                  "sample": sample or "all", "seed": settings.random_seed,
                  "max_repairs": MAX_REPAIRS, "max_tokens": MAX_TOKENS, "temperature": 0,
                  "mode": "batch" if batch else "direct"}
        try:
            run_id = log_to_mlflow(s, params, tracking_uri=settings.mlflow_tracking_uri)
            typer.echo(f"MLflow run {run_id}")
        except Exception as exc:  # the rows are stored; only the log is missing
            typer.echo(f"MLflow logging failed ({type(exc).__name__}); the claims are stored")


@extract_app.command("claims-carry")
def extract_claims_carry(
    model: Annotated[str, typer.Option(help="Gateway model ID whose claims are copied")],
    from_ver: Annotated[str, typer.Option(help="The earlier claim extractor version to copy from")],
    corpus: Annotated[Optional[str], typer.Option(help="Corpus label")] = None,
) -> None:
    """Copy valid claim skeletons to the current contexts where the model's input
    is unchanged, so only the rest need asking (ADR-056). Calls nothing."""
    from sentinel.extract.claims import EXTRACTOR_VER, carry_over, stored_summary

    settings = get_settings()
    label = corpus or settings.default_corpus_label
    if label not in _corpus_labels():
        raise typer.BadParameter(f"unknown corpus {label!r}. Known corpora: {_corpus_labels()}")
    if from_ver == EXTRACTOR_VER:
        raise typer.BadParameter(f"--from-ver must be an earlier version than {EXTRACTOR_VER}")
    extractor = f"llm:{model}"
    with record_stage(get_engine(), "extract", source=f"claims-carry:{model}", corpus_label=label) as stage:
        cs = carry_over(get_engine(), label=label, extractor=extractor, from_ver=from_ver)
        stage.items = cs.matched
        stage.counts = {"model": model, "from_ver": from_ver, "to_ver": EXTRACTOR_VER,
                        "matched": cs.matched, "carried": cs.carried, "failed_rules": cs.failed_rules}
    typer.echo(f"{cs.matched:,} contexts unchanged with a valid {from_ver} skeleton: {cs.carried:,} "
               f"copied to {EXTRACTOR_VER}, {cs.failed_rules:,} broke the current rules")
    with get_engine().connect() as conn:
        stored = stored_summary(conn, label=label, extractor=extractor)
    typer.echo(f"stored for {extractor} {EXTRACTOR_VER}: {stored['valid']:,} valid, "
               f"{stored['invalid']:,} invalid")


@ml_app.command("features")
def ml_features(
    corpus: Annotated[Optional[str], typer.Option(help="Corpus label")] = None,
    out: Annotated[Optional[Path], typer.Option(help="Also write the table as CSV")] = None,
) -> None:
    """The tabular baseline's metadata features, one row per citation, and how
    complete each is (S2.9, ADR-057). Reads only."""
    import csv

    from sentinel.ml.features import FEATURES_VER, CitationFeatures, build_features, describe

    settings = get_settings()
    label = corpus or settings.default_corpus_label
    if label not in _corpus_labels():
        raise typer.BadParameter(f"unknown corpus {label!r}. Known corpora: {_corpus_labels()}")
    with get_engine().connect() as conn:
        rows = build_features(conn, label=label)
    summary = describe(rows)
    typer.echo(f"features {FEATURES_VER}: {summary['rows']:,} citations from {summary['contexts']:,} "
               f"contexts in {summary['citing_papers']:,} papers")
    for name, s in summary["numeric"].items():
        known = f"{s['known']:,} known ({100 * s['known'] / max(summary['rows'], 1):.1f}%)"
        spread = (f"median {s['median']:g}, 5th-95th {s['p5']:g}-{s['p95']:g}"
                  if s["known"] else "no values")
        typer.echo(f"  {name}: {known}; {spread}")
    for name, counts in summary["categories"].items():
        typer.echo(f"  {name}: " + ", ".join(f"{k} {v:,}" for k, v in counts.items()))
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(CitationFeatures.model_fields))
            writer.writeheader()
            writer.writerows(r.model_dump() for r in rows)
        typer.echo(f"wrote {out}")


@ml_app.command("baseline")
def ml_baseline(
    labels: Annotated[Optional[Path], typer.Option(
        help="CSV of labelled citations: citation_context_id, reference_id, label")] = None,
    placebo: Annotated[Optional[int], typer.Option(
        help="Instead of labels, N random citations with random labels: a plumbing check that "
             "should score at chance")] = None,
    corpus: Annotated[Optional[str], typer.Option(help="Corpus label")] = None,
    mlflow: Annotated[bool, typer.Option(help="Log the run to MLflow")] = True,
) -> None:
    """Train, calibrate and explain the tabular baseline by cross-validation
    grouped by citing paper (S2.9, ADR-057). Real labels arrive with S3.4."""
    from datetime import datetime, timezone

    from sentinel.ml.baseline import (
        LABELS, OUTER_FOLDS, load_labels, log_to_mlflow, placebo_labels, run, save)
    from sentinel.ml.features import build_features

    if (labels is None) == (placebo is None):
        raise typer.BadParameter("give --labels or --placebo, not both")
    settings = get_settings()
    label = corpus or settings.default_corpus_label
    if label not in _corpus_labels():
        raise typer.BadParameter(f"unknown corpus {label!r}. Known corpora: {_corpus_labels()}")
    with get_engine().connect() as conn:
        features = build_features(conn, label=label)
    try:
        if placebo is not None:
            rows, source = placebo_labels(features, placebo, settings.random_seed), f"placebo:{placebo}"
        else:
            rows, source = load_labels(labels), str(labels)
        result = run(features, rows, seed=settings.random_seed, label_source=source)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    m = result.cv.metrics
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    kind = "placebo" if placebo is not None else "labelled"
    out_dir = Path(settings.data_dir) / "ml" / f"baseline-{kind}-{stamp}"
    files = save(result, out_dir, title="Placebo labels" if placebo is not None else "Tabular baseline")
    counts = ", ".join(f"{n} {result.params[f'n_{n}']}" for n in LABELS)
    typer.echo(f"{len(rows)} labelled citations from {result.params['citing_papers']} citing papers "
               f"({counts}); {result.cv.folds} folds by citing paper (up to {OUTER_FOLDS})")
    typer.echo(f"macro F1 {m['macro_f1']:.3f} (chance: {m['chance_macro_f1']:.3f} always the commonest "
               f"label, {m['chance_macro_f1_guessing']:.3f} guessing by the shares); "
               f"accuracy {m['accuracy']:.3f}; log loss {m['log_loss']:.3f}")
    typer.echo(f"PR-AUC, not supported: {m['pr_auc_not_supported']:.3f} "
               f"(prevalence {m['chance_pr_auc_not_supported']:.3f})")
    typer.echo(f"ECE: {m['raw_ece']:.3f} uncalibrated, {m['ece']:.3f} after Platt scaling")
    typer.echo("SHAP, largest first: " + ", ".join(
        f"{g} {sum(v.values()):.3f}" for g, v in list(result.shap["mean_abs_shap"].items())[:6]))
    typer.echo(f"files in {out_dir}")
    if mlflow:
        try:
            run_id = log_to_mlflow(result, files, tracking_uri=settings.mlflow_tracking_uri,
                                   run_name=f"{kind} {len(rows)}", tags={"labels": kind})
            typer.echo(f"MLflow run {run_id}")
        except Exception as exc:  # the files are written; only the log is missing
            typer.echo(f"MLflow logging failed ({type(exc).__name__}); the files are in {out_dir}")


@app.command()
def ingest(
    source: Annotated[str, typer.Argument(help=f"One of: {', '.join(ALL_SOURCES)}")],
    limit: Annotated[Optional[int], typer.Option(help="Stop after N records")] = None,
    resume: Annotated[bool, typer.Option(help="Continue from the stored cursor")] = False,
    nct: Annotated[
        Optional[list[str]], typer.Option(help="NCT ID to fetch (clinicaltrials; repeatable)")
    ] = None,
    corpus: Annotated[
        Optional[str], typer.Option(help="Corpus label to harvest (europepmc, fulltext)")
    ] = None,
    doi: Annotated[
        Optional[list[str]], typer.Option(help="DOI to look up (crossref/openalex/doi-handle)")
    ] = None,
) -> None:
    """Harvest a source, or fetch records by ID from a lookup source."""
    from sentinel.ingest.europepmc import EuropePMCHarvester
    from sentinel.ingest.http import build_http_client

    settings = get_settings()
    if source == "clinicaltrials":
        if not nct:
            raise typer.BadParameter("clinicaltrials needs at least one --nct")
        _fetch_trials(settings, nct)
        return
    if source in ("crossref", "openalex", "doi-handle"):
        if not doi:
            raise typer.BadParameter(f"{source} needs at least one --doi")
        _lookup_dois(settings, source, doi)
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
            with record_stage(get_engine(), "ingest", source=source, corpus_label=label) as stage:
                stats = harvester.run(get_engine(), resume=resume, limit=limit, batch_size=25)
                _stage_counts(stage, stats)
        _echo_stats(source, stats)
        if harvester.not_found:
            typer.echo(f"trials not in the registry (not linked): {sorted(harvester.not_found)}")
        if harvester.no_fulltext:
            typer.echo(f"no full text, stored without references: {sorted(harvester.no_fulltext)}")
        if harvester.fulltext_failed:
            typer.echo(f"full text failed upstream, stored without references "
                       f"(re-run to retry): {sorted(harvester.fulltext_failed)}")
        return
    if source == "fulltext":
        _ingest_fulltext(settings, corpus or settings.default_corpus_label, resume, limit)
        return
    if source not in HARVESTERS:
        raise typer.BadParameter(f"unknown source {source!r}; choose from {ALL_SOURCES}")
    with record_stage(get_engine(), "ingest", source=source) as stage:
        stats = HARVESTERS[source](settings).run(
            get_engine(), resume=resume, limit=limit, batch_size=settings.ingest_batch_size
        )
        _stage_counts(stage, stats)
    _echo_stats(source, stats)


def _ingest_fulltext(settings: Settings, label: str, resume: bool, limit: int | None) -> None:
    """Text availability for a corpus and the works it cites (ADR-045)."""
    from sentinel.ingest.fulltext import FulltextHarvester
    from sentinel.ingest.http import build_http_client

    with build_http_client(settings) as client:
        try:
            harvester = FulltextHarvester(get_engine(), _europepmc(settings, client), label=label,
                                          cache_dir=settings.fulltext_cache_dir)
        except ValueError as exc:
            raise typer.BadParameter(f"{exc}. Known corpora: {_corpus_labels()}") from exc
        # One batch is one lookup query plus the XML fetches it needs.
        with record_stage(get_engine(), "ingest", source="fulltext", corpus_label=label) as stage:
            stats = harvester.run(get_engine(), resume=resume, limit=limit, batch_size=25)
            _stage_counts(stage, stats)
    _echo_stats("fulltext", stats)
    if harvester.fetch_failed:
        typer.echo(f"open-access XML failed upstream (re-run to retry): "
                   f"{sorted(harvester.fetch_failed)}")


def _stage_counts(stage: StageHandle, stats) -> None:
    stage.items = stats.seen
    stage.counts = {"inserted": stats.inserted, "updated": stats.updated,
                    "unchanged": stats.unchanged, "failed": stats.failed}


def _lookup_dois(settings: Settings, source: str, raw_dois: list[str]) -> None:
    """Look up each DOI; one failure is reported and skipped, exit 1 if any failed."""
    import httpx

    from sentinel.ingest.crossref import CrossrefClient
    from sentinel.ingest.doi_handle import DoiHandleClient
    from sentinel.ingest.http import RetryableHTTPError, build_http_client
    from sentinel.ingest.openalex import OpenAlexClient

    bad = [d for d in raw_dois if normalize_doi(d) is None]
    if bad:
        raise typer.BadParameter(f"not DOIs: {bad}", param_hint="--doi")
    failed = 0
    with build_http_client(settings) as client:
        if source == "crossref":
            lookup = CrossrefClient(
                _rate_limited(settings, client, source, settings.rate_crossref_rps),
                base_url=settings.crossref_base_url, mailto=settings.contact_email,
            ).lookup
        elif source == "openalex":
            lookup = OpenAlexClient(
                _rate_limited(settings, client, source, settings.rate_openalex_rps),
                base_url=settings.openalex_base_url, api_key=settings.openalex_api_key,
                daily_budget_usd=settings.openalex_daily_budget_usd,
            ).lookup
        else:
            handle = DoiHandleClient(
                _rate_limited(settings, client, source, settings.rate_doi_handle_rps),
                base_url=settings.doi_handle_base_url,
            )
        for value in raw_dois:
            doi_norm = normalize_doi(value)
            try:
                if source == "doi-handle":
                    typer.echo(f"{doi_norm}: {'registered' if handle.exists(doi_norm) else 'not registered'}")
                    continue
                stored = lookup(get_engine(), doi_norm)
            except (httpx.HTTPError, RetryableHTTPError, ValueError) as exc:
                failed += 1
                log.error("lookup_failed", source=source, doi=doi_norm, error=type(exc).__name__)
                typer.echo(f"{doi_norm}: failed ({type(exc).__name__})")
                continue
            if stored is None:
                typer.echo(f"{doi_norm}: not found in {source}")
            else:
                typer.echo(f"{doi_norm}: paper {stored.paper_id} ({stored.action})")
    if failed:
        raise typer.Exit(code=1)


def _echo_stats(source: str, stats) -> None:
    typer.echo(
        f"{source}: seen {stats.seen}, inserted {stats.inserted}, "
        f"updated {stats.updated}, unchanged {stats.unchanged}, failed {stats.failed}"
    )


def _fetch_trials(settings: Settings, raw_ids: list[str]) -> None:
    """Fetch each study. One failure is logged and skipped, not fatal to the
    rest; the command exits non-zero if any study failed."""
    import httpx

    from sentinel.ingest.clinicaltrials import normalise_nct_id
    from sentinel.ingest.http import RetryableHTTPError, build_http_client

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
def resolve(
    corpus: Annotated[Optional[str], typer.Option(help="Corpus label")] = None,
    sample: Annotated[
        Optional[int], typer.Option(help="Resolve a seeded sample of N references")
    ] = None,
    limit: Annotated[Optional[int], typer.Option(help="Stop after N references")] = None,
    retry: Annotated[bool, typer.Option(help="Re-resolve references already done")] = False,
    batch_size: Annotated[int, typer.Option(help="References per commit")] = 50,
    structured: Annotated[
        bool, typer.Option(help="Only references from structured lists (section 9.10's target)")
    ] = False,
) -> None:
    """Match references to known works (stages 1-5, sentinel-project.md section 9.5)."""
    from sentinel.ingest.crossref import CrossrefClient
    from sentinel.ingest.doi_handle import DoiHandleClient
    from sentinel.ingest.http import build_http_client
    from sentinel.ingest.openalex import OpenAlexBudgetExhausted, OpenAlexClient
    from sentinel.resolve.candidates import CandidateSource
    from sentinel.resolve.cascade import (
        Resolver,
        Thresholds,
        resolve_references,
        select_references,
        summarise,
    )

    settings = get_settings()
    label = corpus or settings.default_corpus_label
    engine = get_engine()
    if label not in _corpus_labels():
        raise typer.BadParameter(f"no corpus {label!r}. Known corpora: {_corpus_labels()}",
                                 param_hint="--corpus")
    ids = select_references(engine, corpus=label, retry=retry, sample=sample,
                            seed=settings.random_seed if sample else None, limit=limit,
                            structured=structured)
    sample_ids = (select_references(engine, corpus=label, retry=True, sample=sample,
                                    seed=settings.random_seed, structured=structured)
                  if sample else None)
    typer.echo(f"{len(ids)} references to resolve")
    with build_http_client(settings) as client:
        crossref = CrossrefClient(_rate_limited(settings, client, "crossref", settings.rate_crossref_rps),
                                  base_url=settings.crossref_base_url, mailto=settings.contact_email)
        openalex = OpenAlexClient(_rate_limited(settings, client, "openalex", settings.rate_openalex_rps),
                                  base_url=settings.openalex_base_url, api_key=settings.openalex_api_key,
                                  daily_budget_usd=settings.openalex_daily_budget_usd)
        doi_handle = DoiHandleClient(
            _rate_limited(settings, client, "doi_handle", settings.rate_doi_handle_rps),
            base_url=settings.doi_handle_base_url)
        resolver = Resolver(
            CandidateSource(engine, crossref, openalex, doi_handle),
            Thresholds(settings.resolve_trigram_min, settings.resolve_trigram_strict,
                       settings.resolve_year_tolerance, settings.resolve_ambiguous_margin),
        )
        with record_stage(engine, "resolve", corpus_label=label) as stage:
            try:
                stats = resolve_references(engine, resolver, ids, batch_size=batch_size)
            except OpenAlexBudgetExhausted as exc:
                stage.status = "stopped"
                typer.echo(f"stopped: {exc}. Finished batches are kept; run again tomorrow to resume.")
                raise typer.Exit(code=3) from exc
            stage.items = stats.seen
            stage.counts = {"by_status": stats.by_status, "by_method": stats.by_method,
                            "openalex_spent_usd": round(openalex.budget.spent_usd, 4)}
    typer.echo(f"resolved this run: {stats.by_status} (parsed {stats.parsed}); "
               f"OpenAlex spent ${openalex.budget.spent_usd:.3f}")
    if sample_ids:
        summary = summarise(engine, sample_ids)
        done = summary["by_status"].get("resolved", 0)
        typer.echo(f"sample of {len(sample_ids)}: {summary['by_status']} "
                   f"-> {100 * done / len(sample_ids):.1f}% resolved; methods {summary['by_method']}")


@app.command()
def check(
    checks: Annotated[
        Optional[str], typer.Option(help="Comma-separated check IDs, e.g. C05")
    ] = None,
    limit: Annotated[Optional[int], typer.Option(help="Stop after N subjects")] = None,
    nct: Annotated[
        Optional[list[str]], typer.Option(help="Only these trials (repeatable)")
    ] = None,
    corpus: Annotated[
        Optional[str], typer.Option(help="Corpus label for paper checks")
    ] = None,
    paper: Annotated[
        Optional[list[int]], typer.Option(help="Only these paper IDs (repeatable)")
    ] = None,
) -> None:
    """Run checks over stored data and record a check_run."""
    from sentinel.checks.registry import build_checks
    from sentinel.checks.runner import run_checks
    from sentinel.ingest.clinicaltrials import normalise_nct_id

    settings = get_settings()
    requested = [c.strip().upper() for c in checks.split(",")] if checks else None
    try:
        built = build_checks(settings, requested)
        nct_ids = [normalise_nct_id(n) for n in nct] if nct else None
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if not built:
        raise typer.BadParameter("no implemented checks are enabled")
    try:
        result = run_checks(
            get_engine(),
            built,
            config_hash=settings.config_hash,
            corpus_label=corpus or settings.default_corpus_label,
            nct_ids=nct_ids,
            paper_ids=paper,
            limit=limit,
        )
    except ValueError as exc:
        raise typer.BadParameter(f"{exc}. Known corpora: {_corpus_labels()}") from exc
    typer.echo(f"run {result.run_id}: {result.counts}")


def _corpus_labels() -> list[str]:
    with get_engine().connect() as conn:
        return list(conn.execute(select(corpus_table.c.label).order_by(corpus_table.c.label)).scalars())


@app.command()
def report(
    subject: Annotated[str, typer.Argument(help="NCT ID, DOI or paper ID")],
    format: Annotated[str, typer.Option(help="text or json")] = "text",
) -> None:
    """Print the report card for a trial, or a paper with its linked trials."""
    if format not in ("text", "json"):
        raise typer.BadParameter("format must be text or json", param_hint="--format")
    try:
        kind, value = detect_subject(subject)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if kind != "trial":
        with get_engine().connect() as conn:
            paper_card = load_paper_card(conn, value if kind == "paper" else None,
                                         doi=value if kind == "doi" else None)
        if paper_card is None:
            typer.echo(f"No paper {value} in the database.", err=True)
            raise typer.Exit(code=1)
        typer.echo(render_paper_json(paper_card) if format == "json"
                   else render_paper_text(paper_card))
        return
    with get_engine().connect() as conn:
        card = load_trial_card(conn, value)
    if card is None:
        typer.echo(f"{value} is not in the database. Fetch it with "
                   f"`sentinel ingest clinicaltrials --nct {value}`.", err=True)
        raise typer.Exit(code=1)
    typer.echo(render_json(card) if format == "json" else render_text(card))


@app.command()
def stats(
    run: Annotated[int, typer.Option(help="Check run ID")],
    format: Annotated[str, typer.Option(help="text or json")] = "text",
) -> None:
    """Per-check counts and flag rates, cascade ratios, and wall time per stage."""
    if format not in ("text", "json"):
        raise typer.BadParameter("format must be text or json", param_hint="--format")
    with get_engine().connect() as conn:
        result = load_run_stats(conn, run)
    if result is None:
        typer.echo(f"No check run {run}.", err=True)
        raise typer.Exit(code=1)
    typer.echo(render_stats_json(result) if format == "json" else render_stats_text(result))


@app.command()
def export(
    run: Annotated[int, typer.Option(help="Check run ID")],
    out: Annotated[Path, typer.Option(help="File to write")],
    format: Annotated[str, typer.Option(help="csv or json")] = "csv",
) -> None:
    """Write a run's findings to a file, one row per finding."""
    if format not in ("csv", "json"):
        raise typer.BadParameter("format must be csv or json", param_hint="--format")
    with get_engine().connect() as conn:
        status = run_status(conn, run)
        if status is None:
            typer.echo(f"No check run {run}.", err=True)
            raise typer.Exit(code=1)
        n = export_findings(conn, run, out, format)
    note = "" if status == "done" else f" (run is {status}: findings may be incomplete)"
    typer.echo(f"{n} findings from run {run} written to {out}{note}")


if __name__ == "__main__":
    app()
