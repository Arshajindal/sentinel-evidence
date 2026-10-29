# sentinel-evidence

Corpus-scale integrity auditing for biomedical literature. Sentinel checks published papers and registered clinical trials for problems a reader can verify: references that can't be found, citations of retracted work, reported statistics that don't add up, impossible means, and trials with no posted results.

Phase 1 (this release) has five deterministic checks over a 1,000-paper corpus of open-access randomised controlled trials. Every finding carries its evidence and says only what the evidence supports. An unmatched reference "could not be verified"; Sentinel never calls it fabricated. An overdue trial has "no results posted"; Sentinel never calls it non-compliant.

- Design and phase plan: [`docs/sentinel-project.md`](docs/sentinel-project.md)
- Decisions (ADRs), failures and current state: [`PROGRESS.md`](PROGRESS.md)

## Contents

- [Setup](#setup)
- [Running the pipeline](#running-the-pipeline)
- [The checks](#the-checks)
- [Results on the Phase 1 corpus](#results-on-the-phase-1-corpus)
- [Tests](#tests)
- [Data sources and licences](#data-sources-and-licences)

## Setup

**Needs:** Python 3.11+, PostgreSQL 15+ with the `pg_trgm` extension (shipped with Postgres; the first migration creates it, which works for a role that owns the database), and git.

1. **Database.** As a Postgres superuser:
   ```sql
   CREATE ROLE sentinel LOGIN PASSWORD 'choose-a-password';
   CREATE DATABASE sentinel OWNER sentinel;
   CREATE DATABASE sentinel_test OWNER sentinel;  -- disposable, for the test suite
   ```
2. **Install.**
   ```bash
   python -m venv .venv
   .venv/bin/pip install -e ".[dev]"        # Windows: .venv\Scripts\pip
   ```
3. **Configure.** Copy `.env.example` to `.env` and set at least:
   - `SENTINEL_DATABASE_URL`: your database and password.
   - `SENTINEL_CONTACT_EMAIL`: a real address. Crossref and OpenAlex use it for their polite pools.
   - `SENTINEL_OPENALEX_API_KEY`: optional, from openalex.org/signup. With a key, set `SENTINEL_OPENALEX_DAILY_BUDGET_USD=1.0`.
   - `SENTINEL_DEFAULT_CORPUS_LABEL`: the corpus label you'll select in step 5.
   - `TEST_DATABASE_URL`: the disposable `sentinel_test` database. The database tests downgrade it to empty.

   Every setting that can change a finding (thresholds, the corpus query, the seed) has no default, so a missing one stops startup (ADR-022). `.env.example` holds the values used for the published results. `.env` is gitignored.
4. **Schema and reference data.**
   ```bash
   alembic upgrade head
   git clone https://gitlab.com/crossref/retraction-watch-data data/retraction-watch-data
   ```
5. **Check it works.** Run `sentinel --help` and `pytest`.

## Running the pipeline

The commands run in order. Each one is resumable or idempotent, so a killed run can be restarted.

```bash
sentinel corpus select --n 1000 --label pmc-rct-2015-2024   # sample the corpus (query + seed recorded)
sentinel ingest europepmc --corpus pmc-rct-2015-2024        # metadata, reference lists, linked trials
sentinel ingest retraction-watch                            # ~73,000 retraction notices
sentinel extract stats --corpus pmc-rct-2015-2024           # reported test statistics from the full text
sentinel resolve --corpus pmc-rct-2015-2024                 # match references to known works
sentinel check --corpus pmc-rct-2015-2024                   # run C01-C05, record a check_run
sentinel stats --run <run-id>                               # flag rates, cascade ratios, stage times
sentinel export --run <run-id> --format csv --out out/findings.csv
sentinel report 10.1007/s11357-023-00725-5                  # one paper's report card (DOI, paper ID or NCT ID)
```

**Resuming:**
- **Harvesters** (`ingest europepmc`, `ingest retraction-watch`, `extract stats`) save a cursor with each batch. `--resume` continues from it.
- **`resolve`** picks up the references still `pending`.
- **OpenAlex budget:** OpenAlex charges $0.001 per search. When the day's budget runs out, `resolve` stops with exit code 3 and keeps finished batches; run it again the next day.
- **Lookups:** `ingest crossref|openalex|doi-handle --doi ...` and `ingest clinicaltrials --nct ...` fetch single records on demand.

**Timing:** each ingest, resolve and extract run is recorded in `stage_run`, and `sentinel stats` reports wall time per stage.

**A report card** is read from stored findings, so run `sentinel check` first. It takes about 5 seconds including Python start-up.

## The checks

Each check is a pure function of data already stored: no network calls while checking. Each produces one finding per problem, plus a summary per paper or trial, with evidence attached even when the result is clean. The version is recorded with every finding. Thresholds come from `.env.example`.

| Check | Question | Runs on | Flags at |
|---|---|---|---|
| C01 | Does each cited journal article exist? | paper references | medium |
| C02 | Does the paper cite retracted work, or work under an expression of concern? | paper references | high / medium / low |
| C03 | Do reported test statistics agree with their p-values? | paper body text | low (inconsistent) / high (crosses α) |
| C04 | Can each reported mean arise from whole-number data (GRIM)? | reported means | medium |
| C05 | Has a completed trial posted results on ClinicalTrials.gov? | registry records | medium |

### C01: Reference existence

References are resolved first (`sentinel resolve`), in stages: DOI, PMID, exact title, the same title but for a subtitle (with the same first author and year), similar title with the same first author and year, and very similar long title. Candidates are fetched from Crossref, with OpenAlex as a budgeted fallback. Two or more equally good candidates make a reference `ambiguous`, and nothing is picked.

C01 then classifies each reference from the recorded trail. It flags only a reference that:
- looks like a journal article,
- matched nothing in Crossref or OpenAlex,
- has no DOI that doi.org knows,
- and has no probable match under a variant title.

The finding reads "Reference N could not be verified: no matching record in Crossref or OpenAlex."

Not flagged:
- non-journal items: books, chapters, reports, guidelines, websites, theses, preprints, conference items, package inserts, organisation authors;
- references too incomplete to judge;
- ambiguous references.

**Known limits:**
- "Could not be verified" means *not indexed*, not *doesn't exist*. In the hand-checked fixture set, all six remaining C01 flags are real-looking works that neither index lists: regional journals, an old meeting abstract. C01 says what it can verify, and a reader has to look.
- **On the full corpus, most flags are works that do exist.** In 20 corpus flags sampled and searched by hand (seed 20261029), 8 could not be found anywhere and 12 exist in Crossref (precision 40%, 95% CI 22–61%). The causes of the 12:
  - Crossref's whole-string search didn't return the work, though a title-plus-author search does (2).
  - Books or rating scales not recognised as non-journal (3).
  - A typo or altered title in the citation (2).
  - A subtitle after a full stop rather than a colon (2).
  - A title cited in translation (1).
  - A record titled only "Letters" (1).
  - The online year cited two years before the print year (1).

  Read the corpus C01 rate as an upper bound.
- Non-journal detection uses the XML's publication type and text cues. It can only stop a flag, so a journal article tagged as a book is never checked. Cochrane reviews cited "In: Cochrane Database..." are also treated as non-journal.
- Matching depends on the source metadata. A Crossref record with the author's names swapped can't pass the first-author rule, and is caught only by the near-identical-title rule.
- References whose untagged text parses poorly (no title, author, year or journal) aren't judged. This happens to about 1 in 5 untagged references.
- No check yet looks for a DOI that resolves to a *differently titled* work. The title agreement is recorded for a later check.

### C02: Retraction and concern status

Each reference's DOI or PMID, or its resolved paper's, is matched against Retraction Watch. The band depends on when the citing paper appeared (its Europe PMC first publication date) relative to the earliest retraction notice:

| Situation | Severity | Wording |
|---|---|---|
| published more than 6 months after the retraction | high | cites a work retracted N months before publication |
| published 0–6 months after | low | cited while the paper was in press |
| published before the retraction | low | cited before retraction; downstream claims may need review |
| retraction marked "Retract and Replace" or "Withdrawn as Out of Date" | low | a current version exists and should be checked |
| expression of concern | medium | |
| reinstated after retraction, or a correction | not flagged | |

**Known limits:**
- Coverage is Retraction Watch's. A retraction it hasn't recorded is invisible, and matching needs a DOI or PMID. An unresolved reference without one isn't checked.
- Retraction Watch sometimes records an approximate date, often 1 January, with the reason "Date of Article and/or Notice Unknown". The band is computed from that date.
- The citing date is the publication date, not when the reference was written. The 6-month in-press window is a heuristic.

### C03: Statistical consistency

C03 is statcheck-style. It finds APA-style reports in the body text, such as `t(28) = 2.20, p = .036`, `F(1, 74) = 1.39, p = .41`, `χ²(3) = 5.6, p = .13`, `r(48) = .32, p = .02` and `z = 2.1, p = .04`. It then recomputes p from the statistic and degrees of freedom, across every value of the statistic that rounds to the one printed.

A reported p outside that range is **inconsistent** (low). If it is also on the other side of α = 0.05, it is **gross** (high). A one-tailed p is accepted only when the paper says the test was one-tailed (ADR-009). `p = .001` used as a floor and `p < .000` are treated as reporting conventions, not errors.

**Known limits:**
- Only test statistics written in the text are read. Tables, figures and supplements aren't. Most RCT reports give effect sizes with confidence intervals instead: 32 of 1,000 corpus papers report recheckable tests.
- Rank tests (Wilcoxon, Mann–Whitney, Kruskal–Wallis, Friedman, U/V/W/T/H) are skipped, because their z or p often comes from an exact test.
- A p-value truncated rather than rounded (0.0177 reported as 0.017) is flagged low.
- An inconsistency can come from a typo in the statistic, the df or the p-value. C03 can't tell which, and nothing here suggests intent.
- Corrections such as Greenhouse–Geisser or Welch are recognised only when the printed df already reflects them.

### C04: Impossible means (GRIM)

A mean of N whole-number values must equal some integer total divided by N. C04 tries the whole-number totals on both sides of mean × N (× items per participant). It flags a mean that no total produces at the printed precision.

It applies only when every one of these holds:
- the measure is known to be integer-valued;
- N is known and at most 100;
- the mean is printed to at least 2 decimals;
- N × items is below 10^decimals. Above that, every mean is possible.

**Known limits:**
- **Phase 1 extracts no means from papers**, so C04 is `not_applicable` on every corpus paper. Text rarely says whether a measure is integer-valued, and guessing would produce false flags.
- The logic is proven on constructed cases and a brute-force comparison. Table parsing in Phase 2 can feed it real means.

### C05: Results reporting

C05 runs on ClinicalTrials.gov records alone. It flags a trial when all of these hold:
- it is completed and interventional, and not Early Phase 1 or Phase 1;
- it is within FDAAA's plausible reach: it has a US site or an FDA-regulated product, and it started after 2007-09-27 or completed on or after 2007-12-26 (ADR-031);
- its primary completion date is more than 12 whole months before the record was fetched;
- it has no `results_first_posted` date.

The wording is "No results posted on ClinicalTrials.gov N months after primary completion." A missing primary completion date gives `insufficient_data`.

**Known limits:**
- Whether a trial was *legally required* to post depends on facts the registry doesn't show: whether it is an "applicable clinical trial", whether it has a certification to delay, and whether it has waivers. The scope rule is deliberately broad, keying on overall completion, so it can include trials whose primary completion predates FDAAA. Findings say only that no results are posted.
- Results posted elsewhere, such as the EU Clinical Trials Register or a journal, don't count.
- Findings are true as of the fetch date recorded in their evidence. To update one, re-fetch the trial.

## Results on the Phase 1 corpus

Check run 19, 2026-10-28: 1,000 papers sampled from Europe PMC (open-access randomised trials, 2015–2024, seed 20260922) and the 1,032 ClinicalTrials.gov records they link to. `sentinel stats --run 19` prints all of this.

| Check | Flagged | Of applicable | Rate | Flags | Hand-checked |
|---|---:|---:|---:|---|---|
| C01 reference existence | 195 papers | 1,000 | 19.5% | 272 references, medium | 20 sampled: 8 correct; read as an upper bound (see C01's limits) |
| C02 retraction status | 23 papers | 1,000 | 2.3% | 25: 8 high, 3 medium, 14 low | all 25: each matches the right notice |
| C03 statistical consistency | 7 papers | 33 | 21.2% | 16, all low; none gross | all 16 in S1.12 |
| C04 GRIM | 0 | 0 | – | – | not applicable: Phase 1 extracts no means |
| C05 results reporting | 69 trials | 320 | 21.6% | 69, medium | 10 sampled: all match the registry record |

Notes:
- Only 33 papers report a test statistic in a form C03 parses ("t(df) = x, p = y" and similar); most trial papers report effect sizes with confidence intervals instead.
- C05's run also covers two trials outside the corpus, looked up by hand in S1.4. One is flagged, so the run itself reports 70 of 321; the table gives the corpus figures.

**Resolution cascade (§9.5):**

| | References | Share |
|---|---:|---:|
| Resolved | 34,593 | 89.4% |
| Unresolved | 3,902 | 10.1% |
| Ambiguous | 190 | 0.5% |
| **All references** | **38,685** | |

| Resolved by | References | Share of resolved |
|---|---:|---:|
| DOI | 29,116 | 84.2% |
| Exact title | 4,609 | 13.3% |
| PMID | 462 | 1.3% |
| Similar title, same author and year | 353 | 1.0% |
| Title but for a subtitle | 41 | 0.1% |
| Title alone | 12 | <0.1% |

Of the 5,015 resolved by title, the match came from Crossref search for 3,846, from works already stored for 659, and from OpenAlex for 510. On the §9.10 sample of 1,000 references from structured reference lists, 94.4% resolve.

**Time and cost:**

| Step | Time | Notes |
|---|---|---|
| Resolve, full corpus | 2 h 41 min | 35,670 references over four runs (runs 11–14, two days); $1.71 of OpenAlex searches |
| Check run, all five checks | 6 s | 3.9 s of it loading contexts |
| Retraction Watch ingest | 58 s | 68,283 rows |
| `sentinel report <doi>` | 4.6–4.8 s | Mostly start-up imports; the card itself takes about 0.3 s |

Over the four resolve runs, run 11 was stopped by a dropped Crossref connection, which isn't retried, and run 12 by the OpenAlex daily budget; each re-run picked up where it stopped. `stats` counts items only for runs that finished, so it shows fewer than 35,670. The corpus's Europe PMC harvest ran before stage timing existed, so it has no recorded time.

## Tests

`pytest` runs everything with no network: fixtures are saved API responses and frozen database contexts.

- **The fixture set** (`tests/fixtures/set/`, ADR-040): 46 real papers and trials with hand-checked expected findings. All five checks must reproduce them exactly: check, status, severity, and which reference or statistic. It includes:
  - citations of retracted work in every timing band;
  - hand-verified statistical inconsistencies;
  - papers whose books and reports must not be flagged;
  - clean papers, which must produce zero flags;
  - overdue, posted and out-of-scope trials;
  - edge cases;
  - every false positive found during development.
- **Rule tests:**
  - no personal field survives into a stored trial record (ADR-018);
  - removing any required setting stops startup (ADR-022);
  - migrations run up, down and up against a real Postgres.
- **Database tests** need `TEST_DATABASE_URL`, and skip without it.

## Data sources and licences

| Source | Used for | Terms |
|---|---|---|
| Europe PMC (REST, Annotations) | corpus, metadata, full-text reference lists, trial links | each article's own licence; Sentinel keeps reference lists and statistics, not article text |
| Crossref | DOI metadata, candidate search | metadata, no reuse restriction (abstracts are dropped) |
| OpenAlex | DOI metadata, candidate search (budgeted) | CC0 |
| doi.org handle API | does a DOI exist at any agency | public |
| Retraction Watch (via Crossref) | retraction and concern notices | Crossref metadata: free to reuse without a licence; Crossref asks for a citation of the source (see `tests/fixtures/README.md`). Cite as: Retraction Watch Database, Crossref |
| ClinicalTrials.gov API v2 | trial records | US government data; personal names and contacts are dropped before storage (ADR-018, ADR-025) |

Code: MIT (see `LICENSE`).
