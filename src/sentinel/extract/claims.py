"""Claim skeletons for a corpus's citation contexts (§10.1 item 6, S2.7, ADR-053, ADR-054).

Each context gets one `claim` row per extractor and version. The extractor
is "llm:<model>", so models can be compared on the same contexts. The model
answers in a strict JSON schema at temperature 0. An answer that breaks a
grounding rule (`skeleton.grounding_errors`) goes back to the model with the
rules it broke, up to MAX_REPAIRS times. What is still invalid is stored as
valid=false with no skeleton.

Two ways to ask, with the same requests and the same rules:
- **direct:** a few calls at a time, in context order;
- **batch:** the gateway's batch API, which runs on capacity interactive
  users aren't using, as its docs advise for large runs. Contexts go in
  batches of BATCH_CONTEXTS, several at once, and each repair round is a
  new, smaller batch. A context the gateway didn't answer (an error line,
  or an expired batch) gets no row, so the next run asks again. If the run
  stops early, its batches still running are cancelled.

S2.7's metric is the validation failure rate: on the first answer, and after
repairs. Both are logged, and a run can be logged to MLflow.

A run skips contexts that already have a row from this extractor and
version, so a killed run resumes by being run again. `--sample N` takes N
contexts in an order fixed by the random seed, so every model in a
comparison sees the same ones.
"""

from __future__ import annotations

import time
from collections import Counter, deque
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from pydantic import ValidationError
from sqlalchemy import Connection, Engine, Text, cast, exists, func, literal, select
from sqlalchemy.dialects.postgresql import insert

from sentinel.extract import prompt
from sentinel.extract.llm import BatchClient, ChatClient, Completion, GatewayError, parse_completion, request_body
from sentinel.extract.skeleton import ClaimRecord, ClaimSkeleton, grounding_errors, response_schema
from sentinel.fulltext.contexts import EXTRACTOR_VERSION as CONTEXT_VERSION
from sentinel.ingest.base import Harvester, UpsertCounts
from sentinel.log import get_logger
from sentinel.models import citation_context, claim, corpus_paper

log = get_logger(__name__)

# Bump on any change to the prompt, the schema, the grounding rules or the
# settings below: each changes what is extracted.
EXTRACTOR_VER = "1.0.0"
MAX_REPAIRS = 2
MAX_TOKENS = 1024
SCHEMA_NAME = "claim_skeleton"
CHUNK = 32

# Batch mode. Operational: they change when answers arrive, not what they are.
BATCH_CONTEXTS = 5000  # about 21 MB of requests; the gateway takes up to 200 MB per file
BATCH_ACTIVE = 8  # the gateway runs up to 10 batches per key at once
BATCH_WINDOW = "24h"  # requests not started by then expire and are asked again next run
BATCH_POLL_S = 30.0
TERMINAL = ("completed", "failed", "expired", "cancelled")


@dataclass
class Outcome:
    """What happened to one context."""

    citation_context_id: int
    skeleton: ClaimSkeleton | None
    calls: int = 0
    first_errors: list[str] = field(default_factory=list)
    final_errors: list[str] = field(default_factory=list)
    rejected: bool = False  # the gateway refused the request itself (a 400)
    truncated: int = 0  # answers cut off at MAX_TOKENS
    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0
    energy_wh: float = 0.0
    co2_g: float = 0.0
    deployments: list[str] = field(default_factory=list)


@dataclass
class Attempt:
    """A context on its way through the rounds: its row, the conversation so
    far, the round it is in, and what has happened."""

    row: object
    messages: list[dict]
    out: Outcome
    round: int = 0


def check_answer(content: str, *, sentence: str, context: str) -> tuple[ClaimSkeleton | None, list[str]]:
    """The skeleton if the answer is valid, else None and the rules it breaks."""
    try:
        skeleton = ClaimSkeleton.model_validate_json(content)
    except ValidationError:
        return None, ["invalid_json"]
    errors = grounding_errors(skeleton, sentence=sentence, context=context)
    return (None if errors else skeleton), errors


def start(row) -> Attempt:
    return Attempt(row=row, out=Outcome(citation_context_id=row.id, skeleton=None),
                   messages=prompt.messages(sentence=row.sentence, context=row.context_text,
                                            marker=row.marker, section=row.section))


def ask(model: str, attempt: Attempt) -> dict:
    """What to ask for this attempt: the arguments of `ChatClient.complete`."""
    return {"model": model, "messages": attempt.messages, "schema": response_schema(),
            "schema_name": SCHEMA_NAME, "max_tokens": MAX_TOKENS}


def body(model: str, attempt: Attempt) -> dict:
    """The same request as a batch line's body."""
    return request_body(**ask(model, attempt))


def record_answer(attempt: Attempt, done: Completion) -> bool:
    """Count one answer and check it. True when the context is finished:
    valid, or out of repairs. Otherwise the repair request is queued in
    `attempt.messages` for the next round."""
    out = attempt.out
    out.calls += 1
    out.prompt_tokens += done.prompt_tokens
    out.completion_tokens += done.completion_tokens
    out.seconds += done.seconds
    out.energy_wh += done.energy_wh
    out.co2_g += done.co2_g
    out.truncated += done.finish_reason == "length"
    out.deployments.append(f"{done.deployment}|{done.fingerprint}")
    row = attempt.row
    skeleton, errors = check_answer(done.content, sentence=row.sentence, context=row.context_text)
    if attempt.round == 0:
        out.first_errors = errors
    out.final_errors = errors
    if skeleton is not None:
        out.skeleton = skeleton
        return True
    if attempt.round >= MAX_REPAIRS:
        return True
    attempt.messages = [*attempt.messages, {"role": "assistant", "content": done.content},
                        {"role": "user", "content": prompt.repair_message(errors)}]
    attempt.round += 1
    return False


def reject(attempt: Attempt) -> None:
    """The gateway refused the request itself (a 400): no answer to check."""
    attempt.out.rejected = True
    attempt.out.final_errors = ["request_rejected"]
    if attempt.round == 0:
        attempt.out.first_errors = ["request_rejected"]


def extract_one(chat: ChatClient, model: str, row) -> Outcome:
    """Direct mode: ask, check, and ask again with the broken rules, up to
    MAX_REPAIRS times."""
    attempt = start(row)
    while True:
        try:
            done = chat.complete(**ask(model, attempt))
        except GatewayError as exc:
            if exc.status != 400:
                raise  # a bad key, model or quota: stop the run, don't mark contexts
            reject(attempt)
            return attempt.out
        if record_answer(attempt, done):
            return attempt.out


@dataclass
class ExtractStats:
    contexts: int = 0
    valid_first: int = 0
    valid_repaired: int = 0
    invalid: int = 0
    rejected: int = 0
    unanswered: int = 0  # batch mode: no answer came back; asked again next run
    calls: int = 0
    truncated: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0
    energy_wh: float = 0.0
    co2_g: float = 0.0
    batches: int = 0
    first_errors: Counter = field(default_factory=Counter)
    final_errors: Counter = field(default_factory=Counter)
    kinds: Counter = field(default_factory=Counter)
    deployments: Counter = field(default_factory=Counter)

    def add(self, out: Outcome) -> None:
        self.contexts += 1
        if out.rejected:
            self.rejected += 1
        elif out.skeleton is None:
            self.invalid += 1
        elif out.first_errors:
            self.valid_repaired += 1
        else:
            self.valid_first += 1
        if out.skeleton is not None:
            self.kinds[out.skeleton.kind] += 1
        self.first_errors.update(out.first_errors)
        if out.skeleton is None:
            self.final_errors.update(out.final_errors)
        self.calls += out.calls
        self.truncated += out.truncated
        self.prompt_tokens += out.prompt_tokens
        self.completion_tokens += out.completion_tokens
        self.seconds += out.seconds
        self.energy_wh += out.energy_wh
        self.co2_g += out.co2_g
        self.deployments.update(out.deployments)

    def rates(self) -> dict[str, float]:
        """S2.7's metric: the share of contexts whose first answer failed
        validation, and the share still failing after repairs."""
        n = self.contexts or 1
        return {
            "failure_rate_first": 1 - self.valid_first / n,
            "failure_rate_final": (self.invalid + self.rejected) / n,
            "repaired_share": self.valid_repaired / n,
            "calls_per_context": self.calls / n,
            "seconds_per_call": self.seconds / (self.calls or 1),
            "prompt_tokens_per_context": self.prompt_tokens / n,
            "completion_tokens_per_context": self.completion_tokens / n,
            "energy_wh_per_context": self.energy_wh / n,
        }


class ClaimExtractor(Harvester):
    def __init__(self, engine: Engine, chat: ChatClient | None, *, label: str, model: str, seed: int,
                 sample: int | None = None, workers: int = 4, batch: BatchClient | None = None,
                 batch_contexts: int = BATCH_CONTEXTS, poll_s: float = BATCH_POLL_S,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        if sample is not None and sample <= 0:
            raise ValueError("sample must be positive")
        if (chat is None) == (batch is None):
            raise ValueError("give a chat client for direct calls or a batch client, not both")
        self.engine = engine
        self.chat = chat
        self.batch = batch
        self.label = label
        self.model = model
        self.seed = seed
        self.sample = sample
        self.workers = workers
        self.batch_contexts = batch_contexts
        self.poll_s = poll_s
        self._sleep = sleep
        self.extractor = f"llm:{model}"
        self.source = f"claims:{label}:{model}"
        self.stats = ExtractStats()

    def _scope(self):
        """The corpus's citation contexts from the current context extractor;
        with a sample, the first N in the seed's hash order."""
        in_corpus = select(corpus_paper.c.paper_id).where(corpus_paper.c.label == self.label)
        stmt = select(citation_context.c.id).where(
            citation_context.c.citing_paper_id.in_(in_corpus),
            citation_context.c.extractor_ver == CONTEXT_VERSION)
        if self.sample:
            order = func.md5(literal(f"{self.seed}:", Text) + cast(citation_context.c.id, Text))
            stmt = stmt.order_by(order, citation_context.c.id).limit(self.sample)
        return stmt

    def pending_ids(self) -> list[int]:
        scope = self._scope().subquery()
        done = exists().where(claim.c.citation_context_id == scope.c.id,
                              claim.c.extractor == self.extractor,
                              claim.c.extractor_ver == EXTRACTOR_VER)
        with self.engine.connect() as conn:
            return list(conn.execute(select(scope.c.id).where(~done).order_by(scope.c.id)).scalars())

    def _rows(self, ids: list[int]) -> list:
        cols = (citation_context.c.id, citation_context.c.sentence, citation_context.c.context_text,
                citation_context.c.marker, citation_context.c.section)
        with self.engine.connect() as conn:
            return conn.execute(select(*cols).where(citation_context.c.id.in_(ids))
                                .order_by(citation_context.c.id)).all()

    def fetch(self, cursor: str | None) -> Iterator[tuple[dict, str | None]]:
        # The cursor isn't needed to resume: contexts with a row are skipped.
        ids = self.pending_ids()
        log.info("claims_pending", extractor=self.extractor, contexts=len(ids), sample=self.sample,
                 mode="batch" if self.batch else "direct")
        outcomes = self._batched(ids) if self.batch else self._direct(ids)
        for out in outcomes:
            self.stats.add(out)
            yield ({"citation_context_id": out.citation_context_id, "skeleton": out.skeleton},
                   f"{self.label}:{out.citation_context_id}")

    def _direct(self, ids: list[int]) -> Iterator[Outcome]:
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            for i in range(0, len(ids), CHUNK):
                yield from pool.map(lambda r: extract_one(self.chat, self.model, r),
                                    self._rows(ids[i:i + CHUNK]))

    def _submit(self, attempts: dict[str, Attempt]) -> str:
        lines = [{"custom_id": cid, "method": "POST", "url": "/v1/chat/completions",
                  "body": body(self.model, a)} for cid, a in attempts.items()]
        batch_id = self.batch.submit(lines, name=f"sentinel-{self.source}", window=BATCH_WINDOW)
        self.stats.batches += 1
        log.info("claims_batch_submitted", batch=batch_id, requests=len(lines),
                 round=next(iter(attempts.values())).round)
        return batch_id

    def _batched(self, ids: list[int]) -> Iterator[Outcome]:
        waiting = deque(ids[i:i + self.batch_contexts] for i in range(0, len(ids), self.batch_contexts))
        active: dict[str, dict[str, Attempt]] = {}
        try:
            while waiting or active:
                while waiting and len(active) < BATCH_ACTIVE:
                    attempts = {f"{r.id}:0": start(r) for r in self._rows(waiting.popleft())}
                    active[self._submit(attempts)] = attempts
                finished_any = False
                for batch_id in list(active):
                    status = self.batch.status(batch_id)
                    if status.get("status") not in TERMINAL:
                        continue
                    finished_any = True
                    attempts = active.pop(batch_id)
                    log.info("claims_batch_done", batch=batch_id, status=status.get("status"),
                             counts=status.get("request_counts"), errors=status.get("errors"))
                    again: dict[str, Attempt] = {}
                    for line in self.batch.result_lines(status):
                        attempt = attempts.pop(line.get("custom_id"), None)
                        if attempt is None:
                            continue
                        response = line.get("response") or {}
                        code = response.get("status_code")
                        if code == 400:
                            reject(attempt)
                            yield attempt.out
                        elif code != 200 or line.get("error"):
                            attempts[line["custom_id"]] = attempt  # unanswered; asked next run
                        elif record_answer(attempt, parse_completion(response["body"])):
                            yield attempt.out
                        else:
                            again[f"{attempt.row.id}:{attempt.round}"] = attempt
                    self.stats.unanswered += len(attempts)
                    if again:
                        active[self._submit(again)] = again
                if active and not finished_any:
                    self._sleep(self.poll_s)
        finally:
            for batch_id in active:  # stopped early: free the gateway's capacity
                try:
                    self.batch.cancel(batch_id)
                    log.warning("claims_batch_cancelled", batch=batch_id)
                except Exception as exc:  # noqa: BLE001 - best effort while unwinding
                    log.warning("claims_batch_cancel_failed", batch=batch_id, error=type(exc).__name__)

    def record_key(self, raw: dict) -> str | None:
        return str(raw.get("citation_context_id"))

    def normalise(self, raw: dict) -> ClaimRecord:
        return ClaimRecord(citation_context_id=raw["citation_context_id"], skeleton=raw["skeleton"],
                           extractor=self.extractor, extractor_ver=EXTRACTOR_VER,
                           valid=raw["skeleton"] is not None)

    def upsert(self, conn: Connection, records: list[ClaimRecord]) -> UpsertCounts:
        written = len(conn.execute(
            insert(claim).on_conflict_do_nothing(constraint="claim_context_extractor_key")
            .returning(claim.c.id), [r.row() for r in records]).all())
        return UpsertCounts(inserted=written, unchanged=len(records) - written)


def stored_summary(conn: Connection, *, label: str, extractor: str) -> dict:
    """What is stored for a corpus from one extractor and version, across runs."""
    in_corpus = select(corpus_paper.c.paper_id).where(corpus_paper.c.label == label)
    rows = conn.execute(
        select(claim.c.valid, claim.c.skeleton["kind"].astext.label("kind"), func.count())
        .join(citation_context, citation_context.c.id == claim.c.citation_context_id)
        .where(citation_context.c.citing_paper_id.in_(in_corpus), claim.c.extractor == extractor,
               claim.c.extractor_ver == EXTRACTOR_VER)
        .group_by(claim.c.valid, "kind")).all()
    valid = sum(r[2] for r in rows if r.valid)
    invalid = sum(r[2] for r in rows if not r.valid)
    return {"valid": valid, "invalid": invalid,
            "kinds": {r.kind: r[2] for r in rows if r.valid}}


def log_to_mlflow(stats: ExtractStats, params: dict[str, object], *, tracking_uri: str,
                  experiment: str = "claim-extraction") -> str:
    """One MLflow run: the settings as params, the rates and counts as metrics."""
    import sys

    import mlflow

    for stream in (sys.stdout, sys.stderr):  # cp1252 consoles (see eval.retrieval)
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment(experiment)
    with mlflow.start_run(run_name=f"{params['model']} {params['sample']}") as run:
        mlflow.log_params({**params, "deployments": ", ".join(sorted(stats.deployments))[:500]})
        mlflow.log_metrics({**stats.rates(), "contexts": stats.contexts,
                            "valid_first": stats.valid_first, "valid_repaired": stats.valid_repaired,
                            "invalid": stats.invalid, "rejected": stats.rejected,
                            "unanswered": stats.unanswered, "truncated": stats.truncated,
                            "batches": stats.batches, "energy_wh": stats.energy_wh,
                            "co2_g": stats.co2_g,
                            **{f"kind_{k}": v for k, v in stats.kinds.items()},
                            **{f"first_error_{k}": v for k, v in stats.first_errors.items()},
                            **{f"final_error_{k}": v for k, v in stats.final_errors.items()}})
        return run.info.run_id
