# Sentinel — Progress Log

**Location:** repository root. Committed with the code. Never in `.gitignore`.
**Purpose:** what was built, why it was built that way, and where it lives.

This file exists for three readers: you in week twelve trying to remember why you rejected something in week three; Claude Code picking up context in a new session; and an interviewer asking "walk me through a decision you made."

---

## How to update this

Low friction or it rots. The rules:

| Section | When to update | How |
|---|---|---|
| Current state | Start of each work session | Rewrite in place. The only section that gets overwritten. |
| Decision log | When you choose between real alternatives | Append a numbered entry. Never edit an old one — supersede it. |
| Model registry | When a model is added, swapped, or benchmarked | Edit the row. Keep replaced models in the graveyard below. |
| Experiment log | When you get a number | Append. Even, especially, bad numbers. |
| Metrics ledger | End of each phase | Append a dated row. |
| Failure log | When something breaks or surprises you | Append immediately, while you still remember the detail. |
| Open questions | Whenever one appears or resolves | Edit freely. |

**Two-minute rule:** if an entry takes longer than two minutes to write, it's too detailed. Write the short version now; expand later only if it matters.

**The failure log is the highest-value section.** It's the rarest portfolio deliverable and the hardest to reconstruct after the fact. Write to it the day something goes wrong.

---

## Current state

> Rewrite this section each session. Keep it short.

**Phase:** 1 of 5 — data foundation and deterministic checks
**Plan:** `docs/sentinel-project.md` §15 session IDs. Old brief and HTML spec are in `docs/archive/`.
**Last worked:** 2026-10-25 — S1.9 (resolution cascade and candidate fetch)

**Sessions:**

| Session | Status | Evidence / what's left |
|---|---|---|
| S1.1 Scaffold | done | Config loads; `tests/test_config.py` covers ADR-022 (each required variable, hash scope, `.env.example` ↔ `Settings` in sync); `alembic current` runs cleanly. |
| S1.2 Models + initial migration | done | `c9051bc4f780` plus `157fbb99af3c` (finding is about a paper or a trial, §8). `tests/test_migrations.py` runs up, down, up and checks the trigram index; `alembic check` shows no drift. |
| S1.3 Harvester base + Retraction Watch | done | `ingest/base.py`, `ingest/retraction_watch.py`, migration `17fb3d1f4207` (ADR-024). `sentinel ingest retraction-watch` loaded 73,283 notices in ~12 s; a re-run inserted 0. The 20-row fixture covers load, re-run, update in place and kill-and-resume. |
| S1.4 ClinicalTrials.gov lookup client | done | `ingest/clinicaltrials.py`, `ingest/http.py` (token bucket; retries on 429/5xx only), `LookupClient` base. `sentinel ingest clinicaltrials --nct …` stored NCT02016378 and NCT05711641 with version-0 outcomes; ADR-018 test passes on real and synthetic records (ADR-025, ADR-026). |
| S1.5 Check base, contexts, C05, report card | done | `checks/` (base, context, C05, registry, runner), `report/card.py`, `sentinel check` and `sentinel report`. Live: `sentinel report NCT02016378` prints "No results posted on ClinicalTrials.gov 45 months after primary completion" (medium); NCT05711641 is clean. Fixture pair passes (ADR-027). **Vertical slice complete.** |
| S1.6 Corpus selection + Europe PMC harvester | done | Corpus `pmc-rct-2015-2024`: 1,000 of 30,457 hits, seed 20260922, selected 2026-10-24 (ADR-028). 1,000 papers, 38,685 references from full-text `<ref-list>` (ADR-030), 1,051 own-trial links from 926 papers to 1,032 trials, all fetched. Migration `9780a018baa5` (ADR-029). |
| S1.7 Crossref, OpenAlex, doi.org clients | done | `ingest/crossref.py`, `ingest/openalex.py` (budgeted, ADR-032), `ingest/doi_handle.py`, `ingest/papers.py` (first source owns the row). `sentinel ingest crossref\|openalex\|doi-handle --doi …` checked live; rate limiting, retries and the budget guard tested against recorded responses. |
| S1.8 Reference parser | done | `parse/references.py` (ADR-033). 50 hand-checked strings: 0 wrong or invented, 88.2% filled. On the corpus's 22,019 untagged strings: first author 92.6%, year 97.3%, title 78.9%. Not yet written to `reference`; S1.9 applies it. |
| S1.9 Resolution cascade + candidate fetch | done | `resolve/cascade.py`, `resolve/candidates.py`, `sentinel resolve`, migration `80034d5e56ce`, parser 1.1.0 (ADR-034). §9.10 sample (structured, seed 20260922): 944/1,000 resolved (94.4%); all-reference sample: 88.7%. No wrong title-stage match found. 1,786 references processed so far (1,637 resolved); the other 36,899 resolve in S1.15. |
| S1.10 – S1.15 | not started | |

**What works:** corpus selection and harvest; DOI lookups; parsing and resolution (`sentinel resolve`); `sentinel check` / `sentinel report`. C05 v1.1.0 flags 70 of 1,034 corpus trials. 348 tests: 334 run without a database, and the other 14 need `TEST_DATABASE_URL`.

**What's broken:** nothing known. PMC6561645's full text returns a persistent HTTP 500 upstream, so it's stored without references (a re-run retries it). The DB tests skip on the dev DB now that it holds real data; a disposable `sentinel_test` database is still to be created.

**Next three things:**
1. S1.10: C01. Classify unresolved references (non-journal / unparseable / journal-like), ask doi.org before flagging, and surface low title agreement on DOI matches
2. S1.11: C02
3. S1.12: Statistic extraction and C03

**Then:** S1.13 onward. Target: `sentinel report <nct-id>` prints a real overdue-results finding.

**Blocked on:** nothing.

---

## Decision log

Append-only. Each entry: what was decided, why, what was rejected, and whether it still holds.

Status values: `active` · `superseded by ADR-NNN` · `revisit at <milestone>`

---

### ADR-001 — Citation and evidence integrity, not horizon scanning
**Status:** active

Considered two projects: a convergence horizon scanner detecting emerging research fields, and an evidence integrity engine verifying citations and claims.

Chose integrity. The horizon scanner depended on expert curation decisions as training labels, and those labels turned out not to exist — the filtering happened before anything was recorded. Integrity needs no proprietary labels: ground truth is constructible from public data plus programmatic corruption.

**Rejected:** horizon scanning as the primary product. Retained as an optional later phase, since the corpus and infrastructure support it.

---

### ADR-002 — Corpus audit, not single-paper lookup
**Status:** active

A single-paper tool never forces the problems the project exists to demonstrate: throughput, batching, concurrency, cost per unit, caching, partial failure, retry semantics. Those appear at 400,000 verifications, not 40.

A single-paper API endpoint is retained for demonstration, built on the corpus backend.

---

### ADR-003 — PostgreSQL with pgvector, not a managed vector database
**Status:** active

One system for relational data, full-text search, and embeddings. Hybrid retrieval becomes a join rather than a distributed query. No second system to operate.

**Rejected:** Pinecone, Qdrant, Weaviate. They win on multi-tenancy, billion-scale, and outsourced operations — none of which apply here. Be able to state this in an interview; the reasoning matters more than the choice.

---

### ADR-004 — SQLAlchemy Core, not the ORM
**Status:** active

The workload is bulk ingestion and analytical queries, not object graphs. Core gives direct control over batch upserts and avoids surprise N+1 behaviour in checks. Alembic manages schema; `create_all` is never called.

---

### ADR-005 — Bulk snapshots over API pagination
**Status:** active

Ingest Crossref and OpenAlex from bulk distributions rather than paginating their APIs. Once local, existence checking is a table join instead of a network call, and throughput stops being governed by someone else's rate limiter.

Highest-leverage decision in Phase 1. Doing it later means re-architecting the ingest layer.

**Cost:** larger local storage, staler data between refreshes. Both acceptable.

---

### ADR-006 — Deterministic tier first, zero ML in Phase 1
**Status:** active

The five deterministic checks are arithmetic and lookups. They ship real findings in week two, cost nothing to run, have near-perfect precision, and serve as the baseline every later model is measured against.

They are also insurance: if the ML tiers underperform, the project still has a working product.

**Rejected:** starting with the interesting modelling work. Reaching for a transformer to check whether a p-value matches its test statistic is a judgment error, and an interviewer will notice.

---

### ADR-007 — Unresolved is not fabricated
**Status:** active

A reference can fail resolution because it's a book, a thesis, a conference abstract, a non-indexed regional journal, a personal communication, or badly formatted.

Three-way classification: `unresolved_nonjournal` (not a finding), `unresolved_unparseable` (`insufficient_data`), `unresolved_journal_like` (the only flag, at `medium`, worded "could not be verified").

The published prevalence studies were careful about this. Overclaiming here destroys credibility faster than any other error the system can make.

---

### ADR-008 — Capture every version of trial outcomes
**Status:** active

ClinicalTrials.gov keeps a change history. Outcome switching in Phase 2 is detected by diffing registry versions against the publication.

Storing only the current version makes the history unrecoverable and forces a full re-ingest later. Cheap now, expensive to retrofit.

---

### ADR-009 — Test the one-tailed alternative before flagging a p-value
**Status:** active

Many papers report one-tailed tests without saying so. If halving the computed p-value reconciles it with the reported value, record `one_tailed_ok = true` and do not flag.

Without this, the statistical check produces a wall of false positives on day one.

---

### ADR-010 — Precision over recall as the operating priority
**Status:** active

At a 1% flag rate over 420,000 citations, humans receive 4,200 items. If a third are false positives, the tool is abandoned within a month.

This inverts the usual ML priority. Tune thresholds for precision, accept missed cases, and state the tradeoff explicitly in the output.

**Corollary:** where a check's precision can't be made near-perfect, narrow its applicability gate rather than accepting false positives. A check running on 40% of papers and never wrong beats one running on all of them and crying wolf.

---

### ADR-011 — LangGraph for orchestration
**Status:** active

Needs a state machine with Postgres checkpointing and a human-interrupt node. A run must survive dying at citation 180,000 and resume there rather than restarting.

**Rejected:** hand-rolled loops (don't survive long-running work), CrewAI (weaker checkpointing), Temporal (heavier than a solo build justifies).

---

### ADR-012 — Langfuse self-hosted, not LangSmith
**Status:** active

Full tracing without sending prompts to a third party. Mention both on the resume; run Langfuse.

---

### ADR-013 — Three independent ground-truth sources
**Status:** active

Programmatic corruption of verified true pairs, published annotated corpora, and a hand-labelled holdout selected by active learning.

Three independent sources is unusual for a portfolio project and is the strongest single argument for this design over alternatives.

---

### ADR-014 — Always report synthetic and real numbers together
**Status:** active

Published work saw a verification pipeline drop from 97% on generated benchmarks to 90% on real cases, with one frontier model collapsing from 96% to 33% F1.

Reporting only the favourable number is the tell of an unserious system. The gap itself is a finding worth analysing.

---

### ADR-015 — Findings carry evidence even when clean
**Status:** active

`finding.evidence` is populated for `clean` results too. A check reporting "clean" without showing what it examined is unauditable.

`finding.check_ver` exists so an improved check produces comparable rows rather than silently overwriting history.

---

### ADR-016 — Keeping the name Sentinel
**Status:** active

Known tradeoff, recorded so it isn't relitigated: "sentinel surveillance" is an established epidemiology term meaning monitored subset sites, and Microsoft Sentinel, HashiCorp Sentinel, and the Sentinel satellites all exist. Discoverability is therefore poor.

Accepted anyway. Renaming costs ten minutes at week six; losing week six to naming costs a week.

**Revisit:** only if a package or repo name collision forces it.

---

### ADR-017 — Grace window before blaming a paper for citing retracted work
**Status:** active

Papers sit in press for six to twelve months. Flagging a paper `high` for citing something retracted three weeks before its publication date blames an author for something they could not have known.

Three severity bands instead of two, governed by `SENTINEL_C02_GRACE_MONTHS` (default 6). Cited more than the grace period after the notice → `high`. Inside the window → `low`, framed as "retracted while in press". Before the notice → `low`. Unknown publication date → `insufficient_data`.

Follows from ADR-010: this trades recall for precision, which is the correct direction at this tier. It also moves the headline flag rate, so the window value belongs in `config_hash` and must be stated wherever prevalence is reported.

**Revisit:** if measured in-press lag on the chosen corpus differs materially from six months.

---

### ADR-018 — Strip personal contact data from trial records at ingestion
**Status:** active

ClinicalTrials.gov study records include named individuals and their contact details: `centralContacts`, `overallOfficials`, and per-location `contacts`. That data is published so prospective participants can reach someone about enrolling. Bulk-ingesting it into a database is a different use, and not one anyone consented to.

`trial.raw` is a JSONB column, so the default behaviour is to store the whole payload including those fields. Drop them during `normalise()`, before anything is written. Retain institutional fields only: sponsor name and class, country-level locations, dates, status, enrolment, outcomes.

Sentinel's stated position is that it touches no personal data. Storing trial coordinators' phone numbers in a public repository contradicts that, and someone will notice.

Governed by `SENTINEL_STRIP_PERSONAL_FIELDS` and `SENTINEL_TRIAL_DROP_PATHS`. The flag exists to make the behaviour visible in config, not because turning it off is a supported option.

**Test requirement:** a fixture asserting that no dropped path survives into `trial.raw`. Run it on every ingest change.

---

### ADR-019 — Bulk download for the initial ClinicalTrials.gov load
**Status:** active

ClinicalTrials.gov publishes every study as one zip at `/api/v2/studies/download?format=json.zip`, using the same JSON schema as the per-study endpoint. This satisfies ADR-005 with no pagination logic for the initial load.

The paginated `/studies` endpoint is retained for incremental refresh. It uses a `pageToken` cursor with a 1,000-row page cap, which maps directly onto the watermark design in the harvester contract.

`/api/v2/version` returns a `dataTimestamp`. Check it before each refresh run to confirm the daily rebuild has completed.

An OpenAPI spec is published at `/api/oas/v2/ctg-oas-v2.yaml`. Generate or validate the trial models against it rather than hand-writing them.

---

### ADR-020 — Amends ADR-005: API resolution, not bulk, at current scale
**Status:** active · amends ADR-005

ADR-005's reasoning holds — don't let someone else's rate limiter govern throughput at scale. The error was assuming this project is at that scale.

The working corpus is 1,000 papers, roughly 40,000 references. At OpenAlex's polite-pool rate of ~10 rps that resolves in about an hour. The 10,000-paper validation run is ~400,000 references, an overnight job. Downloading hundreds of gigabytes to avoid that is the wrong trade.

Revised split:

| Source | Approach | Footprint |
|---|---|---|
| Retraction Watch | Bulk CSV | ~50 MB |
| ClinicalTrials.gov | Bulk zip (ADR-019) | a few GB |
| Crossref, OpenAlex | API only | none stored |
| PMC full text | Fetch corpus only | a few GB |

Total under ~10 GB, so disk ceased to be a design constraint.

The harvester contract is unchanged — resumable, watermarked, rate-limited. Those matter *more* for API ingestion than bulk, because that is where a run dies mid-stream.

**Revisit at:** roughly 50,000 papers, or if OpenAlex daily quota becomes binding.

---

### ADR-021 — Starting corpus: PMC open access, NCT-linked, n=1,000
**Status:** active

Working corpus: 1,000 papers from the PubMed Central open-access subset that carry an NCT number, any disease area.

The NCT filter does the work. Phase 1's C05 needs registry records; Phase 2's outcome switching needs papers linked to registered trials. One corpus serves both with no reload. Trial reports are also structured, which eases statistics extraction while the parser is still immature.

**Rejected:** narrowing by therapeutic area. Adds a filter step without materially improving coherence or reviewability at this size.

**Known consequence:** C04 (GRIM) will return `not_applicable` for nearly all of this corpus. GRIM needs integer-scale measures with small N — psychology territory, not clinical trials reporting continuous outcomes in hundreds of patients. C04 is proven by fixtures, not by the working corpus. This is expected, not a defect.

Validation corpus, week 13: a random sample of ~10,000 PMC open-access papers across all fields. This is the set compared against published prevalence estimates, because it is a comparable sample. The working corpus is not, and must never be used for that comparison.

---

### ADR-022 — Defaults for operational settings, none for anything that changes findings
**Status:** active

A hidden default is a setting nobody chose. If a check threshold silently defaults, results depend on a value never reviewed, and `config_hash` is built on invisible inputs. But requiring all forty variables means a fresh clone will not start, which makes testing miserable.

The rule: **if it changes the findings, it must be stated explicitly.**

*Defaults permitted* — batch sizes, timeouts, pool size, max connections, retry counts, backoff, concurrency, log level and format, export directory, corpus label. Changing these alters speed, not answers.

*No defaults, startup fails if unset* — `DATABASE_URL`, `CONTACT_EMAIL`, `USER_AGENT`, `RANDOM_SEED`, `CHECKS_ENABLED`, every `C0N_*` check parameter, every `RESOLVE_*` threshold, and `STRIP_PERSONAL_FIELDS`.

`config.py` implements this by declaring the second group with no default in the pydantic-settings model, so pydantic raises on startup. `config_hash` is computed over the second group only — operational settings do not change run identity.

**Test requirement:** a test asserting that removing any required variable causes startup to fail. Without it this rule decays the first time someone adds a convenience default.

---

### ADR-023 — Amends ADR-019: ClinicalTrials.gov fetched per study by default
**Status:** active · amends ADR-019

ADR-019 made the bulk zip the initial load. The zip holds every study in the registry, around 600,000 records. The working corpus needs roughly 1,000 linked trials (ADR-021). Downloading and parsing the whole registry to use a fraction of a percent of it is the same mistake ADR-020 corrected for Crossref and OpenAlex.

Default: fetch one study at a time from `/api/v2/studies/{nctId}` through a lookup client, upserting into `trial` and `trial_outcome` and keeping the raw response (ADR-018 drop paths applied first). The bulk zip is kept for the 10,000-paper run (ADR-021) or registry-wide statistics. The rest of ADR-019 stands: same JSON schema, `/api/v2/version` before refreshes, models checked against the OpenAPI spec.

**Rejected:** bulk zip for the 1,000-paper corpus. A few GB and a registry-wide parse buys nothing at this size.

**Revisit at:** the 10,000-paper run, or if per-study fetching becomes the bottleneck.

---

### ADR-024 — Retraction notices keyed on the source's record ID
**Status:** active

Idempotent upserts need a natural key, and §8's `retraction_notice` had none. In the 2026-10-16 Retraction Watch CSV, `(retracted DOI, notice DOI, type)` collides 3,766 times across 73,283 rows, while `Record ID` is unique in every row. Added `source_record_id` with `UNIQUE (source, source_record_id)`, nullable so a future source without IDs can still write rows.

The same data has 163 `Reinstatement` rows, a type §8 didn't list. They're stored as `notice_type = 'reinstatement'` so C02 can avoid calling a reinstated paper retracted. Author and Institution are left out of `raw`: C02 matches on DOI and PMID only, so they aren't needed.

**Rejected:** a unique index on `raw->>'Record ID'` (key hidden in JSONB), and deduplicating in code (nothing in the database prevents duplicates).

---

### ADR-025 — Amends ADR-018: two more personal-data paths in trial records
**Status:** active · amends ADR-018

ADR-018's three drop paths miss two places the live v2 schema puts named people: `protocolSection.sponsorCollaboratorsModule.responsibleParty` (`investigatorFullName`, title, affiliation when a PI is responsible) and `resultsSection.moreInfoModule.pointOfContact` (name or title, email, phone). Real records carry personal email addresses there. Both objects are now dropped whole.

`responsibleParty.type` (sponsor or PI) goes with it. Nothing in Phases 1–3 needs it, and per-field paths would be easier to get wrong.

The client refuses to run if `SENTINEL_STRIP_PERSONAL_FIELDS` is false, instead of storing the data.

**Rejected:** dropping only the personal subfields of `responsibleParty`; leaving ADR-018 as written.

---

### ADR-026 — Month-only registry dates resolve to the last day
**Status:** active

ClinicalTrials.gov dates are often `YYYY-MM` (sometimes `YYYY`), but `trial` stores DATE columns and C05 counts months from `primary_completion_date`. Partial dates resolve to the latest day they could mean: `2012-10` becomes 2012-10-31, and `2019` becomes 2019-12-31. A date-based check can therefore never fire early (ADR-010). The original string stays in `trial.raw`.

**Rejected:** the first day of the month (a trial can look up to a month more overdue than it is); NULL (C05 loses many older trials to `insufficient_data`).

---

### ADR-027 — C05 measures time to the record's fetch date, not today
**Status:** active

Checks are pure (§9.6), so C05 can't read the clock. It counts whole months from `primary_completion_date` to the day the registry record was fetched (`trial.ingested_at`). That is also the last time `results_first_posted` was observed, so the finding is true as of the data it rests on. Evidence records `record_fetched_on`. To bring a finding up to date, re-fetch the trial.

Scope as implemented: completed, interventional trials whose phases aren't all Early Phase 1 / Phase 1 (Phase 1/2 and NA are in scope). A different status, or a non-interventional study, gives `not_applicable`. A missing phase or primary completion date gives `insufficient_data`.

**Rejected:** today's date (a stale record could flag a trial that has since posted results, and re-running the same data would change findings); the run's start date (same problem).

---

### ADR-028 — Narrows ADR-021: the corpus is 2015–2024 RCTs; links only a paper's own trials
**Status:** active · narrows ADR-021

Q3 resolved. The working corpus query is:

`ACCESSION_TYPE:nct AND OPEN_ACCESS:y AND IN_PMC:y AND HAS_REFLIST:y AND PUB_YEAR:[2015 TO 2024] AND PUB_TYPE:"Randomized Controlled Trial"`

It is held in `SENTINEL_CORPUS_QUERY`, with no default. Selected 2026-10-24: 30,457 hits, 1,000 sampled with seed 20260922 after sorting by PMCID, label `pmc-rct-2015-2024`.

ADR-021 said "any paper carrying an NCT number". Without the RCT filter about 28% of hits are reviews, and among research articles 31% only mention other trials. Among MEDLINE-tagged RCTs, 97/100 name their own NCT in the Title, Abstract or Methods.

Trial links come from Europe PMC's text-mined accession annotations. An NCT is linked (confidence 1.0) only when it is mentioned in the Title, Abstract or Methods. Mentions only in the Introduction, Discussion, Tables or unclassified text are not linked, because they usually cite someone else's trial.

**Rejected:** 2015–2024 research articles (142,775 hits; weaker links); all years and types (287,155 hits; reviews and future issue years); linking every mention scored by section.

---

### ADR-029 — Corpus membership tables and reference keys
**Status:** active

§8 had no way to say which `paper` rows form a corpus (cited and candidate works will share the table) and nowhere to record how a corpus was chosen. Added `corpus` (label, source, query, seed, hit_count, n, selected_at) and `corpus_paper` (label, ordinal, source IDs, paper_id). Members are chosen before they're harvested, so `paper_id` fills in later.

`reference` gains `UNIQUE (citing_paper_id, ordinal)`, so re-harvesting doesn't duplicate (the same reasoning as ADR-024). It also gains `parsed_pmid`, which cascade stage 2 needs, and `raw` JSONB for the source entry. Migration `9780a018baa5`.

---

### ADR-030 — Reference lists come from the full-text XML, not the references API
**Status:** active

Europe PMC's `/{source}/{id}/references` returns only references Europe PMC has matched (PMC11662519: 49 returned against 54 in the paper). The unmatched ones are exactly what C01 examines, and resolution rates would be inflated by construction.

Each corpus paper's `<ref-list>` is read from `/{PMCID}/fullTextXML` instead, which is open access and costs one request per paper as before. Fields are taken only where the XML marks them up (`element-citation`). Plain `mixed-citation` entries keep only `raw_string` for the S1.8 parser. The body is neither parsed nor stored, which stays Phase 2. XML is parsed with no DTDs, entities or network access.

**Rejected:** the references API with the gap documented (C01 would be meaningless); merging both sources (fiddly alignment, double the requests).

---

### ADR-031 — C05 covers only trials within FDAAA's plausible reach
**Status:** active

C05 v1.0.0 flagged trials whose primary completion was as early as 2004, before ClinicalTrials.gov's results requirements existed. FDAAA 801 covers trials initiated after 2007-09-27 or still ongoing on 2007-12-26 (ClinicalTrials.gov FAQ; Anderson et al., NEJM 2015). The statute and eCFR text couldn't be fetched automatically, so these are the agreeing secondary statements.

C05 v1.1.0 applies only when `start > 2007-09-27` or `completion >= 2007-12-26`. Otherwise the finding is `not_applicable` (`before_fdaaa`). With neither date recorded, it is `insufficient_data`. For this gate, partial dates are read at their earliest possible day from the registry's own strings, so a trial counts as covered only if every reading of its dates says so. ADR-026's latest-day rule still applies to the grace-window arithmetic. Both directions favour precision.

The era gate alone moved only 10 trials (flagged 467 → 461). The real source of the 45% flag rate was reach: only 66 of the 461 flagged trials had a US site, and the registry marked just 6 as FDA-regulated. C05 therefore also requires a US site, or `oversightModule.isFdaRegulatedDrug`/`isFdaRegulatedDevice` true. Otherwise the finding is `not_applicable` (`outside_fdaaa_scope`). A trial with neither never had a duty to post on ClinicalTrials.gov.

Effect on `pmc-rct-2015-2024`, run 7 (v1.0.0) vs run 9 (v1.1.0): flagged 467 → 70. 478 trials are `outside_fdaaa_scope` and 10 are `before_fdaaa`. 21.8% of in-scope trials have no results after the grace window. Q8 resolved.

**Rejected (era and reach):** for the era, primary completion ≥ 2007-12-26 (drops covered trials that were still ongoing) and the Final Rule date 2017-01-18 (far narrower than the statute). 2008-01-18 was proposed first and was wrong: it isn't an FDAAA date. For reach: keeping non-US trials at `low` severity (the claim would still imply a duty that doesn't exist), and leaving it for later.

---

### ADR-032 — Amends ADR-020: OpenAlex is metered; Crossref is the primary candidate source
**Status:** active · amends ADR-020

ADR-020 assumed OpenAlex's free polite pool, at about 10 rps with no daily cap. Live on 2026-10-25, OpenAlex meters usage in dollars: about $0.10 a day without a key and $1 with a free one (openalex.org/signup). DOI lookups cost $0 and searches $0.001, so a free key allows roughly 1,000 searches a day. `mailto` no longer changes anything. Crossref is unchanged and free: with `mailto`, the polite pool gives 10 rps and 3 concurrent requests.

Decisions:
- Crossref's `query.bibliographic` search is the primary candidate source for S1.9. OpenAlex title search is a fallback within a daily budget (`SENTINEL_OPENALEX_DAILY_BUDGET_USD`; operational).
- OpenAlex DOI lookups stay unlimited. Each response's `X-RateLimit-Cost-USD` and `-Remaining-USD` are recorded. A search raises `OpenAlexBudgetExhausted` instead of returning nothing, so an exhausted budget can never make a reference look unresolvable.
- The OpenAlex key goes in the `Authorization` header, never in a URL that could be logged.
- Works from lookups are stored by DOI, PMID or PMCID, and the first source owns the row. A later source reuses it without overwriting, and conflicting identifiers are never merged (`ingest/papers.py`).

**Rejected:** dropping OpenAlex (loses a second metadata source); using OpenAlex as an equal search source with no guard (S1.9 would hit the cap within minutes).

---

### ADR-033 — Reference parser: located-or-null, measured against tagged citations
**Status:** active

`parse/references.py` (parser 1.0.0) follows §9.4 with one stricter rule: every field is either located by a clear rule or left None.
- The year must fall between 1900 and the citing paper's year + 1, which is passed in, never read from the clock. Years inside DOIs, URLs and access dates are ignored. A citation-position year wins, a range takes its first year, and conflicting candidates give None.
- Title and container are produced only when both the author list and the citation details are located.
- Glued DOIs, organisation "surnames" and author-ish titles give None.

One change from §9.4's wording: the title is everything between the authors and the container, not the longest segment. Subtitles and abbreviations like "vs." contain ". " too, and the tag-derived truth keeps them.

Measured on 7,627 `mixed-citation` strings whose XML tags give the truth: precision 99.2% (first author), 100% (year), 99.3% (title), 99.6% (container). On the 22,019 untagged strings it is built for, it fills first author 92.6%, year 97.3% and title 78.9%. The 50 hand-checked strings: 0 wrong or invented, 88.2% of true fields filled.

**Rejected:** an ML or CRF parser (§9.4 says rule-based); the "longest segment" title rule (truncates subtitles).

---

### ADR-034 — Resolution cascade: stages, precision guards, evidence, and how the target is measured
**Status:** active

`resolve/cascade.py` implements §9.5's five stages. Missing works are fetched: a DOI via Crossref then OpenAlex, a PMID via OpenAlex (both free), and titles via a Crossref bibliographic search, then OpenAlex search within budget (ADR-032). Ties at any stage give `ambiguous`. An exhausted OpenAlex budget stops the run; it never marks a reference unresolved.

Precision guards beyond §9.5 (both cost only recall):
- At stages 3 and 5, a candidate whose year (beyond ±tolerance) or first author contradicts the reference is rejected. Authors are compared by shared name tokens, so "al shahi salman" and "salman" are compatible.
- Stage 5 needs a title of at least 4 words.

Evidence: new column `reference.resolution_evidence` (migration `80034d5e56ce`) holds stages tried, near-misses, lookups, the parser version, and for DOI/PMID matches the agreement between cited and matched titles. A low agreement means the cited identifier names a differently titled work; it's left for C01, and the identifier match stands. Untitled records a source returns are not stored, but are recorded as "registered". Searches a service rejects (4xx) are recorded and skipped.

Parser 1.1.0 (amends ADR-033's version) adds "et al" glued to initials, Harvard "Author, 2017. Title", author lists ending in ":", hyphenated initials, and APA with no container. Tagged precision is unchanged (title 99.2%, container 99.6%). Untagged references whose fields came from an older parser version are re-parsed; tag-derived fields never are.

**How the ≥90% target is measured:** §9.10 defines it on references from structured reference lists (`sentinel resolve --sample 1000 --structured`). Seeded sample (seed 20260922), 2026-10-25: **944/1,000 resolved (94.4%)**, 1 ambiguous, 55 unresolved. Methods: DOI 897, PMID 29, title exact 15, title+author+year 3, title only 1. For context, a sample of 1,000 drawn from all reference types resolves **88.7%**: unstructured strings resolve 85.7%, and at least 68 of its 110 misses are grey literature (guidelines, agency reports, websites, books).

Precision: no checkable title-stage match was wrong. The three "disagreements" were references whose tagged DOIs are malformed in the source XML. A random read of title matches found none wrong.

**Rejected:** judging on the all-reference sample (it measures grey-literature share as much as resolution); relaxing the author guard to win back about 2 references with odd Crossref author metadata.

---

### ADR-0NN — template
**Status:** active

*Context in one or two sentences. What decision was made. Why. What was rejected and why not.*

---

## Model registry

Every model in the system: what it does, where it runs, why it was chosen, and what it scored.

| Role | Model | Where | Why chosen | Key metric | Status |
|---|---|---|---|---|---|
| Dense retrieval | *TBD — MedCPT vs SPECTER2 vs general* | TEI container | Pending bake-off, week 4 | nDCG@10 | not selected |
| Reranker | *TBD — bge-reranker-v2-m3 vs MedCPT cross-encoder* | TEI container | Pending bake-off, week 5 | nDCG@10 delta | not selected |
| Claim NER | *TBD — GLiNER-biomed vs fine-tuned encoder* | local | Pending, week 6 | entity F1 | not selected |
| Outcome equivalence | *TBD* | local | Pending, week 7 | macro F1 | not selected |
| Entailment, zero-shot | *TBD — NLI head* | local | Baseline, week 7 | macro F1 | not selected |
| Entailment, fine-tuned | *TBD* | local | Week 8 | macro F1 | not selected |
| Judge | *API model* | LiteLLM router | Distillation teacher, week 8 | macro F1, $/1k | not selected |
| Distilled verdict | *derived from judge* | local | Week 10 | F1 retained, cost ratio | not built |
| Tabular baseline | Gradient boosting | local | Honest ablation | PR-AUC | not built |
| Vision, figures | *TBD — VLM* | API | Week 13 | numeric extraction accuracy | not selected |

**Selection criteria, decided in advance so results aren't rationalised after the fact:**
- Retrieval and reranking: nDCG@10 on the held-out query set. Ties broken by latency.
- Classifiers: macro F1 on the real holdout, not synthetic. Ties broken by calibration error.
- Judge: macro F1 first, cost second. It's the teacher, so quality dominates.
- Distilled: retain ≥85% of teacher F1. Below that, the distillation failed and gets reported as such.

### Where things live

| Artifact | Path or identifier |
|---|---|
| MLflow tracking | *TBD* |
| Model registry | MLflow, stage-tagged |
| Fixture corpus | `tests/fixtures/` |
| Synthetic eval set | *TBD* |
| Hand-labelled holdout | *TBD* |
| Eval reports | `docs/evals/` |
| Config | `.env`, `src/sentinel/config.py` |

### Graveyard

*Models tried and dropped. Keep them — "I tried X and it underperformed by Y" is a stronger interview answer than never having tried.*

| Model | Role | Why dropped | Number |
|---|---|---|---|
| — | — | — | — |

---

## Experiment log

Append a row whenever you get a number. Bad numbers especially — they're the raw material for the post-mortem.

| Date | ID | Question | Setup | Result | Verdict |
|---|---|---|---|---|---|
| — | E001 | *example: does reranking beat fusion alone?* | *1k queries, held out* | *nDCG@10 0.61 → 0.68* | *keep* |

---

## Metrics ledger

Headline numbers at each phase boundary. This becomes the evaluation report.

| Date | Phase | Metric | Value | Notes |
|---|---|---|---|---|
| — | — | — | — | — |

**Metrics to track from week one, not retroactively:**
- Cascade ratios — volume entering each tier
- Cost per thousand units, per tier
- p50 and p95 latency per stage
- Resolution rate, by method
- Flag rate per check
- False positive rate on clean fixtures

---

## Failure log

What broke, what surprised you, what you got wrong. Write the day it happens.

This section becomes deliverable five: the post-mortem. Most candidates ship a README and stop; a post-mortem is what convinces a hiring manager you've shipped before.

### Template

**Date · one-line title**
What happened. What you expected instead. Root cause. What changed as a result. Whether a fixture or test now covers it.

**2026-10-18 · ClinicalTrials.gov OpenAPI URL in ADR-019 returns 404**
`/api/oas/v2/ctg-oas-v2.yaml` is gone; the spec is served as YAML at `/api/oas/v2`. Found while confirming endpoints before S1.4. Fixed in `config.py` and `.env.example`. A URL written from memory or from an old doc rots; confirm each one live.

**2026-10-25 · Two single records stopped whole resolution runs**
First, OpenAlex answers HTTP 400 when a title filter contains `?` or `*`, which it treats as wildcards ("…what is needed?"), and that one reference stopped the sample run after 701. Second, a DOI whose Crossref record has no title failed `PaperRecord` validation and stopped the structured run. Fixes: strip `?` and `*` from OpenAlex title filters; treat a 4xx on a search as "no candidates from this source" (recorded); record untitled records as "registered" without storing them. Each has a test. Lesson: anything from one external record must fail that record, not the run.

**2026-10-25 · Some citations cite a DOI that names a different paper**
In the structured sample, a reference titled "Effects of caffeine on exercise performance in sedentary females" (2010) cites a DOI that belongs to "Effects of caffeine on time trial performance in sedentary men" (2012). Stage 1 trusts DOIs (§9.5), so the match goes to the DOI's paper. Title agreement is now recorded in the evidence for every DOI/PMID match so C01 can surface these.

**2026-10-25 · Tagged mixed-citation text glues identifiers together**
In S1.6, `raw_string` for a `mixed-citation` with child tags joins adjacent elements with no space ("…1636–16442716813510.1007/…"). The citations themselves have no whitespace there, and `jats._text` concatenates. It's harmless for parsing, because those references already have tag-derived fields and the parser runs on the untagged ones. But it makes the strings ugly as C01 evidence. A spacing fix in `jats.py` plus a re-harvest would clean it. Not done in S1.8.

**2026-10-25 · OpenAlex went from a free polite pool to metered dollars**
ADR-020's throughput estimate (40,000 references in about an hour at 10 rps) assumed OpenAlex was free. The response headers now carry `X-RateLimit-Limit-USD: 0.1`, and a search costs $0.001, so without a key about 100 searches a day are possible. Found while probing endpoints before S1.7. ADR-032 makes Crossref primary and puts OpenAlex searches behind a budget. `test_lookups.py` covers the guard.

**2026-10-24 · Europe PMC's references API silently drops unmatched references**
Expected the structured reference list to be the paper's bibliography. It returns only entries Europe PMC matched to its own records: 49 of 54 for PMC11662519, with every returned entry carrying a PMID. That would have hidden every case C01 is meant to find. Caught by comparing against the full-text `<ref-list>`. ADR-030 switches to the XML. `test_europepmc.py` asserts the full counts (54/59/47).

**2026-10-18 · ADR-018 drop paths missed personal data**
The live v2 schema also has PI names in `responsibleParty` and contact emails in `resultsSection.moreInfoModule.pointOfContact`, and real records fill them. ADR-018 listed only the contacts module. ADR-025 extends the list. `test_clinicaltrials.py` checks every path on real records and on a synthetic record carrying all five.

---

## Open questions

| Question | Why it matters | Resolve by | Status |
|---|---|---|---|
| Which starting corpus? | Affects fixture design and prevalence comparability | Week 1 | **resolved — ADR-021** |
| Does `config.py` carry defaults, or require every variable? | Decides whether `.env.example` is documentation or mandatory setup | Week 1 | **resolved — ADR-022** |
| GPU available, or rent one? | Determines local vs API serving for encoders and fine-tuning | Week 6 | open — not needed until Phase 3; hourly rental is likely under $100 total. Tracked as Q1 |
| Full-text coverage rate for *cited* sources? | Caps the addressable share of claim grounding. Distinct from coverage of citing papers, which is ~100% by construction | Week 4 | open — measure on 100 papers, report beside accuracy. Tracked as Q2 |
| Publish individual findings, or aggregate only? | A false fabrication accusation is a serious harm | Week 10 | open — leaning: deterministic checks may name papers, model-based checks report rates only. Tracked as Q4 |
| Q1 — Is a GPU available? | Decides local or API serving for encoders and training | Before S2.4 | open |
| Q2 — What share of cited sources has open full text? | Caps how much C09 can check | Measured in S2.2 | open |
| Q3 — What exact Europe PMC query selects the corpus? | Decides what the working corpus is | S1.6 | **resolved — ADR-028** |
| Q4 — Publish individual findings, or aggregate only? | A false accusation of fabrication is a serious harm | Before S5.8 | open |
| Q5 — Where do earlier versions of registered outcomes come from? The public v2 API has no history endpoint. Options: the website's change-history tab (check terms of use), a third-party reconstructed history dataset (check licence and access), or the current record only, which supports the weaker claim "registry–publication mismatch" rather than "switching". | Decides what C06 can honestly claim | Before S3.9, ideally end of Phase 1 | open |
| Q6 — Which external labelled dataset, and is its licence compatible? | Validity of the external evaluation | Before S2.8 | open |
| Q7 — Which LLM for extraction and judging, and what's the budget cap? | Cost and reproducibility | Before S2.7 | open |
| Q8 — Should C05 cover only trials plausibly under FDAAA (a US site or an FDA-regulated product)? 461/1,034 corpus trials are flagged, 393 of them non-industry and many entirely outside the US. | Precision of C05's headline rate (ground rule 4) | Before S1.14 fixtures | **resolved — ADR-031** |

---

## Glossary

Domain terms, since the project spans two fields.

**GRIM** — Granularity-Related Inconsistency of Means. A reported mean of N integer-valued items must be a multiple of 1/N. If it isn't, the number can't arise from the stated data.

**Outcome switching** — a trial registers one primary outcome and publishes a different one. Roughly 31% prevalence; associated with about 16% larger reported effect sizes.

**Quotation error / citation distortion** — the cited source is real but doesn't support the claim made. Around 17% of quotations in medicine, about half of them major.

**Expression of concern** — a journal notice that a paper may be unreliable, short of full retraction.

**Entailment / NLI** — deciding whether text A supports, contradicts, or is neutral toward text B. The core primitive of claim grounding.

**Calibration** — whether a model's stated confidence matches its actual accuracy. A model that says 90% should be right 90% of the time.

**Distillation** — training a small model on a large model's outputs to approximate its behaviour at lower cost.

**Reciprocal rank fusion** — combining rankings from multiple retrievers by summing the reciprocals of each item's rank.

**Cascade** — routing work through progressively more expensive stages, escalating only what earlier stages flag.
