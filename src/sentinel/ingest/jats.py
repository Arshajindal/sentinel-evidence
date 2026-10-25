"""Reference lists from JATS full-text XML (ADR-030).

Only the <ref-list> is read; the body isn't parsed or stored (Phase 2).
Fields are taken only where the XML marks them up. Everything else stays
NULL for the reference parser (S1.8) to work from `raw_string`. Paper XML is
untrusted: no DTDs, no entity expansion, no network.
"""

from __future__ import annotations

import re

from lxml import etree

from sentinel.resolve.normalize import normalize_doi, normalize_pmid, normalize_surname
from sentinel.schemas import ReferenceRecord

_PARSER = etree.XMLParser(
    resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False, recover=False
)
_CITATION_TAGS = ("element-citation", "mixed-citation", "citation", "nlm-citation")
_YEAR_RE = re.compile(r"\b(1[89]\d{2}|20\d{2})\b")


def parse_ref_list(xml: str | bytes) -> list[ReferenceRecord]:
    """Every <ref> of the main article in document order, numbered from 1.

    Europe PMC XML puts the <ref-list> in <back> or inside a <body> section,
    so position isn't relied on. Refs in <sub-article>/<response> (reviewer
    reports, author replies) belong to those documents and are skipped.
    """
    data = xml.encode("utf-8") if isinstance(xml, str) else xml
    root = etree.fromstring(data, parser=_PARSER)
    refs = []
    for ref in root.iter("ref"):
        ancestors = {a.tag for a in ref.iterancestors()}
        if "ref-list" in ancestors and not ancestors & {"sub-article", "response"}:
            refs.append(ref)
    return [_reference(ref, i) for i, ref in enumerate(refs, start=1)]


def _reference(ref: etree._Element, ordinal: int) -> ReferenceRecord:
    citation = next((c for t in _CITATION_TAGS if (c := ref.find(f".//{t}")) is not None), None)
    node = citation if citation is not None else ref
    # element-citation has no text between its tags, so pieces need spaces;
    # mixed-citation carries its own punctuation and spacing.
    structured = citation is not None and etree.QName(citation).localname == "element-citation"
    raw_string = (_spaced_text(node) if structured else _text(node)) or None

    article_title = _first_text(node, "article-title") or _first_text(node, "chapter-title")
    source = _first_text(node, "source")
    pub_type = citation.get("publication-type") if citation is not None else None

    return ReferenceRecord(
        ordinal=ordinal,
        raw_string=raw_string,
        parsed_doi=normalize_doi(_pub_id(node, "doi")),
        parsed_pmid=normalize_pmid(_pub_id(node, "pmid")),
        # A book's own title sits in <source> when there's no article title.
        parsed_title=article_title or (source if pub_type == "book" else None),
        parsed_first_author=_first_author(node),
        parsed_year=_year(node),
        parsed_container=source if article_title else None,
        raw={
            "label": _first_text(ref, "label"),
            "id": ref.get("id"),
            "citation_tag": etree.QName(citation).localname if citation is not None else None,
            "publication_type": pub_type,
            "xml": etree.tostring(ref, encoding="unicode", with_tail=False),
        },
    )


def _text(node: etree._Element | None) -> str:
    if node is None:
        return ""
    return " ".join("".join(node.itertext()).split())


def _spaced_text(node: etree._Element) -> str:
    return " ".join(" ".join(t.strip() for t in node.itertext() if t.strip()).split())


def _first_text(node: etree._Element, tag: str) -> str | None:
    return _text(node.find(f".//{tag}")) or None


def _pub_id(node: etree._Element, kind: str) -> str | None:
    found = node.find(f".//pub-id[@pub-id-type='{kind}']")
    return _text(found) or None


def _first_author(node: etree._Element) -> str | None:
    group = node.find(".//person-group[@person-group-type='author']")
    scope = group if group is not None else node
    surname = scope.find(".//name/surname")
    if surname is None:
        surname = scope.find(".//string-name/surname")
    if surname is not None:
        return normalize_surname(_text(surname))
    collab = scope.find(".//collab")
    return normalize_surname(_text(collab)) if collab is not None else None


def _year(node: etree._Element) -> int | None:
    match = _YEAR_RE.search(_first_text(node, "year") or "")
    return int(match.group(1)) if match else None
