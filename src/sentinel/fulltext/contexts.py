"""Citation contexts: the sentence around each in-text citation, linked to the
references it cites (§10.1 item 3, ADR-046). Deterministic and pure.

Adjacent citations joined only by ",", ";", "–" or "-" form one group, one
context: "[3], [5]" or "3–5". A range is expanded to the references between
its ends, in reference-list order:
- two citations joined by a dash ("<xref>3</xref>–<xref>5</xref>");
- one citation whose text is a numeric range ("9–13"), when its start
  number agrees with the reference it points at (its label, else its place
  in the list).
Author-year citations ("Oliveira-Maia, 2017") are never expanded. The
context is the citing sentence plus one sentence either side, within the
paragraph. A citation pointing at no stored reference is counted, not linked.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sentinel.fulltext import sentences
from sentinel.fulltext.body import Cite, Paragraph

EXTRACTOR_VERSION = "1.0.0"
MAX_RANGE = 50  # a longer "range" is a misreading, not a citation
_JOIN = re.compile(r"\s*[,;–\-]?\s*")
_NUMERIC_RANGE = re.compile(r"\s*(\d+)\s*[–\-]\s*(\d+)\s*")


@dataclass(frozen=True)
class RefInfo:
    reference_id: int
    ordinal: int
    label: int | None  # the numeric label printed in the list, when there is one


@dataclass(frozen=True)
class Context:
    ordinal: int
    section: str
    marker: str
    sentence: str
    context_text: str
    reference_ids: tuple[int, ...]


@dataclass
class ContextStats:
    citations: int = 0
    groups: int = 0
    contexts: int = 0
    unknown_rids: int = 0
    unlinked_groups: int = 0
    ranges_expanded: int = 0
    reference_ids: set[int] = field(default_factory=set)


def extract(body: list[Paragraph], refs: dict[str, RefInfo],
            stats: ContextStats | None = None) -> list[Context]:
    """`refs` maps each XML ref id to its stored reference."""
    stats = stats if stats is not None else ContextStats()
    by_ordinal = {r.ordinal: r for r in refs.values()}
    out: list[Context] = []
    for para in body:
        if not para.cites:
            continue
        spans = sentences.split(para.text, [(c.start, c.end) for c in para.cites])
        for group in _groups(para):
            stats.citations += len(group)
            stats.groups += 1
            ids = _reference_ids(para.text, group, refs, by_ordinal, stats)
            if not ids:
                stats.unlinked_groups += 1
                continue
            i = next((k for k, (s, e) in enumerate(spans) if s <= group[0].start < e),
                     len(spans) - 1)
            lo, hi = max(i - 1, 0), min(i + 1, len(spans) - 1)
            out.append(Context(
                ordinal=len(out) + 1,
                section=para.section,
                marker=para.text[group[0].start:group[-1].end],
                sentence=para.text[spans[i][0]:spans[i][1]],
                context_text=para.text[spans[lo][0]:spans[hi][1]],
                reference_ids=ids,
            ))
            stats.contexts += 1
            stats.reference_ids.update(ids)
    return out


def _groups(para: Paragraph) -> list[list[Cite]]:
    groups: list[list[Cite]] = []
    for c in sorted(para.cites, key=lambda c: (c.start, c.rid)):
        if groups and (c.start < groups[-1][-1].end or
                       _JOIN.fullmatch(para.text, groups[-1][-1].end, c.start)):
            groups[-1].append(c)
        else:
            groups.append([c])
    return groups


def _reference_ids(text: str, group: list[Cite], refs: dict[str, RefInfo],
                   by_ordinal: dict[int, RefInfo], stats: ContextStats) -> tuple[int, ...]:
    found: list[RefInfo] = []
    for k, c in enumerate(group):
        ref = refs.get(c.rid)
        if ref is None:
            stats.unknown_rids += 1
            continue
        found.append(ref)
        found += _inner_range(c, ref, by_ordinal, stats)
        # Some publishers mark the dash itself as a citation:
        # <xref>20</xref><xref rid="B21">–</xref><xref>27</xref>.
        if c.text.strip() in ("–", "-") and 0 < k < len(group) - 1:
            before, after = refs.get(group[k - 1].rid), refs.get(group[k + 1].rid)
            if before and after and 1 < after.ordinal - before.ordinal <= MAX_RANGE:
                found += [by_ordinal[o] for o in range(before.ordinal + 1, after.ordinal)
                          if o in by_ordinal]
                stats.ranges_expanded += 1
        if k + 1 < len(group):
            nxt = refs.get(group[k + 1].rid)
            joint = text[c.end:group[k + 1].start]
            if nxt and re.search(r"[–\-]", joint) and 1 < nxt.ordinal - ref.ordinal <= MAX_RANGE:
                found += [by_ordinal[o] for o in range(ref.ordinal + 1, nxt.ordinal)
                          if o in by_ordinal]
                stats.ranges_expanded += 1
    ids: list[int] = []
    for r in found:
        if r.reference_id not in ids:
            ids.append(r.reference_id)
    return tuple(ids)


def _inner_range(c: Cite, ref: RefInfo, by_ordinal: dict[int, RefInfo],
                 stats: ContextStats) -> list[RefInfo]:
    m = _NUMERIC_RANGE.fullmatch(c.text)
    if not m:
        return []
    first, last = int(m.group(1)), int(m.group(2))
    start = ref.label if ref.label is not None else ref.ordinal
    if first != start or not 0 < last - first <= MAX_RANGE:
        return []
    stats.ranges_expanded += 1
    return [by_ordinal[ref.ordinal + k] for k in range(1, last - first + 1)
            if ref.ordinal + k in by_ordinal]
