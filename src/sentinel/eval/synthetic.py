"""The synthetic evaluation set: supported claim–passage pairs from full text,
corrupted by rule, with no language model (§10.1 item 7, ADR-051).

**Pairs.** A cited work with full text reports its results twice: in the
abstract, then in the body. A sentence from the abstract is the claim, and
the body's results passages that repeat every one of its anchor numbers are
its gold. Anchors are the numbers that pin a result down: decimals,
percentages, and whole numbers of 10 or more that aren't years. Confidence
levels ("95% CI") and P values aren't anchors, since every results section
prints them. A pair needs at least two anchors, one of them a decimal or a
percentage, and one to three gold passages. Each paper gives at most three
pairs, the first in abstract order.

**Corruptions**, recorded on every item as `corruption_type`:
- `none`: the pair as found. Supported.
- `direction_flipped`: every direction word swapped (higher and lower,
  increased and decreased, ...). Contradicted, with the same gold. Skipped
  when the sentence negates ("not", "similar"), since "not higher" flipped
  to "not lower" may still be true.
- `number_changed`: one anchor scaled by 0.5 or 1.5, to a value the source
  prints nowhere. Contradicted, with the same gold. This is §10.1's "weaken
  or strengthen the effect": rules can't tell which number is the effect, so
  any anchor may change.
- `population_changed`: one population word swapped (children and adults,
  men and women, mice and rats to humans, type 1 and type 2 diabetes), only
  when the new word appears nowhere in the source. Insufficient, no gold.
- `source_swapped`: the claim paired with another cited work that doesn't
  repeat its anchors, one cited by the same corpus paper where possible.
  Insufficient, no gold.

**Licences.** A corrupted claim is adapted text, so only sources whose
licence allows adaptation are used: Creative Commons without NoDerivatives,
CC0 and public domain. Text-mining licences, publisher terms, ND and missing
licences are skipped.

**Splits** are by source paper (§10.2): a hash of the paper ID seeded with
SENTINEL_RANDOM_SEED, 60/20/20, the same rule as SciFact's. A swapped source
is drawn from the claim paper's split, so an item's papers share one split.

**Limits.** The claim is the source's own sentence, so its words are close
to the gold passage's: retrieval on these items flatters BM25, and verdicts
are easier than on real citing sentences. The abstract passage the claim
came from is in `origin` (`claim_passage_id`), and retrieval on this set
searches body passages only. Rules make some bad items; ADR-051 has the spot
check.

Everything above `generate` is pure.
"""

from __future__ import annotations

import random
import re
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from itertools import batched

from sqlalchemy import Connection, Engine, insert, select

from sentinel.eval.items import EvalItemRecord, hash_split
from sentinel.fulltext import sentences
from sentinel.log import get_logger
from sentinel.models import corpus_paper, eval_item, eval_item_gold_passage, paper, passage, reference
from sentinel.retrieve.embed import scope_ids

log = get_logger(__name__)

DATASET = "synthetic-rules"
GENERATOR_VERSION = "1.0.0"  # items record it; bump on any rule change
MIN_ANCHORS = 2
MAX_GOLD = 3
MAX_PAIRS_PER_PAPER = 3
CLAIM_WORDS = (8, 60)
NUMBER_FACTORS = (0.5, 1.5)
GOLD_SECTIONS = frozenset({"results"})
SWAP_TRIES = 5
LABEL = {"none": "supported", "direction_flipped": "contradicted", "number_changed": "contradicted",
         "population_changed": "insufficient", "source_swapped": "insufficient"}
CORRUPTIONS = tuple(LABEL)


# --- numbers -----------------------------------------------------------------------

_NUMBER = re.compile(
    r"(?<![\w.·,])(?<![A-Za-z]-)"           # not inside a word, a decimal, a list or a name ("SF-36")
    r"(\d{1,3}(?:,\d{3})+(?!\d)|\d+)"        # the whole part, perhaps with thousands commas
    r"(?:([.·])(\d+))?"                      # decimals; "·" is the Lancet's decimal point
    r"(\s?%)?")
_P_VALUE = re.compile(r"[Pp]\s*(?:[- ]?values?\s*)?[=<>≤≥]\s*$")
_CONFIDENCE = re.compile(r"\s*%?\s*(?:CI\b|confidence|credible)", re.IGNORECASE)


@dataclass(frozen=True)
class Number:
    start: int
    end: int          # the digits only: a "%" after them is kept on replacement
    value: str        # canonical: no thousands commas, "." as the point
    decimals: int
    percent: bool
    grouped: bool     # printed with thousands commas
    point: str        # "." or "·"
    noise: bool       # a P value or a confidence level

    @property
    def strong(self) -> bool:
        return self.decimals > 0 or self.percent

    @property
    def anchor(self) -> bool:
        if self.noise:
            return False
        if self.strong:
            return True
        whole = int(self.value)
        return whole >= 10 and (self.grouped or not 1900 <= whole <= 2030)


def numbers(text: str) -> list[Number]:
    out = []
    for m in _NUMBER.finditer(text):
        whole, point, frac, pct = m.groups()
        start = m.start()
        window_start = max(0, start - 16)
        p = _P_VALUE.search(text[window_start:start])
        p_value = bool(p) and (window_start + p.start() == 0
                               or not text[window_start + p.start() - 1].isalnum())
        out.append(Number(
            start=start, end=m.end(3) if frac else m.end(1),
            value=whole.replace(",", "") + (f".{frac}" if frac else ""),
            decimals=len(frac or ""), percent=bool(pct), grouped="," in whole, point=point or ".",
            noise=p_value or bool(_CONFIDENCE.match(text, m.end()))))
    return out


def values(text: str) -> set[str]:
    """Every number printed in `text`, canonical."""
    return {n.value for n in numbers(text)}


# --- pairs -------------------------------------------------------------------------

_HEADINGS = (
    "background", "introduction", "context", "importance", "rationale", "objective", "objectives",
    "aim", "aims", "purpose", "hypothesis", "methods", "method", "methodology", "design",
    "study design", "setting", "settings", "participants", "patients", "subjects", "population",
    "interventions", "intervention", "exposure", "exposures", "measurements", "main outcome measures",
    "main outcomes and measures", "outcome measures", "outcomes", "results", "main results",
    "result", "findings", "conclusion", "conclusions", "interpretation", "discussion", "implications",
    "significance", "trial registration", "clinical trial registration", "registration", "funding",
    "summary")
# A structured abstract's headings run into its first sentence: "Results Of the 38 wards, ...".
_HEADING = re.compile(r"^(?:(?i:" + "|".join(sorted(map(re.escape, _HEADINGS), key=len, reverse=True))
                      + r")\s*[:.]?\s+)+(?=[A-Z0-9(\[])")
# A heading inside a sentence means two sentences ran together. Abstracts
# cleaned before the S2.2 fix lost the text from a "<" to the next tag:
# "(P<0.001).<h4>Conclusion</h4>Our ..." became "(P Conclusion Our ...".
_HEADING_INSIDE = re.compile(r"\s(?:Results?|Conclusions?|Interpretation|Findings|Methods?|Background|"
                             r"Significance|Discussion|Objectives?)\s+[A-Z(]")
# Aims and objectives say what was studied, not what was found.
_NOT_A_FINDING = re.compile(r"^To\s|\b(?:aim(?:ed|s)?\s+(?:to|of|was|were)|objectives?\s+(?:of|was|were|is)|"
                            r"purpose\s+of)\b", re.IGNORECASE)


def strip_heading(sentence: str) -> str:
    return _HEADING.sub("", sentence, count=1)


def claim_anchors(claim: str) -> frozenset[str] | None:
    """The claim's anchor values, or None if it can't make a pair."""
    if not CLAIM_WORDS[0] <= len(claim.split()) <= CLAIM_WORDS[1]:
        return None
    if _HEADING_INSIDE.search(claim) or _NOT_A_FINDING.search(claim):
        return None
    found = [n for n in numbers(claim) if n.anchor]
    if len({n.value for n in found}) < MIN_ANCHORS or not any(n.strong for n in found):
        return None
    return frozenset(n.value for n in found)


@dataclass(frozen=True)
class PassageRow:
    id: int
    ordinal: int
    section: str | None
    text: str


@dataclass(frozen=True)
class Pair:
    paper_id: int
    index: int               # the sentence's place in the abstract
    claim: str
    claim_passage_id: int    # the abstract passage holding the sentence
    anchors: frozenset[str]
    gold: tuple[int, ...]


def abstract_sentences(rows: Iterable[PassageRow]) -> list[tuple[str, int]]:
    """The abstract's sentences in order, each once (windows overlap), with
    the first passage that holds it."""
    seen: set[str] = set()
    out = []
    for row in sorted((r for r in rows if r.section == "abstract"), key=lambda r: r.ordinal):
        for s, e in sentences.split(row.text):
            sentence = " ".join(row.text[s:e].split())
            if sentence not in seen:
                seen.add(sentence)
                out.append((sentence, row.id))
    return out


def find_pairs(paper_id: int, rows: list[PassageRow]) -> list[Pair]:
    body = [(r.id, values(r.text)) for r in sorted(rows, key=lambda r: r.ordinal)
            if r.section in GOLD_SECTIONS]
    pairs: list[Pair] = []
    for index, (sentence, passage_id) in enumerate(abstract_sentences(rows)):
        claim = strip_heading(sentence)
        anchors = claim_anchors(claim)
        if anchors is None:
            continue
        gold = tuple(pid for pid, vals in body if anchors <= vals)
        if 1 <= len(gold) <= MAX_GOLD:
            pairs.append(Pair(paper_id, index, claim, passage_id, anchors, gold))
            if len(pairs) == MAX_PAIRS_PER_PAPER:
                break
    return pairs


# --- corruptions -------------------------------------------------------------------

DIRECTION = {
    "increased": "decreased", "decreased": "increased", "increase": "decrease", "decrease": "increase",
    "increases": "decreases", "decreases": "increases", "increasing": "decreasing",
    "decreasing": "increasing", "reduced": "increased", "reduce": "increase", "reduces": "increases",
    "reducing": "increasing", "reduction": "increase", "reductions": "increases",
    "higher": "lower", "lower": "higher", "highest": "lowest", "lowest": "highest",
    "greater": "smaller", "larger": "smaller", "smaller": "larger", "longer": "shorter",
    "shorter": "longer", "improved": "worsened", "worsened": "improved", "improves": "worsens",
    "worsens": "improves", "improving": "worsening", "improvement": "worsening",
    "worsening": "improvement", "better": "worse", "worse": "better", "superior": "inferior",
    "inferior": "superior", "above": "below", "below": "above", "positively": "negatively",
    "negatively": "positively", "more likely": "less likely", "less likely": "more likely",
}
_DIRECTION = re.compile(r"(?<![\w-])(?:(an?)\s+)?("
                        + "|".join(sorted(map(re.escape, DIRECTION), key=len, reverse=True))
                        + r")(?![\w-])", re.IGNORECASE)
# "lower" and "higher" also name body parts, bounds, income and education.
_NOT_DIRECTION = re.compile(
    r"\s+(?:limbs?|extremit\w*|respiratory|urinary|back|abdom\w*|gastrointestinal|GI|legs?|body|"
    r"quadrant|lobes?|airways?|tract|o?esophag\w*|bounds?|education|income)\b", re.IGNORECASE)
_NEGATION = re.compile(r"\b(?:no|not|nor|neither|without|cannot|similar|similarly|comparable|"
                       r"unchanged|non-?significant(?:ly)?)\b|n't\b", re.IGNORECASE)

POPULATION = {
    "children": "adults", "adults": "children", "adolescents": "adults", "infants": "adults",
    "men": "women", "women": "men", "boys": "girls", "girls": "boys", "males": "females",
    "females": "males", "mice": "humans", "rats": "humans",
    "type 1 diabetes": "type 2 diabetes", "type 2 diabetes": "type 1 diabetes",
}
_POPULATION = re.compile(r"(?<![\w-])("
                         + "|".join(sorted(map(re.escape, POPULATION), key=len, reverse=True))
                         + r")(?![\w-])", re.IGNORECASE)
# A source that uses any of these words has studied the new population, or
# a sample holding it ("33.7% were women" in a paper that also says "male").
_MALE = ("men", "man", "male", "males", "boys", "boy")
_FEMALE = ("women", "woman", "female", "females", "girls", "girl")
_YOUNG = ("children", "child", "paediatric", "pediatric", "infants", "infant", "adolescents",
          "adolescent", "boys", "girls")
_SEEN_AS = {
    "men": _MALE, "males": _MALE, "boys": _MALE, "women": _FEMALE, "females": _FEMALE, "girls": _FEMALE,
    "children": _YOUNG, "adults": ("adults", "adult"),
    "humans": ("humans", "human", "patients", "participants", "volunteers", "people"),
    "type 1 diabetes": ("type 1 diabetes", "type i diabetes", "t1d", "t1dm"),
    "type 2 diabetes": ("type 2 diabetes", "type ii diabetes", "t2d", "t2dm"),
}
# A count or share of the population ("females 46.6%", "six females", "33.7%
# (28 of 83) were women") describes a mixed sample: swapped, it contradicts.
_SHARE = (r"(?<![\w-])(?:{word})\s*[:=]?\s*\(?\s*(?:n\s*=\s*)?\d"
          r"|(?:\d[\d.,]*\s*%?\)?|\b(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve))"
          r"\s*(?:\([^)]*\)\s*)?(?:were\s+|are\s+)?(?:{word})(?![\w-])")


def _cased(word: str, like: str) -> str:
    return word[:1].upper() + word[1:] if like[:1].isupper() else word


def flip_direction(claim: str) -> tuple[str, list[list[str]]] | None:
    """The claim with every direction word swapped, and the swaps; None if it
    has none or negates."""
    if _NEGATION.search(claim):
        return None
    swaps: list[list[str]] = []

    def swap(m: re.Match) -> str:
        article, term = m.group(1), m.group(2)
        if term.lower() in ("lower", "higher") and _NOT_DIRECTION.match(claim, m.end()):
            return m.group(0)
        new = _cased(DIRECTION[term.lower()], term)
        swaps.append([term, new])
        gap = m.group(0)[len(article or ""):m.start(2) - m.start()]
        if article:  # "an increase" becomes "a decrease"
            article = _cased("an" if new[0].lower() in "aeiou" else "a", article)
        return (article or "") + gap + new

    flipped = _DIRECTION.sub(swap, claim)
    return (flipped, swaps) if swaps else None


def change_number(claim: str, source_values: set[str],
                  rng: random.Random) -> tuple[str, dict] | None:
    """The claim with one anchor scaled to a value the source never prints."""
    anchors = [n for n in numbers(claim) if n.anchor]
    rng.shuffle(anchors)
    for n in anchors:
        for factor in rng.sample(NUMBER_FACTORS, len(NUMBER_FACTORS)):
            scaled = _scaled(n, factor)
            if scaled is None or scaled[1] in source_values:
                continue
            printed, value = scaled
            return (claim[:n.start] + printed + claim[n.end:],
                    {"number_from": n.value, "number_to": value, "factor": factor})
    return None


def _scaled(n: Number, factor: float) -> tuple[str, str] | None:
    """(as printed, canonical) for n × factor, keeping n's decimals, point and
    grouping; None if it rounds to zero, is unchanged, or passes 100%."""
    value = f"{float(n.value) * factor:.{n.decimals}f}"
    if float(value) <= 0 or value == n.value or (n.percent and float(value) > 100):
        return None
    whole, _, frac = value.partition(".")
    printed = (f"{int(whole):,}" if n.grouped else whole) + (n.point + frac if frac else "")
    return printed, value


def change_population(claim: str, source_text: str) -> tuple[str, str, str] | None:
    """(claim, old word, new word): the claim's one population word swapped
    for one the source never uses, nor any word for the same group. None if
    it names no population, or more than one."""
    found = {m.group(1).lower() for m in _POPULATION.finditer(claim)}
    if len(found) != 1:
        return None
    old = found.pop()
    new = POPULATION[old]
    if re.search(_SHARE.format(word=re.escape(old)), claim, re.IGNORECASE):
        return None
    seen = "|".join(map(re.escape, _SEEN_AS[new]))
    if re.search(rf"(?<![\w-])(?:{seen})(?![\w-])", source_text, re.IGNORECASE):
        return None  # the source is the claim's own paper, so this also covers the claim
    changed = _POPULATION.sub(lambda m: _cased(new, m.group(1)) if m.group(1).lower() == old
                              else m.group(0), claim)
    return changed, old, new


Partner = Callable[[Pair, random.Random], "tuple[int, str] | None"]


def corrupt(pair: Pair, source_text: str, source_values: set[str], rng: random.Random,
            find_partner: Partner) -> list[tuple[str, str, int, dict]]:
    """(corruption type, claim, source paper, details) for the intact pair and
    each corruption that applies to it."""
    out = [("none", pair.claim, pair.paper_id, {})]
    if flipped := flip_direction(pair.claim):
        out.append(("direction_flipped", flipped[0], pair.paper_id, {"swapped": flipped[1]}))
    if changed := change_number(pair.claim, source_values, rng):
        out.append(("number_changed", changed[0], pair.paper_id, changed[1]))
    if moved := change_population(pair.claim, source_text):
        out.append(("population_changed", moved[0], pair.paper_id,
                    {"population_from": moved[1], "population_to": moved[2]}))
    if partner := find_partner(pair, rng):
        out.append(("source_swapped", pair.claim, partner[0], {"partner_kind": partner[1]}))
    return out


# --- licences ----------------------------------------------------------------------

_CC_URL = re.compile(r"creativecommons\.org/(?:licenses/([a-z-]+)/(\d(?:\.\d)?)|publicdomain/(zero|mark))")
_CC_SHORT = re.compile(r"cc[\s-]?(by(?:[\s-](?:nc|sa|nd))*)")


def adaptable_license(raw: str | None) -> str | None:
    """A canonical code ("CC-BY-4.0", "CC0") for a licence that allows
    adapted text; None for NoDerivatives, text-mining or publisher terms, and
    anything unknown."""
    text = (raw or "").strip().lower()
    if text in ("cc0", "public-domain"):
        return "CC0" if text == "cc0" else "public-domain"
    if m := _CC_URL.search(text):
        if m.group(3):
            return "CC0" if m.group(3) == "zero" else "public-domain"
        code, version = m.group(1), m.group(2)
    elif m := _CC_SHORT.fullmatch(text):
        code, version = re.sub(r"\s", "-", m.group(1)), None
    else:
        return None
    parts = code.split("-")
    if parts[0] != "by" or not set(parts) <= {"by", "nc", "sa"}:
        return None  # NoDerivatives, or a code not recognised (one record writes "bync-nd")
    return "CC-" + "-".join(p.upper() for p in parts) + (f"-{version}" if version else "")


# --- generating --------------------------------------------------------------------


@dataclass
class Generated:
    sources: int = 0                 # cited works with body passages
    adaptable: int = 0               # ...whose licence allows adapted text
    papers_with_pairs: int = 0
    pairs: int = 0
    items_added: int = 0
    gold_links: int = 0
    by_type: dict[str, int] = field(default_factory=dict)
    by_split_label: dict[str, int] = field(default_factory=dict)
    partners: dict[str, int] = field(default_factory=dict)
    scifact_title_overlap: int = 0   # source papers whose title is also a SciFact abstract's
    already_present: bool = False


def generate(engine: Engine, *, corpus: str, seed: int, splitter_ver: str) -> Generated:
    """Pairs from the corpus's cited works with full text, and their
    corruptions, written as one transaction. Running again adds nothing."""
    result = Generated()
    with engine.begin() as conn:
        if conn.execute(select(eval_item.c.id).where(
                eval_item.c.origin["dataset"].astext == DATASET,
                eval_item.c.origin["generator_ver"].astext == GENERATOR_VERSION,
                eval_item.c.origin["splitter_ver"].astext == splitter_ver,
                eval_item.c.origin["corpus"].astext == corpus).limit(1)).first():
            log.info("synthetic_items_present", corpus=corpus, generator_ver=GENERATOR_VERSION)
            result.already_present = True
            return result

        with_body = (select(passage.c.paper_id).distinct()
                     .where(passage.c.splitter_ver == splitter_ver, passage.c.section != "abstract"))
        sources = conn.execute(select(paper.c.id, paper.c.license, paper.c.title_norm)
                               .where(paper.c.id.in_(scope_ids(corpus, "cited")), paper.c.id.in_(with_body))
                               .order_by(paper.c.id)).all()
        result.sources = len(sources)
        split_of = {pid: hash_split(f"{seed}:{DATASET}:{pid}") for pid, _, _ in sources}
        in_split: dict[str, list[int]] = defaultdict(list)
        for pid, split in split_of.items():
            in_split[split].append(pid)
        co_cited = _co_cited(conn, corpus, set(split_of))
        scifact_titles = set(conn.execute(select(paper.c.title_norm).where(paper.c.source == "scifact")).scalars())

        def find_partner(pair: Pair, rng: random.Random) -> tuple[int, str] | None:
            split = split_of[pair.paper_id]
            pools = (("co-cited", sorted(b for b in co_cited.get(pair.paper_id, ()) if split_of[b] == split)),
                     ("random", in_split[split]))
            for kind, pool in pools:
                drawn = [b for b in rng.sample(pool, min(SWAP_TRIES + 1, len(pool))) if b != pair.paper_id]
                for b in drawn[:SWAP_TRIES]:
                    texts = conn.execute(select(passage.c.text).where(
                        passage.c.paper_id == b, passage.c.splitter_ver == splitter_ver)).scalars()
                    if not any(pair.anchors <= values(t) for t in texts):
                        return b, kind
            return None

        records: list[EvalItemRecord] = []
        counts: dict[str, dict[str, int]] = {"type": defaultdict(int), "split": defaultdict(int),
                                             "partner": defaultdict(int)}
        for pid, raw_license, title_norm in sources:
            code = adaptable_license(raw_license)
            if code is None:
                continue
            result.adaptable += 1
            rows = [PassageRow(*r) for r in conn.execute(
                select(passage.c.id, passage.c.ordinal, passage.c.section, passage.c.text)
                .where(passage.c.paper_id == pid, passage.c.splitter_ver == splitter_ver)).all()]
            pairs = find_pairs(pid, rows)
            if not pairs:
                continue
            result.papers_with_pairs += 1
            result.pairs += len(pairs)
            result.scifact_title_overlap += title_norm in scifact_titles
            source_text = " ".join(r.text for r in rows)
            source_values = set().union(*(values(r.text) for r in rows))
            for pair in pairs:
                rng = random.Random(f"{seed}:{DATASET}:{pid}:{pair.index}")
                for corruption, claim, source_id, details in corrupt(pair, source_text, source_values,
                                                                     rng, find_partner):
                    label = LABEL[corruption]
                    records.append(EvalItemRecord(
                        set_name="synthetic", split=split_of[pid], claim_text=claim,
                        source_paper_id=source_id, label=label, corruption_type=corruption,
                        license=code,
                        origin={"dataset": DATASET, "generator_ver": GENERATOR_VERSION,
                                "splitter_ver": splitter_ver, "corpus": corpus, "seed": seed,
                                "pair": f"{pid}:{pair.index}", "claim_paper_id": pid,
                                "claim_passage_id": pair.claim_passage_id,
                                "sentence_index": pair.index, "anchors": sorted(pair.anchors),
                                "license_raw": raw_license,
                                "adapted": claim != pair.claim, **details},
                        gold_passage_ids=[] if label == "insufficient" else list(pair.gold)))
                    counts["type"][corruption] += 1
                    counts["split"][f"{split_of[pid]}/{label}"] += 1
                    if corruption == "source_swapped":
                        counts["partner"][details["partner_kind"]] += 1

        for chunk in batched(records, 1000):
            ids = conn.execute(insert(eval_item).returning(eval_item.c.id, sort_by_parameter_order=True),
                               [r.model_dump(exclude={"gold_passage_ids"}) for r in chunk]).scalars().all()
            links = [{"eval_item_id": i, "passage_id": p}
                     for i, r in zip(ids, chunk, strict=True) for p in r.gold_passage_ids]
            if links:
                conn.execute(insert(eval_item_gold_passage), links)
            result.items_added += len(chunk)
            result.gold_links += len(links)
    result.by_type = {t: counts["type"][t] for t in CORRUPTIONS}
    result.by_split_label = dict(sorted(counts["split"].items()))
    result.partners = dict(counts["partner"])
    log.info("synthetic_generated", corpus=corpus, sources=result.sources, adaptable=result.adaptable,
             pairs=result.pairs, items=result.items_added, by_type=result.by_type)
    return result


def _co_cited(conn: Connection, corpus: str, works: set[int]) -> dict[int, set[int]]:
    """For each work, the other works some corpus paper cites alongside it."""
    rows = conn.execute(select(reference.c.citing_paper_id, reference.c.resolved_paper_id)
                        .join(corpus_paper, corpus_paper.c.paper_id == reference.c.citing_paper_id)
                        .where(corpus_paper.c.label == corpus, reference.c.resolved_paper_id.is_not(None))
                        .distinct()).all()
    by_citing: dict[int, set[int]] = defaultdict(set)
    for citing, cited in rows:
        if cited in works:
            by_citing[citing].add(cited)
    out: dict[int, set[int]] = defaultdict(set)
    for group in by_citing.values():
        for work in group:
            out[work] |= group - {work}
    return out
