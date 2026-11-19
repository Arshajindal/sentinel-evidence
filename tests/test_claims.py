"""Claim skeletons (§10.1 item 6, S2.7, ADR-053). No network: the gateway is
a saved response behind httpx.MockTransport, or a scripted fake."""

from __future__ import annotations

import json
from collections import Counter

import httpx
import pytest
from sqlalchemy import delete, insert, select

from sentinel.extract import prompt
from sentinel.extract.claims import (
    EXTRACTOR_VER, MAX_REPAIRS, ClaimExtractor, carry_over, check_answer, extract_one, stored_summary)
from sentinel.extract.llm import BatchClient, ChatClient, Completion, GatewayError, parse_completion
from sentinel.extract.skeleton import (
    MAX_ANSWER_CHARS, ClaimRecord, ClaimSkeleton, grounding_errors, norm, numbers_in, response_schema,
    statement_in_sentence)
from sentinel.fulltext.contexts import EXTRACTOR_VERSION as CONTEXT_VERSION
from sentinel.ingest.http import RateLimitedHTTP
from sentinel.models import (
    citation_context, citation_context_reference, claim, corpus, corpus_paper, ingest_watermark, paper,
    reference)
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


def _cites(sentence: str, *marks: str) -> list[tuple[int, int]]:
    return [(sentence.index(m), sentence.index(m) + len(m)) for m in marks]


def test_the_statement_may_leave_out_the_citation_markers_as_the_prompt_asks():
    # Both shapes are from the full S2.7 run's invalid contexts (failure log, 2026-11-14).
    sentence = "Halse et al. [60] noted that drains [61] were removed early."
    cites = _cites(sentence, "[60]", "[61]")
    for statement in ("Halse et al. noted that drains were removed early",  # every marker out
                      "Halse et al. noted that drains [61] were removed",  # only its own out
                      "Halse et al. [60] noted that drains [61] were removed early"):  # exact copy
        assert statement_in_sentence(statement, sentence, cites), statement
    superscript = "These include chemotherapy,16,17,18,19,20 offering relief."
    cites = [(superscript.index("16"), superscript.index(" offering"))]
    assert statement_in_sentence("chemotherapy, offering relief", superscript, cites)


def test_leaving_out_a_marker_excuses_nothing_else():
    sentence = "Halse et al. [60] noted that drains [61] were removed early."
    cites = _cites(sentence, "[60]", "[61]")
    assert not statement_in_sentence("Halse noted that drains were removed early", sentence, cites)
    assert not statement_in_sentence("drains were taken out early", sentence, cites)  # paraphrase
    assert not statement_in_sentence("Halse et al. noted", sentence, [])  # 1.0.0's rule without spans
    superscript = "These include chemotherapy,16,17,18,19,20 offering relief."
    cites = [(superscript.index("16"), superscript.index(" offering"))]
    assert not statement_in_sentence("chemotherapy offering relief", superscript, cites)  # comma kept


def test_grounding_takes_the_sentences_citations():
    sentence = "Drug A lowered systolic blood pressure [7] by 12 mmHg in 1,204 adults [8]."
    sk = finding(statement="Drug A lowered systolic blood pressure by 12 mmHg in 1,204 adults",
                 population="adults", comparator=None)
    assert grounding_errors(sk, sentence=sentence, context=sentence) == ["statement_not_in_sentence"]
    assert grounding_errors(sk, sentence=sentence, context=sentence,
                            cites=_cites(sentence, "[7]", "[8]")) == []


def test_an_invalid_claim_keeps_its_answer_and_a_valid_one_only_its_skeleton():
    invalid = ClaimRecord(citation_context_id=1, skeleton=None, extractor="llm:m", extractor_ver="1.1.0",
                          valid=False, answer="not json", errors=["invalid_json"])
    assert invalid.row()["answer"] == "not json" and invalid.row()["errors"] == ["invalid_json"]
    with pytest.raises(ValueError):
        ClaimRecord(citation_context_id=1, skeleton=finding(), extractor="llm:m", extractor_ver="1.1.0",
                    valid=True, answer="{}")
    with pytest.raises(ValueError):
        ClaimRecord(citation_context_id=1, skeleton=None, extractor="llm:m", extractor_ver="1.1.0",
                    valid=False, answer="x" * (MAX_ANSWER_CHARS + 1), errors=["invalid_json"])


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
    assert request.headers["x-obleth-boons"] == "off"  # add-ons off, for reproducibility
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


def test_the_current_gateway_reports_the_variant_energy_and_co2():
    data = _saved_completion()
    data["usage"] |= {"energy_wh": 0.39, "co2_g": 0.13}
    done = parse_completion(data, {"x-obleth-model-variant": "olmo3-32b-instruct"})
    assert (done.deployment, done.energy_wh, done.co2_g) == ("olmo3-32b-instruct", 0.39, 0.13)
    assert parse_completion(_saved_completion()).deployment is None  # a batch line has no headers


def test_the_batch_client_uploads_jsonl_creates_reads_and_cancels():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        if path == "/v1/files":
            return httpx.Response(200, json={"id": "file-in", "purpose": "batch"})
        if path == "/v1/batches":
            return httpx.Response(200, json={"id": "batch-1", "status": "validating"})
        if path == "/v1/batches/batch-1":
            return httpx.Response(200, json={"id": "batch-1", "status": "completed",
                                             "output_file_id": "file-out", "error_file_id": "file-err"})
        if path == "/v1/files/file-out/content":
            return httpx.Response(200, text='{"custom_id": "1:0", "response": {"status_code": 200}}\n')
        if path == "/v1/files/file-err/content":
            return httpx.Response(200, text='{"custom_id": "2:0", "error": {"code": "batch_expired"}}\n\n')
        if path == "/v1/batches/batch-1/cancel":
            return httpx.Response(200, json={"id": "batch-1", "status": "cancelling"})
        return httpx.Response(404)

    http = RateLimitedHTTP(httpx.Client(transport=httpx.MockTransport(handler)), source="llm",
                           rate_per_s=1000, max_retries=0, backoff_base_s=0)
    client = BatchClient(http, base_url="https://gateway.test/v1", api_key="test-key")
    lines = [{"custom_id": "1:0", "method": "POST", "url": "/v1/chat/completions", "body": {"model": "m"}},
             {"custom_id": "2:0", "method": "POST", "url": "/v1/chat/completions", "body": {"model": "m"}}]
    assert client.submit(lines, name="test", window="24h") == "batch-1"
    upload, create = seen[0], seen[1]
    assert b'name="purpose"' in upload.content and b"batch" in upload.content
    assert b'{"custom_id": "1:0"' in upload.content and b'{"custom_id": "2:0"' in upload.content
    assert json.loads(create.content) == {"input_file_id": "file-in", "endpoint": "/v1/chat/completions",
                                          "completion_window": "24h", "metadata": {"name": "test"}}
    assert all(r.headers["Authorization"] == "Bearer test-key" for r in seen)
    status = client.status("batch-1")
    assert [line["custom_id"] for line in client.result_lines(status)] == ["1:0", "2:0"]
    client.cancel("batch-1")
    assert seen[-1].method == "POST" and seen[-1].url.path == "/v1/batches/batch-1/cancel"


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
    def __init__(self, id: int, context: str = CONTEXT, sentence: str = SENTENCE,
                 cites: list | None = None) -> None:
        self.id, self.context_text, self.sentence = id, context, sentence
        self.marker, self.section, self.sentence_cites = "7", "discussion", cites


def test_a_broken_rule_goes_back_to_the_model_and_a_fixed_answer_is_kept():
    chat = FakeChat({CONTEXT: [finding(population="hypertensive adults"), finding()]})
    out = extract_one(chat, "m", Row(1))
    assert out.skeleton == finding() and out.calls == 2
    assert out.first_errors == ["population_not_in_context"] and out.final_errors == []
    repair = chat.requests[1]
    assert [m["role"] for m in repair] == ["system", "user", "assistant", "user"]
    assert "population is not copied word for word" in repair[3]["content"]


def test_a_statement_without_its_marker_is_valid_at_once_when_the_context_has_spans():
    sentence = "In the TRIAL-X study [7], Drug A lowered systolic blood pressure."
    statement = "In the TRIAL-X study, Drug A lowered systolic blood pressure"
    answer = finding(statement=statement, population=None, effect=None, n=None, comparator=None,
                     outcome="systolic blood pressure", direction="decrease")
    old = extract_one(FakeChat({sentence: [answer] * 3}), "m", Row(1, sentence, sentence))
    assert old.skeleton is None and old.final_errors == ["statement_not_in_sentence"]  # 1.0.0 contexts
    new = extract_one(FakeChat({sentence: [answer]}), "m", Row(1, sentence, sentence, _cites(sentence, "[7]")))
    assert new.skeleton == answer and new.calls == 1


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
    with s27_engine.connect() as conn:
        kept = conn.execute(select(claim.c.valid, claim.c.answer, claim.c.errors)
                            .order_by(claim.c.citation_context_id)).all()
    assert kept[3] == (False, "not json", ["invalid_json"])  # the last answer, for re-checking
    assert all(r.answer is None and r.errors is None for r in kept if r.valid)
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


@requires_db
def test_valid_skeletons_carry_over_only_where_the_models_input_is_unchanged(s27_engine):
    """Old contexts 1..6 beside the fixture's current ones: 1 unchanged and
    valid, 2 invalid, 3 with another passage, 4 citing other references, 5
    already done, 6 unchanged but breaking the current rules."""
    with s27_engine.begin() as conn:
        pid = conn.execute(select(paper.c.id).where(paper.c.title == TITLE)).scalar_one()
        new = dict(conn.execute(select(citation_context.c.ordinal, citation_context.c.id)).all())
        old = dict(conn.execute(insert(citation_context).returning(
            citation_context.c.ordinal, citation_context.c.id), [
            {"citing_paper_id": pid, "extractor_ver": "0.9.0", "ordinal": i, "section": "discussion",
             "marker": "7", "sentence": SENTENCE,
             "context_text": f"Other {i}. {SENTENCE}" if i == 3 else _context(i)} for i in range(1, 7)]).all())
        ref = conn.execute(insert(reference).values(citing_paper_id=pid, ordinal=1, raw={})
                           .returning(reference.c.id)).scalar_one()
        conn.execute(insert(citation_context_reference).values(citation_context_id=old[4], reference_id=ref))
        drifted = finding(statement="a statement the sentence never had")
        conn.execute(insert(claim), [
            ClaimRecord(citation_context_id=old[i], skeleton=None if i == 2 else drifted if i == 6 else finding(),
                        extractor="llm:m", extractor_ver="0.9.0", valid=i != 2,
                        errors=["invalid_json"] if i == 2 else None).row() for i in range(1, 7)])
        conn.execute(insert(claim).values(ClaimRecord(
            citation_context_id=new[5], skeleton=finding(effect=None), extractor="llm:m",
            extractor_ver=EXTRACTOR_VER, valid=True).row()))

    stats = carry_over(s27_engine, label=LABEL, extractor="llm:m", from_ver="0.9.0")
    assert (stats.matched, stats.carried, stats.failed_rules) == (2, 1, 1)
    with s27_engine.connect() as conn:
        current = dict(conn.execute(select(claim.c.citation_context_id, claim.c.skeleton)
                                    .where(claim.c.extractor_ver == EXTRACTOR_VER)).all())
    assert current == {new[1]: finding().model_dump(), new[5]: finding(effect=None).model_dump()}

    ex = ClaimExtractor(s27_engine, FakeChat({}), label=LABEL, model="m", seed=1)
    assert ex.pending_ids() == sorted(new[i] for i in (2, 3, 4, 6))  # only these get asked
    again = carry_over(s27_engine, label=LABEL, extractor="llm:m", from_ver="0.9.0")
    assert (again.matched, again.carried) == (1, 0)  # only 6 matches again, and still fails


# --- batch mode against the test database ------------------------------------------------

class FakeBatch:
    """The gateway's batch API in memory. Each batch is in progress on its
    first poll and complete on the next. Answers come from a script keyed by
    the passage: a skeleton or text is a 200 answer, an int another status,
    and "lost" an error line."""

    def __init__(self, script: dict[str, list]) -> None:
        self.script = {k: list(v) for k, v in script.items()}
        self.batches: dict[str, list[dict]] = {}
        self.polls: Counter = Counter()
        self.cancelled: list[str] = []

    def submit(self, lines, *, name, window):
        batch_id = f"b{len(self.batches) + 1}"
        self.batches[batch_id] = lines
        return batch_id

    def status(self, batch_id):
        self.polls[batch_id] += 1
        if self.polls[batch_id] == 1:
            return {"id": batch_id, "status": "in_progress"}
        return {"id": batch_id, "status": "completed", "output_file_id": f"out-{batch_id}"}

    def result_lines(self, status):
        out = []
        for line in self.batches[status["id"]]:
            user = line["body"]["messages"][1]["content"]
            passage = user.split("<passage>\n", 1)[1].split("\n</passage>", 1)[0]
            answer = self.script[passage].pop(0)
            cid = line["custom_id"]
            if answer == "lost":
                out.append({"custom_id": cid, "response": None, "error": {"code": "batch_expired"}})
            elif isinstance(answer, int):
                out.append({"custom_id": cid, "response": {"status_code": answer, "body": {}}})
            else:
                content = answer.model_dump_json() if isinstance(answer, ClaimSkeleton) else answer
                body = _saved_completion()
                body["choices"][0]["message"]["content"] = content
                out.append({"custom_id": cid, "response": {"status_code": 200, "body": body}})
        return out

    def cancel(self, batch_id):
        self.cancelled.append(batch_id)


def _batch_script() -> dict[str, list]:
    return {
        _context(1): [finding()],
        _context(2): [finding(population="hypertensive adults"), finding()],  # fixed in round 1
        _context(3): [finding()],
        _context(4): ["not json"] * (MAX_REPAIRS + 1),  # still invalid after round 2
        _context(5): ["lost"],  # no answer: no row, asked again next run
        _context(6): [400],  # refused by the gateway
    }


@requires_db
def test_batch_mode_runs_repair_rounds_and_leaves_unanswered_contexts_for_the_next_run(s27_engine):
    fake = FakeBatch(_batch_script())
    naps: list[float] = []
    ex = ClaimExtractor(s27_engine, None, batch=fake, label=LABEL, model="m", seed=1,
                        batch_contexts=4, poll_s=7, sleep=naps.append)
    stats = ex.run(s27_engine, batch_size=4)
    assert stats.inserted == 5
    # Two first-round batches (4 + 2 contexts), then one repair round each for
    # context 2 and context 4, and a last round for context 4.
    sizes = [len(lines) for lines in fake.batches.values()]
    assert sorted(sizes[:2]) == [2, 4] and sum(sizes[2:]) == 3
    assert all(line["custom_id"].endswith(":0") for b in list(fake.batches.values())[:2] for line in b)
    assert naps and set(naps) == {7}
    assert fake.cancelled == []
    with s27_engine.connect() as conn:
        rows = dict(conn.execute(select(claim.c.citation_context_id, claim.c.valid)).all())
        ids = dict(conn.execute(select(citation_context.c.ordinal, citation_context.c.id)).all())
    assert {o: rows.get(ids[o]) for o in range(1, 7)} == {
        1: True, 2: True, 3: True, 4: False, 5: None, 6: False}
    s = ex.stats
    assert (s.valid_first, s.valid_repaired, s.invalid, s.rejected, s.unanswered) == (2, 1, 1, 1, 1)
    assert s.calls == 1 + 2 + 1 + 3  # context 6 was refused, context 5 never answered

    again = ClaimExtractor(s27_engine, None, batch=FakeBatch({_context(5): [finding()]}),
                           label=LABEL, model="m", seed=1, sleep=lambda s: None)
    assert again.pending_ids() == [ids[5]]
    assert again.run(s27_engine).inserted == 1


@requires_db
def test_a_batch_run_that_stops_early_cancels_its_running_batches(s27_engine):
    class Broken(FakeBatch):
        def status(self, batch_id):
            raise RuntimeError("gateway down")

    fake = Broken(_batch_script())
    ex = ClaimExtractor(s27_engine, None, batch=fake, label=LABEL, model="m", seed=1,
                        batch_contexts=4, sleep=lambda s: None)
    with pytest.raises(RuntimeError):
        ex.run(s27_engine)
    assert sorted(fake.cancelled) == ["b1", "b2"]


def test_a_claim_extractor_takes_one_way_of_asking():
    with pytest.raises(ValueError):
        ClaimExtractor(None, None, label=LABEL, model="m", seed=1)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        ClaimExtractor(None, FakeChat({}), batch=FakeBatch({}), label=LABEL, model="m", seed=1)  # type: ignore[arg-type]
