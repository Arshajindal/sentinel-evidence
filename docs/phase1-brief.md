# Sentinel — Phase 1 Implementation Brief

**Scope:** Weeks 1–3. Data foundation and the deterministic check tier.
**Audience:** Claude Code, working in a fresh repository.
**Status:** Authoritative for Phase 1. Later phases are described only where they constrain decisions made now.

---

## 1. What Sentinel is

Sentinel audits the integrity of biomedical research publications at corpus scale. Given a set of papers, it produces a structured **report card** per paper: a list of findings, each with a severity, a machine-checkable basis, and an evidence trail.

The full system runs twelve checks across three bands:

| Band | Checks | Phase |
|---|---|---|
| Deterministic | Reference existence, retraction status, statistical self-consistency, impossible statistics, results-reporting compliance | **Phase 1** |
| Cross-document | Outcome switching, disclosure mismatch, duplicate publication | Phase 2 |
| Claim grounding | Claim–source support, spin detection | Phase 3 |

Phase 1 delivers a working, useful tool with zero machine learning. It is the credibility floor: if every later phase underperforms, Phase 1 still produces real findings on real papers.

**Primary user:** systematic review teams. The output is designed to be triaged by a human, not consumed automatically.

---

## 2. Non-goals for Phase 1

Do not build these. They belong to later phases and building them now will distort the schema.

- No embeddings, no vector search, no `pgvector` extension yet
- No LLM calls of any kind
- No agent framework, no LangGraph
- No web UI — CLI only
- No full-text PDF parsing beyond what the deterministic checks need
- No trial outcome comparison logic (Phase 2), though trial data **is** ingested now
- No authentication, no multi-user concepts

If a task seems to require one of these, stop and flag it rather than improvising.

---

## 3. Assumptions and environment

- Python 3.11+
- PostgreSQL 15+ with the `pg_trgm` extension
- Linux or WSL for development
- No GPU required in this phase
- All data sources are public and free; no API keys are strictly required, but a contact email should be supplied to every polite-pool endpoint

Dependencies: `httpx`, `sqlalchemy` (Core, not ORM), `psycopg[binary]` (v3 — the `postgresql+psycopg://` dialect), `alembic`, `pydantic` v2, `pydantic-settings`, `scipy`, `polars` or `pandas`, `lxml`, `typer`, `structlog`, `tenacity`, `pytest`.

Use SQLAlchemy **Core**, not the ORM. Schema changes go through Alembic migrations, never `create_all`.

---

## 4. Repository layout

```
sentinel/
  pyproject.toml
  alembic.ini
  README.md
  .env.example
  migrations/
  src/sentinel/
    __init__.py
    config.py            # pydantic-settings, all config from env
    db.py                # engine, session helpers, connection pool
    models.py            # SQLAlchemy Core Table definitions
    schemas.py           # pydantic models for all structured data
    ingest/
      __init__.py
      base.py            # Harvester ABC: watermarks, retry, rate limit
      crossref.py
      openalex.py
      retraction_watch.py
      clinicaltrials.py
      europepmc.py
    parse/
      references.py      # reference string -> structured fields
      statistics.py      # extract reported test statistics from text
    resolve/
      cascade.py         # reference -> known paper, staged matching
      normalize.py       # DOI, title, author name normalisation
    checks/
      base.py            # Check ABC: run(paper) -> list[Finding]
      c01_existence.py
      c02_retraction.py
      c03_statcheck.py
      c04_grim.py
      c05_results_reporting.py
      registry.py        # check id -> class, versioned
    report/
      card.py            # assemble findings into a report card
      export.py          # JSON and CSV output
    cli.py               # typer entrypoint
  tests/
    fixtures/            # real papers with known-correct expected findings
    test_*.py
```

---

## 5. Database schema

Write this as the initial Alembic migration. Column choices below are load-bearing; flag disagreements rather than silently changing them.

```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- A work we know about, from any source.
CREATE TABLE paper (
    id              BIGSERIAL PRIMARY KEY,
    doi             TEXT UNIQUE,              -- normalised: lowercase, no prefix
    pmid            TEXT UNIQUE,
    pmcid           TEXT UNIQUE,
    title           TEXT NOT NULL,
    title_norm      TEXT NOT NULL,            -- for fuzzy matching
    container       TEXT,                     -- journal or venue name
    year            SMALLINT,
    first_author    TEXT,                     -- surname only, normalised
    authors         JSONB,
    source          TEXT NOT NULL,            -- 'crossref' | 'openalex' | 'pubmed'
    is_oa           BOOLEAN,
    license         TEXT,
    fulltext_status TEXT NOT NULL DEFAULT 'none',  -- none|abstract|full
    raw             JSONB,
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX paper_title_trgm ON paper USING gin (title_norm gin_trgm_ops);
CREATE INDEX paper_author_year ON paper (first_author, year);

-- One row per reference in a citing paper's bibliography.
CREATE TABLE reference (
    id                  BIGSERIAL PRIMARY KEY,
    citing_paper_id     BIGINT NOT NULL REFERENCES paper(id) ON DELETE CASCADE,
    ordinal             INT,                  -- position in the bibliography
    raw_string          TEXT,
    parsed_doi          TEXT,
    parsed_title        TEXT,
    parsed_first_author TEXT,
    parsed_year         SMALLINT,
    parsed_container    TEXT,
    resolved_paper_id   BIGINT REFERENCES paper(id),
    resolution_method   TEXT,                 -- see section 8
    resolution_score    REAL,                 -- 0..1
    resolution_status   TEXT NOT NULL DEFAULT 'pending',
                                              -- pending|resolved|unresolved|ambiguous
    resolved_at         TIMESTAMPTZ
);
CREATE INDEX reference_citing ON reference (citing_paper_id);
CREATE INDEX reference_status ON reference (resolution_status);

-- Retraction and concern notices, keyed loosely because sources disagree.
CREATE TABLE retraction_notice (
    id              BIGSERIAL PRIMARY KEY,
    retracted_doi   TEXT,
    retracted_pmid  TEXT,
    notice_doi      TEXT,
    notice_type     TEXT NOT NULL,   -- retraction|correction|expression_of_concern
    notice_date     DATE,
    reason          TEXT,
    source          TEXT NOT NULL,
    raw             JSONB
);
CREATE INDEX retraction_doi ON retraction_notice (retracted_doi);
CREATE INDEX retraction_pmid ON retraction_notice (retracted_pmid);

-- Registry record for a clinical trial. Ingested now; compared in Phase 2.
CREATE TABLE trial (
    nct_id                  TEXT PRIMARY KEY,
    brief_title             TEXT,
    overall_status          TEXT,
    study_type              TEXT,
    phase                   TEXT,
    enrollment              INT,
    start_date              DATE,
    primary_completion_date DATE,
    completion_date         DATE,
    results_first_posted    DATE,
    lead_sponsor            TEXT,
    sponsor_class           TEXT,
    raw                     JSONB,
    ingested_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Outcome definitions, versioned. ClinicalTrials.gov keeps a change history;
-- capture every version, because outcome switching is detected by diffing them.
CREATE TABLE trial_outcome (
    id              BIGSERIAL PRIMARY KEY,
    nct_id          TEXT NOT NULL REFERENCES trial(nct_id) ON DELETE CASCADE,
    version_no      INT NOT NULL,
    version_date    DATE,
    outcome_type    TEXT NOT NULL,   -- primary|secondary|other
    measure         TEXT NOT NULL,
    description     TEXT,
    time_frame      TEXT,
    UNIQUE (nct_id, version_no, outcome_type, measure)
);
CREATE INDEX trial_outcome_nct ON trial_outcome (nct_id);

CREATE TABLE paper_trial_link (
    paper_id    BIGINT NOT NULL REFERENCES paper(id) ON DELETE CASCADE,
    nct_id      TEXT NOT NULL REFERENCES trial(nct_id) ON DELETE CASCADE,
    link_source TEXT NOT NULL,   -- registry|fulltext|crossref
    confidence  REAL,
    PRIMARY KEY (paper_id, nct_id)
);

-- Every statistical test string found in a paper, with recomputation.
CREATE TABLE stat_test (
    id              BIGSERIAL PRIMARY KEY,
    paper_id        BIGINT NOT NULL REFERENCES paper(id) ON DELETE CASCADE,
    location        TEXT,            -- section or page hint
    raw_string      TEXT NOT NULL,
    test_type       TEXT NOT NULL,   -- t|F|chi2|r|z|Q
    statistic       DOUBLE PRECISION,
    df1             DOUBLE PRECISION,
    df2             DOUBLE PRECISION,
    reported_p      DOUBLE PRECISION,
    reported_p_op   TEXT,            -- '='|'<'|'>'
    computed_p      DOUBLE PRECISION,
    one_tailed_ok   BOOLEAN,         -- consistent if one-tailed assumed
    inconsistent    BOOLEAN,
    gross           BOOLEAN          -- crosses the .05 decision boundary
);
CREATE INDEX stat_test_paper ON stat_test (paper_id);

-- The report card, one row per finding.
CREATE TABLE finding (
    id          BIGSERIAL PRIMARY KEY,
    paper_id    BIGINT NOT NULL REFERENCES paper(id) ON DELETE CASCADE,
    run_id      BIGINT NOT NULL REFERENCES check_run(id) ON DELETE CASCADE,
    check_id    TEXT NOT NULL,       -- 'C03'
    check_ver   TEXT NOT NULL,       -- bump when logic changes
    severity    TEXT NOT NULL,       -- info|low|medium|high
    status      TEXT NOT NULL,       -- flagged|clean|not_applicable|insufficient_data
    summary     TEXT NOT NULL,       -- one human-readable line
    evidence    JSONB NOT NULL,      -- structured, check-specific, always populated
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX finding_paper ON finding (paper_id);
CREATE INDEX finding_check ON finding (check_id, status);

CREATE TABLE check_run (
    id           BIGSERIAL PRIMARY KEY,
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at  TIMESTAMPTZ,
    corpus_label TEXT,
    config_hash  TEXT NOT NULL,
    checks       TEXT[] NOT NULL,
    counts       JSONB,
    status       TEXT NOT NULL DEFAULT 'running'
);

CREATE TABLE ingest_watermark (
    source       TEXT PRIMARY KEY,
    cursor       TEXT,
    last_run_at  TIMESTAMPTZ,
    rows_seen    BIGINT NOT NULL DEFAULT 0
);
```

**Two schema decisions worth understanding.** `finding.evidence` is always populated, including for clean results, because a check that reports "clean" without showing what it examined is unauditable. And `finding.check_ver` exists so that re-running an improved check produces comparable rows rather than silently overwriting history.

---

## 6. Data sources

All public. Supply a contact email to every endpoint that offers a polite pool — it materially improves rate limits.

| Source | Access | Use |
|---|---|---|
| Crossref | REST API and bulk public data file | DOI resolution, metadata, reference lists where deposited |
| OpenAlex | S3 snapshot preferred; REST API for lookups | Work metadata, citation graph, author identifiers |
| Retraction Watch | CSV, distributed through Crossref | Retraction and concern notices |
| ClinicalTrials.gov | REST API v2, no key required | Trial records and outcome version history |
| Europe PMC | REST API | Full text for open-access records, reference lists |
| PMC OA subset | Bulk download | Full text corpus for the scale run |

**Verify every endpoint URL before coding against it.** These services change paths. Do not hardcode a URL found in training data without confirming it resolves.

### Harvester contract

Every harvester subclasses `ingest.base.Harvester` and implements:

```python
class Harvester(ABC):
    source: str
    @abstractmethod
    def fetch(self, cursor: str | None) -> Iterator[tuple[dict, str]]:
        """Yield (raw_record, next_cursor). Must be resumable from cursor."""
    @abstractmethod
    def normalise(self, raw: dict) -> BaseModel:
        """Raw payload -> validated pydantic model. Never write unvalidated data."""
```

Requirements for all harvesters:

- Resumable from `ingest_watermark.cursor`. A run killed at record 400,000 resumes there.
- Rate limited with token bucket; configurable per source.
- Retry with exponential backoff via `tenacity`, on 429 and 5xx only.
- Idempotent upsert on the natural key. Never delete; mark superseded.
- Structured logging of records seen, written, skipped, failed, per batch.

### Prefer bulk over API

For OpenAlex and Crossref, ingest the bulk snapshot rather than paginating the API. Once the data is local, existence checking becomes a table join rather than a network call, and throughput stops being governed by someone else's rate limiter. This is the single highest-leverage decision in Phase 1 — do it now, not later.

---

## 7. Reference parsing

Input: either a structured reference list from Crossref or Europe PMC, or an unstructured bibliography string.

Structured references are straightforward — map fields and move on. For unstructured strings, extract: DOI (if present), title, first author surname, year, container.

Use a rule-based parser. Do not reach for a model.

- DOI: regex for `10.\d{4,9}/\S+`, then normalise
- Year: four-digit number in 1900–current+1, preferring one adjacent to parentheses
- Author: leading surname pattern before initials or "et al"
- Title: the longest segment between delimiters that is not the container

Parsing will fail on some strings. That is expected and fine. Record `parsed_*` as NULL and let resolution handle it. **Do not guess.** A wrong parse is worse than a null, because it produces a confident wrong resolution downstream.

---

## 8. Entity resolution

The staged cascade. Stop at the first stage that produces a unique match.

| Stage | Method | Score | Notes |
|---|---|---|---|
| 1 | Normalised DOI exact | 1.00 | Lowercase, strip `https://doi.org/`, strip trailing punctuation |
| 2 | PMID exact | 1.00 | |
| 3 | Normalised title exact | 0.95 | NFKD, lowercase, strip punctuation and whitespace |
| 4 | Title trigram ≥ 0.85 **and** first-author match **and** year ±1 | 0.70–0.90 | `pg_trgm` similarity; scale score by similarity |
| 5 | Title trigram ≥ 0.92 alone | 0.60 | Weak; mark for review |

Record the winning stage in `resolution_method`. If two or more candidates tie at a stage, set status `ambiguous` and stop — do not pick one.

### The critical distinction

**Unresolved is not the same as fabricated.** A reference can fail every stage because it is a book, a conference abstract, a non-indexed regional journal, a personal communication, or simply badly formatted.

Check C01 must therefore separate:

- `unresolved_nonjournal` — parsed metadata suggests a book, thesis, website, or personal communication. Not a finding.
- `unresolved_unparseable` — the string could not be parsed well enough to attempt resolution. Reported as `insufficient_data`, not as a flag.
- `unresolved_journal_like` — parsed as a journal article with a plausible title, author, and year, and still found nothing. **This is the only case that becomes a flag**, and even then at `medium` severity with the phrase "could not be verified," never "fabricated."

The published prevalence studies were careful about this and so must we. Overclaiming here destroys the tool's credibility faster than any other error.

---

## 9. The deterministic checks

Every check implements:

```python
class Check(ABC):
    check_id: str          # 'C03'
    check_ver: str         # '1.0.0' — bump on any logic change
    @abstractmethod
    def run(self, paper: PaperContext) -> list[Finding]: ...
```

Checks must be pure with respect to their inputs: same paper, same version, same findings. No network calls inside `run()` — all data is pre-loaded into `PaperContext`.

Any stochastic behaviour — audit sampling, tie-breaking, shuffling — draws from a seeded generator supplied on `PaperContext`, never from module-level `random`. A run must be reproducible from `config_hash` plus `SENTINEL_RANDOM_SEED`, or `config_hash` is not a run identity.

### C01 — Reference existence

For each reference: resolve via the cascade, then classify per section 8. Flag only `unresolved_journal_like`.

Evidence payload: the raw string, the parsed fields, every stage attempted, and the near-misses with their scores. A reviewer must be able to see why it failed.

### C02 — Retraction and concern status

For each resolved reference, look up `retraction_notice` by DOI and PMID.

Severity depends on timing, in three bands. A paper can sit in press for six to twelve months, so a citing paper published shortly after a retraction notice could not reasonably have known.

Let `gap = citing_paper.publication_date − notice.notice_date`, and `G = SENTINEL_C02_GRACE_MONTHS` (default 6):

| Condition | Severity | Framing |
|---|---|---|
| `gap > G` | `high` | Cited after retraction |
| `0 < gap ≤ G` | `low` | Retracted while this paper was in press |
| `gap ≤ 0` | `low` | Cited before retraction; downstream claims may need review |
| Expression of concern, any timing | `medium` | Concern raised about a cited source |

When the citing paper's publication date is unknown, return `insufficient_data` rather than assuming a band.

Evidence: notice type, notice date, citing publication date, computed gap, band applied, and reason if available.

This grace window is a precision decision and it moves the headline flag rate. See ADR-017.

### C03 — Statistical self-consistency

Extract reported null-hypothesis tests from the full text and recompute each p-value from the test statistic and degrees of freedom.

Patterns to match (case-insensitive, tolerant of spacing and Unicode):

```
t(58) = 2.10, p = .03
F(2, 45) = 3.21, p < .05
χ2(1, N = 120) = 4.50, p = .034
r(28) = .45, p < .01
z = 2.10, p = .036
```

Recomputation, all two-tailed unless the paper says otherwise:

- `t` → `2 * scipy.stats.t.sf(abs(stat), df)`
- `F` → `scipy.stats.f.sf(stat, df1, df2)` (one-tailed by definition)
- `χ²` → `scipy.stats.chi2.sf(stat, df)`
- `r` → convert to `t = r * sqrt(df / (1 - r²))`, then as `t`
- `z` → `2 * scipy.stats.norm.sf(abs(stat))`

Comparison rules, which matter more than the recomputation:

1. Respect the reported precision. If the paper prints `p = .03`, any computed value rounding to `.03` is consistent.
2. Respect the operator. `p < .05` is consistent with any computed value below `.05`.
3. Test the one-tailed alternative before flagging. If halving the p-value makes it consistent, set `one_tailed_ok = true` and **do not flag** — many papers report one-tailed tests without saying so.
4. Flag `inconsistent` only when neither tailing assumption reconciles the numbers.
5. Flag `gross` when the reported and recomputed values fall on opposite sides of `.05`.

Severity: `low` for inconsistent, `high` for gross.

Published work applying this method at scale found roughly half of psychology papers using significance testing contained at least one inconsistency, and about one in eight contained a gross one. Biomedical rates are not the same; measuring them is part of the point.

### C04 — Impossible statistics (GRIM)

For a reported mean `M` of `N` items measured on an integer scale, `M × N` must be an integer. If it is not, within the rounding tolerance implied by the number of decimal places reported, the mean cannot arise from the stated data.

```
tolerance = 0.5 * 10^(-decimals)
granular  = round(M * N) / N
consistent = abs(M - granular) <= tolerance + epsilon
```

Applicability is narrow and must be enforced: integer-valued measures only, `N` known and small enough to matter (under about 100), and the mean reported to at least two decimal places. When these do not hold, return `not_applicable` rather than guessing.

Severity `medium`. This check has very high precision when applicable, which is exactly why the applicability gate must be strict.

### C05 — Results reporting compliance

For trials in the `trial` table: if `overall_status` is completed and `primary_completion_date` is more than twelve months in the past and `results_first_posted` is null, flag.

Severity `medium`. Evidence: the dates, the sponsor, and the sponsor class.

This check needs no paper at all — it runs on registry data alone, which makes it the cheapest finding in the system and a good end-to-end test of the pipeline.

---

## 10. CLI

```
sentinel ingest <source> [--limit N] [--resume]
sentinel resolve [--batch-size N]
sentinel check [--checks C01,C03] [--corpus LABEL] [--limit N]
sentinel report <paper-id|doi> [--format json|text]
sentinel export --run <run-id> --format csv --out PATH
sentinel stats --run <run-id>
```

`sentinel stats` prints per-check counts, flag rates, and wall time per stage. Wire it in from the first commit — the cascade ratios are a headline metric of the whole project and you cannot report them retroactively.

---

## 11. Tests

Unit tests are necessary but not sufficient. The important ones are the fixture tests.

**Build a fixture corpus of 30–50 real papers** with hand-verified expected findings. Include:

- At least five papers citing known retracted work, both before and after the notice
- At least five with known statistical inconsistencies, verified by hand
- At least five with references that are books, theses, or personal communications, to confirm C01 does not flag them
- At least five completely clean papers, to measure the false positive rate
- Edge cases: Unicode in author names, DOIs with trailing punctuation, references with no year

Fixtures live in `tests/fixtures/` as JSON with the expected `Finding` list. The suite asserts exact matches on check id, status, and severity.

**Every false positive found during development gets added as a fixture.** This is how the suite becomes a regression net rather than a formality.

---

## 12. Acceptance criteria

Phase 1 is done when all of these hold:

1. `sentinel ingest` runs to completion on all five sources and is resumable after a kill.
2. Resolution achieves ≥ 90% resolved on a sample of 1,000 references from open-access papers with structured reference lists.
3. All five checks run against the fixture corpus with zero unexpected findings.
4. A single paper's report card is produced end to end from a DOI in under five seconds, excluding ingestion.
5. A 1,000-paper corpus run completes and `sentinel stats` reports per-check flag rates.
6. False positive rate on the clean fixture subset is zero. Not low — zero. At this tier, a deterministic check that produces false positives has a bug.
7. README documents setup, the checks, and the known limitations of each.

---

## 13. What Phase 2 will need from this

Decisions to make now so Phase 2 does not require a rewrite:

- `trial_outcome` captures **every version**, not the current one. Outcome switching is detected by diffing versions against the publication, and the history cannot be recovered later.
- `paper.fulltext_status` and `paper.license` are populated at ingestion. Retrofitting licence data across a large corpus is painful.
- `finding.evidence` is JSONB and check-specific, so later checks with richer evidence need no schema change.
- `PaperContext` is the single object passed to every check. Phase 2 adds fields to it; the interface does not change.

Do not add `pgvector` yet, but leave the migration path clear.

---

## 14. Working notes

- Commit early and often; each check is its own commit with its fixtures.
- Log structured JSON from the start. Cost and latency instrumentation is not a Phase 3 concern.
- When a source's behaviour contradicts this brief, the source wins — flag the discrepancy rather than forcing the spec.
- If a check's precision cannot be made perfect, narrow its applicability gate rather than accepting false positives. A check that runs on 40% of papers and is never wrong is worth more than one that runs on all of them and cries wolf.
