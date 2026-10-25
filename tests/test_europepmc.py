"""Europe PMC corpus selection and harvester, against saved responses.

Fixtures are three real papers from the S1.6 corpus query: search metadata
(affiliations and author IDs redacted), accession annotations, and each
paper's <ref-list> cut from its full-text XML (the body isn't kept).
PMC11662519 names two trials in its Abstract/Methods, PMC11667321 one, and
PMC11634091 mentions one only in unclassified text, so it gets no link.
"""

from __future__ import annotations

import json

import httpx
import pytest
from sqlalchemy import delete, func, insert, select

from sentinel.ingest.clinicaltrials import ClinicalTrialsClient
from sentinel.ingest.europepmc import (
    EuropePMC,
    EuropePMCHarvester,
    corpus_paper_record,
    own_trial_ids,
    sample_pmcids,
)
from sentinel.ingest.http import RateLimitedHTTP, RetryableHTTPError
from sentinel.ingest.jats import parse_ref_list
from sentinel.models import (
    corpus,
    corpus_paper,
    ingest_watermark,
    paper,
    paper_trial_link,
    reference,
    trial,
)
from tests.conftest import FIXTURES, requires_db
from tests.test_clinicaltrials import DROP_PATHS

EPMC = FIXTURES / "europepmc"
PMCIDS = ["PMC11662519", "PMC11667321", "PMC11634091"]
LABEL = "test-fixture"


def _xml(pmcid: str) -> bytes:
    return (EPMC / f"fulltext_{pmcid}.xml").read_bytes()


def _core() -> dict[str, dict]:
    data = json.loads((EPMC / "core.json").read_text("utf-8"))
    return {r["pmcid"]: r for r in data["resultList"]["result"]}


def _annotations() -> dict[str, list[dict]]:
    data = json.loads((EPMC / "annotations.json").read_text("utf-8"))
    return {a["pmcid"]: a["annotations"] for a in data}


def _members() -> list[dict]:
    return json.loads((EPMC / "members.json").read_text("utf-8"))


def _raw(pmcid: str, ordinal: int = 1) -> dict:
    return {
        "member": {"ordinal": ordinal, "pmcid": pmcid},
        "core": _core()[pmcid],
        "fulltext_xml": _xml(pmcid),
        "annotations": _annotations()[pmcid],
    }


# --- JATS ref-list --------------------------------------------------------------


def test_ref_list_counts_match_the_bibliographies():
    # The references API returned 49 for PMC11662519; the paper lists 54.
    assert [len(parse_ref_list(_xml(p))) for p in PMCIDS] == [54, 59, 47]


def test_structured_citation_fields():
    first = parse_ref_list(_xml("PMC11667321"))[0]
    assert first.ordinal == 1
    assert first.parsed_first_author == "shiri"
    assert first.parsed_year == 2006
    assert first.parsed_title == (
        "Prevalence and determinants of lateral and medial epicondylitis: a population study")
    assert first.parsed_container == "Am J Epidemiol"
    assert first.parsed_doi == "10.1093/aje/kwj325"
    assert first.parsed_pmid == "16968862"
    assert first.raw_string.startswith("Shiri R Viikari-Juntura E Varonen H")
    assert first.raw["citation_tag"] == "element-citation"


def test_unstructured_citation_keeps_text_and_guesses_nothing():
    refs = parse_ref_list(_xml("PMC11662519"))
    assert refs[0].raw_string.startswith("Feigin VL, et al. Global and regional burden of stroke")
    for r in refs:
        assert r.raw_string
        assert (r.parsed_doi, r.parsed_pmid, r.parsed_title, r.parsed_first_author,
                r.parsed_year, r.parsed_container) == (None,) * 6


def test_ordinals_are_document_order():
    refs = parse_ref_list(_xml("PMC11634091"))
    assert [r.ordinal for r in refs] == list(range(1, 48))


def test_sub_article_refs_are_skipped():
    xml = b"""<article><back><ref-list>
      <ref id="r1"><mixed-citation>Main A. One. 2020.</mixed-citation></ref>
      <ref id="r2"><mixed-citation>Main B. Two. 2021.</mixed-citation></ref>
    </ref-list></back>
    <sub-article><back><ref-list>
      <ref id="s1"><mixed-citation>Reviewer C. Three. 2022.</mixed-citation></ref>
    </ref-list></back></sub-article></article>"""
    assert [r.raw["id"] for r in parse_ref_list(xml)] == ["r1", "r2"]


def test_book_title_comes_from_source():
    xml = b"""<article><back><ref-list><ref id="b1">
      <element-citation publication-type="book">
        <person-group person-group-type="author"><name><surname>Altman</surname>
        <given-names>DG</given-names></name></person-group>
        <source>Practical Statistics for Medical Research</source><year>1991</year>
      </element-citation></ref></ref-list></back></article>"""
    ref = parse_ref_list(xml)[0]
    assert ref.parsed_title == "Practical Statistics for Medical Research"
    assert ref.parsed_container is None
    assert (ref.parsed_first_author, ref.parsed_year) == ("altman", 1991)


def test_external_entities_are_not_resolved():
    xml = b"""<?xml version="1.0"?>
    <!DOCTYPE article [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
    <article><back><ref-list><ref id="x"><mixed-citation>&xxe; Smith J. 2020.</mixed-citation>
    </ref></ref-list></back></article>"""
    ref = parse_ref_list(xml)[0]
    assert "root:" not in (ref.raw_string or "")


# --- trial links (ADR-028) --------------------------------------------------------


def test_own_trial_ids_on_real_annotations():
    ann = _annotations()
    assert own_trial_ids(ann["PMC11662519"]) == ["NCT03946488", "NCT04804384"]
    assert own_trial_ids(ann["PMC11667321"]) == ["NCT04803825"]
    assert own_trial_ids(ann["PMC11634091"]) == []  # only in unclassified "Article" text


def test_own_trial_ids_section_rule():
    ann = [
        {"exact": "NCT00000001", "section": "Introduction (http://purl.org/orb/Introduction)"},
        {"exact": "NCT00000002", "section": "Discussion (x)"},
        {"exact": "nct00000003", "section": "Methods (http://purl.org/orb/Methods)"},
        {"exact": "NCT0000004", "section": "Abstract (x)"},       # malformed: 7 digits
        {"exact": "NCT00000005", "section": "Title (x)"},
        {"exact": "NCT00000005", "section": "Abstract (x)"},
    ]
    assert own_trial_ids(ann) == ["NCT00000003", "NCT00000005"]


# --- record building --------------------------------------------------------------


def test_corpus_paper_record():
    record = corpus_paper_record(_raw("PMC11667321", ordinal=7), LABEL)
    p = record.paper
    assert (record.label, record.ordinal) == (LABEL, 7)
    assert (p.pmcid, p.pmid, p.doi, p.year) == (
        "PMC11667321", "39806585", "10.1136/bmjopen-2024-085916", 2024)
    assert p.container == "BMJ open"
    assert p.first_author == "sveinall"
    assert p.title_norm.startswith("heavy slow resistance training radial extracorporeal")
    assert (p.source, p.is_oa, p.license, p.fulltext_status) == (
        "europepmc", True, "cc by-nc", "abstract")
    assert len(record.references) == 59
    assert record.nct_ids == ["NCT04803825"]


def test_paper_raw_drops_affiliations_and_author_ids():
    for pmcid in PMCIDS:
        p = corpus_paper_record(_raw(pmcid), LABEL).paper
        assert not {"authorList", "authorIdList", "affiliation"} & set(p.raw)
        assert "REDACTED" not in json.dumps(p.raw) + json.dumps(p.authors)
        assert all(set(a) <= {"full_name", "last_name", "initials", "collective_name"}
                   for a in p.authors)


def test_missing_fulltext_stores_paper_without_references():
    raw = dict(_raw("PMC11662519"), fulltext_xml=None)
    record = corpus_paper_record(raw, LABEL)
    assert record.references == []
    assert record.paper.pmcid == "PMC11662519"


def test_missing_metadata_is_rejected():
    with pytest.raises(ValueError, match="no metadata"):
        corpus_paper_record(dict(_raw("PMC11662519"), core=None), LABEL)


# --- sampling -------------------------------------------------------------------------


def test_sampling_is_seeded_and_order_independent():
    pool = [f"PMC{i}" for i in range(1000, 1500)]
    a = sample_pmcids(pool, seed=20260922, n=25)
    assert a == sample_pmcids(list(reversed(pool)), seed=20260922, n=25)
    assert a != sample_pmcids(pool, seed=1, n=25)
    assert len(set(a)) == 25


def test_sampling_refuses_too_few_hits():
    with pytest.raises(ValueError, match="fewer than n"):
        sample_pmcids(["PMC1", "PMC2"], seed=1, n=3)


# --- harvest against the database -------------------------------------------------------

TRIAL_IDS = ("NCT03946488", "NCT04804384", "NCT04803825")


def _transport(failing: frozenset[str] = frozenset()) -> httpx.MockTransport:
    core = _core()
    ann = _annotations()
    study = json.loads((FIXTURES / "clinicaltrials" / "NCT02016378.json").read_text("utf-8"))

    def handler(request: httpx.Request) -> httpx.Response:
        url, path = request.url, request.url.path
        if url.host == "ctgov.test":
            nct = path.rsplit("/", 1)[-1]
            if nct == "NCT04804384":
                return httpx.Response(404, text=f"NCT number {nct} not found")
            body = json.loads(json.dumps(study))
            body["protocolSection"]["identificationModule"]["nctId"] = nct
            return httpx.Response(200, json=body)
        if path.endswith("/fullTextXML"):
            pmcid = path.split("/")[-2]
            if pmcid in failing:
                return httpx.Response(500, text="Internal Server Error")
            return httpx.Response(200, content=_xml(pmcid))
        if path.endswith("/annotationsByArticleIds"):
            ids = [i.removeprefix("PMC:") for i in url.params["articleIds"].split(",")]
            return httpx.Response(200, json=[
                {"pmcid": i, "annotations": ann.get(i, [])} for i in ids])
        if path.endswith("/search"):
            wanted = [t.removeprefix("PMCID:") for t in url.params["query"].split(" OR ")]
            hits = [core[p] for p in wanted if p in core]
            return httpx.Response(200, json={"hitCount": len(hits), "resultList": {"result": hits}})
        return httpx.Response(500)

    return httpx.MockTransport(handler)


def _clients(failing: frozenset[str] = frozenset()):
    client = httpx.Client(transport=_transport(failing))

    def http(source):
        return RateLimitedHTTP(client, source=source, rate_per_s=1000, max_retries=0,
                               backoff_base_s=0, sleep=lambda s: None)

    epmc = EuropePMC(http("europepmc"), base_url="https://epmc.test/rest",
                     annotations_url="https://epmc.test/annotations_api")
    trials = ClinicalTrialsClient(http("clinicaltrials"), base_url="https://ctgov.test/api/v2",
                                  drop_paths=DROP_PATHS, strip_personal_fields=True)
    return epmc, trials


def _offline_harvester(failing: frozenset[str], max_failures: int = 5) -> EuropePMCHarvester:
    """A harvester with members given directly, so fetch/normalise need no DB."""
    epmc, trials = _clients(failing)
    members = [{"ordinal": i, "source": m["source"], "source_id": m["id"], "pmcid": m["pmcid"]}
               for i, m in enumerate(_members(), start=1)]
    return EuropePMCHarvester(None, epmc, trials, label=LABEL, members=members,
                              max_consecutive_failures=max_failures)


def test_one_broken_fulltext_does_not_stop_the_corpus():
    # PMC6561645 returned a persistent 500 during the real harvest (2026-10-24).
    h = _offline_harvester(frozenset({"PMC11667321"}))
    records = [h.normalise(raw) for raw, _ in h.fetch(None)]
    assert [r.paper.pmcid for r in records] == ["PMC11662519", "PMC11667321", "PMC11634091"]
    assert [len(r.references) for r in records] == [54, 0, 47]
    assert h.fulltext_failed == {"PMC11667321"}
    assert h.no_fulltext == set()


def test_consecutive_fulltext_failures_stop_the_run():
    h = _offline_harvester(frozenset({"PMC11662519", "PMC11667321"}), max_failures=2)
    with pytest.raises(RetryableHTTPError):
        list(h.fetch(None))


def test_a_success_resets_the_failure_count():
    h = _offline_harvester(frozenset({"PMC11662519", "PMC11634091"}), max_failures=2)
    assert len(list(h.fetch(None))) == 3


@pytest.fixture
def epmc_engine(db_engine):
    with db_engine.connect() as conn:
        if conn.execute(select(paper.c.id).where(paper.c.pmcid.not_in(PMCIDS))).first():
            pytest.skip("paper holds real data; not a disposable database")
    _clear(db_engine)
    with db_engine.begin() as conn:
        conn.execute(insert(corpus).values(label=LABEL, source="europepmc", query="test",
                                           seed=1, hit_count=3, n=3))
        conn.execute(insert(corpus_paper), [
            {"label": LABEL, "ordinal": i, "source": m["source"], "source_id": m["id"],
             "pmcid": m["pmcid"]} for i, m in enumerate(_members(), start=1)])
    yield db_engine
    _clear(db_engine)


def _clear(engine) -> None:
    with engine.begin() as conn:
        conn.execute(delete(corpus).where(corpus.c.label == LABEL))
        conn.execute(delete(paper).where(paper.c.pmcid.in_(PMCIDS)))
        conn.execute(delete(trial).where(trial.c.nct_id.in_(TRIAL_IDS)))
        conn.execute(delete(ingest_watermark).where(
            ingest_watermark.c.source == f"europepmc:{LABEL}"))


def _counts(engine) -> dict[str, int]:
    with engine.connect() as conn:
        def n(table):
            return conn.execute(select(func.count()).select_from(table)).scalar_one()
        return {"paper": n(paper), "reference": n(reference), "link": n(paper_trial_link)}


@requires_db
def test_harvest_stores_papers_references_and_links(epmc_engine):
    epmc, trials = _clients()
    harvester = EuropePMCHarvester(epmc_engine, epmc, trials, label=LABEL)
    stats = harvester.run(epmc_engine, batch_size=25)
    assert (stats.seen, stats.inserted, stats.failed) == (3, 3, 0)
    assert harvester.not_found == {"NCT04804384"}

    with epmc_engine.connect() as conn:
        refs = dict(conn.execute(
            select(paper.c.pmcid, func.count(reference.c.id))
            .join(reference, reference.c.citing_paper_id == paper.c.id)
            .group_by(paper.c.pmcid)).all())
        links = conn.execute(
            select(paper.c.pmcid, paper_trial_link.c.nct_id, paper_trial_link.c.confidence)
            .join(paper_trial_link, paper_trial_link.c.paper_id == paper.c.id)
            .order_by(paper.c.pmcid)).all()
        unlinked_members = conn.execute(select(func.count()).select_from(corpus_paper).where(
            corpus_paper.c.label == LABEL, corpus_paper.c.paper_id.is_(None))).scalar_one()
    assert refs == {"PMC11662519": 54, "PMC11667321": 59, "PMC11634091": 47}
    # NCT04804384 isn't in the registry (404), so its link is skipped.
    assert [tuple(r) for r in links] == [("PMC11662519", "NCT03946488", 1.0),
                                         ("PMC11667321", "NCT04803825", 1.0)]
    assert unlinked_members == 0


@requires_db
def test_rerun_adds_no_duplicates(epmc_engine):
    epmc, trials = _clients()
    EuropePMCHarvester(epmc_engine, epmc, trials, label=LABEL).run(epmc_engine)
    before = _counts(epmc_engine)
    stats = EuropePMCHarvester(epmc_engine, epmc, trials, label=LABEL).run(epmc_engine)
    assert (stats.inserted, stats.updated) == (0, 3)
    assert _counts(epmc_engine) == before


@requires_db
def test_killed_harvest_resumes(epmc_engine):
    epmc, trials = _clients()
    first = EuropePMCHarvester(epmc_engine, epmc, trials, label=LABEL)
    assert first.run(epmc_engine, limit=2, batch_size=1).seen == 2
    rest = EuropePMCHarvester(epmc_engine, epmc, trials, label=LABEL).run(
        epmc_engine, resume=True, batch_size=1)
    assert (rest.seen, rest.inserted) == (1, 1)
    assert _counts(epmc_engine)["paper"] == 3
