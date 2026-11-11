"""The synthetic evaluation set: number-anchored pairs from full text,
corrupted by rule (S2.8, ADR-051). The texts here are made up."""

from __future__ import annotations

import random

import pytest
from pydantic import ValidationError
from sqlalchemy import delete, func, insert, select

from sentinel.eval.items import EvalItemRecord, hash_split
from sentinel.eval.synthetic import (
    DATASET,
    LABEL,
    MAX_GOLD,
    Pair,
    PassageRow,
    _scaled,
    adaptable_license,
    change_number,
    change_population,
    claim_anchors,
    corrupt,
    find_pairs,
    flip_direction,
    generate,
    numbers,
    strip_heading,
    values,
)
from sentinel.models import corpus, corpus_paper, eval_item, eval_item_gold_passage, paper, passage, reference
from tests.conftest import requires_db

# --- numbers and anchors -------------------------------------------------------------


def test_numbers_keep_anchors_and_mark_noise():
    text = ("Mortality was 12.3% vs 18·1% (HR 0.65, 95% CI 0.51-0.83; P<0.001) among 1,234 "
            "patients enrolled in 2019, scored on the SF-36 at week 6.")
    assert {n.value: n.anchor for n in numbers(text)} == {
        "12.3": True, "18.1": True, "0.65": True, "0.51": True, "0.83": True, "1234": True,
        "95": False,      # a confidence level
        "0.001": False,   # a P value
        "2019": False,    # a year
        "6": False,       # under 10
    }  # "36" belongs to "SF-36", so it isn't a number at all


@pytest.mark.parametrize("raw, clean", [
    ("Results Of the 38 wards, 19 were assigned.", "Of the 38 wards, 19 were assigned."),
    ("RESULTS: Mortality fell.", "Mortality fell."),
    ("Summary Background Malaria persists.", "Malaria persists."),
    ("Results were similar in both arms.", "Results were similar in both arms."),
])
def test_structured_abstract_headings_are_stripped(raw, clean):
    assert strip_heading(raw) == clean


def test_a_claim_needs_two_anchors_one_a_decimal_or_percentage():
    assert claim_anchors("Pain fell by 12.5% from 40 points in the treated group overall.") == {"12.5", "40"}
    assert claim_anchors("Of the 38 wards, 19 were assigned to the treated group overall.") is None
    assert claim_anchors("Pain fell by 12.5% in the treated group, a clear result overall.") is None
    assert claim_anchors("Pain fell 12.5% from 40.2.") is None  # too short to be a claim


def test_merged_sentences_and_aims_are_not_claims():
    # What abstracts cleaned before the S2.2 fix look like: "(P<0.001).<h4>Conclusion</h4>" lost.
    assert claim_anchors("Pain fell by 12.5% from 40.2 points (P Conclusion The drug helps.") is None
    assert claim_anchors("To assess whether pain fell by 12.5% from 40.2 points in the trial.") is None
    assert claim_anchors("The aim was a 12.5% fall from 40.2 points in the treated group.") is None


# --- pairs ---------------------------------------------------------------------------

CLAIM = "Pain fell by 12.5% (from 40.2 to 35.2 points) in women given the drug, and walking distance was higher."


def _rows(*specs: tuple[str, str]) -> list[PassageRow]:
    return [PassageRow(i, i, section, text) for i, (section, text) in enumerate(specs, start=1)]


def test_a_pair_is_an_abstract_sentence_and_the_results_passages_repeating_its_numbers():
    rows = _rows(
        ("abstract", f"Background Knee pain limits walking. Results {CLAIM}"),
        ("abstract", f"Results {CLAIM} Conclusions The drug helps."),  # windows overlap
        ("methods", "Pain was 40.2 and 35.2 points, falling 12.5%, in a pilot."),  # never gold
        ("results", "Mean pain fell from 40.2 to 35.2 points, a 12.5% reduction (P = 0.01)."),
        ("results", "Adverse events were rare."),
    )
    assert find_pairs(7, rows) == [Pair(7, 1, CLAIM, 1, frozenset({"12.5", "40.2", "35.2"}), (4,))]


def test_numbers_repeated_in_too_many_passages_make_no_pair():
    repeat = "Pain fell from 40.2 to 35.2 points, a 12.5% reduction."
    rows = _rows(("abstract", CLAIM), *[("results", repeat)] * (MAX_GOLD + 1))
    assert find_pairs(7, rows) == []


# --- corruptions ---------------------------------------------------------------------


def test_direction_words_flip_with_their_article_and_case():
    assert flip_direction("Mortality was lower and an increase of 12.5% in survival was seen.") == (
        "Mortality was higher and a decrease of 12.5% in survival was seen.",
        [["lower", "higher"], ["increase", "decrease"]])
    assert flip_direction("Higher doses reduced pain by 12.5%.") == (
        "Lower doses increased pain by 12.5%.", [["Higher", "Lower"], ["reduced", "increased"]])


def test_direction_flips_skip_negation_names_and_anatomy():
    assert flip_direction("Pain was not lower with the drug (12.5% vs 13.0%).") is None
    assert flip_direction("Reduced-intensity conditioning gave 12.5% survival.") is None
    assert flip_direction("Lower limb pain decreased by 12.5%.") == (
        "Lower limb pain increased by 12.5%.", [["decreased", "increased"]])


def test_a_changed_number_is_one_the_source_never_prints():
    claim = "Response was 60.0% vs 30.0% in the two arms."
    blocked = values(claim) | {"90.0", "15.0", "45.0"}  # every 0.5x and 1.5x of both anchors
    assert change_number(claim, blocked, random.Random(1)) is None
    changed, details = change_number(claim, blocked - {"45.0"}, random.Random(1))
    assert changed == "Response was 60.0% vs 45.0% in the two arms."
    assert details == {"number_from": "30.0", "number_to": "45.0", "factor": 1.5}


def test_scaled_numbers_keep_their_print_and_stay_plausible():
    small, grouped = numbers("18·2 and 1,234")
    assert _scaled(small, 0.5) == ("9·1", "9.1")
    assert _scaled(grouped, 1.5) == ("1,851", "1851")
    (share,) = numbers("80.0%")
    assert _scaled(share, 1.5) is None  # 120%
    assert _scaled(share, 0.5) == ("40.0", "40.0")


def test_population_swaps_only_to_a_word_the_source_never_uses():
    claim = "Pain fell by 12.5% in women given the drug."
    assert change_population(claim, "Women aged 40 to 80 took part.") == (
        "Pain fell by 12.5% in men given the drug.", "women", "men")
    assert change_population(claim, "Women and men took part.") is None
    assert change_population(claim, "Of 83 patients, 55 were male.") is None  # a mixed sample
    for share in ("The cohort was 58,589 patients (females 46.6%).", "Of them, six females improved by 12.5%.",
                  "Of them, 33.7% (28 of 83) were women, and 12.5% had pain."):
        assert change_population(share, "") is None, share  # also a mixed sample, said in the claim
    assert change_population("Children and adults improved by 12.5%.", "") is None  # two populations
    assert change_population("Children improved by 12.5%.", "") == ("Adults improved by 12.5%.", "children",
                                                                  "adults")


def test_corrupt_lists_every_corruption_that_applies():
    pair = Pair(7, 1, CLAIM, 1, frozenset({"12.5", "40.2", "35.2"}), (4,))
    out = corrupt(pair, CLAIM, values(CLAIM), random.Random(1), lambda p, rng: (99, "co-cited"))
    assert [(t, source) for t, _, source, _ in out] == [
        ("none", 7), ("direction_flipped", 7), ("number_changed", 7), ("population_changed", 7),
        ("source_swapped", 99)]
    assert out[0][1] == out[4][1] == CLAIM
    assert all(claim != CLAIM for _, claim, _, _ in out[1:4])


@pytest.mark.parametrize("raw, code", [
    ("http://creativecommons.org/licenses/by/4.0/", "CC-BY-4.0"),
    ("https://creativecommons.org/licenses/by-nc-sa/3.0/", "CC-BY-NC-SA-3.0"),
    ("http://creativecommons.org/licenses/by/3.0/igo/legalcode", "CC-BY-3.0"),
    ("https://creativecommons.org/publicdomain/zero/1.0/", "CC0"),
    ("cc by-nc", "CC-BY-NC"),
    ("cc-by", "CC-BY"),
    ("cc0", "CC0"),
    ("public-domain", "public-domain"),
    ("http://creativecommons.org/licenses/by-nc-nd/4.0/", None),
    ("http://creativecommons.org/licenses/bync-nd/4.0/", None),
    ("cc-by-nc-nd", None),
    ("https://www.elsevier.com/tdm/userlicense/1.0/", None),
    ("other-oa", None),
    (None, None),
])
def test_only_licences_that_allow_adapted_text_are_used(raw, code):
    assert adaptable_license(raw) == code


def test_synthetic_items_need_a_corruption_type_and_others_have_none():
    base = dict(split="train", claim_text="c", source_paper_id=1, label="insufficient", license="L",
                origin={}, gold_passage_ids=[])
    with pytest.raises(ValidationError):
        EvalItemRecord(**base, set_name="synthetic")
    with pytest.raises(ValidationError):
        EvalItemRecord(**base, set_name="external", corruption_type="none")
    assert EvalItemRecord(**base, set_name="synthetic", corruption_type="none").corruption_type == "none"


# --- generate, against the test database ----------------------------------------------

LABEL_ = "test-s28"
VER = "1.0.0/150-300-1"
TITLES = ("s28-corpus", "s28-a", "s28-b", "s28-nd")
TEXTS = {
    "s28-a": [("abstract", f"Background Knee pain limits walking. Results {CLAIM}"),
              ("methods", "Women aged 40 to 80 were enrolled."),
              ("results", "Mean pain fell from 40.2 to 35.2 points, a 12.5% reduction (P = 0.01).")],
    "s28-b": [("abstract", "Results Blood pressure dropped from 150.4 to 138.6 mmHg, a fall of 7.8% in adults."),
              ("results", "Systolic pressure went from 150.4 to 138.6 mmHg, down 7.8%.")],
    "s28-nd": [("abstract", "Results Sleep rose from 5.5 to 6.8 hours, a gain of 23.6% in adults."),
               ("results", "Sleep rose from 5.5 to 6.8 hours, up 23.6%.")],
}
LICENCES = {"s28-a": "http://creativecommons.org/licenses/by/4.0/", "s28-b": "cc by",
            "s28-nd": "http://creativecommons.org/licenses/by-nc-nd/4.0/"}


@pytest.fixture
def s28_engine(db_engine):
    with db_engine.connect() as conn:
        if conn.execute(select(paper.c.id).where(paper.c.title.not_in(TITLES))).first():
            pytest.skip("paper holds other data; not a disposable database")
    _clear(db_engine)
    with db_engine.begin() as conn:
        ids = {t: conn.execute(insert(paper).values(title=t, title_norm=t, source="test", raw={},
                                                    license=LICENCES.get(t))
                               .returning(paper.c.id)).scalar_one() for t in TITLES}
        conn.execute(insert(corpus).values(label=LABEL_, source="t", query="t", seed=1, hit_count=1, n=1))
        conn.execute(insert(corpus_paper).values(label=LABEL_, ordinal=1, source="PMC", source_id="x",
                                                 pmcid="PMC1", paper_id=ids["s28-corpus"]))
        conn.execute(insert(reference), [
            {"citing_paper_id": ids["s28-corpus"], "ordinal": i, "resolved_paper_id": ids[t],
             "resolution_status": "resolved"} for i, t in enumerate(TITLES[1:], start=1)])
        conn.execute(insert(passage), [
            {"paper_id": ids[t], "splitter_ver": VER, "ordinal": k, "section": section, "text": text}
            for t, specs in TEXTS.items() for k, (section, text) in enumerate(specs, start=1)])
    yield db_engine, ids
    _clear(db_engine)


def _clear(engine) -> None:
    with engine.begin() as conn:
        conn.execute(delete(eval_item).where(eval_item.c.origin["corpus"].astext == LABEL_))
        ids = select(paper.c.id).where(paper.c.title.in_(TITLES))
        conn.execute(delete(reference).where(reference.c.citing_paper_id.in_(ids)))
        conn.execute(delete(paper).where(paper.c.title.in_(TITLES)))
        conn.execute(delete(corpus).where(corpus.c.label == LABEL_))


@requires_db
def test_generate_writes_pairs_and_corruptions_split_by_source_paper(s28_engine):
    engine, ids = s28_engine
    # A seed that puts the three cited works in one split, so each can be the
    # others' swapped source (IDs differ between runs of the suite).
    seed = next(s for s in range(1, 1000)
                if len({hash_split(f"{s}:{DATASET}:{ids[t]}") for t in TITLES[1:]}) == 1)
    result = generate(engine, corpus=LABEL_, seed=seed, splitter_ver=VER)
    assert (result.sources, result.adaptable, result.pairs) == (3, 2, 2)  # the ND paper is skipped
    assert result.by_type == {"none": 2, "direction_flipped": 1, "number_changed": 2,
                              "population_changed": 2, "source_swapped": 2}
    assert result.items_added == 9

    mine_only = eval_item.c.origin["corpus"].astext == LABEL_
    with engine.connect() as conn:
        items = conn.execute(select(eval_item).where(mine_only)).all()
        gold = {(r.eval_item_id, r.passage_id) for r in conn.execute(select(eval_item_gold_passage)).all()}
        results_passage = dict(conn.execute(select(passage.c.paper_id, passage.c.id)
                                            .where(passage.c.section == "results")).all())
    split_of = {pid: hash_split(f"{seed}:{DATASET}:{pid}") for pid in ids.values()}
    for item in items:
        claim_paper = item.origin["claim_paper_id"]
        assert claim_paper in (ids["s28-a"], ids["s28-b"])
        assert item.set_name == "synthetic" and item.label == LABEL[item.corruption_type]
        assert item.split == split_of[claim_paper] == split_of[item.source_paper_id]
        mine = {p for i, p in gold if i == item.id}
        if item.label == "insufficient":
            assert mine == set()
        else:
            assert mine == {results_passage[claim_paper]} and item.source_paper_id == claim_paper
        if item.corruption_type == "source_swapped":
            assert item.source_paper_id != claim_paper and item.origin["partner_kind"] == "co-cited"
    flipped = next(i for i in items if i.corruption_type == "direction_flipped")
    assert flipped.claim_text == CLAIM.replace("higher", "lower") and flipped.license == "CC-BY-4.0"

    again = generate(engine, corpus=LABEL_, seed=seed, splitter_ver=VER)
    assert again.already_present and again.items_added == 0
    with engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(eval_item).where(mine_only)).scalar_one() == 9
