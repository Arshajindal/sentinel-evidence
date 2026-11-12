"""Full-text availability and coverage (S2.2, ADR-045), against saved responses.

`fixtures/fulltext/core_*.json` are real Europe PMC search responses
(2026-11-01, author lists and other unused lists removed): an open-access
paper with full text (PMC8950561), a PMC record that isn't open access and
has no abstract (PMC7393191), a PubMed-only record with an abstract, a DOI
Europe PMC doesn't know, and two PubMed IDs. The XML served for PMC8950561 is
a small constructed JATS document.
"""

from __future__ import annotations

import json

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import delete, insert, select

from sentinel.ingest.europepmc import EuropePMC
from sentinel.ingest.fulltext import (
    FulltextHarvester,
    Work,
    best_record,
    clean_markup,
    coverage,
    from_inverted_index,
    render_coverage,
    text_availability_record,
)
from sentinel.ingest.http import RateLimitedHTTP, RetryableHTTPError
from sentinel.models import corpus, corpus_paper, ingest_watermark, paper, reference, text_availability
from sentinel.schemas import TextAvailability
from tests.conftest import FIXTURES, requires_db

FT = FIXTURES / "fulltext"
LABEL = "test-fulltext"
OA_DOI, PMC_NOT_OA_DOI, ABSTRACT_DOI = (
    "10.3390/pathogens11030300", "10.1111/bph.15085", "10.1093/europace/euab216")
JATS = (b'<?xml version="1.0"?><article><front><article-meta><abstract><p>Abstract.</p>'
        b'</abstract></article-meta></front><body><sec><title>Results</title>'
        b'<p>Body paragraph.</p></sec></body></article>')
JATS_NO_BODY = b'<?xml version="1.0"?><article><front><article-meta/></front></article>'


def _records() -> list[dict]:
    out = []
    for name in ("core_dois.json", "core_pmids.json"):
        out += json.loads((FT / name).read_text("utf-8"))["resultList"]["result"]
    return out


def _core(doi: str) -> dict:
    return next(r for r in _records() if r.get("doi") == doi)


class Upstream:
    """Europe PMC search over the fixture records, plus XML for chosen PMCIDs."""

    def __init__(self, xml: dict[str, bytes] | None = None, failing: frozenset[str] = frozenset()):
        self.xml = {"PMC8950561": JATS} if xml is None else xml
        self.failing = failing
        self.xml_requests: list[str] = []
        self.searches = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/fullTextXML"):
            pmcid = path.split("/")[-2]
            self.xml_requests.append(pmcid)
            if pmcid in self.failing or pmcid not in self.xml:
                return httpx.Response(500, text="Internal Server Error")  # as the live API does
            return httpx.Response(200, content=self.xml[pmcid])
        if path.endswith("/search"):
            self.searches += 1
            terms = request.url.params["query"].split(" OR ")
            if terms[0].startswith("DOI:"):
                wanted = {t.removeprefix("DOI:").strip('"').lower() for t in terms}
                hits = [r for r in _records() if (r.get("doi") or "").lower() in wanted]
            else:
                wanted = {t.removeprefix("EXT_ID:") for t in terms}
                hits = [r for r in _records() if r.get("pmid") in wanted]
            return httpx.Response(200, json={"hitCount": len(hits), "resultList": {"result": hits}})
        return httpx.Response(404)


def _epmc(upstream: Upstream) -> EuropePMC:
    http = RateLimitedHTTP(httpx.Client(transport=httpx.MockTransport(upstream)), source="europepmc",
                           rate_per_s=1000, max_retries=0, backoff_base_s=0, sleep=lambda s: None)
    return EuropePMC(http, base_url="https://epmc.test/rest", annotations_url="https://epmc.test/a")


# --- pure parts ----------------------------------------------------------------------


def test_best_record_prefers_open_access_with_pmcid():
    preprint = {"id": "PPR1", "source": "PPR", "doi": "10.1/x"}
    med = {"id": "2", "source": "MED", "doi": "10.1/x"}
    pmc = {"id": "3", "source": "MED", "pmcid": "PMC1", "isOpenAccess": "N"}
    oa = {"id": "4", "source": "PMC", "pmcid": "PMC2", "isOpenAccess": "Y"}
    assert best_record([preprint, med]) is med
    assert best_record([med, pmc]) is pmc
    assert best_record([pmc, oa, med]) is oa
    assert best_record([]) is None


def test_clean_markup_joins_inline_tags_and_spaces_blocks():
    assert clean_markup(_core(OA_DOI)["abstractText"]).startswith(
        "Pseudomonas aeruginosa is associated with several human infections")
    assert clean_markup("<h4>Background</h4>Text with <i>P</i>s &amp; <sup>2</sup>.") == \
        "Background Text with Ps & 2."
    assert clean_markup("<jats:title>Abstract</jats:title><jats:p>A <jats:italic>b</jats:italic></jats:p>") == \
        "Abstract A b"
    assert clean_markup("<p> </p>") is None
    assert clean_markup(None) is None


def test_escaped_markup_goes_but_real_less_than_signs_stay():
    # Both from the real run: a Crossref abstract with escaped tags, and a
    # Europe PMC abstract whose "<" is part of the text.
    assert clean_markup("&lt;em&gt;Introduction:&lt;/em&gt;&lt;strong&gt; &lt;/strong&gt;The HADS"
                        "&lt;br&gt;&lt;br&gt;Methods: we") == "Introduction: The HADS Methods: we"
    assert clean_markup("normal weight (18.5&lt;BMI&lt;25), cut-off &lt;or=35 mm, age &lt;/=50") ==         "normal weight (18.5<BMI<25), cut-off <or=35 mm, age </=50"


def test_bare_less_than_sign_before_a_tag_keeps_the_text_between():
    # Europe PMC's abstractText mixes real tags with unescaped "<". The old
    # pattern read "<0.001)." as a tag running on to the heading's ">", so the
    # first sentence lost its end and the heading (2026-11-11 failure log).
    assert clean_markup("in the BSB group (P<0.001).<h4>Conclusion</h4>Our BSB could be") == \
        "in the BSB group (P<0.001). Conclusion Our BSB could be"
    # A thin space after "<" (U+2009, common in Europe PMC) becomes a plain one.
    assert clean_markup("<h4>Results</h4>OR 0.8 (P <\u20090.05), age <65 y and n<i>=</i>40.") == \
        "Results OR 0.8 (P < 0.05), age <65 y and n=40."
    assert clean_markup("normal weight (18.5<BMI<25), cut-off <or=35 mm, age </=50<h4>Methods</h4>") == \
        "normal weight (18.5<BMI<25), cut-off <or=35 mm, age </=50 Methods"
    # Tags with attributes, self-closing tags and namespaces still go.
    assert clean_markup('A<br/>B<br />C<span class="x">D</span><jats:sec id="s1">E</jats:sec>') == \
        "A B CD E"


def test_placeholder_text_is_not_an_abstract():
    assert clean_markup("<p>Abstract is missing.</p>") is None
    assert clean_markup("[Figure: see text].") is None
    assert from_inverted_index({"No": [0], "abstract": [1], "available": [2]}) is None
    assert clean_markup("No abstract available, but a sentence follows.") is not None


def test_inverted_index_rebuilds_word_order():
    assert from_inverted_index({"trial": [1], "A": [0], "randomised": [2], "a": [3]}) == \
        "A trial randomised a"
    assert from_inverted_index({}) is None


def test_open_access_with_body_is_full_text():
    r = text_availability_record({"paper_id": 1, "lookup_key": f"doi:{OA_DOI}",
                                  "core": _core(OA_DOI), "xml": JATS})
    assert (r.fulltext, r.pmcid, r.is_oa, r.license, r.fulltext_error) == \
        ("full", "PMC8950561", True, "cc by", None)
    assert r.abstract_source == "europepmc" and "<i>" not in r.abstract
    assert "abstractText" not in r.raw and r.raw["isOpenAccess"] == "Y"


def test_xml_without_a_body_keeps_the_abstract():
    r = text_availability_record({"paper_id": 1, "core": _core(OA_DOI), "xml": JATS_NO_BODY,
                                  "lookup_key": f"doi:{OA_DOI}"})
    assert (r.fulltext, r.fulltext_error) == ("abstract", "no_body")


def test_stored_crossref_then_openalex_abstract_when_europepmc_has_none():
    core = _core(PMC_NOT_OA_DOI)  # in PMC, not open access, no abstract
    crossref = text_availability_record({"paper_id": 1, "core": core, "stored": {
        "crossref": "<jats:p>From Crossref.</jats:p>", "openalex": {"x": [0]}}})
    openalex = text_availability_record({"paper_id": 1, "core": None, "stored": {
        "openalex": {"From": [0], "OpenAlex.": [1]}}})
    nothing = text_availability_record({"paper_id": 1, "core": None, "stored": {}})
    assert (crossref.fulltext, crossref.abstract, crossref.abstract_source, crossref.in_pmc) == \
        ("abstract", "From Crossref.", "crossref", True)
    assert (openalex.abstract, openalex.abstract_source, openalex.found) == \
        ("From OpenAlex.", "openalex", False)
    assert (nothing.fulltext, nothing.abstract, nothing.found) == ("none", None, False)


def test_full_text_needs_open_access():
    with pytest.raises(ValidationError):
        TextAvailability(paper_id=1, source="europepmc", found=True, pmcid="PMC1", is_oa=False,
                         fulltext="full")
    with pytest.raises(ValidationError):
        TextAvailability(paper_id=1, source="europepmc", found=True, fulltext="abstract")


def _harvester(upstream: Upstream, works: list[Work], tmp_path, **kw) -> FulltextHarvester:
    h = FulltextHarvester(None, _epmc(upstream), label=LABEL, cache_dir=tmp_path, works=works, **kw)
    h._stored_abstracts = lambda ids: {}  # no database in these tests
    return h


def test_xml_is_requested_only_for_open_access_records(tmp_path):
    up = Upstream()
    works = [Work(1, OA_DOI, None), Work(2, PMC_NOT_OA_DOI, None), Work(3, ABSTRACT_DOI, None)]
    records = [r for r, _ in _harvester(up, works, tmp_path).fetch(None)]
    assert up.xml_requests == ["PMC8950561"]
    assert up.searches == 1  # every DOI found: no PubMed ID query
    assert [text_availability_record(r).fulltext for r in records] == ["full", "none", "abstract"]
    assert (tmp_path / "PMC8950561.xml").read_bytes() == JATS


def test_doi_miss_falls_back_to_pubmed_id(tmp_path):
    up = Upstream()
    works = [Work(1, "10.9999/not-a-real-doi", "10411764"), Work(2, None, "9731008"),
             Work(3, None, None)]
    records = [r for r, _ in _harvester(up, works, tmp_path).fetch(None)]
    assert [r["lookup_key"] for r in records] == ["pmid:10411764", "pmid:9731008", None]
    assert [r["core"]["pmid"] if r["core"] else None for r in records] == \
        ["10411764", "9731008", None]


def test_a_failed_xml_fetch_is_recorded_and_consecutive_failures_stop(tmp_path):
    works = [Work(1, OA_DOI, None)]
    h = _harvester(Upstream(failing=frozenset({"PMC8950561"})), works, tmp_path)
    (raw, _), = h.fetch(None)
    r = text_availability_record(raw)
    assert (r.fulltext, r.fulltext_error) == ("abstract", "http_500")
    assert h.fetch_failed == {"PMC8950561"}
    stopping = _harvester(Upstream(failing=frozenset({"PMC8950561"})), works, tmp_path,
                          max_consecutive_failures=1)
    with pytest.raises(RetryableHTTPError):
        list(stopping.fetch(None))


def test_cached_xml_is_not_fetched_again(tmp_path):
    (tmp_path / "PMC8950561.xml").write_bytes(JATS)
    up = Upstream(xml={})
    (raw, _), = _harvester(up, [Work(1, OA_DOI, None)], tmp_path).fetch(None)
    assert up.xml_requests == [] and raw["xml"] == JATS


# --- against the test database -------------------------------------------------------

DOIS = {"corpus": "10.9999/test-fulltext-corpus", "oa": OA_DOI, "pmc": PMC_NOT_OA_DOI,
        "abs": ABSTRACT_DOI, "pmid": "10.9999/not-a-real-doi", "none": "10.9999/test-no-text"}
TITLES = [*DOIS, "noid"]  # each fixture paper's title is its key


@pytest.fixture
def ft_engine(db_engine):
    with db_engine.connect() as conn:
        if conn.execute(select(paper.c.id).where(paper.c.title.not_in(TITLES))).first():
            pytest.skip("paper holds other data; not a disposable database")
    _clear(db_engine)
    with db_engine.begin() as conn:
        def add(key, source, **kw):
            return conn.execute(insert(paper).values(
                title=key, title_norm=key, source=source, raw=kw.pop("raw", {}), **kw,
            ).returning(paper.c.id)).scalar_one()

        citing = add("corpus", "europepmc", doi=DOIS["corpus"], fulltext_status="full")
        cited = [
            add("oa", "crossref", doi=DOIS["oa"]),
            add("pmc", "crossref", doi=DOIS["pmc"], raw={"abstract": "<jats:p>Crossref text.</jats:p>"}),
            add("abs", "crossref", doi=DOIS["abs"]),
            add("pmid", "openalex", doi=DOIS["pmid"], pmid="10411764"),
            add("noid", "openalex", raw={"abstract_inverted_index": {"Only": [0], "OpenAlex": [1]}}),
            add("none", "crossref", doi=DOIS["none"]),
        ]
        conn.execute(insert(corpus).values(label=LABEL, source="europepmc", query="test",
                                           seed=1, hit_count=1, n=1))
        conn.execute(insert(corpus_paper).values(label=LABEL, ordinal=1, source="PMC",
                                                 source_id="x", pmcid="PMC1", paper_id=citing))
        conn.execute(insert(reference), [
            {"citing_paper_id": citing, "ordinal": i, "resolved_paper_id": pid,
             "resolution_status": "resolved"} for i, pid in enumerate(cited, start=1)]
            + [{"citing_paper_id": citing, "ordinal": 99, "resolved_paper_id": None,
                "resolution_status": "unresolved"}])
    yield db_engine
    _clear(db_engine)


def _clear(engine) -> None:
    with engine.begin() as conn:
        conn.execute(delete(corpus).where(corpus.c.label == LABEL))
        fixture_papers = select(paper.c.id).where(paper.c.title.in_(TITLES))
        # References first: resolved_paper_id has no cascade.
        conn.execute(delete(reference).where(reference.c.citing_paper_id.in_(fixture_papers)))
        conn.execute(delete(paper).where(paper.c.title.in_(TITLES)))
        conn.execute(delete(ingest_watermark).where(ingest_watermark.c.source == f"fulltext:{LABEL}"))


@requires_db
def test_harvest_records_text_and_measures_coverage(ft_engine, tmp_path):
    up = Upstream()
    stats = FulltextHarvester(ft_engine, _epmc(up), label=LABEL, cache_dir=tmp_path).run(
        ft_engine, batch_size=25)
    assert (stats.seen, stats.inserted, stats.failed) == (7, 7, 0)

    with ft_engine.connect() as conn:
        rows = {r.title: r for r in conn.execute(
            select(paper.c.title, paper.c.fulltext_status, text_availability.c.fulltext,
                   text_availability.c.abstract_source, text_availability.c.lookup_key)
            .join(text_availability, text_availability.c.paper_id == paper.c.id))}
        cov = coverage(conn, LABEL)
    assert {k: (r.fulltext, r.abstract_source) for k, r in rows.items()} == {
        "corpus": ("none", None), "oa": ("full", "europepmc"), "pmc": ("abstract", "crossref"),
        "abs": ("abstract", "europepmc"), "pmid": ("abstract", "europepmc"),
        "noid": ("abstract", "openalex"), "none": ("none", None)}
    assert rows["pmid"].lookup_key == "pmid:10411764"
    # Raised, never lowered: the corpus paper S1.12 examined stays 'full'.
    assert rows["corpus"].fulltext_status == "full"
    assert rows["oa"].fulltext_status == "full" and rows["none"].fulltext_status == "none"

    assert cov["cited_works"] == {"full": 1, "abstract": 4, "none": 1}
    assert cov["references"] == {"total": 7, "resolved": 6, "unresolved_or_ambiguous": 1,
                                 "by_cited_text": {"full": 1, "abstract": 4, "none": 1}}
    assert cov["share_full_of_resolved_references"] == pytest.approx(1 / 6)
    assert cov["share_full_of_all_references"] == pytest.approx(1 / 7)
    assert cov["cited_full_text_licences"] == {"cc by": 1}
    assert "of resolved references" in render_coverage(cov)


@requires_db
def test_killed_run_resumes_and_rerun_only_updates(ft_engine, tmp_path):
    up = Upstream()
    first = FulltextHarvester(ft_engine, _epmc(up), label=LABEL, cache_dir=tmp_path)
    assert first.run(ft_engine, limit=3, batch_size=1).seen == 3
    rest = FulltextHarvester(ft_engine, _epmc(up), label=LABEL, cache_dir=tmp_path).run(
        ft_engine, resume=True, batch_size=1)
    assert (rest.seen, rest.inserted) == (4, 4)

    again = FulltextHarvester(ft_engine, _epmc(up), label=LABEL, cache_dir=tmp_path).run(ft_engine)
    assert (again.inserted, again.updated) == (0, 7)
    assert up.xml_requests == ["PMC8950561"]  # later runs read the cache
    with ft_engine.connect() as conn:
        n = conn.execute(select(text_availability.c.paper_id)).all()
    assert len(n) == 7
