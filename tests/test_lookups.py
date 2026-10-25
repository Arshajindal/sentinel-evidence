"""Crossref, OpenAlex and doi.org lookup clients against recorded responses.

Each fixture is {"status", "body"} as the live API returned it on 2026-10-25,
with ORCIDs and affiliations redacted. No test calls a live API.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest
from sqlalchemy import delete, func, select

from sentinel.ingest.crossref import CrossrefClient
from sentinel.ingest.doi_handle import DoiHandleClient
from sentinel.ingest.http import RateLimitedHTTP
from sentinel.ingest.openalex import OpenAlexBudgetExhausted, OpenAlexClient
from sentinel.ingest.papers import upsert_paper
from sentinel.models import paper
from sentinel.schemas import PaperRecord
from tests.conftest import FIXTURES, requires_db

LOOKUPS = FIXTURES / "lookups"
DOI = "10.1093/aje/kwj325"


def _fixture(name: str) -> tuple[int, object]:
    data = json.loads((LOOKUPS / name).read_text("utf-8"))
    return data["status"], data["body"]


def _respond(name: str, headers: dict | None = None) -> httpx.Response:
    status, body = _fixture(name)
    if isinstance(body, str):
        return httpx.Response(status, text=body, headers=headers or {})
    return httpx.Response(status, json=body, headers=headers or {})


def _http(handler: Callable[[httpx.Request], httpx.Response], source: str) -> RateLimitedHTTP:
    return RateLimitedHTTP(httpx.Client(transport=httpx.MockTransport(handler)), source=source,
                           rate_per_s=1000, max_retries=2, backoff_base_s=0, sleep=lambda s: None)


def _sequence(*responses: Callable[[], httpx.Response]):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return responses[min(len(seen), len(responses)) - 1]()

    return handler, seen


def _crossref(handler) -> CrossrefClient:
    return CrossrefClient(_http(handler, "crossref"), base_url="https://cr.test",
                          mailto="test@example.org")


def _openalex(handler, *, api_key: str = "", budget: float = 0.10) -> OpenAlexClient:
    return OpenAlexClient(_http(handler, "openalex"), base_url="https://oa.test",
                          api_key=api_key, daily_budget_usd=budget)


def _message(name: str) -> dict:
    return _fixture(name)[1]["message"]


# --- Crossref ---------------------------------------------------------------------


def test_crossref_normalise():
    record = _crossref(None).normalise(_message("crossref_work.json"))
    assert record.doi == DOI
    assert record.title == ("Prevalence and Determinants of Lateral and Medial Epicondylitis: "
                            "A Population Study")
    assert record.title_norm.startswith("prevalence and determinants of lateral")
    assert (record.container, record.year, record.first_author) == (
        "American Journal of Epidemiology", 2006, "shiri")
    assert record.source == "crossref"
    assert record.authors[0] == {"last_name": "Shiri", "full_name": "R. Shiri"}
    assert "author" not in record.raw
    assert "REDACTED" not in json.dumps(record.model_dump())


def test_crossref_work_by_doi_uses_polite_pool():
    handler, seen = _sequence(lambda: _respond("crossref_work.json"))
    work = _crossref(handler).work_by_doi(DOI)
    assert work["DOI"] == DOI
    assert seen[0].url.path == f"/works/{DOI}"
    assert seen[0].url.params["mailto"] == "test@example.org"


def test_crossref_unknown_doi_is_none():
    handler, _ = _sequence(lambda: _respond("crossref_404.json"))
    assert _crossref(handler).work_by_doi("10.9999/not-a-real-doi-xyz") is None


def test_crossref_search_returns_candidates_best_first():
    handler, seen = _sequence(lambda: _respond("crossref_search.json"))
    items = _crossref(handler).search("Prevalence and determinants of lateral epicondylitis", rows=3)
    assert [i["DOI"] for i in items][:1] == [DOI]
    assert len(items) == 3
    assert seen[0].url.params["query.bibliographic"].startswith("Prevalence")
    assert seen[0].url.params["rows"] == "3"


@pytest.mark.parametrize("status", [429, 503])
def test_crossref_retries_then_succeeds(status):
    handler, seen = _sequence(lambda: httpx.Response(status), lambda: _respond("crossref_work.json"))
    assert _crossref(handler).work_by_doi(DOI)["DOI"] == DOI
    assert len(seen) == 2


def test_crossref_skips_untitled_candidates():
    item = dict(_message("crossref_work.json"), title=[])
    with pytest.raises(ValueError):
        _crossref(None).normalise(item)


# --- OpenAlex ---------------------------------------------------------------------


def test_openalex_normalise():
    record = _openalex(None).normalise(_fixture("openalex_work.json")[1])
    assert record.doi == DOI
    assert record.pmid == "16968862"
    assert (record.year, record.container) == (2006, "American Journal of Epidemiology")
    assert record.first_author == "shiri"  # last token of "Rahman Shiri"
    assert record.source == "openalex"
    assert record.is_oa is False
    assert "authorships" not in record.raw
    assert all(set(a) == {"full_name"} for a in record.authors)


def test_openalex_key_goes_in_header_not_url():
    handler, seen = _sequence(lambda: _respond("openalex_work.json"))
    _openalex(handler, api_key="secret-key").work_by_doi(DOI)
    assert seen[0].headers["Authorization"] == "Bearer secret-key"
    assert "secret-key" not in str(seen[0].url)
    assert seen[0].url.path == f"/works/doi:{DOI}"


def test_openalex_unknown_doi_is_none():
    handler, _ = _sequence(lambda: _respond("openalex_404.json"))
    assert _openalex(handler).work_by_doi("10.9999/not-a-real-doi-xyz") is None


def test_openalex_search_filters_and_records_cost():
    priced = {"X-RateLimit-Cost-USD": "0.001", "X-RateLimit-Remaining-USD": "0.097"}
    handler, seen = _sequence(lambda: _respond("openalex_search.json", priced))
    client = _openalex(handler)
    results = client.search_title("Prevalence and determinants: lateral, medial epicondylitis",
                                  year=2006)
    assert results[0]["doi"] == f"https://doi.org/{DOI}"
    assert seen[0].url.params["filter"] == (
        "title.search:Prevalence and determinants lateral medial epicondylitis,"
        "publication_year:2005-2007")
    assert client.budget.spent_usd == pytest.approx(0.001)
    assert client.budget.server_remaining_usd == pytest.approx(0.097)


def test_openalex_stops_searching_when_local_budget_is_spent():
    priced = {"X-RateLimit-Cost-USD": "0.001", "X-RateLimit-Remaining-USD": "0.5"}
    handler, seen = _sequence(lambda: _respond("openalex_search.json", priced))
    client = _openalex(handler, budget=0.002)
    client.search_title("a")
    client.search_title("b")
    with pytest.raises(OpenAlexBudgetExhausted):
        client.search_title("c")
    assert len(seen) == 2  # the third search never left the process


def test_openalex_stops_searching_when_server_budget_is_spent():
    priced = {"X-RateLimit-Cost-USD": "0.001", "X-RateLimit-Remaining-USD": "0"}
    handler, seen = _sequence(lambda: _respond("openalex_search.json", priced))
    client = _openalex(handler, budget=1.0)
    client.search_title("a")
    with pytest.raises(OpenAlexBudgetExhausted):
        client.search_title("b")
    assert len(seen) == 1


def test_openalex_doi_lookups_continue_after_budget_is_spent():
    free = {"X-RateLimit-Cost-USD": "0", "X-RateLimit-Remaining-USD": "0"}
    handler, _ = _sequence(lambda: _respond("openalex_work.json", free))
    client = _openalex(handler, budget=0.0)
    assert client.work_by_doi(DOI)["doi"] == f"https://doi.org/{DOI}"
    with pytest.raises(OpenAlexBudgetExhausted):
        client.search_title("anything")


def test_openalex_retries_429():
    handler, seen = _sequence(lambda: httpx.Response(429), lambda: _respond("openalex_work.json"))
    assert _openalex(handler).work_by_doi(DOI) is not None
    assert len(seen) == 2


# --- doi.org handle API -------------------------------------------------------------


@pytest.mark.parametrize(
    ("fixture", "doi", "expected"),
    [
        ("handle_crossref.json", DOI, True),
        ("handle_datacite.json", "10.5061/dryad.8515", True),  # not a Crossref DOI
        ("handle_404.json", "10.9999/not-a-real-doi-xyz", False),
    ],
)
def test_doi_handle_exists(fixture, doi, expected):
    handler, seen = _sequence(lambda: _respond(fixture))
    client = DoiHandleClient(_http(handler, "doi_handle"), base_url="https://doi.test/api/handles")
    assert client.exists(doi) is expected
    assert seen[0].url.path == f"/api/handles/{doi}"


def test_doi_handle_rejects_non_doi():
    client = DoiHandleClient(_http(lambda r: httpx.Response(500), "doi_handle"),
                             base_url="https://doi.test/api/handles")
    with pytest.raises(ValueError):
        client.exists("not-a-doi")


# --- storing works (database; touches only its own synthetic DOIs) ----------------------

TEST_DOIS = ("10.55555/sentinel-test-a", "10.55555/sentinel-test-b", "10.55555/sentinel-test-c")


@pytest.fixture
def lookup_engine(db_engine):
    def clear():
        with db_engine.begin() as conn:
            conn.execute(delete(paper).where(
                paper.c.doi.in_(TEST_DOIS) | paper.c.pmid.in_(["990000001", "990000002"])))
    clear()
    yield db_engine
    clear()


def _record(doi: str | None, source: str, title: str = "A test title", pmid: str | None = None):
    return PaperRecord(doi=doi, pmid=pmid, title=title, title_norm=title.lower(),
                       source=source, raw={"t": title})


@requires_db
def test_upsert_inserts_then_refreshes_same_source(lookup_engine):
    with lookup_engine.begin() as conn:
        first = upsert_paper(conn, _record(TEST_DOIS[0], "crossref"))
        again = upsert_paper(conn, _record(TEST_DOIS[0], "crossref", title="Corrected title"))
        title = conn.execute(select(paper.c.title).where(paper.c.id == first.paper_id)).scalar_one()
    assert (first.action, again.action) == ("inserted", "updated")
    assert again.paper_id == first.paper_id
    assert title == "Corrected title"


@requires_db
def test_upsert_never_overwrites_another_source(lookup_engine):
    with lookup_engine.begin() as conn:
        owner = upsert_paper(conn, _record(TEST_DOIS[1], "europepmc", title="Owner"))
        other = upsert_paper(conn, _record(TEST_DOIS[1], "openalex", title="Intruder"))
        title = conn.execute(select(paper.c.title).where(paper.c.id == owner.paper_id)).scalar_one()
    assert other == type(other)(owner.paper_id, "existing")
    assert title == "Owner"


@requires_db
def test_upsert_refuses_to_merge_conflicting_rows(lookup_engine):
    with lookup_engine.begin() as conn:
        upsert_paper(conn, _record(TEST_DOIS[2], "crossref"))
        upsert_paper(conn, _record(None, "crossref", pmid="990000001"))
        clash = upsert_paper(conn, _record(TEST_DOIS[2], "openalex", pmid="990000001"))
        n = conn.execute(select(func.count()).select_from(paper).where(
            paper.c.doi.in_(TEST_DOIS) | (paper.c.pmid == "990000001"))).scalar_one()
    assert clash.action == "conflict" and clash.paper_id is None
    assert n == 2


@requires_db
def test_crossref_lookup_stores_once(lookup_engine):
    work = dict(_message("crossref_work.json"), DOI=TEST_DOIS[0])
    handler, seen = _sequence(lambda: httpx.Response(200, json={"message": work}))
    client = _crossref(handler)
    first = client.lookup(lookup_engine, TEST_DOIS[0])
    second = client.lookup(lookup_engine, TEST_DOIS[0])
    with lookup_engine.connect() as conn:
        rows = conn.execute(select(paper.c.source, paper.c.year).where(
            paper.c.doi == TEST_DOIS[0])).all()
    assert (first.action, second.action) == ("inserted", "updated")
    assert [tuple(r) for r in rows] == [("crossref", 2006)]
