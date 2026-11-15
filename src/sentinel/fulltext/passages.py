"""Passages: a work's text cut into windows of whole sentences (§10.1 item 2, ADR-046).

Each section (the abstract, then each body section in order) is cut
separately, so a passage never spans two sections. Sentences are added until
the window reaches `min_words`; one that would take it past `max_words`
starts the next window. Each window after the first starts with the last
`overlap` sentences of the one before. A last window shorter than
`min_words` joins the one before when the two fit in `max_words`. A sentence
longer than `max_words` on its own is cut by words. Pure.
"""

from __future__ import annotations

from dataclasses import dataclass

from sentinel.fulltext import sentences
from sentinel.fulltext.body import Paragraph

SPLITTER_VERSION = "1.0.0"


@dataclass(frozen=True)
class PassageParams:
    min_words: int
    max_words: int
    overlap: int  # sentences carried into the next window

    def version(self) -> str:
        """splitter_ver: the code version plus the sizes, since both decide the text."""
        return f"{SPLITTER_VERSION}/{self.min_words}-{self.max_words}-{self.overlap}"


@dataclass(frozen=True)
class PassageText:
    section: str
    text: str


def make_passages(abstract: str | None, body: list[Paragraph],
                  params: PassageParams) -> list[PassageText]:
    sections: list[tuple[str, list[str]]] = []
    if abstract:
        sections.append(("abstract", _sentences(abstract)))
    for para in body:
        # Passages 1.0.0 keep the splitter's 1.0.0 citation rules (ADR-056): new
        # rules would change stored text that vectors and the BM25 index were made from.
        sents = [para.text[s:e] for s, e in sentences.split(
            para.text, [(c.start, c.end) for c in para.cites], cites_across_space=False)]
        if sections and sections[-1][0] == para.section and sections[-1][0] != "abstract":
            sections[-1][1].extend(sents)
        else:
            sections.append((para.section, sents))
    out = []
    for section, sents in sections:
        out += [PassageText(section, w) for w in windows(sents, params)]
    return out


def windows(sents: list[str], params: PassageParams) -> list[str]:
    return [text for text, _ in sentence_windows(sents, params)]


def sentence_windows(sents: list[str], params: PassageParams) -> list[tuple[str, set[int]]]:
    """Each window's text and the indexes of the sentences in it, so a label
    on a sentence (an evaluation set's evidence) maps to passages exactly."""
    pieces = [(i, piece) for i, s in enumerate(sents) for piece in _cut_long(s, params.max_words)]
    units = [piece for _, piece in pieces]
    spans = _window_spans([len(u.split()) for u in units], params)
    return [(" ".join(units[s:e]), {pieces[j][0] for j in range(s, e)}) for s, e in spans]


def _window_spans(sizes: list[int], params: PassageParams) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    start = 0
    while start < len(sizes):
        end, words = start, 0
        while (end < len(sizes) and words < params.min_words
               and (end == start or words + sizes[end] <= params.max_words)):
            words += sizes[end]
            end += 1
        spans.append((start, end))
        if end >= len(sizes):
            break
        # Carry the overlap, unless it and the next sentence wouldn't fit;
        # always move forward.
        nxt = max(end - params.overlap, start + 1)
        if sum(sizes[nxt:end]) + sizes[end] > params.max_words:
            nxt = end
        start = nxt
    if len(spans) > 1:
        (ps, _), (s, e) = spans[-2], spans[-1]
        if sum(sizes[s:e]) < params.min_words and sum(sizes[ps:e]) <= params.max_words:
            spans[-2:] = [(ps, e)]
    return spans


def _sentences(text: str) -> list[str]:
    return [text[s:e] for s, e in sentences.split(text)]


def _cut_long(sentence: str, max_words: int) -> list[str]:
    words = sentence.split()
    if len(words) <= max_words:
        return [sentence]
    return [" ".join(words[i:i + max_words]) for i in range(0, len(words), max_words)]
