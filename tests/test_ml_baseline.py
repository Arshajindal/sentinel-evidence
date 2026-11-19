"""The tabular baseline (S2.9, ADR-057): metadata features, grouped
cross-validation, calibration and SHAP. No network and no MLflow: labels are
made up here, and only the files a run writes are checked."""

from __future__ import annotations

import csv
import json

import numpy as np
import pytest
from pydantic import ValidationError
from sqlalchemy import delete, insert, select

from sentinel.fulltext.contexts import EXTRACTOR_VERSION as CONTEXT_VERSION
from sentinel.ml.baseline import (
    LABELS, LabelRow, _folds, assemble, chance, cross_validate, ece, load_labels, placebo_labels, run, save)
from sentinel.ml.features import (
    CitationFeatures, author_keys, build_features, feature_group, feature_names, shared_authors, to_matrix)
from sentinel.models import (
    citation_context, citation_context_reference, corpus, corpus_paper, paper, paper_trial_link, reference,
    retraction_notice, text_availability, trial)
from tests.conftest import requires_db

# --- authors ---------------------------------------------------------------------------


def test_author_keys_read_every_sources_shape():
    europepmc = [{"last_name": "Chan", "initials": "JM", "full_name": "Chan JM"}]
    crossref = [{"full_name": "Ann Van den Bruel", "last_name": "Van den Bruel"}]
    openalex = [{"full_name": "Beverly E. Thorn"}]
    initials_only = [{"full_name": "Müller K", "last_name": "Müller"}]
    assert author_keys(europepmc) == {("chan", "j")}
    assert author_keys(crossref) == {("vandenbruel", "a")}
    assert author_keys(openalex) == {("thorn", "b")}
    assert author_keys(initials_only) == {("muller", "k")}  # accents and the name order handled
    assert author_keys(None) == set() and author_keys([{"full_name": ""}]) == set()


def test_shared_authors_count_and_unknown_when_a_list_is_empty():
    citing = [{"last_name": "Chan", "initials": "JM"}, {"last_name": "Thorn", "initials": "BE"}]
    cited = [{"full_name": "June Chan", "last_name": "Chan"}, {"full_name": "Beverly E. Thorn"},
             {"full_name": "Someone Else"}]
    assert shared_authors(citing, cited) == 2
    assert shared_authors(citing, [{"full_name": "Jo Chan"}]) == 1  # same surname and initial
    assert shared_authors(citing, [{"full_name": "Kim Chan"}]) == 0
    assert shared_authors(citing, []) is None and shared_authors(None, cited) is None


# --- rows and encoding -------------------------------------------------------------------

def _row(i: int, *, paper: int, section="introduction", cited_text="abstract", **kw) -> CitationFeatures:
    base = dict(citation_context_id=i, reference_id=1000 + i, citing_paper_id=paper, cited_paper_id=None,
                citing_year=2020, citing_venue_papers=3, citing_references=40, citing_sponsor="OTHER",
                cited_year=2012, year_gap=8, cited_venue_citations=20, cited_resolution="resolved",
                cited_text=cited_text, cited_notice="none", shared_authors=0, section=section, co_cited=1)
    return CitationFeatures(**{**base, **kw})


def test_a_feature_row_refuses_impossible_values_and_unknown_categories():
    with pytest.raises(ValidationError):
        _row(1, paper=1, citing_year=3020)
    with pytest.raises(ValidationError):
        _row(1, paper=1, section="abstract")  # contexts come from the body
    with pytest.raises(ValidationError):
        _row(1, paper=1, cited_notice="retracted")


def test_the_matrix_keeps_unknowns_unknown_and_one_hot_has_an_unknown_column():
    rows = [_row(1, paper=1), _row(2, paper=1, cited_year=None, year_gap=None, cited_text=None)]
    x, names = to_matrix(rows), feature_names()
    assert x.shape == (2, len(names))
    col = {n: i for i, n in enumerate(names)}
    assert x[0, col["cited_year"]] == 2012 and np.isnan(x[1, col["cited_year"]])
    assert x[0, col["cited_text=abstract"]] == 1 and x[1, col["cited_text=unknown"]] == 1
    assert x[1, col["cited_text=abstract"]] == 0
    group_cols = [i for i, n in enumerate(names) if feature_group(n) == "cited_text"]
    assert (x[:, group_cols].sum(axis=1) == 1).all()  # exactly one value per category


# --- training ------------------------------------------------------------------------------

def _planted(n_papers: int = 60, per_paper: int = 5, noise: float = 0.1, seed: int = 7):
    """Labels that follow two features: no text held -> insufficient; a
    results-section citation -> contradicted; otherwise supported."""
    rng = np.random.default_rng(seed)
    rows, labels = [], []
    for p in range(n_papers):
        for j in range(per_paper):
            i = p * per_paper + j
            text = rng.choice(["full", "abstract", "none"], p=[0.3, 0.4, 0.3])
            section = rng.choice(["introduction", "methods", "results", "discussion"], p=[0.3, 0.2, 0.3, 0.2])
            row = _row(i, paper=p, section=str(section), cited_text=str(text),
                       citing_year=int(rng.integers(2015, 2025)), cited_year=int(rng.integers(1990, 2015)),
                       citing_references=int(rng.integers(20, 90)))
            label = "insufficient" if text == "none" else "contradicted" if section == "results" else "supported"
            if rng.random() < noise:
                label = str(rng.choice(LABELS))
            rows.append(row)
            labels.append(LabelRow(citation_context_id=i, reference_id=1000 + i, label=label))
    return rows, labels


def test_the_baseline_finds_a_planted_signal_and_shap_names_it():
    rows, labels = _planted()
    result = run(rows, labels, seed=20260922, label_source="planted")
    m = result.cv.metrics
    assert m["macro_f1"] > 0.8 and m["macro_f1"] > m["chance_macro_f1_guessing"] + 0.3
    assert m["pr_auc_not_supported"] > 0.9
    assert set(result.shap["order"][:2]) == {"cited_text", "section"}
    assert 0 <= m["ece"] <= 1 and 0 <= m["raw_ece"] <= 1
    assert result.params["outer_folds"] == 5 and result.params["n_labelled"] == 300


def test_random_labels_score_near_chance():
    rows, _ = _planted()
    labels = placebo_labels(rows, 300, seed=3)
    m = cross_validate(assemble(rows, labels), seed=3).metrics
    assert m["macro_f1"] < 0.45
    assert abs(m["pr_auc_not_supported"] - chance(assemble(rows, labels).y)["chance_pr_auc_not_supported"]) < 0.1


def test_folds_never_split_a_citing_paper():
    rows, labels = _planted()
    data = assemble(rows, labels)
    for train, test in _folds(5, data.y, data.groups, seed=1):
        assert not set(data.groups[train]) & set(data.groups[test])
    with pytest.raises(ValueError):
        _folds(5, data.y[:5], np.zeros(5, dtype=int), seed=1)  # one paper: nothing to hold out


def test_placebo_labels_are_fixed_by_the_seed_and_bounded():
    rows, _ = _planted()
    assert placebo_labels(rows, 50, seed=9) == placebo_labels(rows, 50, seed=9)
    assert placebo_labels(rows, 50, seed=9) != placebo_labels(rows, 50, seed=10)
    with pytest.raises(ValueError):
        placebo_labels(rows, len(rows) + 1, seed=9)


def test_labels_must_match_a_feature_row_and_be_valid(tmp_path):
    rows, labels = _planted(n_papers=4)
    with pytest.raises(ValueError, match="no feature row"):
        assemble(rows, [*labels, LabelRow(citation_context_id=999, reference_id=1, label="supported")])
    path = tmp_path / "labels.csv"
    path.write_text("citation_context_id,reference_id,label\n1,1001,supported\n2,1002,unsure\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_labels(path)
    path.write_text("citation_context_id,reference_id,label\n1,1001,supported\n1,1001,contradicted\n",
                    encoding="utf-8")
    with pytest.raises(ValueError, match="more than once"):
        load_labels(path)


def test_ece_and_chance():
    y = np.array([0, 0, 1, 1])
    sure_and_right = np.array([[1, 0, 0], [1, 0, 0], [0, 1, 0], [0, 1, 0]], dtype=float)
    assert ece(sure_and_right, y) == 0
    overconfident = np.array([[0.9, 0.05, 0.05]] * 4)  # 90% sure, right half the time
    assert ece(overconfident, y) == pytest.approx(0.4)
    c = chance(np.array([0, 0, 0, 1, 2]))
    assert c["chance_macro_f1"] == pytest.approx((2 * 0.6 / 1.6) / 3)
    assert c["chance_macro_f1_guessing"] == pytest.approx(1 / 3)
    assert c["chance_pr_auc_not_supported"] == pytest.approx(0.4)


def test_a_run_saves_metrics_shap_predictions_and_both_plots(tmp_path):
    rows, labels = _planted(n_papers=30)
    result = run(rows, labels, seed=5, label_source="planted")
    files = save(result, tmp_path / "out", title="Planted")
    assert {f.name for f in files} == {"metrics.json", "shap.json", "oof.csv", "shap.png", "reliability.png"}
    saved = json.loads((tmp_path / "out" / "metrics.json").read_text(encoding="utf-8"))
    assert saved["params"]["features_ver"] and saved["metrics"]["macro_f1"] == result.cv.metrics["macro_f1"]
    with (tmp_path / "out" / "oof.csv").open(encoding="utf-8") as f:
        oof = list(csv.DictReader(f))
    assert len(oof) == len(labels)
    assert all(abs(sum(float(r[f"calibrated_{n}"]) for n in LABELS) - 1) < 1e-4 for r in oof)
    assert (tmp_path / "out" / "shap.png").stat().st_size > 1000


# --- the feature table against the test database -------------------------------------------

LABEL = "test-s29"
TITLES = ("s29-citing", "s29-cited-a", "s29-cited-b")


@pytest.fixture
def s29_engine(db_engine):
    with db_engine.connect() as conn:
        if conn.execute(select(paper.c.id).where(paper.c.title.not_in(TITLES))).first():
            pytest.skip("paper holds other data; not a disposable database")
    _clear(db_engine)
    with db_engine.begin() as conn:
        def add(title, **kw):
            return conn.execute(insert(paper).values(title=title, title_norm=title, source="test", raw={}, **kw)
                                .returning(paper.c.id)).scalar_one()
        citing = add(TITLES[0], year=2020, container="Trials",
                     authors=[{"last_name": "Chan", "initials": "JM"}, {"last_name": "Lee", "initials": "K"}])
        cited_a = add(TITLES[1], year=2011, container="Lancet", doi="10.1/s29a",
                      authors=[{"full_name": "June Chan", "last_name": "Chan"}])
        cited_b = add(TITLES[2], year=2018, container="BMJ", authors=[{"full_name": "Ola Berg"}])
        conn.execute(insert(corpus).values(label=LABEL, source="t", query="t", seed=1, hit_count=1, n=1))
        conn.execute(insert(corpus_paper).values(label=LABEL, ordinal=1, source="PMC", source_id="x",
                                                 pmcid="PMC29", paper_id=citing))
        refs = conn.execute(insert(reference).returning(reference.c.id, sort_by_parameter_order=True), [
            {"citing_paper_id": citing, "ordinal": o, "resolved_paper_id": r, "resolution_status": s,
             "parsed_year": y, "parsed_container": v}
            for o, r, s, y, v in ((1, cited_a, "resolved", None, None), (2, cited_b, "resolved", None, None),
                                  (3, None, "unresolved", 1999, "Lancet"))]).scalars().all()
        ctx = conn.execute(insert(citation_context).returning(citation_context.c.id, sort_by_parameter_order=True), [
            {"citing_paper_id": citing, "extractor_ver": CONTEXT_VERSION, "ordinal": 1, "section": "results",
             "marker": "1-2", "sentence": "S.", "context_text": "S."},
            {"citing_paper_id": citing, "extractor_ver": CONTEXT_VERSION, "ordinal": 2, "section": "methods",
             "marker": "3", "sentence": "T.", "context_text": "T."},
            {"citing_paper_id": citing, "extractor_ver": "0.9.0", "ordinal": 1, "section": "results",
             "marker": "1", "sentence": "Old.", "context_text": "Old."},  # an old version: not read
        ]).scalars().all()
        conn.execute(insert(citation_context_reference), [
            {"citation_context_id": ctx[0], "reference_id": refs[0]},
            {"citation_context_id": ctx[0], "reference_id": refs[1]},
            {"citation_context_id": ctx[1], "reference_id": refs[2]},
            {"citation_context_id": ctx[2], "reference_id": refs[0]}])
        conn.execute(insert(text_availability), [
            {"paper_id": cited_a, "source": "europepmc", "found": True, "fulltext": "full"},
            {"paper_id": cited_b, "source": "europepmc", "found": True, "fulltext": "abstract"}])
        conn.execute(insert(retraction_notice), [
            {"retracted_doi": "10.1/s29a", "notice_type": "correction", "source": "s29", "source_record_id": "1"},
            {"retracted_doi": "10.1/s29a", "notice_type": "retraction", "source": "s29", "source_record_id": "2"}])
        conn.execute(insert(trial).values(nct_id="NCT29000001", sponsor_class="INDUSTRY", raw={}))
        conn.execute(insert(paper_trial_link).values(paper_id=citing, nct_id="NCT29000001", link_source="t"))
    yield db_engine
    _clear(db_engine)


def _clear(engine) -> None:
    with engine.begin() as conn:
        ids = select(paper.c.id).where(paper.c.title.in_(TITLES))
        conn.execute(delete(reference).where(reference.c.citing_paper_id.in_(ids)))
        conn.execute(delete(paper).where(paper.c.title.in_(TITLES)))
        conn.execute(delete(corpus).where(corpus.c.label == LABEL))
        conn.execute(delete(retraction_notice).where(retraction_notice.c.source == "s29"))
        conn.execute(delete(trial).where(trial.c.nct_id == "NCT29000001"))


@requires_db
def test_the_feature_table_has_one_row_per_citation_from_current_contexts(s29_engine):
    with s29_engine.connect() as conn:
        rows = build_features(conn, label=LABEL)
    assert len(rows) == 3  # two references in the first context, one in the second; the old version skipped
    a, b, unmatched = sorted(rows, key=lambda r: r.reference_id)
    assert (a.citing_year, a.citing_references, a.citing_sponsor, a.citing_venue_papers) == (2020, 3, "INDUSTRY", 1)
    assert (a.cited_year, a.year_gap, a.cited_text, a.cited_notice) == (2011, 9, "full", "retraction")
    assert (a.shared_authors, a.section, a.co_cited) == (1, "results", 2)  # "June Chan" matches "Chan JM"
    assert (b.shared_authors, b.cited_text, b.cited_notice) == (0, "abstract", "none")
    assert (unmatched.cited_resolution, unmatched.cited_year, unmatched.year_gap) == ("unresolved", 1999, 21)
    assert unmatched.shared_authors is None and unmatched.cited_text is None and unmatched.co_cited == 1
    assert a.cited_venue_citations == unmatched.cited_venue_citations == 2  # both cite "Lancet"
