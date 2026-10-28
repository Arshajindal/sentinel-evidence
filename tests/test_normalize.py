from __future__ import annotations

import pytest

from sentinel.resolve.normalize import (
    first_author_surname,
    normalize_doi,
    normalize_pmid,
    normalize_surname,
    normalize_title,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Acute effect of endurance exercise: a randomised cross-over study.",
         "acute effect of endurance exercise a randomised cross over study"),
        ("Effect of <i>Lactobacillus</i> on IL-6", "effect of lactobacillus on il 6"),
        ("Café  Müller—Ångström", "cafe muller angstrom"),
        ("PEG-IFNα in hepatitis B", "peg ifnα in hepatitis b"),
        # Crossref's title for a real fixture reference (PMC10657635 ref 42): no stray "amp".
        ("chemomechanical agents &amp; conventional methods", "chemomechanical agents conventional methods"),
        ("&lt;i&gt;In vitro&lt;/i&gt; study", "in vitro study"),
        ("", ""),
        (None, ""),
    ],
)
def test_normalize_title(value, expected):
    assert normalize_title(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Berglund IJ, Soras SE, Relling BE.", "berglund"),
        ("van der Berg J, Smith A.", "van der berg"),
        ("Müller-Lüdenscheidt K", "muller ludenscheidt"),
        ("O'Brien CA", "o brien"),
        ("WHO Collaborative Group.", "who collaborative group"),
        ("Smith", "smith"),
        ("", None),
        (None, None),
    ],
)
def test_first_author_surname(value, expected):
    assert first_author_surname(value) == expected


def test_normalize_surname():
    assert normalize_surname("  Ångström ") == "angstrom"
    assert normalize_surname("   ") is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("10.1186/S44315-025-00043-9", "10.1186/s44315-025-00043-9"),
        ("  10.1000/xyz123  ", "10.1000/xyz123"),
        ("https://doi.org/10.1000/ABC", "10.1000/abc"),
        ("http://dx.doi.org/10.1000/abc", "10.1000/abc"),
        ("doi:10.1000/abc", "10.1000/abc"),
        ("10.1000/abc.", "10.1000/abc"),
        ("10.1000/abc;", "10.1000/abc"),
        ("unavailable", None),
        ("Unavailable", None),
        ("", None),
        (None, None),
        # Junk prefixes seen in Retraction Watch: don't guess, return None.
        ("xx10.1007/978-3-030-00524-5_9", None),
        ("10.1/too-short-registrant", None),
        # Seen in source XML: a PubMed link glued on after a comma.
        ("10.1038/s41574-018-0110-5,http://www.ncbi.nlm.nih.gov/pubmed/30367179",
         "10.1038/s41574-018-0110-5"),
        ("10.1000/abc, www.example.org", "10.1000/abc"),
        ("10.1000/has space", "10.1000/has"),
    ],
)
def test_normalize_doi(value, expected):
    assert normalize_doi(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("33211632", "33211632"),
        (" 33211632 ", "33211632"),
        ("0", None),
        ("", None),
        (None, None),
        ("00123", "123"),
        ("12a4", None),
    ],
)
def test_normalize_pmid(value, expected):
    assert normalize_pmid(value) == expected
