from __future__ import annotations

import pytest

from sentinel.resolve.normalize import normalize_doi, normalize_pmid


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
        ("10.1000/has space", None),
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
