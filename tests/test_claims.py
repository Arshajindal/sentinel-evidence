"""Claim skeletons (§10.1 item 6, S2.7, ADR-053). No network: the gateway is
a saved response behind httpx.MockTransport, or a scripted fake."""

from __future__ import annotations

import json

import httpx
import pytest
from sqlalchemy import delete, insert, select

from sentinel.extract import prompt
from sentinel.extract.claims import (
    EXTRACTOR_VER, MAX_REPAIRS, ClaimExtractor, check_answer, extract_one, stored_summary)
from sentinel.extract.llm import ChatClient, Completion, GatewayError
from sentinel.extract.skeleton import (
    ClaimRecord, ClaimSkeleton, grounding_errors, norm, numbers_in, response_schema)
from sentinel.fulltext.contexts import EXTRACTOR_VERSION as CONTEXT_VERSION
from sentinel.ingest.http import RateLimitedHTTP
from sentinel.models import citation_context, claim, corpus, corpus_paper, ingest_watermark, paper
from tests.conftest import FIXTURES, requires_db

# A made-up context in the shape of the corpus's: the citing sentence with a
# sentence either side.
BEFORE = "Hypertension is common in older adults."
SENTENCE = ("In the TRIAL-X study, Drug A lowered systolic blood pressure by 12 mmHg in 1,204 "
            "adults with hypertension compared with placebo [7].")
AFTER = "Its effect on stroke is unknown."
CONTEXT = f"{BEFORE} {SENTENCE} {AFTER}"


def finding(**overrides) -> ClaimSkeleton:
    fields = dict(kind="finding",
                  statement="Drug A lowered systolic blood pressure by 12 mmHg in 1,204 adults "
                            "with hypertension compared with placebo",
                  population="adults with hypertension", intervention="Drug A", comparator="placebo",
                  outcome="systolic blood pressure", effect="by 12 mmHg", n=1204, direction="decrease")
    return ClaimSkeleton(**{**fields, **overrides})


def errors(skeleton: ClaimSkeleton) -> list[str]:
    return grounding_errors(skeleton, sentence=SENTENCE, context=CONTEXT)


# --- the schema and the grounding rules -------------------------------------------------

def test_schema_is_strict_every_field_required_and_nullable_where_unstated():
    schema = response_schema()
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(ClaimSkeleton.model_fields)
    assert "description" not in schema  # the docstring isn't sent to the model
    nullable = {k for k, v in schema["properties"].items()
                if {"type": "null"} in v.get("anyOf", [])}
    assert nullable == {"population", "intervention", "comparator", "outcome", "effect", "n", "direction"}


def test_a_grounded_finding_is_valid():
    assert errors(finding()) == []


def test_case_dashes_and_spacing_do_not_count_as_changes():
    context = "Patients with type‑2 diabetes  had lower HbA1c."
    sk = finding(statement="patients with type-2 diabetes had lower hba1c",
                 population="Patients with type-2 diabetes", intervention=None, comparator=None,
                 outcome="HbA1c", effect=None, n=None)
    assert grounding_errors(sk, sentence=context, context=context) == []
    assert norm(" Type–2  Diabetes. ") == "type-2 diabetes"


def test_a_paraphrased_or_invented_span_fails():
    assert errors(finding(population="hypertensive adults")) == ["population_not_in_context"]
    assert errors(finding(comparator="usual care")) == ["comparator_not_in_context"]
    assert errors(finding(effect="a 12-point reduction")) == ["effect_not_in_context"]


def test_the_statement_must_come_from_the_citing_sentence_not_its_neighbours():
    assert errors(finding(statement="Its effect on stroke is unknown")) == ["statement_not_in_sentence"]
    assert errors(finding(statement="  ")) == ["statement_empty"]


def test_a_sample_size_must_be_written_in_the_context():
    assert errors(finding(n=1204)) == []  # written "1,204"
    assert errors(finding(n=1200)) == ["n_not_in_context"]
    assert errors(finding(n=0)) == ["n_not_in_context"]
    assert numbers_in("n = 1,204; 95% CI 0.5-0.8") == {1204, 95, 0, 5, 8}


def test_only_a_finding_fills_the_finding_fields_and_it_needs_a_direction():
    assert errors(finding(direction=None)) == ["finding_without_direction"]
    method = finding(kind="method")
    assert errors(method) == ["fields_set_for_non_finding"]
    bare = ClaimSkeleton(kind="background", statement="Drug A lowered systolic blood pressure",
                         population=None, intervention=None, comparator=None, outcome=None,
                         effect=None, n=None, direction=None)
    assert errors(bare) == []


def test_empty_and_overlong_spans_fail():
    assert errors(finding(outcome="")) == ["outcome_empty"]
    assert errors(finding(outcome="x" * 401)) == ["outcome_too_long"]


def test_answers_that_are_not_the_schema_fail_as_invalid_json():
    good = finding().model_dump_json()
    assert check_answer(good, sentence=SENTENCE, context=CONTEXT) == (finding(), [])
    extra = json.dumps({**json.loads(good), "confidence": 0.9})
    for bad in ("not json", '{"kind": "finding"}', extra, good.replace('"decrease"', '"down"')):
        assert check_answer(bad, sentence=SENTENCE, context=CONTEXT) == (None, ["invalid_json"])


def test_a_claim_row_holds_a_skeleton_exactly_when_valid():
    ClaimRecord(citation_context_id=1, skeleton=finding(), extractor="llm:m", extractor_ver="1", valid=True)
    ClaimRecord(citation_context_id=1, skeleton=None, extractor="llm:m", extractor_ver="1", valid=False)
    for skeleton, valid in ((None, True), (finding(), False)):
        with pytest.raises(ValueError):
            ClaimRecord(citation_context_id=1, skeleton=skeleton, extractor="llm:m",
                        extractor_ver="1", valid=valid)


# --- the prompt ---------------------------------------------------------------------------

def test_paper_text_goes_only_inside_tags_and_the_system_message_calls_it_untrusted():
    msgs = prompt.messages(sentence=SENTENCE, context=CONTEXT, marker="7", section="discussion")
    assert [m["role"] for m in msgs] == ["system", "user"]
    assert "untrusted" in msgs[0]["content"] and CONTEXT not in msgs[0]["content"]
    user = msgs[1]["content"]
    assert f"<passage>\n{CONTEXT}\n</passage>" in user
    assert f"<citing_sentence>\n{SENTENCE}\n</citing_sentence>" in user
    assert 'citation "7"' in user and "Section: discussion" in user


def test_every_rule_code_has_a_sentence_for_the_repair_message():
    codes = {"statement_empty", "statement_not_in_sentence", "n_not_in_context",
             "finding_without_direction", "fields_set_for_non_finding", "invalid_json"}
    for f in ("population", "intervention", "comparator", "outcome", "effect"):
        codes |= {f"{f}_empty", f"{f}_too_long", f"{f}_not_in_context"}
    assert codes <= set(prompt.RULES)
    message = prompt.repair_message(["population_not_in_context", "n_not_in_context"])
    assert "population is not copied word for word" in message and "n is not a number" in message


# --- the gateway client -------------------------------------------------------------------

def _saved_completion() -> dict:
    return json.loads((FIXTURES / "llm" / "chat_completion.json").read_text(encoding="utf-8"))


def _chat(handler, *, retries: int = 2) -> ChatClient:
    http = RateLimitedHTTP(httpx.Client(transport=httpx.MockTransport(handler)), source="llm",
                           rate_per_s=1000, max_retries=retries, backoff_base_s=0,
                           sleep=lambda s: None)
    return ChatClient(http, base_url="https://gateway.test/v1/", api_key="test-key")


def test_a_completion_is_requested_strict_at_temperature_0_and_parsed():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_saved_completion(), headers={"x-litellm-model-id": "dep-1"})

    done = _chat(handler).complete(model="gemma4-31b-it", messages=[{"role": "user", "content": "x"}],
                                   schema=response_schema(), schema_name="claim_skeleton", max_tokens=64)
    request = seen[0]
    assert str(request.url) == "https://gateway.test/v1/chat/completions"
    assert request.headers["Authorization"] == "Bearer test-key"
    body = json.loads(request.content)
    assert body["model"] == "gemma4-31b-it" and body["temperature"] == 0 and body["max_tokens"] == 64
    fmt = body["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
    assert fmt["json_schema"]["schema"] == response_schema()
    assert (done.prompt_tokens, done.completion_tokens, done.finish_reason) == (912, 88, "stop")
    assert (done.deployment, done.fingerprint) == ("dep-1", "vllm-0.26.0-tp4-24487d56")
    assert ClaimSkeleton.model_validate_json(done.content).n == 1204


def test_a_busy_model_is_retried_and_a_bad_key_is_not():
    calls = []

    def busy_then_ok(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(503, json={"error": {"message": "timed out waiting for model capacity"}},
                                  headers={"Retry-After": "0"})
        return httpx.Response(200, json=_saved_completion())

    _chat(busy_then_ok).complete(model="m", messages=[], schema={}, schema_name="s", max_tokens=8)
    assert len(calls) == 2

    calls.clear()

    def bad_key(request):
        calls.append(1)
        return httpx.Response(401, json={"error": {"message": "invalid api key"}})

    with pytest.raises(GatewayError) as exc:
        _chat(bad_key).complete(model="m", messages=[], schema={}, schema_name="s", max_tokens=8)
    assert exc.value.status == 401 and "invalid api key" in str(exc.value) and len(calls) == 1


def test_an_empty_key_is_refused_before_any_call():
    with pytest.raises(ValueError, match="SENTINEL_LLM_API_KEY"):
        ChatClient(None, base_url="https://gateway.test/v1", api_key="")  # type: ignore[arg-type]


# --- one context: ask, check, repair ---------------------------------------------------

class FakeChat:
    """Answers from a script keyed by the passage, one answer per call."""

    def __init__(self, script: dict[str, list]) -> None:
        self.script = {k: list(v) for k, v in script.items()}
        self.requests: list[list[dict]] = []

    def complete(self, *, model, messages, schema, schema_name, max_tokens) -> Completion:
        self.requests.append(messages)
        passage = messages[1]["content"].split("<passage>\n", 1)[1].split("\n</passage>", 1)[0]
        answer = self.script[passage].pop(0)
        if isinstance(answer, Exception):
            raise answer
        content = answer.model_dump_json() if isinstance(answer, ClaimSkeleton) else answer
        return Completion(content=content, finish_reason="stop", prompt_tokens=100,
                          completion_tokens=20, deployment="dep", fingerprint="fp", seconds=0.5)


class Row:
    def __init__(self, id: int, context: str = CONTEXT, sentence: str = SENTENCE) -> None:
        self.id, self.context_text, self.sentence = id, context, sentence
        self.marker, self.section = "7", "discussion"


def test_a_broken_rule_goes_back_to_the_model_and_a_fixed_answer_is_kept():
    chat = FakeChat({CONTEXT: [finding(population="hypertensive adults"), finding()]})
    out = extract_one(chat, "m", Row(1))
    assert out.skeleton == finding() and out.calls == 2
    assert out.first_errors == ["population_not_in_context"] and out.final_errors == []
    repair = chat.requests[1]
    assert [m["role"] for m in repair] == ["system", "user", "assistant", "user"]
    assert "population is not copied word for word" in repair[3]["content"]


def test_a_context_still_invalid_after_the_repairs_has_no_skeleton():
    chat = FakeChat({CONTEXT: ["not json"] * (MAX_REPAIRS + 1)})
    out = extract_one(chat, "m", Row(1))
    assert out.skeleton is None and out.calls == MAX_REPAIRS + 1
    assert out.first_errors == out.final_errors == ["invalid_json"]


def test_a_refused_request_is_marked_and_a_bad_key_stops_the_run():
    out = extract_one(FakeChat({CONTEXT: [GatewayError(400, "context too long")]}), "m", Row(1))
    assert out.rejected and out.skeleton is None and out.final_errors == ["request_rejected"]
    with pytest.raises(GatewayError):
        extract_one(FakeChat({CONTEXT: [GatewayError(401, "invalid api key")]}), "m", Row(1))


# --- a run against the test database ---------------------------------------------------

LABEL = "test-s27"
TITLE = "s27-citing"


def _context(i: int) -> str:
    return f"Background {i}. {SENTENCE} After {i}."


@pytest.fixture
def s27_engine(db_engine):
    with db_engine.connect() as conn:
        if conn.execute(select(paper.c.id).where(paper.c.title != TITLE)).first():
            pytest.skip("paper holds other data; not a disposable database")
    _clear(db_engine)
    with db_engine.begin() as conn:
        pid = conn.execute(insert(paper).values(title=TITLE, title_norm=TITLE, source="test", raw={})
                           .returning(paper.c.id)).scalar_one()
        conn.execute(insert(corpus).values(label=LABEL, source="t", query="t", seed=1, hit_count=1, n=1))
        conn.execute(insert(corpus_paper).values(label=LABEL, ordinal=1, source="PMC", source_id="x",
                                                 pmcid="PMC1", paper_id=pid))
        conn.execute(insert(citation_context), [
            {"citing_paper_id": pid, "extractor_ver": CONTEXT_VERSION, "ordinal": i, "section": "discussion",
             "marker": "7", "sentence": SENTENCE, "context_text": _context(i)} for i in range(1, 7)])
    yield db_engine
    _clear(db_engine)


def _clear(engine) -> None:
    with engine.begin() as conn:
        conn.execute(delete(paper).where(paper.c.title == TITLE))  # cascades to contexts and claims
        conn.execute(delete(corpus).where(corpus.c.label == LABEL))
        conn.execute(delete(ingest_watermark).where(ingest_watermark.c.source.like(f"claims:{LABEL}:%")))


def _script(ids: range, *, bad: set[int] = frozenset()) -> dict[str, list]:
    return {_context(i): (["not json"] * (MAX_REPAIRS + 1) if i in bad else [finding()]) for i in ids}


@requires_db
def test_a_run_stores_one_claim_per_context_and_a_rerun_does_nothing(s27_engine):
    chat = FakeChat(_script(range(1, 7), bad={4}))
    ex = ClaimExtractor(s27_engine, chat, label=LABEL, model="m", seed=1, workers=3)
    stats = ex.run(s27_engine, batch_size=4)
    assert stats.inserted == 6
    with s27_engine.connect() as conn:
        rows = conn.execute(select(claim.c.valid, claim.c.skeleton, claim.c.extractor, claim.c.extractor_ver)
                            .order_by(claim.c.citation_context_id)).all()
        summary = stored_summary(conn, label=LABEL, extractor="llm:m")
    assert [r.valid for r in rows] == [True, True, True, False, True, True]
    assert rows[3].skeleton is None and rows[0].skeleton == finding().model_dump()
    assert {(r.extractor, r.extractor_ver) for r in rows} == {("llm:m", EXTRACTOR_VER)}
    assert summary == {"valid": 5, "invalid": 1, "kinds": {"finding": 5}}
    assert ex.stats.valid_first == 5 and ex.stats.invalid == 1
    assert ex.stats.rates()["failure_rate_first"] == pytest.approx(1 / 6)

    again = ClaimExtractor(s27_engine, FakeChat({}), label=LABEL, model="m", seed=1)
    assert again.pending_ids() == [] and again.run(s27_engine).seen == 0


@requires_db
def test_a_sample_is_the_same_contexts_for_every_model_and_changes_with_the_seed(s27_engine):
    def sample(model: str, seed: int) -> list[int]:
        return ClaimExtractor(s27_engine, FakeChat({}), label=LABEL, model=model, seed=seed,
                              sample=3).pending_ids()

    first = sample("a", 1)
    assert len(first) == 3 and first == sample("b", 1)
    assert {tuple(sample("a", s)) for s in range(1, 8)} != {tuple(first)}

    # A model that already did part of its sample gets only the rest.
    ex = ClaimExtractor(s27_engine, FakeChat(_script(range(1, 7))), label=LABEL, model="a", seed=1, sample=3)
    ex.run(s27_engine, limit=1)
    assert ex.pending_ids() == first[1:]
