"""A JATS body as paragraphs of plain text, with section labels and the
position of every in-text citation (§10.1 items 2-3, ADR-046).

Tables, figures, the reference list, footnotes and other non-prose parts are
skipped, as in S1.12 (ADR-037). Top-level sections that aren't content
(references, acknowledgements, funding, declarations and the like) are
skipped too, so they never become passages. A citation is an
`<xref ref-type="bibr">`; its span is kept through whitespace squashing so
the sentence around it can be found. XML is parsed with no DTDs, entities or
network access.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from lxml import etree

_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False)
# Elements whose text is never prose, wherever they sit.
_SKIP_TAGS = frozenset({"table-wrap", "table", "fig", "ref-list", "abstract", "sub-article",
                        "response", "back", "front", "fn-group", "app-group", "supplementary-material",
                        "disp-formula", "graphic", "media", "object-id", "label"})
SECTIONS = ("abstract", "introduction", "methods", "results", "discussion", "other")
# Elements inside a paragraph whose text runs on its own, not inline.
_BLOCKS = frozenset({"list", "list-item", "def-list", "def-item", "term", "def", "disp-quote",
                     "boxed-text", "statement", "break", "p", "title", "caption", "sec"})

# Top-level sections that aren't content, by sec-type and by title.
_NON_CONTENT_TYPES = frozenset({"ref-list", "ack", "associated-data", "fn-group",
                                "supplementary-material", "contrib-info", "glossary", "COI-statement",
                                "data-availability", "funding", "ethics-statement", "author-contributions"})
_NON_CONTENT_TITLE = re.compile(
    r"\b(references?|bibliography|acknowledge?ments?|associated data|footnotes?|"
    r"(author|authors[’']?) contributions?|contributors?|contributor information|funding|"
    r"abbreviations|glossary|competing interests?|conflicts? of interests?|declarations?|"
    r"declaration of interests?|disclosures?|data (availability|sharing)|availability of data|"
    r"supplementary|supporting information|additional files?|electronic supplementary|"
    r"pre-publication history|ethics|ethical (approval|statement|standards)|informed consent|"
    r"consent for publication|institutional review board|compliance with ethical|appendix|"
    r"peer review|publisher[’']?s note|notes( on contributors)?|code availability|biography|"
    r"authors[’']? information|about the authors|credit authorship|contribution statement|"
    r"statement of interest|additional information|administrative information|trial status)\b",
    re.IGNORECASE)
_LABEL_BY_TYPE = {"intro": "introduction", "introduction": "introduction", "methods": "methods",
                  "materials|methods": "methods", "materials": "methods", "subjects": "methods",
                  "results": "results", "discussion": "discussion", "conclusions": "discussion",
                  "results|discussion": "results"}
_LABEL_BY_TITLE = (
    ("introduction", re.compile(r"\b(introduction|background|objectives?|aims?)\b", re.IGNORECASE)),
    ("methods", re.compile(r"\b(methods?|materials?|patients|participants|subjects|"
                           r"study design|design|statistical analys[ie]s|procedures?|experimental|"
                           r"protocol|data collection|measures|interventions?|outcomes|"
                           r"sample size|randomi[sz]ation|blinding|allocation)\b", re.IGNORECASE)),
    ("results", re.compile(r"\b(results?|findings)\b", re.IGNORECASE)),
    ("discussion", re.compile(r"\b(discussion|conclusions?|concluding remarks|limitations|summary|"
                              r"implications|recommendations|future directions)\b",
                              re.IGNORECASE)),
)


@dataclass(frozen=True)
class Cite:
    start: int
    end: int
    rid: str
    text: str


@dataclass
class Paragraph:
    section: str  # one of SECTIONS (never "abstract": the body has none)
    text: str
    cites: list[Cite] = field(default_factory=list)


def parse(xml: str | bytes) -> etree._Element:
    return etree.fromstring(xml.encode("utf-8") if isinstance(xml, str) else xml, parser=_PARSER)


def section_label(sec: etree._Element | None) -> str | None:
    """The label for a top-level section, or None when it isn't content."""
    if sec is None:
        return "other"
    sec_type = sec.get("sec-type") or ""
    title_el = sec.find("title")
    title = " ".join("".join(title_el.itertext()).split()) if title_el is not None else ""
    if sec_type in _NON_CONTENT_TYPES or _NON_CONTENT_TITLE.search(title):
        return None
    if sec_type in _LABEL_BY_TYPE:
        return _LABEL_BY_TYPE[sec_type]
    for label, pattern in _LABEL_BY_TITLE:
        if pattern.search(title):
            return label
    return "other"


def paragraphs(root: etree._Element) -> list[Paragraph]:
    """Every prose paragraph of the main article's body, in order."""
    body = root.find("body")
    if body is None:
        body = next((b for b in root.iter("body")
                     if not any(a.tag in ("sub-article", "response") for a in b.iterancestors())),
                    None)
    if body is None:
        return []
    out = []
    for p in body.iter("p"):
        ancestors = list(p.iterancestors())
        if any(a.tag in _SKIP_TAGS or a.tag == "p" for a in ancestors):
            continue
        top = next((a for a in ancestors if a.tag == "sec" and a.getparent() is body), None)
        label = section_label(top)
        if label is None:
            continue
        para = _linearise(p, label)
        if para.text:
            out.append(para)
    return out


def _linearise(p: etree._Element, section: str) -> Paragraph:
    pieces: list[str] = []
    raw_cites: list[tuple[int, int, str]] = []
    pos = 0

    def add(text: str | None) -> None:
        nonlocal pos
        if text:
            pieces.append(text)
            pos += len(text)

    def walk(el: etree._Element) -> None:
        add(el.text)
        for child in el:
            if not isinstance(child.tag, str):  # comments, processing instructions
                add(child.tail)
                continue
            if child.tag in _SKIP_TAGS:
                pass
            elif child.tag in _BLOCKS:
                # A list item or block inside a paragraph is its own run of
                # text: without the space, "ultrasound.Recent" hides a boundary.
                add(" ")
                walk(child)
                add(" ")
            elif child.tag == "xref" and child.get("ref-type") == "bibr":
                start = pos
                add("".join(child.itertext()))
                for rid in (child.get("rid") or "").split():
                    raw_cites.append((start, pos, rid))
            else:
                walk(child)
            add(child.tail)

    walk(p)
    text, mapping = _squash("".join(pieces))
    cites = []
    for start, end, rid in raw_cites:
        s, e = mapping[start], mapping[end]
        # The citation is its text, without the spaces some markup puts inside.
        while s < e and text[s] == " ":
            s += 1
        while e > s and text[e - 1] == " ":
            e -= 1
        if e > s:
            cites.append(Cite(s, e, rid, text[s:e]))
    return Paragraph(section=section, text=text, cites=cites)


def _squash(raw: str) -> tuple[str, list[int]]:
    """Collapse whitespace runs to one space and strip the ends. Returns the
    text and, for every raw offset 0..len(raw), its offset in the result."""
    out: list[str] = []
    mapping = [0] * (len(raw) + 1)
    pending_space = False
    for i, ch in enumerate(raw):
        if ch.isspace():
            pending_space = bool(out)
            mapping[i] = len(out) + (1 if pending_space else 0)
            continue
        if pending_space:
            out.append(" ")
            pending_space = False
        mapping[i] = len(out)
        out.append(ch)
    mapping[len(raw)] = len(out)
    return "".join(out), [min(m, len(out)) for m in mapping]
