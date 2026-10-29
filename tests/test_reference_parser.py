"""Reference parser (sentinel-project.md §9.4): nulls instead of guesses.

`hand_checked.json` holds 50 real corpus reference strings with expected
fields written by reading each one: the true value, or null when the string
has no such field (organisation author, no publication year, no journal).
The rules, which together are S1.8's done-when:

1. Where the truth is null, the parser returns None. Nothing invented.
2. Where the truth exists, the parser returns it or None. Never a wrong value.
3. Across all 50, enough fields are filled that "return None" can't pass.
"""

from __future__ import annotations

import json
import time

import pytest

from sentinel.parse.references import ParsedReference, parse_reference
from sentinel.resolve.normalize import normalize_title
from tests.conftest import FIXTURES

CASES = json.loads((FIXTURES / "references" / "hand_checked.json").read_text("utf-8"))
FIELDS = ("first_author", "year", "title", "container", "doi", "pmid")
FILL_FLOOR = 0.85  # measured 88.2% (186/211) for parser 1.0.0


def _parse(case: dict) -> ParsedReference:
    return parse_reference(case["raw"], max_year=(case["citing_year"] or 2026) + 1)


def _same(field: str, got, expected) -> bool:
    if field in ("title", "container"):
        return normalize_title(got) == normalize_title(expected)
    return got == expected


def test_fixture_has_50_hand_checked_strings():
    assert len(CASES) == 50
    assert len({c["raw"] for c in CASES}) == 50


@pytest.mark.parametrize("case", CASES, ids=[f"{i:02d}" for i in range(len(CASES))])
def test_never_wrong_never_invented(case):
    parsed = _parse(case)
    problems = []
    for field in FIELDS:
        expected, got = case["expected"][field], getattr(parsed, field)
        if expected is None and got is not None:
            problems.append(f"invented {field}={got!r}")
        elif expected is not None and got is not None and not _same(field, got, expected):
            problems.append(f"wrong {field}: expected {expected!r}, got {got!r}")
    assert not problems, f"{case['note']}: {problems}"


def test_fills_enough_to_be_useful():
    present = filled = 0
    for case in CASES:
        parsed = _parse(case)
        for field in FIELDS:
            if case["expected"][field] is not None:
                present += 1
                filled += getattr(parsed, field) is not None
    assert filled / present >= FILL_FLOOR, f"filled {filled}/{present}"


# --- individual rules ------------------------------------------------------------


def test_empty_and_blank():
    assert parse_reference(None, max_year=2026) == ParsedReference()
    assert parse_reference("   ", max_year=2026) == ParsedReference()


def test_vancouver_fields():
    p = parse_reference(
        "1. Smith J, Doe AB, et al. A trial of things: results. N Engl J Med. "
        "2019;380(2):100-110. doi: 10.1056/NEJMoa1234567.", max_year=2021)
    assert (p.first_author, p.year, p.container) == ("smith", 2019, "N Engl J Med")
    assert p.title == "A trial of things: results"
    assert p.doi == "10.1056/nejmoa1234567"


def test_apa_fields():
    p = parse_reference(
        "Marschner, I. C., & Schou, I. M. (2022). Analysis of adaptive platform trials "
        "using a network approach. Clinical Trials, 19(5), 479–489.", max_year=2024)
    assert (p.first_author, p.year) == ("marschner", 2022)
    assert p.title == "Analysis of adaptive platform trials using a network approach"
    assert p.container == "Clinical Trials"


def test_year_after_max_year_is_ignored():
    p = parse_reference("Smith J. Title here. J Med. 2031;1:1-2.", max_year=2021)
    assert p.year is None and p.title is None


def test_conflicting_years_give_none():
    p = parse_reference("Smith J. Results from 1998 and 2004. Book Press; 2010, 2012.",
                        max_year=2021)
    assert p.year is None


def test_year_range_takes_first_year():
    p = parse_reference("Shwartz M. Ratio of costs. Inquiry. 1995-1996;32(4):476-481.",
                        max_year=2020)
    assert p.year == 1995


def test_access_dates_are_not_publication_years():
    p = parse_reference("Some Agency. A web page. https://example.org. Accessed 10 Mar 2021.",
                        max_year=2022)
    assert p.year is None
    p = parse_reference("Smith J. A report. 2005 [cited 2016 Feb 21].", max_year=2022)
    assert p.year == 2005


def test_updated_in_a_title_is_kept():
    p = parse_reference("Smith J, Lee K. An updated systematic review of falls. "
                        "Br J Sports Med. 2020;54:1-9.", max_year=2022)
    assert p.title == "An updated systematic review of falls"


def test_question_mark_title_is_split_from_journal():
    p = parse_reference("Brunton S. GLP-1 agonists vs. DPP-4 inhibitors: is one better? "
                        "Int J Clin Pract. 2014;68:557-67.", max_year=2016)
    assert p.title == "GLP-1 agonists vs. DPP-4 inhibitors: is one better?"
    assert p.container == "Int J Clin Pract"


def test_et_al_without_period():
    p = parse_reference("Varni, J.W. et al PedsQL symptoms module: validity. "
                        "J. Pediatr. Gastroenterol. Nutr. 59, 347–355 (2014).", max_year=2016)
    assert p.first_author == "varni"
    assert p.title == "PedsQL symptoms module: validity"
    assert p.container == "J. Pediatr. Gastroenterol. Nutr"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Smith J. T. J Med. 2019;1:1. doi: 10.1000/abc.", "10.1000/abc"),
        ("Smith J. T. J Med. 2019;1:1. https://doi.org/10.1000/ABC).", "10.1000/abc"),
        ("Smith J. T. J Med. 2019;1:1. doi:10.1000/a(b)c.", "10.1000/a(b)c"),
        # A repeated DOI and a PMCID glued on.
        ("Smith J. T. J Med. 2019;1:1. doi:10.1589/jpts.27.955.10.1589/jpts.27.955PMC4395752",
         "10.1589/jpts.27.955"),
        # Glued straight onto the pages: its end can't be known.
        ("Smith J. T. J Med. 2016;22:125–33.10.1089/acm.2014.009126398441", None),
    ],
)
def test_doi_rules(text, expected):
    assert parse_reference(text, max_year=2026).doi == expected


def test_pmid_label():
    assert parse_reference("Smith J. T. J. 2006;1:1. PMID: 17273013.", max_year=2026).pmid == "17273013"


@pytest.mark.parametrize(
    "text",
    [
        "World Health Organization. Guidelines. Geneva: WHO; 2019.",
        "Organization WH Africa eradicates wild poliovirus. In: News.",
        "Centers for Disease Control. Data. 2018.",
        "R Core Team. R: a language. Vienna; 2019.",
        "SilbersteinSDMechtlerLL, et al. Glued names. Headache. 2016;56:1-9.",
    ],
)
def test_no_person_first_author_is_invented(text):
    assert parse_reference(text, max_year=2026).first_author is None


@pytest.mark.parametrize(
    ("text", "surname"),
    [
        ("van der Velden AM, Kuyken W. T. J. 2015;1:1.", "van der velden"),
        ("Al-Shahi Salman R, Murray GD. T. J. 2019;1:1.", "al shahi salman"),
        ("Pérez-Olmos I, Rodado S. T. J. 2005;1:1.", "perez olmos"),
        ("McAuley SA, Ward GM. T. J. 2016;1:1.", "mcauley"),
        ("O'Brien CP, Lee K. T. J. 2016;1:1.", "o brien"),
        ("Muthén, L, & Muthén, B. Mplus. 2014.", "muthen"),
        ("Calik‐Kutukcu, E., & Arikan, H. (2020). T. J, 1.", "calik kutukcu"),
    ],
)
def test_surname_shapes(text, surname):
    assert parse_reference(text, max_year=2026).first_author == surname


def test_author_names_never_leak_into_a_title():
    # The author list isn't fully recognised (an unexpected token), so the
    # parser must not pass author names off as the title.
    p = parse_reference("Fodor, L. A., Georgescu, R., Szamoskozi, Ş, & Cristea, I. A. (2020). "
                        "Efficacy of bias modification. JAMA Psychiatry, 77(1), 1-9.", max_year=2022)
    assert p.title is None or not p.title.startswith(("A.", ","))
    assert p.title is None or "Georgescu" not in p.title


def test_pathological_input_is_fast():
    worst = ("Smith-Jones-Brown-Lee " * 60) + ", " + ("Aaaa Bbbb Cccc " * 80) + "2015;3:1."
    start = time.perf_counter()
    parse_reference(worst, max_year=2026)
    assert time.perf_counter() - start < 0.5
