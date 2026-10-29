"""Resolution cascade (sentinel-project.md §9.5). No database, no network:
matching is pure, and the stage flow runs against an in-memory source."""

from __future__ import annotations

import pytest

from sentinel.ingest.openalex import OpenAlexBudgetExhausted
from sentinel.resolve.candidates import Candidate, LookupLog
from sentinel.resolve.cascade import (
    RefFields,
    Resolver,
    Thresholds,
    authors_match,
    contradicts,
    match_title,
    search_query,
)
from sentinel.resolve.normalize import normalize_title

TH = Thresholds(trigram_min=0.85, trigram_strict=0.92, year_tolerance=1, ambiguous_margin=0.02)
TITLE = "Daytime melatonin and light independently affect human alertness"
NORM = normalize_title(TITLE)


def _ref(**kw) -> RefFields:
    base = dict(reference_id=1, citing_paper_id=999, doi=None, pmid=None, title=TITLE,
                first_author="lok", year=2019)
    return RefFields(**{**base, **kw})


def _cand(paper_id, sim=1.0, title_norm=NORM, year=2019, first_author="lok",
          title=None) -> Candidate:
    return Candidate(paper_id, title_norm, year, first_author, sim, title)


# --- pure matching ----------------------------------------------------------------


def test_stage3_exact_title():
    r = match_title(_ref(), [_cand(10)], TH)
    assert (r.status, r.method, r.score, r.paper_id) == ("resolved", "title_exact", 0.95, 10)


def test_stage3_two_works_with_the_same_title_is_ambiguous():
    r = match_title(_ref(), [_cand(10), _cand(11)], TH)
    assert (r.status, r.method, r.paper_id) == ("ambiguous", "title_exact", None)
    assert r.evidence["tied"] == [10, 11]


def test_stage3_rejects_a_contradicting_year():
    # Same title, but published six years apart: not this work, and stage 5
    # mustn't take it either.
    assert match_title(_ref(), [_cand(10, year=2013)], TH) is None


def test_stage3_rejects_a_contradicting_author():
    assert match_title(_ref(), [_cand(10, first_author="smith")], TH) is None


FULL = TITLE + ": a randomised crossover study"


def test_stage3b_reference_gives_a_subtitle_the_record_lacks():
    r = match_title(_ref(title=FULL), [_cand(10, sim=0.5)], TH)
    assert (r.status, r.method, r.score, r.paper_id) == ("resolved", "title_main", 0.85, 10)


def test_stage3b_reference_gives_only_the_main_title():
    stored = _cand(10, sim=0.5, title_norm=normalize_title(FULL), title=FULL)
    r = match_title(_ref(), [stored], TH)
    assert (r.status, r.method, r.paper_id) == ("resolved", "title_main", 10)


@pytest.mark.parametrize("change", [{"first_author": "smith"}, {"first_author": None},
                                    {"year": 2017}, {"year": None}])
def test_stage3b_needs_author_and_year_to_agree(change):
    # A subtitle can be all that separates two papers of a series.
    r = match_title(_ref(title=FULL), [_cand(10, sim=0.5, **change)], TH)
    assert r is None


def test_stage3b_two_works_is_ambiguous():
    r = match_title(_ref(title=FULL), [_cand(10, sim=0.5), _cand(11, sim=0.5)], TH)
    assert (r.status, r.method, r.evidence["tied"]) == ("ambiguous", "title_main", [10, 11])


def test_stage3b_needs_the_colon():
    # "...alertness in shift workers" is a different, longer title, not a subtitle.
    longer = TITLE + " in shift workers"
    stored = _cand(10, sim=0.5, title_norm=normalize_title(longer), title=longer)
    assert match_title(_ref(), [stored], TH) is None


def test_stage4_similar_title_author_and_year():
    r = match_title(_ref(), [_cand(10, sim=0.90, title_norm=NORM + " x")], TH)
    assert (r.status, r.method, r.paper_id) == ("resolved", "title_author_year", 10)
    assert r.score == round(0.70 + 0.20 * (0.90 - 0.85) / 0.15, 4)  # stored to 4 places


def test_stage4_year_within_tolerance():
    r = match_title(_ref(), [_cand(10, sim=0.88, title_norm="x", year=2020)], TH)
    assert r.method == "title_author_year"


def test_stage4_tie_within_margin_is_ambiguous():
    r = match_title(_ref(), [_cand(10, sim=0.90, title_norm="a"), _cand(11, sim=0.899, title_norm="b")], TH)
    assert (r.status, r.method) == ("ambiguous", "title_author_year")


def test_stage4_clear_winner_is_not_a_tie():
    r = match_title(_ref(), [_cand(10, sim=0.99, title_norm="a"), _cand(11, sim=0.86, title_norm="b")], TH)
    assert (r.status, r.paper_id) == ("resolved", 10)


def test_stage4_needs_author_and_year_on_the_reference():
    cands = [_cand(10, sim=0.90, title_norm="a")]
    assert match_title(_ref(first_author=None), cands, TH) is None
    assert match_title(_ref(year=None), cands, TH) is None


def test_stage5_very_similar_title_alone():
    r = match_title(_ref(first_author=None), [_cand(10, sim=0.95, title_norm="a", first_author=None)], TH)
    assert (r.status, r.method, r.score, r.paper_id) == ("resolved", "title_only", 0.60, 10)


def test_stage5_needs_four_words():
    ref = _ref(title="Diabetic retinopathy", first_author=None)
    assert match_title(ref, [_cand(10, sim=0.95, title_norm="diabetic retinopathies")], TH) is None


def test_stage5_rejects_contradiction():
    ref = _ref(first_author=None)
    assert match_title(ref, [_cand(10, sim=0.95, title_norm="a", year=2001)], TH) is None


def test_stage5_tie_is_ambiguous():
    ref = _ref(first_author=None)
    r = match_title(ref, [_cand(10, sim=0.95, title_norm="a"), _cand(11, sim=0.94, title_norm="b")], TH)
    assert (r.status, r.method) == ("ambiguous", "title_only")


def test_below_thresholds_no_match():
    assert match_title(_ref(), [_cand(10, sim=0.80, title_norm="a")], TH) is None


@pytest.mark.parametrize(
    ("a", "b", "match"),
    [("lok", "lok", True), ("al shahi salman", "salman", True), ("van der velden", "velden", True),
     ("smith", "smyth", False), (None, "lok", False)],
)
def test_authors_match(a, b, match):
    assert authors_match(a, b) is match


def test_contradiction_reasons():
    assert contradicts(_ref(), _cand(1, year=2015), 1) == "year 2015 vs 2019"
    assert contradicts(_ref(), _cand(1, first_author="smith"), 1).startswith("first author")
    assert contradicts(_ref(year=None), _cand(1, year=1990), 1) is None


def test_search_query():
    assert search_query(_ref(container="J Pineal Res")) == f"lok {TITLE} J Pineal Res 2019"


# --- stage flow, against an in-memory source --------------------------------------------


class FakeOpenAlex:
    class budget:  # noqa: N801 - mimics the client attribute
        spent_usd = 0.0


class FakeSource:
    """Stands in for CandidateSource: `stored` is the paper table, `remote` what
    lookups and searches would add."""

    def __init__(self, stored=None, remote_doi=None, remote_pmid=None, crossref_adds=None,
                 openalex_adds=None, budget_spent=False):
        self.stored = dict(stored or {})            # paper_id -> Candidate
        self.ids = {}                               # ("doi", value) -> paper_id
        self.remote_doi = remote_doi or {}
        self.remote_pmid = remote_pmid or {}
        self.crossref_adds = crossref_adds or []
        self.openalex_adds = openalex_adds or []
        self.budget_spent = budget_spent
        self.openalex = FakeOpenAlex()

    def paper_by(self, column, value):
        return self.ids.get((column, value))

    registered_dois: set = set()

    def doi_registered(self, doi, log):
        log.doi_handle += 1
        return doi in self.registered_dois

    def title_norm_of(self, paper_id):
        c = self.stored.get(paper_id)
        return c.title_norm if c else None

    def fetch_doi(self, doi, log: LookupLog):
        log.crossref_doi += 1
        return self.remote_doi.get(doi)

    def fetch_pmid(self, pmid, log: LookupLog):
        log.openalex_pmid += 1
        return self.remote_pmid.get(pmid)

    def search_crossref(self, query, log: LookupLog):
        log.crossref_search += 1
        for c in self.crossref_adds:
            self.stored[c.paper_id] = c

    def search_openalex(self, title, year, log: LookupLog):
        if self.budget_spent:
            raise OpenAlexBudgetExhausted("spent")
        log.openalex_search += 1
        for c in self.openalex_adds:
            self.stored[c.paper_id] = c

    def local_title_candidates(self, title_norm, exclude_paper_id, limit=10, main_norm=""):
        return sorted((c for c in self.stored.values() if c.paper_id != exclude_paper_id),
                      key=lambda c: -c.similarity)[:limit]


def test_stage1_doi_already_stored():
    src = FakeSource()
    src.ids[("doi", "10.1/x")] = 5
    r = Resolver(src, TH).resolve(_ref(doi="10.1/x"))
    assert (r.status, r.method, r.score, r.paper_id) == ("resolved", "doi", 1.0, 5)
    assert r.evidence["lookups"] == {}


def test_identifier_match_records_title_agreement():
    src = FakeSource(stored={5: _cand(5, title_norm="effects of caffeine on time trial performance in men")})
    src.ids[("doi", "10.1/x")] = 5
    r = Resolver(src, TH).resolve(_ref(doi="10.1/x", title="Effects of caffeine on exercise in women"))
    assert (r.method, r.paper_id) == ("doi", 5)  # the DOI match stands
    assert 0 < r.evidence["title_agreement"] < 0.8  # but the disagreement is on record

    src.stored[5] = _cand(5)
    assert Resolver(src, TH).resolve(_ref(doi="10.1/x")).evidence["title_agreement"] == 1.0


def test_stage1_doi_fetched():
    r = Resolver(FakeSource(remote_doi={"10.1/x": 6}), TH).resolve(_ref(doi="10.1/x"))
    assert (r.method, r.paper_id) == ("doi", 6)
    assert r.evidence["lookups"]["crossref_doi"] == 1


def test_unknown_doi_falls_through_to_title():
    src = FakeSource(stored={7: _cand(7)})
    r = Resolver(src, TH).resolve(_ref(doi="10.1/missing"))
    assert (r.method, r.paper_id) == ("title_exact", 7)
    assert r.evidence["stages"][0] == {"stage": 1, "outcome": "doi_not_found",
                                       "doi_org_registered": False}


def test_doi_unknown_to_crossref_is_checked_at_doi_org():
    src = FakeSource()
    src.registered_dois = {"10.5061/dryad.x"}
    r = Resolver(src, TH).resolve(_ref(doi="10.5061/dryad.x", title=None))
    assert r.status == "unresolved"
    assert r.evidence["stages"][0]["doi_org_registered"] is True
    assert r.evidence["lookups"]["doi_handle"] == 1


def test_stage2_pmid_fetched():
    r = Resolver(FakeSource(remote_pmid={"123": 8}), TH).resolve(_ref(pmid="123"))
    assert (r.method, r.paper_id) == ("pmid", 8)


def test_crossref_candidate_resolves_on_second_pass():
    src = FakeSource(crossref_adds=[_cand(9)])
    r = Resolver(src, TH).resolve(_ref())
    assert (r.method, r.paper_id) == ("title_exact", 9)
    passes = [s.get("pass") for s in r.evidence["stages"] if s["stage"] == "3-5"]
    assert passes == ["local", "crossref"]


def test_openalex_is_only_the_fallback():
    src = FakeSource(crossref_adds=[], openalex_adds=[_cand(12)])
    r = Resolver(src, TH).resolve(_ref())
    assert (r.method, r.paper_id) == ("title_exact", 12)
    assert r.evidence["lookups"] == {"crossref_search": 1, "openalex_search": 1}


def test_nothing_matches_is_unresolved_with_near_misses():
    src = FakeSource(crossref_adds=[_cand(13, sim=0.7, title_norm="other", year=2010)])
    r = Resolver(src, TH).resolve(_ref())
    assert (r.status, r.paper_id) == ("unresolved", None)
    assert r.evidence["reason"] == "no_candidate_matched"
    assert r.evidence["near_misses"][0]["paper_id"] == 13


def test_no_identifier_and_no_title_is_unresolved_without_lookups():
    src = FakeSource()
    r = Resolver(src, TH).resolve(_ref(title=None))
    assert (r.status, r.evidence["reason"]) == ("unresolved", "nothing_to_match_on")
    assert r.evidence["lookups"] == {}


def test_spent_openalex_budget_stops_instead_of_unresolving():
    src = FakeSource(budget_spent=True)
    with pytest.raises(OpenAlexBudgetExhausted):
        Resolver(src, TH).resolve(_ref())


def test_a_rejected_search_is_recorded_not_fatal():
    import httpx

    from sentinel.resolve.candidates import CandidateSource

    def reject(*args, **kwargs):
        raise httpx.HTTPStatusError("bad", request=httpx.Request("GET", "https://x.test"),
                                    response=httpx.Response(400))

    class Client:
        search_and_store = staticmethod(reject)

    src = CandidateSource(None, Client(), Client())
    log = LookupLog()
    src.search_openalex("what is needed?", 2014, log)  # must not raise
    assert log.rejected == ["openalex:400"]


def test_a_server_error_on_search_still_raises():
    import httpx

    from sentinel.resolve.candidates import CandidateSource

    def fail(*args, **kwargs):
        raise httpx.HTTPStatusError("down", request=httpx.Request("GET", "https://x.test"),
                                    response=httpx.Response(503))

    class Client:
        search_and_store = staticmethod(fail)

    with pytest.raises(httpx.HTTPStatusError):
        CandidateSource(None, Client(), Client()).search_crossref("q", LookupLog())


def test_citing_paper_is_never_its_own_match():
    src = FakeSource(stored={999: _cand(999)})  # 999 is the citing paper
    src.crossref_adds = []
    r = Resolver(src, TH).resolve(_ref())
    assert r.status == "unresolved"
