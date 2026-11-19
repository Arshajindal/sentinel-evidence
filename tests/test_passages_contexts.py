"""Passages and citation contexts (S2.3, ADR-046).

The JATS here is constructed, but every pattern in it was seen in corpus
papers: superscript citations after the full stop, ranges written as two
citations joined by a dash and as one citation reading "9–13", author-year
citations with hyphenated names, a citation pointing at no reference, and
non-content sections (references, funding, author biographies).
"""

from __future__ import annotations

import pytest
from lxml import etree
from sqlalchemy import delete, func, insert, select

from sentinel.fulltext import body, sentences
from sentinel.fulltext.contexts import ContextStats, RefInfo, extract
from sentinel.fulltext.extract import ContextExtractor, PassageExtractor, numeric_label
from sentinel.fulltext.passages import PassageParams, make_passages, windows
from sentinel.models import (
    citation_context,
    citation_context_reference,
    corpus,
    corpus_paper,
    ingest_watermark,
    passage,
    paper,
    reference,
    text_availability,
)
from tests.conftest import requires_db

JATS = """<?xml version="1.0"?>
<article><front><article-meta><abstract><p>Not read from here.</p></abstract></article-meta></front>
<body>
<sec sec-type="intro"><title>Introduction</title>
<p>Influenza causes severe illness in children.<sup><xref ref-type="bibr" rid="R1">1</xref></sup> \
It kills many each year.<sup><xref ref-type="bibr" rid="R2">2</xref>,<xref ref-type="bibr" rid="R3">3</xref></sup> \
Vaccines help.</p>
<p>Earlier trials disagreed [<xref ref-type="bibr" rid="R4">4</xref>–<xref ref-type="bibr" rid="R7">7</xref>]. \
Dose matters, e.g. in infants (<xref ref-type="bibr" rid="R9">9–11</xref>). \
Others found no effect (<xref ref-type="bibr" rid="R12">Oliveira-Maia, 2017</xref>). \
One claim cites nothing stored [<xref ref-type="bibr" rid="X99">99</xref>].</p>
</sec>
<sec><title>2. Materials and Methods</title><p>We randomised 100 children. See Table 1.</p>
<table-wrap><table><tr><td>Cell text <xref ref-type="bibr" rid="R1">1</xref></td></tr></table></table-wrap>
<fig><caption><p>Figure caption.</p></caption></fig></sec>
<sec sec-type="results"><title>Results</title><p>Fever fell by 12% (p = 0.03).</p></sec>
<sec><title>Funding</title><p>Funded by a grant.</p></sec>
<sec><title>Notes on contributors</title><p>Dr. A is a professor.</p></sec>
<sec sec-type="ref-list"><title>References</title><p>Should not appear.</p></sec>
</body>
<back><ref-list>""" + "".join(
    f'<ref id="R{i}"><label>{i}.</label><mixed-citation>Ref {i}.</mixed-citation></ref>'
    for i in range(1, 13)) + "</ref-list></back></article>"

REFS = {f"R{i}": RefInfo(reference_id=100 + i, ordinal=i, label=i) for i in range(1, 13)}


def _paras():
    return body.paragraphs(body.parse(JATS))


# --- sentences --------------------------------------------------------------------


@pytest.mark.parametrize("text, expected", [
    ("Smith et al. found a drop. Others (e.g. Jones) did not.",
     ["Smith et al. found a drop.", "Others (e.g. Jones) did not."]),
    ("See Fig. 2 for details. J. Smith agreed (n = 4). It was p = 0.05.",
     ["See Fig. 2 for details.", "J. Smith agreed (n = 4).", "It was p = 0.05."]),
    ("Is it real? Yes! 12 patients left.", ["Is it real?", "Yes!", "12 patients left."]),
    # From a corpus paper: a space between the citation and the full stop.
    ("Lower in Japan than elsewhere 20) . While this holds, it varies.",
     ["Lower in Japan than elsewhere 20) .", "While this holds, it varies."]),
    # Unsure (lowercase start): left joined rather than cut.
    ("A trial [3]. then more", ["A trial [3]. then more"]),
])
def test_sentence_boundaries(text, expected):
    assert [text[s:e] for s, e in sentences.split(text)] == expected


def test_a_citation_after_the_full_stop_stays_with_its_sentence():
    text = "Risk rose in adults.12,13 Next we tested it."
    i = text.index("12")
    spans = sentences.split(text, [(i, i + 2), (i + 3, i + 5)])
    assert [text[s:e] for s, e in spans] == ["Risk rose in adults.12,13", "Next we tested it."]


def _cut(text: str, cites: list[str], *, across: bool = True) -> list[str]:
    spans, start = [], 0
    for c in cites:
        i = text.index(c, start)
        spans.append((i, i + len(c)))
        start = i + len(c)
    return [text[s:e] for s, e in sentences.split(text, spans, cites_across_space=across)]


@pytest.mark.parametrize("text, cites, expected", [
    # Shapes from the corpus (failure log, 2026-11-14; ADR-056).
    ("Pain fell until drain removal. 15 However, it rose later.", ["15"],
     ["Pain fell until drain removal. 15", "However, it rose later."]),
    ("It predicts severity in adults. 16 , 17", ["16", "17"], ["It predicts severity in adults. 16 , 17"]),
    ("It harms quality of life (QoL).3, 4, 5, 6 Mood suffers too.7", ["3", "4", "5", "6", "7"],
     ["It harms quality of life (QoL).3, 4, 5, 6", "Mood suffers too.7"]),
    ("It reduces nausea after ambulatory surgery.[4,24] Previous studies agree.", ["4", "24"],
     ["It reduces nausea after ambulatory surgery.[4,24]", "Previous studies agree."]),
    ("Craving varied. Gorini et al. (2014) tested both sides.", ["Gorini et al. (2014)"],
     ["Craving varied.", "Gorini et al. (2014) tested both sides."]),  # author-year: the subject
    ("They act after transcription. 11 miRNAs matter here.", ["11"],
     ["They act after transcription. 11 miRNAs matter here."]),  # unsure: left joined
])
def test_splitter_1_1_0_keeps_spaced_numeric_citations_with_the_sentence_before(text, cites, expected):
    assert _cut(text, cites) == expected


def test_splitter_1_0_0_rules_still_cut_passages_as_before():
    text = "Pain fell until drain removal. 15 However, it rose later."
    assert _cut(text, ["15"], across=False) == ["Pain fell until drain removal.", "15 However, it rose later."]
    assert _cut("Risk rose in adults.12,13 Next we tested it.", ["12", "13"], across=False) == [
        "Risk rose in adults.12,13", "Next we tested it."]
    para = body.Paragraph(section="introduction", text=text, cites=[body.Cite(text.index("15"), text.index("15") + 2, "R1", "15")])
    assert [p.text for p in make_passages(None, [para], PassageParams(1, 300, 0))] == [
        "Pain fell until drain removal.", "15 However, it rose later."]


# --- body -------------------------------------------------------------------------


def test_body_keeps_prose_and_drops_tables_figures_and_non_content_sections():
    paras = _paras()
    assert [p.section for p in paras] == ["introduction", "introduction", "methods", "results"]
    text = " ".join(p.text for p in paras)
    for absent in ("Cell text", "Figure caption", "Funded", "professor", "Should not appear",
                   "Not read from here"):
        assert absent not in text
    first = paras[0]
    assert [(c.rid, c.text) for c in first.cites] == [("R1", "1"), ("R2", "2"), ("R3", "3")]
    assert first.text.startswith("Influenza causes severe illness in children.1 It kills")


@pytest.mark.parametrize("title, sec_type, label", [
    ("Background", None, "introduction"), ("Objectives", None, "introduction"),
    ("2. Materials and Methods", None, "methods"), ("Assignment of interventions: blinding", None, "methods"),
    ("Findings", None, "results"), ("Results and discussion", None, "results"),
    ("Concluding remarks", None, "discussion"), ("Case presentation", None, "other"), ("", None, "other"),
    ("Anything", "conclusions", "discussion"), ("Acknowledgements", None, None),
    ("Publisher’s note", None, None), ("Trial status", None, None), ("Data availability statement", None, None),
    ("Anything", "ack", None),
])
def test_section_labels(title, sec_type, label):
    attr = f' sec-type="{sec_type}"' if sec_type else ""
    sec = etree.fromstring(f"<sec{attr}><title>{title}</title></sec>".encode())
    assert body.section_label(sec) == label


def test_list_items_inside_a_paragraph_keep_their_sentence_boundaries():
    # From a corpus paper: list items rendered as "ultrasound.Recent" made one
    # 365-word sentence.
    xml = ("<article><body><sec><title>Methods</title><p>Exclusions:<list>"
           "<list-item><p>Signs of renal disease on ultrasound.</p></list-item>"
           "<list-item><p>Recent use of nephrotoxic drugs.</p></list-item></list></p></sec></body></article>")
    (para,) = body.paragraphs(body.parse(xml))
    assert para.text == "Exclusions: Signs of renal disease on ultrasound. Recent use of nephrotoxic drugs."
    assert [para.text[s:e] for s, e in sentences.split(para.text)][-1] == "Recent use of nephrotoxic drugs."


def test_titles_inside_a_boxed_panel_are_spaced_from_their_text():
    # From a cited Lancet paper: "Research in contextEvidence before this study".
    xml = ("<article><body><sec><title>Introduction</title><p><boxed-text><caption><title>Research in "
           "context</title></caption><sec><title>Evidence before this study</title><p>Vaccines work."
           "</p></sec></boxed-text></p></sec></body></article>")
    (para,) = body.paragraphs(body.parse(xml))
    assert para.text == "Research in context Evidence before this study Vaccines work."


def test_numeric_labels():
    assert [numeric_label(x) for x in ("18.", "[18]", "18", "(3)", "Smith 2017", None)] == \
        [18, 18, 18, 3, None, None]


# --- passages ---------------------------------------------------------------------

P = PassageParams(min_words=150, max_words=300, overlap=1)


def _sents(n: int, words: int = 40) -> list[str]:
    return [f"S{i} " + "w " * (words - 1) for i in range(n)]


def test_windows_reach_the_minimum_overlap_by_one_sentence_and_merge_a_short_tail():
    out = windows(_sents(12), P)
    starts = [w.split()[0] for w in out]
    assert starts == ["S0", "S3", "S6"]  # each window starts with the last sentence of the one before
    assert [len(w.split()) for w in out] == [160, 160, 240]  # the 80-word tail joined the last
    assert all(len(w.split()) <= P.max_words for w in out)


def test_a_sentence_longer_than_the_maximum_is_cut_by_words():
    assert [len(w.split()) for w in windows(["x " * 500], P)] == [300, 200]


def test_no_overlap_when_it_would_break_the_maximum():
    out = windows(["a " * 160, "b " * 160], P)
    assert [len(w.split()) for w in out] == [160, 160]


def test_passages_never_cross_sections_and_start_with_the_abstract():
    out = make_passages("An abstract sentence. Another one.", _paras(), P)
    assert [p.section for p in out] == ["abstract", "introduction", "methods", "results"]
    assert out[0].text == "An abstract sentence. Another one."


def test_abstract_only_work():
    assert [(p.section, p.text) for p in make_passages("Only this.", [], P)] == \
        [("abstract", "Only this.")]
    assert make_passages(None, [], P) == []


def test_version_names_the_sizes():
    assert P.version() == "1.0.0/150-300-1"


# --- contexts ---------------------------------------------------------------------


def _contexts():
    stats = ContextStats()
    return extract(_paras(), REFS, stats), stats


def test_contexts_group_citations_and_take_one_sentence_either_side():
    ctx, _ = _contexts()
    first, second = ctx[0], ctx[1]
    assert (first.marker, first.reference_ids) == ("1", (101,))
    assert first.sentence == "Influenza causes severe illness in children.1"
    assert first.context_text == "Influenza causes severe illness in children.1 It kills many each year.2,3"
    assert (second.marker, second.reference_ids) == ("2,3", (102, 103))
    assert second.context_text.endswith("Vaccines help.")  # the sentence after, same paragraph


def test_each_context_records_every_citation_in_its_sentence_brackets_included():
    ctx, _ = _contexts()
    marks = {c.marker: [c.sentence[s:e] for s, e in c.sentence_cites] for c in ctx}
    assert marks["1"] == ["1"] and marks["2,3"] == ["2,3"]
    assert marks["4–7"] == ["[4–7]"] and marks["9–11"] == ["(9–11)"]
    xml = ('<article><body><sec><title>Discussion</title><p>A rose [<xref ref-type="bibr" rid="R3">3</xref>] '
           'and B fell [<xref ref-type="bibr" rid="X9">9</xref>].</p></sec></body></article>')
    (only,) = extract(body.paragraphs(body.parse(xml)), REFS)  # 9 cites no stored reference
    assert [only.sentence[s:e] for s, e in only.sentence_cites] == ["[3]", "[9]"]


def test_a_superscript_printed_after_a_space_cites_the_sentence_before():
    xml = ('<article><body><sec><title>Discussion</title><p>Pain fell until drain removal. '
           '<sup><xref ref-type="bibr" rid="R5">5</xref></sup> However, it rose later.</p></sec></body></article>')
    (ctx,) = extract(body.paragraphs(body.parse(xml)), REFS)
    assert ctx.sentence == "Pain fell until drain removal. 5"
    assert ctx.context_text == "Pain fell until drain removal. 5 However, it rose later."


def test_ranges_expand_in_both_forms_but_author_year_does_not():
    ctx, stats = _contexts()
    by_marker = {c.marker: c.reference_ids for c in ctx}
    assert by_marker["4–7"] == (104, 105, 106, 107)  # two citations joined by a dash
    assert by_marker["9–11"] == (109, 110, 111)  # one citation reading "9–11"
    assert by_marker["Oliveira-Maia, 2017"] == (112,)
    assert stats.ranges_expanded == 2


def test_a_dash_marked_up_as_a_citation_expands_the_range():
    # PMC4750684: (<xref rid="B20">20</xref><xref rid="B21">–</xref><xref rid="B27">27</xref>)
    xml = ('<article><body><sec><title>Methods</title><p>Sequences were analysed '
           '(<xref ref-type="bibr" rid="R4">4</xref><xref ref-type="bibr" rid="R5">–</xref>'
           '<xref ref-type="bibr" rid="R8">8</xref>).</p></sec></body></article>')
    (ctx,) = extract(body.paragraphs(body.parse(xml)), REFS)
    assert (ctx.marker, ctx.reference_ids) == ("4–8", (104, 105, 106, 107, 108))


def test_inner_range_needs_its_start_to_match_the_reference():
    refs = dict(REFS, R9=RefInfo(109, 9, label=8))  # printed label disagrees: no expansion
    by_marker = {c.marker: c.reference_ids for c in extract(_paras(), refs)}
    assert by_marker["9–11"] == (109,)


def test_a_citation_to_no_stored_reference_is_counted_not_linked():
    ctx, stats = _contexts()
    assert "99" not in {c.marker for c in ctx}
    assert (stats.unknown_rids, stats.unlinked_groups) == (1, 1)
    assert [c.ordinal for c in ctx] == list(range(1, len(ctx) + 1))
    assert {c.section for c in ctx} == {"introduction"}  # the table's citation isn't read


# --- against the test database ------------------------------------------------------

LABEL = "test-s23"
TITLES = ("s23-corpus", "s23-cited")


@pytest.fixture
def s23_engine(db_engine, tmp_path):
    with db_engine.connect() as conn:
        if conn.execute(select(paper.c.id).where(paper.c.title.not_in(TITLES))).first():
            pytest.skip("paper holds other data; not a disposable database")
    _clear(db_engine)
    (tmp_path / "PMC900001.xml").write_text(JATS, encoding="utf-8")
    with db_engine.begin() as conn:
        citing = conn.execute(insert(paper).values(
            title=TITLES[0], title_norm="x", source="europepmc", pmcid="PMC900001", raw={},
        ).returning(paper.c.id)).scalar_one()
        cited = conn.execute(insert(paper).values(
            title=TITLES[1], title_norm="y", source="crossref", raw={},
        ).returning(paper.c.id)).scalar_one()
        conn.execute(insert(corpus).values(label=LABEL, source="europepmc", query="t", seed=1,
                                           hit_count=1, n=1))
        conn.execute(insert(corpus_paper).values(label=LABEL, ordinal=1, source="PMC", source_id="x",
                                                 pmcid="PMC900001", paper_id=citing))
        conn.execute(insert(reference), [
            {"citing_paper_id": citing, "ordinal": i, "raw": {"id": f"R{i}", "label": f"{i}."},
             "resolved_paper_id": cited if i == 1 else None,
             "resolution_status": "resolved" if i == 1 else "unresolved"} for i in range(1, 13)])
        conn.execute(insert(text_availability), [
            {"paper_id": citing, "source": "europepmc", "found": True, "pmcid": "PMC900001",
             "is_oa": True, "fulltext": "full", "abstract": "Corpus abstract.",
             "abstract_source": "europepmc"},
            {"paper_id": cited, "source": "europepmc", "found": True, "pmcid": None, "is_oa": False,
             "fulltext": "abstract", "abstract": "Cited abstract only.",
             "abstract_source": "europepmc"}])
    yield db_engine, tmp_path
    _clear(db_engine)


def _clear(engine) -> None:
    with engine.begin() as conn:
        ids = select(paper.c.id).where(paper.c.title.in_(TITLES))
        conn.execute(delete(reference).where(reference.c.citing_paper_id.in_(ids)))
        conn.execute(delete(paper).where(paper.c.title.in_(TITLES)))
        conn.execute(delete(corpus).where(corpus.c.label == LABEL))
        conn.execute(delete(ingest_watermark).where(ingest_watermark.c.source.in_(
            [f"passages:{LABEL}", f"contexts:{LABEL}"])))


@requires_db
def test_passages_and_contexts_are_stored_once(s23_engine):
    engine, cache = s23_engine
    first = PassageExtractor(engine, label=LABEL, cache_dir=cache, params=P).run(engine)
    assert (first.seen, first.inserted) == (2, 2)
    again = PassageExtractor(engine, label=LABEL, cache_dir=cache, params=P).run(engine)
    assert (again.inserted, again.unchanged) == (0, 2)  # same version: nothing rewritten

    ctx = ContextExtractor(engine, label=LABEL, cache_dir=cache)
    assert ctx.run(engine).inserted == 1
    assert ContextExtractor(engine, label=LABEL, cache_dir=cache).run(engine).inserted == 0

    with engine.connect() as conn:
        sections = conn.execute(select(paper.c.title, passage.c.section, passage.c.splitter_ver)
                                .join(passage, passage.c.paper_id == paper.c.id)
                                .order_by(paper.c.title, passage.c.ordinal)).all()
        n_ctx = conn.execute(select(func.count()).select_from(citation_context)).scalar_one()
        links = conn.execute(select(func.count()).select_from(citation_context_reference)).scalar_one()
    assert [(t, s) for t, s, _ in sections] == [
        ("s23-cited", "abstract"), ("s23-corpus", "abstract"), ("s23-corpus", "introduction"),
        ("s23-corpus", "methods"), ("s23-corpus", "results")]
    assert {v for *_, v in sections} == {"1.0.0/150-300-1"}
    assert n_ctx == 5 and links == 1 + 2 + 4 + 3 + 1  # "99" is not linked
    assert ctx.stats.unknown_rids == 1
    with engine.connect() as conn:
        stored = conn.execute(select(citation_context.c.extractor_ver, citation_context.c.sentence,
                                     citation_context.c.sentence_cites)
                              .order_by(citation_context.c.ordinal)).all()
    assert {v for v, *_ in stored} == {"1.1.0"}
    assert [[sentence[s:e] for s, e in cites] for _, sentence, cites in stored][2] == ["[4–7]"]


@requires_db
def test_a_new_splitter_version_adds_rows_beside_the_old(s23_engine):
    engine, cache = s23_engine
    PassageExtractor(engine, label=LABEL, cache_dir=cache, params=P).run(engine)
    other = PassageParams(min_words=5, max_words=300, overlap=0)
    PassageExtractor(engine, label=LABEL, cache_dir=cache, params=other).run(engine)
    with engine.connect() as conn:
        versions = dict(conn.execute(select(passage.c.splitter_ver, func.count())
                                     .group_by(passage.c.splitter_ver)).all())
    assert set(versions) == {"1.0.0/150-300-1", "1.0.0/5-300-0"}
