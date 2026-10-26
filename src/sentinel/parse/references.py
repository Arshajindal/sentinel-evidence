"""Reference string -> structured fields (sentinel-project.md §9.4). Rule-based.

A wrong parse is worse than a null: it leads to a confident wrong match
downstream. So every field is either located by a clear rule or left None.

- DOI: `10.NNNN/suffix`, normalised. PMID: an explicit `PMID:` label.
- Year: 1900..max_year, ignoring years inside DOIs and URLs. A year in a
  citation position (`2015;`, `(2016)`, `2019 Mar;`) wins; otherwise a lone
  candidate is taken, and disagreeing candidates give None.
- First author: the leading surname before initials or "et al".
  Organisations and title-first strings give None.
- Title and container: only when both the author list and the citation
  details are located. The title is the longest segment between them, the
  container the segment just before the details.

`max_year` is passed in (the citing paper's year + 1), so parsing never
depends on today's date.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sentinel.resolve.normalize import normalize_doi, normalize_surname

PARSER_VERSION = "1.1.0"

_MONTHS = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?"
# Surname words contain a lowercase letter ("McAuley", "van", "İdiman");
# initials are short all-caps tokens ("SA", "S.M.", "J. C."). Keeping the two
# disjoint, and each word a single unambiguous run of letters (hyphens only
# between words), means the patterns can't backtrack catastrophically.
_LOWER = "a-zß-öø-ÿĀ-ſ"
_UPPER = "A-ZÀ-ÖØ-ÞŞŠŽČĆĐŁŃŚŹŻİŘŤĽŇ"
_HYPHEN = "\\-‐‑"  # ASCII hyphen, U+2010, U+2011
# A word with two capitals right after a lowercase letter ("SilbersteinSD") is
# a name glued to its initials, not a surname; "McAuley" or "LeBlanc" pass.
_SURWORD = (rf"(?=[^\s,;.]*[{_LOWER}])(?![^\s,;.]*[{_LOWER}][{_UPPER}]{{2}})"
            rf"[^\W\d_]+(?:['’][^\W\d_]+)*")
_SURNAME = rf"{_SURWORD}(?:[ {_HYPHEN}]{_SURWORD}){{0,3}}"
_INITIALS = (rf"(?:[{_UPPER}](?:[{_HYPHEN}][{_UPPER}])+\b"            # H-C, M‐J
             rf"|[{_UPPER}]{{1,4}}\b"                                # SA
             rf"|(?:[{_UPPER}]\.\s?[{_HYPHEN}]?){{1,4}}(?:[{_UPPER}]\b)?)")  # S.M., J. C
_SUFFIX = r"(?:\s*,?\s*(?:Jr|Sr|II|III|IV)\b\.?)"
_AUTHOR = rf"{_SURNAME},?\s+{_INITIALS}{_SUFFIX}?"
_SEP = r"(?:\s*,\s*(?:…\s*|\.\.\.\s*)?|\s*;\s*|,?\s*&\s*|,?\s+and\s+)"
_ETAL = r"(?:,?\s*(?:et\s+al\.?|…|\.\.\.))"
_AUTHOR_LIST = re.compile(rf"{_AUTHOR}(?:{_SEP}{_AUTHOR})*{_ETAL}?\s*,?", re.UNICODE)

_FIRST_AUTHOR = re.compile(
    rf"^(?P<sur>{_SURNAME})(?:,?\s+(?P<ini>{_INITIALS})(?=[\s,.;&]|$)|,?\s+et\s+al\b)",
    re.UNICODE,
)
_LIST_MARKER = re.compile(r"^\s*(?:\[\d+\]|\d+[.)])\s+")
_DOI = re.compile(r"(?i)(?:doi:?\s*|https?://(?:dx\.)?doi\.org/)?(10\.\d{4,9}/[^\s\"<>]+)")
_PMID = re.compile(r"(?i)\bPMID:?\s*(\d{1,9})\b")
_URL = re.compile(r"(?i)\b(?:https?://|www\.)\S+")
_STRONG_YEAR = [
    re.compile(r"\((\d{4})[a-z]?\)"),                       # (2016)
    re.compile(r"(?<![\d.])(\d{4})[a-z]?\s*;"),             # 2015;
    # 2019 Mar 5;  2006 Nov-Dec;
    re.compile(rf"(?<![\d.])(\d{{4}})\s+{_MONTHS}(?:\s*[-–/]\s*{_MONTHS})?(?:\s*\d{{1,2}})?\s*[;:]"),
    re.compile(r"(?<=[A-Za-z.]),\s*(\d{4})[a-z]?\.\s"),     # Harvard: "Nitsche MA, 2017. "
]
_ANY_YEAR = re.compile(r"(?<![\d./])(\d{4})(?![\d/])")
# "Accessed 10 Mar 2021", "[cited 2015 Aug 21]": when it was read, not published.
# Only dated forms: a bare "updated" is common in titles ("an updated review").
_ACCESS_DATE = re.compile(
    r"(?i)(?:\b(?:accessed(?:\s+on)?|cited|retrieved(?:\s+on)?|last\s+updated)\b|\[updated\b)"
    r"(?=[^.;\]]{0,12}\d)[^.;\]]*")
_TRAILING_NUMBERS = re.compile(r"[\s,;:.]*(?<![A-Za-z]\d)\d[\d\s,;:()–\-‐e]*$")
# Organisation words aren't surnames ("Organization WH" is an inverted "WHO").
_NOT_SURNAMES = frozenset({
    "organization", "organisation", "association", "society", "group", "institute",
    "department", "ministry", "council", "committee", "foundation", "agency", "centre",
    "center", "office", "board", "team", "university", "consortium", "collaboration",
    "network", "health", "statistics", "government", "service", "services", "commission",
    "authority", "programme", "program", "alliance", "federation", "college", "academy",
})
_TRAILING_BRACKET = re.compile(r"\s*\[[^\]]{1,40}\]\s*$")  # "[review]", "[Internet]"
_NOT_A_CONTAINER = re.compile(r"(?i)^(?:available|retrieved|accessed|in:|cited|from\b|\[|online)")
_JOURNAL_PIECE = re.compile(r"[A-Z][\w&]{0,15}(?: [A-Z][\w&]{0,15}){0,2}")
# Signs that author names leaked into a title: a leading comma or ampersand,
# or "X., Surname" / "& Surname, X." patterns.
_AUTHORISH = re.compile(rf"^[,&;]|\b[{_UPPER}]\.\s*,\s*[^\W\d_]|&\s+[^\W\d_]+,\s+[{_UPPER}]\.")


@dataclass(frozen=True)
class ParsedReference:
    doi: str | None = None
    pmid: str | None = None
    title: str | None = None
    first_author: str | None = None
    year: int | None = None
    container: str | None = None


def parse_reference(text: str | None, *, max_year: int) -> ParsedReference:
    if not text or not text.strip():
        return ParsedReference()
    s = _LIST_MARKER.sub("", " ".join(text.split()))
    # "Liscio Pet al." -> "Liscio P, et al." ("et al" glued onto initials).
    s = re.sub(rf"(?<=\s[{_UPPER}])(?=et al\b)|(?<=\s[{_UPPER}]{{2}})(?=et al\b)", ", ", s)

    doi = _doi(s)
    pmid_match = _PMID.search(s)
    pmid = (pmid_match.group(1).lstrip("0") or None) if pmid_match else None

    # Identifiers, links and access dates are blanked (same length) so
    # positions still line up.
    core = s
    for pattern in (_DOI, _URL, _PMID, _ACCESS_DATE):
        core = pattern.sub(lambda m: " " * len(m.group(0)), core)
    core = re.sub(r"(?i)\bdoi\b:?", lambda m: " " * len(m.group(0)), core)

    year, year_span = _year(core, max_year)
    first_author = _first_author(s)
    title, container = _title_and_container(core, year_span) if first_author else (None, None)
    return ParsedReference(doi=doi, pmid=pmid, title=title, first_author=first_author,
                           year=year, container=container)


def _doi(s: str) -> str | None:
    m = _DOI.search(s)
    if not m:
        return None
    # Glued to the preceding text ("125–33.10.1089/acm.2014.009126398441"):
    # where the DOI ends can't be known, so don't guess.
    if m.start() > 0 and not re.match(r"[\s(\[:/]", s[m.start() - 1]):
        return None
    doi = m.group(1)
    # A second DOI, a PMCID or a PMID run straight on ("…27.955.10.1589/…PMC4395…").
    doi = re.split(r"\.(?=10\.\d{4,9}/)|PMC\d{4,}|PMID", doi)[0]
    doi = doi.rstrip(".,;:]")
    while doi.endswith(")") and doi.count(")") > doi.count("("):
        doi = doi[:-1].rstrip(".,;:")
    return normalize_doi(doi)


def _year(core: str, max_year: int) -> tuple[int | None, tuple[int, int] | None]:
    def valid(y: str) -> bool:
        return 1900 <= int(y) <= max_year

    strong: list[re.Match] = []
    for pattern in _STRONG_YEAR:
        strong.extend(m for m in pattern.finditer(core) if valid(m.group(1)))
    if strong:
        values = {m.group(1) for m in strong}
        if len(values) == 1:
            first = min(strong, key=lambda m: m.start())
            # A range ("1995-1996;") is cited by its first year, as PubMed does.
            before = re.search(r"(\d{4})\s*[-–]\s*$", core[:first.start(1)])
            if before and valid(before.group(1)) and int(before.group(1)) < int(first.group(1)):
                return int(before.group(1)), first.span()
            return int(first.group(1)), first.span()
        return None, None
    weak = {m.group(1) for m in _ANY_YEAR.finditer(core) if valid(m.group(1))}
    if len(weak) == 1:
        return int(weak.pop()), None  # no citation position, so no anchor
    return None, None


def _first_author(s: str) -> str | None:
    m = _FIRST_AUTHOR.match(s)
    if not m:
        return None
    surname = normalize_surname(m.group("sur"))
    if surname is None or set(surname.split()) & _NOT_SURNAMES:
        return None
    return surname


def _authors_end(core: str, limit: int) -> int | None:
    """Where the author list ends: the last cut point before `limit` where
    everything before it is a well-formed author list. The last, not the
    first: "Smith, I. C., & Jones, A." parses as authors after "I." too."""
    end = None
    for cut in re.finditer(r"\.\s+|\s*:\s+|\s+\(|,\s+(?=\d{4}\b)|(?<=\bet al)\s+", core[:limit]):
        head = core[:cut.start()].rstrip(" ,.")
        if head and _AUTHOR_LIST.fullmatch(head):
            end = cut.start() if core[cut.start()] in " ," else cut.end()
    return end


def _title_and_container(core: str, year_span: tuple[int, int] | None) -> tuple[str | None, str | None]:
    if year_span is None:
        return None, None
    end = _authors_end(core, year_span[1])
    if end is None:
        return None, None

    if end >= year_span[0] - 3:
        # Author-year style: "Authors (2016). Title. Journal, 7..." or "Authors, 2014. Title."
        rest = core[year_span[1]:].lstrip(" ).,:")
        # Only ". " ends the title here: "A question of control? Examining..."
        parts = [p for p in re.split(r"\.\s+", rest) if p.strip()]
        if not parts:
            return None, None
        title = _clean(parts[0])
        if _AUTHORISH.search(title):
            return None, None
        # The container is everything after the title up to the volume
        # ("Int. Rev. Psychiatry 29 (2), ..."); "Steer RA (1997) Beck anxiety
        # inventory" has none.
        after = rest[rest.find(parts[0]) + len(parts[0]):].lstrip(" .?!")
        container = _clean(re.split(r",|\s\d|\(", after)[0]) if after else ""
        if _NOT_A_CONTAINER.match(container):
            container = ""
        return title or None, container or None

    middle = core[end:year_span[0]].lstrip(" :")
    # Volume and pages before a trailing year ("Nutr. 59, 347–355 (2014)").
    middle = _TRAILING_NUMBERS.sub("", middle).strip().rstrip(".").strip()
    # Segments end at ". " or after "? "/"! " (a question-mark title keeps its "?").
    segments = [seg.strip() for seg in re.split(r"\.\s+|(?<=[?!])\s+", middle) if seg.strip()]
    if len(segments) < 2:
        return None, None
    # Abbreviated journal names ("Expert Opin. Drug Metab. Toxicol") split into
    # short capitalised pieces; glue those back onto the container.
    container_parts = [segments.pop()]
    while len(segments) > 1 and _JOURNAL_PIECE.fullmatch(segments[-1]):
        container_parts.insert(0, segments.pop())
    # Everything between authors and container is the title: subtitles and
    # abbreviations like "vs." also contain ". ". Bare numbers aren't title.
    title = _clean(". ".join(seg for seg in segments if not seg.isdigit()))
    if _AUTHORISH.search(title):
        # The author list wasn't fully recognised; a title containing author
        # names would match the wrong work. None instead.
        return None, None
    return title or None, _clean(". ".join(container_parts)) or None


def _clean(value: str) -> str:
    value = _TRAILING_BRACKET.sub("", value.strip())
    return " ".join(value.strip().strip("“”\"'").rstrip(".,;:").split())
