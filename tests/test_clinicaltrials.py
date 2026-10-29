"""ClinicalTrials.gov lookup client against saved study JSON.

Fixtures are real API v2 responses with every value under the drop paths
replaced by REDACTED; the keys are kept so stripping can be tested.
"""

from __future__ import annotations

import json
from datetime import date

import httpx
import pytest
from sqlalchemy import func, select, text

from sentinel.ingest.clinicaltrials import (
    ClinicalTrialsClient,
    drop_paths,
    normalise_nct_id,
    parse_registry_date,
)
from sentinel.ingest.http import RateLimitedHTTP
from sentinel.models import trial, trial_outcome
from tests.conftest import FIXTURES, requires_db

DROP_PATHS = [
    "protocolSection.contactsLocationsModule.centralContacts",
    "protocolSection.contactsLocationsModule.overallOfficials",
    "protocolSection.contactsLocationsModule.locations[].contacts",
    "protocolSection.sponsorCollaboratorsModule.responsibleParty",
    "resultsSection.moreInfoModule.pointOfContact",
]
BASE = "https://ctgov.test/api/v2"


def _study(nct_id: str) -> dict:
    return json.loads((FIXTURES / "clinicaltrials" / f"{nct_id}.json").read_text("utf-8"))


def _client(handler=None) -> ClinicalTrialsClient:
    transport = httpx.MockTransport(handler or (lambda r: httpx.Response(500)))
    http = RateLimitedHTTP(
        httpx.Client(transport=transport, follow_redirects=True),
        source="clinicaltrials",
        rate_per_s=1000,
        max_retries=1,
        backoff_base_s=0,
        sleep=lambda s: None,
    )
    return ClinicalTrialsClient(
        http, base_url=BASE, drop_paths=DROP_PATHS, strip_personal_fields=True
    )


def _get(obj: dict, path: str):
    """Values at a drop path; `[]` fans out over lists."""
    nodes = [obj]
    for seg in path.split("."):
        nxt = []
        for node in nodes:
            if not isinstance(node, dict):
                continue
            if seg.endswith("[]"):
                nxt.extend(node.get(seg[:-2]) or [])
            elif seg in node:
                nxt.append(node[seg])
        nodes = nxt
    return nodes


# --- ADR-018: no dropped path survives into trial.raw -------------------------


@pytest.mark.parametrize("nct_id", ["NCT05711641", "NCT02016378"])
def test_adr018_no_drop_path_survives_on_real_records(nct_id):
    raw = _study(nct_id)
    assert any(_get(raw, p) for p in DROP_PATHS)  # the fixture really has them
    record = _client().normalise(raw)
    for path in DROP_PATHS:
        assert _get(record.raw, path) == [], path
    assert "REDACTED" not in json.dumps(record.raw)


def test_adr018_every_drop_path_including_list_elements():
    raw = {
        "protocolSection": {
            "identificationModule": {"nctId": "NCT00000001"},
            "sponsorCollaboratorsModule": {
                "leadSponsor": {"name": "Org", "class": "OTHER"},
                "responsibleParty": {"type": "PRINCIPAL_INVESTIGATOR",
                                     "investigatorFullName": "A Person"},
            },
            "contactsLocationsModule": {
                "centralContacts": [{"name": "A Person", "email": "a@x.org"}],
                "overallOfficials": [{"name": "A Person"}],
                "locations": [
                    {"facility": "Hospital", "country": "France",
                     "contacts": [{"name": "A Person", "phone": "1"}]},
                    {"facility": "Clinic", "country": "Spain"},
                    {"facility": "Unit", "country": "Italy",
                     "contacts": [{"name": "B Person"}]},
                ],
            },
        },
        "resultsSection": {"moreInfoModule": {
            "pointOfContact": {"email": "b@x.org"}, "certainAgreement": {}}},
    }
    record = _client().normalise(raw)
    for path in DROP_PATHS:
        assert _get(record.raw, path) == [], path
    dumped = json.dumps(record.raw)
    assert "Person" not in dumped and "@" not in dumped
    # Institutional fields stay.
    locations = record.raw["protocolSection"]["contactsLocationsModule"]["locations"]
    assert [loc["country"] for loc in locations] == ["France", "Spain", "Italy"]
    assert record.raw["resultsSection"]["moreInfoModule"] == {"certainAgreement": {}}
    assert record.lead_sponsor == "Org"


def test_drop_paths_does_not_mutate_input():
    raw = _study("NCT05711641")
    before = json.dumps(raw, sort_keys=True)
    drop_paths(raw, DROP_PATHS)
    assert json.dumps(raw, sort_keys=True) == before


def test_refuses_to_run_with_stripping_off():
    with pytest.raises(ValueError, match="ADR-018"):
        ClinicalTrialsClient(
            _client().http, base_url=BASE, drop_paths=DROP_PATHS, strip_personal_fields=False
        )


# --- normalise ----------------------------------------------------------------


def test_normalise_study_with_results():
    record = _client().normalise(_study("NCT05711641"))
    assert record.nct_id == "NCT05711641"
    assert record.brief_title == "Action of Intra-auricular Topical Lidocaine on Tinnitus"
    assert (record.overall_status, record.study_type, record.phase) == (
        "COMPLETED", "INTERVENTIONAL", "NA")
    assert record.enrollment == 32
    assert record.start_date == date(2022, 9, 2)
    assert record.primary_completion_date == date(2023, 6, 1)
    assert record.completion_date == date(2023, 6, 1)
    assert record.results_first_posted == date(2024, 8, 30)
    assert (record.lead_sponsor, record.sponsor_class) == (
        "University of Sao Paulo General Hospital", "OTHER")
    assert [o.measure for o in record.outcomes] == [
        "Change in Visual Analogue Scale (VAS) Score",
        "Change in Tinnitus Loudness",
        "Change in Minimum Masking Level (MML)",
    ]
    assert {(o.version_no, o.outcome_type, o.version_date) for o in record.outcomes} == {
        (0, "primary", date(2024, 8, 30))}


def test_normalise_month_only_dates_use_last_day():
    record = _client().normalise(_study("NCT02016378"))
    assert record.phase == "PHASE3"
    assert record.results_first_posted is None
    assert record.primary_completion_date == date(2022, 12, 31)  # "2022-12"
    assert record.start_date == date(2014, 1, 31)  # "2014-01"
    assert record.outcomes[0].time_frame == (
        "baseline, 6 weeks and 6 months after the start of treatment")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2023-06-01", date(2023, 6, 1)),
        ("2022-12", date(2022, 12, 31)),
        ("2024-02", date(2024, 2, 29)),
        ("2023-02", date(2023, 2, 28)),
        ("2019", date(2019, 12, 31)),
        (None, None),
        ("", None),
    ],
)
def test_parse_registry_date(value, expected):
    assert parse_registry_date(value) == expected


@pytest.mark.parametrize("value", ["June 2023", "2023/06/01", "2023-06-01-01"])
def test_parse_registry_date_rejects_other_formats(value):
    with pytest.raises(ValueError):
        parse_registry_date(value)


def test_multiple_phases_are_joined():
    raw = _study("NCT02016378")
    raw["protocolSection"]["designModule"]["phases"] = ["PHASE2", "PHASE3"]
    assert _client().normalise(raw).phase == "PHASE2,PHASE3"


def test_duplicate_measures_keep_first():
    raw = _study("NCT02016378")
    first = raw["protocolSection"]["outcomesModule"]["primaryOutcomes"][0]
    raw["protocolSection"]["outcomesModule"]["primaryOutcomes"].append(
        dict(first, timeFrame="12 months"))
    outcomes = _client().normalise(raw).outcomes
    assert len(outcomes) == 1 and outcomes[0].time_frame == first["timeFrame"]


@pytest.mark.parametrize(("value", "expected"), [("nct02016378", "NCT02016378"),
                                                 (" NCT02016378 ", "NCT02016378")])
def test_normalise_nct_id(value, expected):
    assert normalise_nct_id(value) == expected


@pytest.mark.parametrize("value", ["NCT123", "02016378", "NCT0201637X"])
def test_normalise_nct_id_rejects_bad_ids(value):
    with pytest.raises(ValueError):
        normalise_nct_id(value)


# --- fetch_study: mocked HTTP --------------------------------------------------


def test_fetch_study_hits_per_study_endpoint():
    seen: list[httpx.URL] = []

    def handler(request):
        seen.append(request.url)
        return httpx.Response(200, json=_study("NCT02016378"))

    raw = _client(handler).fetch_study("nct02016378")
    assert raw["protocolSection"]["identificationModule"]["nctId"] == "NCT02016378"
    assert seen[0].path == "/api/v2/studies/NCT02016378"
    assert seen[0].params["format"] == "json"


def test_fetch_study_not_found_returns_none():
    handler = lambda r: httpx.Response(404, text="NCT number NCT99999999 not found")
    assert _client(handler).fetch_study("NCT99999999") is None


def test_fetch_study_follows_alias_redirect():
    def handler(request):
        if request.url.path.endswith("NCT00000002"):
            return httpx.Response(301, headers={"Location": f"{BASE}/studies/NCT02016378"})
        return httpx.Response(200, json=_study("NCT02016378"))

    raw = _client(handler).fetch_study("NCT00000002")
    assert _client().normalise(raw).nct_id == "NCT02016378"


def test_fetch_study_raises_on_unexpected_status():
    with pytest.raises(httpx.HTTPStatusError):
        _client(lambda r: httpx.Response(400, text="bad")).fetch_study("NCT02016378")


# --- database -------------------------------------------------------------------


@pytest.fixture
def ct_engine(db_engine):
    ids = ("NCT05711641", "NCT02016378")
    with db_engine.begin() as conn:
        others = conn.execute(
            select(func.count()).select_from(trial).where(trial.c.nct_id.not_in(ids))
        ).scalar_one()
        if others:
            pytest.skip("trial holds real data; not a disposable database")
    _clear(db_engine, ids)
    yield db_engine
    _clear(db_engine, ids)


def _clear(engine, ids) -> None:
    # trial_outcome rows go with their trial (ON DELETE CASCADE).
    with engine.begin() as conn:
        conn.execute(trial.delete().where(trial.c.nct_id.in_(ids)))


def _serve(*studies: dict):
    by_id = {s["protocolSection"]["identificationModule"]["nctId"]: s for s in studies}
    return lambda r: httpx.Response(200, json=by_id[r.url.path.rsplit("/", 1)[-1]])


@requires_db
def test_lookup_stores_trial_and_version0_outcomes(ct_engine):
    client = _client(_serve(_study("NCT05711641")))
    client.lookup(ct_engine, "NCT05711641")

    with ct_engine.connect() as conn:
        row = conn.execute(select(trial).where(trial.c.nct_id == "NCT05711641")).one()
        outcomes = conn.execute(
            select(trial_outcome).where(trial_outcome.c.nct_id == "NCT05711641")
        ).all()
    assert row.overall_status == "COMPLETED"
    assert row.results_first_posted == date(2024, 8, 30)
    assert {o.version_no for o in outcomes} == {0}
    assert len(outcomes) == 3
    for path in DROP_PATHS:  # ADR-018, checked on what was actually written
        assert _get(row.raw, path) == [], path


@requires_db
def test_lookup_twice_is_idempotent_and_replaces_version0(ct_engine):
    study = _study("NCT02016378")
    _client(_serve(study)).lookup(ct_engine, "NCT02016378")
    _client(_serve(study)).lookup(ct_engine, "NCT02016378")

    changed = json.loads(json.dumps(study))
    changed["protocolSection"]["outcomesModule"]["primaryOutcomes"][0]["measure"] = "Renamed"
    _client(_serve(changed)).lookup(ct_engine, "NCT02016378")

    with ct_engine.connect() as conn:
        trials = conn.execute(select(func.count()).select_from(trial)).scalar_one()
        measures = conn.execute(
            select(trial_outcome.c.measure).where(trial_outcome.c.nct_id == "NCT02016378")
        ).scalars().all()
    assert trials == 1
    assert measures == ["Renamed"]
