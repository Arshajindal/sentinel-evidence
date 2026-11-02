# Sentinel — Progress Log

**Where this file lives:** the repository root. It is committed with the code and is never added to `.gitignore`.
**What it's for:** a record of what was built, why it was built that way, and where to find it.

It's written for three readers:
- you, months from now, trying to remember why you turned an idea down;
- Claude Code, catching up at the start of a new session;
- an interviewer who asks "walk me through a decision you made."

---

## Quick guide to the words used here

**What Sentinel is.** A tool that audits biomedical papers and clinical trials for problems with their citations, statistics and reporting.

**Project terms**

| Term | Meaning |
|---|---|
| Phase, session | The project has 5 phases. Each phase is split into small numbered work sessions (S1.1, S1.2, …), listed in §15 of `docs/sentinel-project.md`. |
| § numbers | Section numbers in `docs/sentinel-project.md`. For example, §9.10 is the checklist that says when Phase 1 is finished. |
| ADR | Architecture Decision Record: one numbered entry in the decision log below, saying what was decided and why. |
| Corpus | The set of papers Sentinel runs over. The working corpus, `pmc-rct-2015-2024`, is 1,000 open-access reports of randomised controlled trials (RCTs) from 2015–2024. |
| Reference | One entry in a paper's reference list (bibliography). |
| Resolving a reference | Matching it to a real, known publication record. "Unresolved" means no match was found, which is not the same as "the work doesn't exist". |
| Cascade | The series of matching steps a reference goes through, most reliable first, stopping at the first step that gives exactly one match. |
| Check | One automated test run on a paper or a trial. Phase 1 has five (C01–C05, below). |
| Finding | The result of a check: `clean`, `flagged`, `not_applicable` (the check doesn't apply) or `insufficient_data` (not enough information to decide). A flag has a severity: `low`, `medium` or `high`. |
| Flag rate | The share of papers or trials that got at least one flag. |
| Fixture | A saved real example, with the answer checked by hand, used in the tests. Tests never call live APIs. |
| False positive | A flag on something that is actually fine. |
| Precision, recall | Precision: of the things flagged, how many are real problems. Recall: of the real problems, how many were flagged. Sentinel puts precision first (ADR-010). |
| Run | One numbered execution of a command, such as "check run 19". |
| Migration | An Alembic database schema change, named by a hex ID such as `9780a018baa5`. |
| `check_ver` | The version number of a check's logic. It goes up whenever the logic changes. |
| Pure check | A check that makes no network calls and doesn't read the clock. It sees only the data it is given. |
| `config_hash` | A fingerprint of the settings that can change findings, so two runs can be compared. |

**The five Phase 1 checks**

| Check | What it catches |
|---|---|
| C01 Reference existence | A reference that can't be matched to any real publication |
| C02 Retraction status | Citing a paper that was retracted or has an expression of concern |
| C03 Statistical consistency | A p-value that doesn't match its own test statistic |
| C04 Impossible means (GRIM) | A mean that can't come from the stated sample size |
| C05 Results reporting | A trial that finished but never posted results on ClinicalTrials.gov |

**Data sources and IDs**

- **Europe PMC:** where the corpus papers and their full text come from.
- **Crossref, OpenAlex:** catalogues of published works, used to match references.
- **doi.org:** the official DOI registry, which knows DOIs that Crossref doesn't.
- **Retraction Watch:** a database of retraction notices.
- **ClinicalTrials.gov:** the US trial registry.
- **DOI, PMID, PMCID:** IDs for a paper (general, PubMed, PubMed Central). **NCT number:** the ID of a trial on ClinicalTrials.gov.

Medical and statistical terms (GRIM, outcome switching and others) are in the Glossary at the end.

---

## How to keep this file up to date

Keep updates quick, or the file stops being kept up. The rules:

| Section | When to update | How |
|---|---|---|
| Current state | At the start of each work session | Rewrite it. It's the only section that gets overwritten. |
| Decision log | When you choose between real alternatives | Add a new numbered entry at the end. Never edit an old one; write a new one that replaces it. |
| Model registry | When a model is added, swapped or benchmarked | Edit its row. Move replaced models to the graveyard table. |
| Experiment log | When you get a number | Add a row, even (especially) for bad numbers. |
| Metrics ledger | At the end of each phase | Add a dated row. |
| Failure log | When something breaks or surprises you | Add an entry straight away, while you still remember the details. |
| Open questions | When a question comes up or gets answered | Edit freely. |

**Two-minute rule:** if an entry takes more than two minutes to write, it's too detailed. Write the short version now, and expand it later only if it matters.

**The failure log is the most valuable section.** Few portfolio projects have one, and it's the hardest thing to rebuild after the fact. Write to it the day something goes wrong.

---

## Current state

> Rewrite this section each session. Keep it short.

**Phase:** 2 of 5: full text, retrieval, and the evaluation foundation. Phase 1 is finished and merged (PR #1).
**Plan:** the session list in §15 of `docs/sentinel-project.md`. The old brief and HTML spec are in `docs/archive/`.
**Last worked:** 2026-11-01, session S2.2 (full-text fetch; Q2 measured).

**Sessions:**

| Session | Status | What was done, and the evidence |
|---|---|---|
| S1.1 Project scaffold | done | Settings load. `tests/test_config.py` tests the ADR-022 rules: startup fails if any required setting is missing, `config_hash` covers the right settings, and `.env.example` lists the same settings as `Settings`. `alembic current` runs without errors. |
| S1.2 Database tables and first migration | done | Migrations `c9051bc4f780` and `157fbb99af3c` (the second lets a finding be about either a paper or a trial, §8). `tests/test_migrations.py` upgrades, downgrades and upgrades again, and checks the trigram index used for fuzzy title search. `alembic check` finds no difference between the code and the database. |
| S1.3 Harvester base and Retraction Watch | done | Code: `ingest/base.py`, `ingest/retraction_watch.py`. Migration `17fb3d1f4207` (ADR-024). `sentinel ingest retraction-watch` loaded 73,283 retraction notices in about 12 seconds; running it again added none, so there are no duplicates. A 20-row fixture tests the first load, a re-run, updating rows in place, and resuming after the process is killed. |
| S1.4 ClinicalTrials.gov lookup client | done | Code: `ingest/clinicaltrials.py`; `ingest/http.py`, a rate limiter that retries only on 429 ("too many requests") and 5xx server errors; and a `LookupClient` base class. `sentinel ingest clinicaltrials --nct …` saved trials NCT02016378 and NCT05711641 with their version-0 outcomes. The test that personal data is removed (ADR-018) passes on real and synthetic records (ADR-025, ADR-026). |
| S1.5 Check framework, C05, report card | done | Code: `checks/` (base, context, C05, registry, runner), `report/card.py`, and the `sentinel check` and `sentinel report` commands. Live result: `sentinel report NCT02016378` prints "No results posted on ClinicalTrials.gov 45 months after primary completion" (medium); NCT05711641 has no problems. The pair of fixtures passes (ADR-027). **The first end-to-end slice works: data in, check, report out.** |
| S1.6 Corpus selection and Europe PMC harvester | done | Corpus `pmc-rct-2015-2024`: 1,000 papers sampled from 30,457 search hits with random seed 20260922, chosen 2026-10-24 (ADR-028). Collected all 1,000 papers and 38,685 references, read from each paper's full-text `<ref-list>` (ADR-030). Found 1,051 links from 926 papers to the 1,032 trials they report, and fetched all those trials. Migration `9780a018baa5` (ADR-029). |
| S1.7 Crossref, OpenAlex and doi.org clients | done | Code: `ingest/crossref.py`; `ingest/openalex.py`, which has a spending budget (ADR-032); `ingest/doi_handle.py`; and `ingest/papers.py`, where whichever source saves a work first owns its row. `sentinel ingest crossref\|openalex\|doi-handle --doi …` was tried live. Rate limiting, retries and the budget guard are tested against recorded responses. |
| S1.8 Reference parser | done | Code: `parse/references.py` (ADR-033). It splits a plain-text citation into fields: author, year, title and journal. On 50 hand-checked citations it got 0 fields wrong or invented, and filled 88.2% of fields. On the corpus's 22,019 citations that have no XML tags, it found the first author in 92.6%, the year in 97.3% and the title in 78.9%. Not yet saved to the `reference` table; S1.9 does that. |
| S1.9 Matching cascade and candidate fetch | done | Code: `resolve/cascade.py`, `resolve/candidates.py`, `sentinel resolve`. Migration `80034d5e56ce`. Parser 1.1.0 (ADR-034). On the §9.10 test sample (references from structured reference lists, seed 20260922), 944 of 1,000 matched (94.4%). On a sample of all kinds of reference, 88.7% matched. No wrong title-based match was found. At this point 1,786 references had been processed (1,637 matched); the other 36,899 were done in S1.15. |
| S1.10 C01 | done | Code: `checks/c01_existence.py` (ADR-035). The doi.org check runs during matching, the runner now runs paper checks, and papers have report cards. A real fixture of 48 references was labelled by hand, and C01 agrees on all 48; it flags no non-journal item. Live on three fully matched papers: each had one reference to an unindexed journal flagged. |
| S1.11 C02 | done | Code: `checks/c02_retraction.py` (ADR-036): three timing bands, plus a low band for papers that were retracted and replaced. Fixtures: 12 real papers (cited well after retraction, cited before it, replaced, expression of concern, corrected), plus made-up "in press" and edge cases. Corpus: 6 citations in 5 papers were made more than 6 months after a plain retraction. |
| S1.12 Statistics extraction and C03 | done | Code: `parse/statistics.py`, `ingest/fulltext_stats.py` (`sentinel extract stats`), `checks/c03_statcheck.py` (ADR-037). All five §9.7 patterns are read correctly, the one-tailed rule is tested, and 22 real statistics from the corpus are in the tests. Corpus: 332 statistical tests found in 32 papers, giving 16 low flags in 7 papers. None was a gross error (one that changes whether a result is significant). |
| S1.13 C04 | done | Code: `checks/c04_grim.py` (ADR-038). Each condition that decides whether the check applies is tested both passing and failing, and the arithmetic was checked against a brute-force calculation. Phase 1 doesn't pull means out of papers, so every corpus paper is `not_applicable`, as ADR-021 predicted. |
| S1.14 Fixture set, `stats` and `export` | done | 46 real fixtures (35 papers, 11 trials) in `tests/fixtures/set/`, each with hand-checked expected findings. All five checks match them exactly: no unexpected findings, and no flags on the 8 clean papers (ADR-040). Building the set found 3 C01 false positives and a C02 wording bug, all fixed (C01 1.0.1, C02 1.0.1, and HTML entities such as `&amp;` are now decoded in titles). New `stage_run` table (migration `b41d7c2e9a10`) records how long ingest, resolve and extract take. New commands `sentinel stats --run N` and `sentinel export --run N --format csv\|json` (ADR-039). Run 17, over the 34 corpus papers in the fixture set, demonstrates both. |
| S1.15 Full corpus run and README | done | All 35,670 remaining references matched (runs 11–14, $1.71 spent on OpenAlex). The 133 references matched before the glued-text fix were retried, and none changed. The Retraction Watch load was killed and restarted live: it resumed at row 5,000 with no duplicates. A report card takes 4.62–4.75 s now that imports load only when needed. Checking a sample of corpus flags showed that Crossref subtitles were being dropped (ADR-041); fixing that cut C01 flags from 347 to 272. Check run 19 and `stats` give every flag rate and cascade ratio (see the metrics ledger). The README covers setup, each check's limits, and the results. |
| S2.0 Retries, pgvector and MLflow | done | Dropped connections and timeouts are now retried like 5xx responses (ADR-042, 8 new tests). Postgres moved to a pgvector 0.8.6 container on 127.0.0.1:5434 with a role that isn't a superuser; every table's row count and the migration version match the old server (ADR-043). MLflow runs at 127.0.0.1:5000 with its data in the same Postgres, checked with one test run. |
| S2.1 Phase 2 tables | done | Migration `6bb29bc195ff` (ADR-044): passages with a splitter version, one vector row per passage per model (HNSW, cosine), citation contexts linked to several references, claims, evaluation items with a required licence, and gold passages protected by a foreign key. Up, down, up runs cleanly; `alembic check` finds no difference; applied to the working database. |
| S2.2 Full-text fetch | done | `ingest/fulltext.py`, `sentinel ingest fulltext`, `sentinel corpus coverage`, migration `8d0bd1c5a522` (ADR-045). All 33,664 works checked in Europe PMC; 8,427 open-access XML files cached. **Q2: 22.6% of resolved references cite a work with full text**, 92.8% at least an abstract. Europe PMC answers 500 for non-open-access full text, so only open-access records are requested. |

**What works:** the whole Phase 1 pipeline over the 1,000-paper corpus. That means loading data, matching references (89.4% of all references, 94.4% of the structured sample), pulling out statistics, all five checks, `report`, `stats`, `export` and stage timing. 716 tests, all passing, including the database tests and the 46-item fixture set. Phase 2 so far: pgvector and the Phase 2 tables, MLflow, and text availability for every corpus paper and cited work (Q2 measured).

**What's broken:**
- Most of C01's corpus flags are works that do exist: only 8 of 20 sampled flags were correct (see the README's section on C01's limits). So C01's flag rate is published as an upper bound.
- A stage that is force-killed leaves its `stage_run` row saying `running` forever (run 9).
- `stats` counts items only for runs that finished.
- The full text of PMC6561645 still returns an HTTP 500 error from Europe PMC.

**Next three things:**
1. S2.3: split papers into passages and extract citation contexts. This needs a new ADR, because ADR-037 says body text isn't stored in the database. Passages come from the 8,427 cached XML files plus abstracts for the rest.
2. Answer Q1 before S2.4. This machine has no NVIDIA GPU, so local embedding runs on the CPU, over roughly 8,400 full texts.
3. Answer Q6 before loading the external dataset, and Q7 before claim extraction.

**After that:** the session-order problem from planning. S2.6 (retrieval evaluation) needs the gold passages that S2.8 builds, so loading the external set should move ahead of S2.6. S2.9's tabular baseline has no real labels until S3.4's hand-labelled set. Still open from Phase 1: nothing checks for a DOI that points to a paper with a different title, and the title-plus-author Crossref search for C01 (ADR-041) has no session yet.

**Blocked on:** nothing.

---

## Decision log

Entries are only ever added, never edited. Each one says what was decided, why, what was turned down, and whether it still holds. An "In short" line at the top of each gives the gist.

Status values: `active` (still holds) · `superseded by ADR-NNN` (replaced) · `revisit at <milestone>` (to look at again later)

---

### ADR-001 — Check evidence, not spot research trends
**Status:** active

**In short:** Sentinel checks whether papers' citations and claims hold up. The other idea was dropped because the training labels it needed don't exist.

Two ideas were considered:
- a "horizon scanner" that spots new research fields as they emerge;
- an "evidence integrity engine" that checks citations and claims.

Integrity was chosen. The horizon scanner would have learned from experts' past choices about what to watch, but those choices were never written down: the filtering happened before anything was recorded. The integrity engine needs no private labels. Correct answers can be built from public data, plus copies of it that are deliberately broken by a script.

**Turned down:** horizon scanning as the main product. It stays as a possible later phase, since the same corpus and infrastructure would support it.

---

### ADR-002 — Audit a whole corpus, not one paper at a time
**Status:** active

**In short:** the hard engineering problems only show up at scale, so Sentinel is built to process thousands of papers.

A tool that checks one paper never runs into the problems this project is meant to show it can handle: throughput, batching, concurrency, cost per item, caching, partial failure and retries. Those appear at 400,000 checks, not 40.

A single-paper API endpoint is still kept for demos, built on top of the corpus backend.

---

### ADR-003 — PostgreSQL with pgvector, not a separate vector database
**Status:** active

**In short:** one database does everything.

PostgreSQL holds the relational data, does full-text search and, with the pgvector extension, stores embeddings. Combining keyword search with vector search becomes an ordinary SQL join instead of a query across two systems, and there's no second system to run.

**Turned down:** Pinecone, Qdrant, Weaviate. They're better when there are many customers, billions of vectors, or a wish to hand off operations. None of that applies here. Be ready to explain this in an interview; the reasoning matters more than the choice.

---

### ADR-004 — SQLAlchemy Core, not the ORM
**Status:** active

**In short:** write queries directly instead of mapping rows to Python objects.

The work is bulk loading and analysis queries, not moving between linked objects. Core gives direct control over batch insert-or-update ("upsert") and avoids the ORM's hidden habit of running one query per row (the "N+1" problem) inside checks. Alembic manages the schema; `create_all` is never called.

---

### ADR-005 — Download bulk data instead of paging through APIs
**Status:** active (see ADR-020, which changes this for Crossref and OpenAlex)

**In short:** keep a local copy of the data, so checking a reference is a database lookup and not a web request.

Load Crossref and OpenAlex from their bulk downloads instead of paging through their APIs. Once the data is local, checking whether a work exists is a table join rather than a network call, and speed no longer depends on someone else's rate limit.

This was seen as the highest-impact decision in Phase 1, because doing it later would mean rebuilding the data-loading layer.

**Cost:** more local storage, and the data goes a little stale between refreshes. Both acceptable.

---

### ADR-006 — Rule-based checks first; no machine learning in Phase 1
**Status:** active

**In short:** the five Phase 1 checks are arithmetic and lookups, and they are the baseline every later model has to beat.

They give real findings by week two, cost nothing to run, are almost never wrong, and every later model is measured against them.

They're also insurance. If the machine-learning parts underperform, the project still has a working product.

**Turned down:** starting with the interesting modelling work. Using a transformer to check whether a p-value matches its test statistic would be poor judgement, and an interviewer would notice.

---

### ADR-007 — "Couldn't match it" doesn't mean "it doesn't exist"
**Status:** active

**In short:** Sentinel flags only a reference that looks like a journal article and still can't be matched, and it says the reference "could not be verified".

A reference can fail to match because it's a book, a thesis, a conference abstract, a regional journal that isn't indexed, a personal communication, or just badly formatted.

So unmatched references are sorted three ways:
- `unresolved_nonjournal`: looks like a book, website or similar. Not a finding.
- `unresolved_unparseable`: couldn't be read well enough to try matching. Reported as `insufficient_data`.
- `unresolved_journal_like`: looks like a journal article and still matched nothing. This is the only case that is flagged, at `medium`, worded "could not be verified".

The published studies of how often this happens were careful about this distinction. Overclaiming here would damage Sentinel's credibility faster than any other mistake it could make.

---

### ADR-008 — Keep every version of a trial's registered outcomes
**Status:** active

**In short:** save the history now so that Phase 2 can detect outcome switching.

ClinicalTrials.gov keeps a change history for each trial. Phase 2 detects outcome switching by comparing the registry's versions with what the paper published.

Storing only the current version would lose the history for good and force a full reload later. It's cheap now and expensive to add afterwards.

---

### ADR-009 — Before flagging a p-value, check whether it's a one-tailed test
**Status:** active

**In short:** many papers use one-tailed tests without saying so. Don't flag those.

If halving the computed p-value makes it match the reported value, record `one_tailed_ok = true` and don't flag it.

Without this, the statistics check would produce a flood of false alarms on day one.

---

### ADR-010 — Being right matters more than catching everything
**Status:** active

**In short:** a flag must almost always be correct, even if that means some real problems are missed.

At a 1% flag rate over 420,000 citations, people would have 4,200 items to review. If a third of those were false alarms, the tool would be abandoned within a month.

This turns the usual machine-learning priority around. Tune thresholds for precision, accept that some cases will be missed, and say so plainly in the output.

**Follow-on rule:** if a check can't be made nearly always right, narrow the cases it applies to instead of accepting false alarms. A check that runs on 40% of papers and is never wrong beats one that runs on all of them and cries wolf.

---

### ADR-011 — LangGraph for orchestration
**Status:** active

**In short:** long runs must be able to stop and carry on where they left off.

This needs a state machine that saves progress to Postgres and can pause for a human. A run that dies at citation 180,000 must resume there, not start again.

**Turned down:** hand-written loops (they don't survive long jobs), CrewAI (weaker save-and-resume), Temporal (heavier than a solo project needs).

---

### ADR-012 — Self-hosted Langfuse, not LangSmith
**Status:** active

**In short:** trace every model call without sending prompts to a third party.

Mention both on the resume; run Langfuse.

---

### ADR-013 — Three independent sources of correct answers
**Status:** active

**In short:** test against three kinds of ground truth, so no single one can mislead.

1. Real, verified citation pairs that a script deliberately breaks.
2. Published datasets that others have already labelled.
3. A hand-labelled test set, with items chosen by active learning (the examples the model is least sure about).

Three independent sources is unusual for a portfolio project, and it's the strongest single argument for this design over the alternatives.

---

### ADR-014 — Always report results on generated data and real data side by side
**Status:** active

**In short:** accuracy on real data is usually lower, so show both numbers.

In published work, one verification pipeline dropped from 97% on generated test data to 90% on real cases, and one leading model fell from 96% to 33% F1.

Reporting only the better number is a sign of an unserious system. The gap itself is worth analysing.

---

### ADR-015 — Findings show their evidence, even when clean
**Status:** active

**In short:** every result shows what was examined, so that it can be audited.

`finding.evidence` is filled in for `clean` results too. A check that says "clean" without showing what it looked at can't be audited.

`finding.check_ver` records the check's version. An improved check then adds new rows that can be compared with the old ones, instead of quietly overwriting history.

---

### ADR-016 — Keep the name Sentinel
**Status:** active

**In short:** the name is crowded, but renaming isn't worth the time.

The downside is known and written down here so it isn't argued again. "Sentinel surveillance" is an established epidemiology term (monitoring a chosen subset of sites), and Microsoft Sentinel, HashiCorp Sentinel and the Sentinel satellites all exist. So the name is hard to find by searching.

Accepted anyway. Renaming at week six would take ten minutes; losing week six to choosing a name would cost a week.

**Revisit:** only if a clash with a package or repository name forces it.

---

### ADR-017 — A grace period before blaming a paper for citing retracted work
**Status:** active

**In short:** papers can be in press for months, so a citation made around the time of a retraction isn't the author's fault.

Papers often wait six to twelve months between acceptance and publication. Flagging a paper `high` for citing something retracted three weeks before it was published would blame the authors for something they couldn't have known.

So there are three severity bands instead of two, controlled by `SENTINEL_C02_GRACE_MONTHS` (default 6):
- cited more than the grace period after the retraction notice → `high`;
- cited within the grace period → `low`, described as "retracted while in press";
- cited before the notice → `low`;
- publication date unknown → `insufficient_data`.

This follows from ADR-010: it gives up some recall for precision, which is the right direction for these checks. It also changes the headline flag rate, so the grace-period value is part of `config_hash` and must be stated wherever a prevalence figure is reported.

**Revisit:** if the measured time papers spend in press in the chosen corpus is very different from six months.

---

### ADR-018 — Remove personal contact details from trial records when loading them
**Status:** active

**In short:** Sentinel stores no personal data, so names, phone numbers and emails are dropped before anything is saved.

ClinicalTrials.gov records name people and give their contact details in `centralContacts`, `overallOfficials`, and the `contacts` for each location. They're published so that people interested in joining a trial can reach someone. Bulk-loading them into a database is a different use, and nobody agreed to it.

`trial.raw` is a JSONB column, so by default the whole record, those fields included, would be stored. Instead, they are dropped in `normalise()`, before anything is written. Only institution-level fields are kept: sponsor name and type, country-level locations, dates, status, enrolment and outcomes.

Sentinel says it handles no personal data. Storing trial coordinators' phone numbers in a public repository would contradict that, and someone would notice.

This is controlled by `SENTINEL_STRIP_PERSONAL_FIELDS` and `SENTINEL_TRIAL_DROP_PATHS`. The on/off setting exists to make the behaviour visible in the config, not because turning it off is supported.

**Test requirement:** a fixture that checks no dropped path ends up in `trial.raw`. Run it after every change to data loading.

---

### ADR-019 — Load ClinicalTrials.gov from its bulk download at first
**Status:** active (see ADR-023, which changes the default)

**In short:** ClinicalTrials.gov offers the whole registry as one download, so the first load needs no paging.

ClinicalTrials.gov publishes every study as one zip file at `/api/v2/studies/download?format=json.zip`, in the same JSON format as the single-study endpoint. That meets ADR-005 with no paging code for the first load.

The paged `/studies` endpoint is kept for later updates. It uses a `pageToken` cursor and returns at most 1,000 rows per page, which fits the harvester's watermark design (remembering where the last load stopped).

`/api/v2/version` returns a `dataTimestamp`. Check it before each refresh to confirm the registry's daily rebuild has finished.

An OpenAPI spec is published at `/api/oas/v2/ctg-oas-v2.yaml`. Generate the trial models from it, or check them against it, instead of writing them by hand. *(Later found to be out of date: that URL returns 404, and the spec is now served at `/api/oas/v2`. See the failure log, 2026-10-18.)*

---

### ADR-020 — Changes ADR-005: at this size, use the APIs, not bulk downloads
**Status:** active · amends ADR-005

**In short:** the corpus is small enough that calling the Crossref and OpenAlex APIs is fine, so downloading their entire datasets isn't worth it.

ADR-005's reasoning still holds: at scale, don't let someone else's rate limit control your speed. The mistake was assuming this project is at that scale.

The working corpus is 1,000 papers, about 40,000 references. At OpenAlex's free "polite pool" rate of about 10 requests a second, that takes about an hour. The 10,000-paper validation run is about 400,000 references, an overnight job. Downloading hundreds of gigabytes to avoid that is the wrong trade. *(ADR-032 later found that OpenAlex now charges for searches.)*

The new split:

| Source | How it's loaded | Disk space |
|---|---|---|
| Retraction Watch | Bulk CSV | ~50 MB |
| ClinicalTrials.gov | Bulk zip (ADR-019) | a few GB |
| Crossref, OpenAlex | API only | none stored |
| PMC full text | Fetch the corpus only | a few GB |

That's under about 10 GB in total, so disk space stopped being a design concern.

The harvester rules don't change: it must be able to resume, keep a watermark, and respect rate limits. Those matter even more for API loading than for bulk loading, because API loads are where runs die partway through.

**Revisit at:** about 50,000 papers, or if OpenAlex's daily quota starts to limit the work.

---

### ADR-021 — Starting corpus: 1,000 open-access PMC papers that name a registered trial
**Status:** active (see ADR-028, which narrows it)

**In short:** pick papers that name a ClinicalTrials.gov trial, so one corpus serves both the Phase 1 and Phase 2 checks.

The working corpus is 1,000 papers from the PubMed Central (PMC) open-access set that contain an NCT number, from any disease area.

The NCT filter is what matters. Phase 1's C05 needs registry records, and Phase 2's outcome-switching check needs papers linked to registered trials, so one corpus serves both with no reloading. Trial reports also follow a regular structure, which makes pulling out statistics easier while the parser is still new.

**Turned down:** limiting the corpus to one disease area. It adds a filter step without making the set noticeably more coherent or easier to review at this size.

**Known consequence:** C04 (GRIM) will return `not_applicable` for almost all of this corpus. GRIM needs measures that take whole-number values and small samples, which is typical of psychology, not of clinical trials that report continuous outcomes in hundreds of patients. So C04 is proven with fixtures, not with the working corpus. This is expected, not a defect.

The validation corpus, for week 13, is a random sample of about 10,000 PMC open-access papers from all fields. That is the set to compare against published prevalence estimates, because it's a comparable sample. The working corpus isn't, and must never be used for that comparison.

---

### ADR-022 — Speed settings may have defaults; settings that change findings may not
**Status:** active

**In short:** if a setting can change a result, someone has to set it on purpose.

A hidden default is a setting nobody chose. If a check threshold quietly falls back to a default, the results depend on a value nobody reviewed, and `config_hash` is built on inputs nobody can see. But requiring all forty settings would mean a fresh clone won't even start, which makes testing painful.

The rule: **if it changes the findings, it must be set explicitly.**

*Defaults allowed:* batch sizes, timeouts, pool size, maximum connections, retry counts, backoff, concurrency, log level and format, export folder, corpus label. Changing these changes speed, not answers.

*No defaults, and startup fails if they're missing:* `DATABASE_URL`, `CONTACT_EMAIL`, `USER_AGENT`, `RANDOM_SEED`, `CHECKS_ENABLED`, every `C0N_*` check setting, every `RESOLVE_*` threshold, and `STRIP_PERSONAL_FIELDS`.

`config.py` does this by declaring the second group with no default in the pydantic-settings model, so pydantic raises an error at startup if one is missing. `config_hash` is computed over the second group only, because speed settings don't change what a run means.

**Test requirement:** a test showing that removing any required setting makes startup fail. Without it, this rule will quietly break the first time someone adds a convenient default.

---

### ADR-023 — Changes ADR-019: fetch ClinicalTrials.gov trials one at a time by default
**Status:** active · amends ADR-019

**In short:** the corpus needs about 1,000 trials, so downloading all of the roughly 600,000 is wasteful.

ADR-019 made the bulk zip the first load. The zip holds every study in the registry, about 600,000 records, while the working corpus needs about 1,000 linked trials (ADR-021). Downloading and reading the whole registry to use a fraction of a percent of it is the same mistake ADR-020 fixed for Crossref and OpenAlex.

The default is now to fetch one study at a time from `/api/v2/studies/{nctId}` through a lookup client. Each trial is inserted or updated in `trial` and `trial_outcome`, and the raw response is kept (with the ADR-018 personal fields removed first). The bulk zip is kept for the 10,000-paper run (ADR-021) or for registry-wide statistics. The rest of ADR-019 still holds: same JSON format, check `/api/v2/version` before refreshes, and check the models against the OpenAPI spec.

**Turned down:** the bulk zip for the 1,000-paper corpus. A few GB and reading the whole registry gains nothing at this size.

**Revisit at:** the 10,000-paper run, or if fetching one study at a time becomes the bottleneck.

---

### ADR-024 — Retraction notices are identified by Retraction Watch's own record ID
**Status:** active

**In short:** each notice needs a unique key so that reloading doesn't create duplicates, and the source's Record ID is the only reliable one.

To insert-or-update safely, each row needs a natural key (a field in the source data that identifies it uniquely), and the `retraction_notice` table in §8 had none. In the 2026-10-16 Retraction Watch CSV, the combination of retracted DOI, notice DOI and type repeats 3,766 times across 73,283 rows, while `Record ID` is unique in every row. So `source_record_id` was added, with `UNIQUE (source, source_record_id)`. It can be empty, so a future source without IDs can still save rows.

The same data has 163 `Reinstatement` rows, a type §8 didn't list. They're stored as `notice_type = 'reinstatement'` so that C02 doesn't call a reinstated paper retracted. The Author and Institution columns are left out of `raw`: C02 matches on DOI and PMID only, so they aren't needed.

**Turned down:** a unique index on `raw->>'Record ID'` (the key would be hidden inside JSON), and removing duplicates in code (nothing in the database would stop them).

---

### ADR-025 — Changes ADR-018: two more places where trial records hold personal data
**Status:** active · amends ADR-018

**In short:** the live registry format names people in two more places, so those are removed too.

ADR-018's three removal paths miss two places where the live v2 format names people:
- `protocolSection.sponsorCollaboratorsModule.responsibleParty`, which has `investigatorFullName`, title and affiliation when a principal investigator (PI) is the responsible party;
- `resultsSection.moreInfoModule.pointOfContact`, which has a name or title, email and phone.

Real records have personal email addresses there. Both objects are now removed whole.

`responsibleParty.type` (sponsor or PI) goes with it. Nothing in Phases 1–3 needs it, and listing individual fields to remove would be easier to get wrong.

The client refuses to run if `SENTINEL_STRIP_PERSONAL_FIELDS` is false, rather than store the data.

**Turned down:** removing only the personal subfields of `responsibleParty`; leaving ADR-018 as it was.

---

### ADR-026 — Month-only registry dates are read as the last day of the month
**Status:** active

**In short:** when a date is vague, assume the latest day it could mean, so a date-based check never fires too early.

ClinicalTrials.gov dates are often only `YYYY-MM`, and sometimes only `YYYY`. But the `trial` table stores full dates, and C05 counts months from `primary_completion_date`. A partial date becomes the latest day it could mean: `2012-10` becomes 2012-10-31, and `2019` becomes 2019-12-31. So a date-based check can never fire early (ADR-010). The original text stays in `trial.raw`.

**Turned down:** the first day of the month (a trial could look up to a month more overdue than it is); leaving the date empty (C05 would lose many older trials to `insufficient_data`).

---

### ADR-027 — C05 measures time up to when the record was fetched, not up to today
**Status:** active

**In short:** checks can't read the clock, so "overdue" is measured as of the day the registry record was downloaded.

Checks are pure (§9.6), so C05 can't use today's date. It counts whole months from `primary_completion_date` to the day the registry record was fetched (`trial.ingested_at`). That's also the last time `results_first_posted` was seen, so the finding is true as of the data it is based on. The evidence records `record_fetched_on`. To bring a finding up to date, fetch the trial again.

What C05 covers, as built: completed, interventional trials, except those whose listed phases are all Early Phase 1 or Phase 1 (Phase 1/2 and "NA" are included). Any other status, or a study that isn't interventional, gives `not_applicable`. A missing phase or primary completion date gives `insufficient_data`.

**Turned down:** today's date (an old record could flag a trial that has since posted results, and re-running the same data would give different answers); the run's start date (same problem).

---

### ADR-028 — Narrows ADR-021: the corpus is RCTs from 2015–2024, and only a paper's own trials are linked
**Status:** active · narrows ADR-021

**In short:** restricting to randomised controlled trials makes it much more likely that each paper reports its own trial, rather than mentioning someone else's.

This answers Q3. The corpus query is:

`ACCESSION_TYPE:nct AND OPEN_ACCESS:y AND IN_PMC:y AND HAS_REFLIST:y AND PUB_YEAR:[2015 TO 2024] AND PUB_TYPE:"Randomized Controlled Trial"`

It is stored in `SENTINEL_CORPUS_QUERY`, with no default. Chosen on 2026-10-24: 30,457 hits, sorted by PMCID, then 1,000 sampled with seed 20260922, under the label `pmc-rct-2015-2024`.

Why narrow it: ADR-021 said "any paper carrying an NCT number". Without the RCT filter, about 28% of hits are reviews, and 31% of the research articles only mention other people's trials. Among RCTs tagged as such in MEDLINE, 97 of 100 name their own NCT number in the title, abstract or methods.

How trials are linked: Europe PMC scans papers' text and tags the trial IDs it finds. A trial is linked to a paper (confidence 1.0) only when its ID appears in the title, abstract or methods. Mentions only in the introduction, discussion, tables or unlabelled text aren't linked, because those usually refer to someone else's trial.

**Turned down:** all research articles from 2015–2024 (142,775 hits, and weaker links); all years and types (287,155 hits, including reviews and issues dated in the future); linking every mention and scoring it by section.

---

### ADR-029 — Tables that record which papers are in a corpus, and keys for references
**Status:** active

**In short:** new tables say which papers make up each corpus and how it was chosen, and a new key stops duplicate references.

§8 had no way to say which `paper` rows make up a corpus (the same table also holds cited works and candidate matches), and nowhere to record how a corpus was chosen. Added:
- `corpus`: label, source, query, seed, hit_count, n, selected_at;
- `corpus_paper`: label, ordinal, source IDs, paper_id. Members are chosen before they're downloaded, so `paper_id` is filled in later.

`reference` gets `UNIQUE (citing_paper_id, ordinal)`, so downloading again doesn't create duplicates (the same reasoning as ADR-024). It also gets `parsed_pmid`, which stage 2 of the matching cascade needs, and a `raw` JSONB column holding the original entry from the source. Migration `9780a018baa5`.

---

### ADR-030 — Read reference lists from the full-text XML, not the references API
**Status:** active

**In short:** the references API leaves out exactly the references C01 needs to see.

Europe PMC's `/{source}/{id}/references` returns only the references Europe PMC has already matched to its own records. For PMC11662519 it returned 49, against 54 in the paper. The unmatched ones are exactly what C01 examines, and match rates would be inflated by design.

Instead, each corpus paper's `<ref-list>` is read from `/{PMCID}/fullTextXML`. That's open access and still costs one request per paper. Fields are taken only where the XML marks them up (`element-citation`). Plain `mixed-citation` entries keep only `raw_string`, for the S1.8 parser. The body of the paper is neither parsed nor stored; that's for Phase 2. For safety, XML is parsed with DTDs, entities and network access turned off.

**Turned down:** the references API with the gap documented (C01 would be meaningless); combining both sources (lining them up is fiddly, and it doubles the requests).

---

### ADR-031 — C05 covers only trials the US results-reporting law could plausibly apply to
**Status:** active

**In short:** don't call a trial overdue if it never had a legal duty to post results on ClinicalTrials.gov.

C05 v1.0.0 flagged trials whose primary completion was as early as 2004, before ClinicalTrials.gov's results rules existed. FDAAA 801, the US law that requires results to be posted, covers trials started after 2007-09-27 or still running on 2007-12-26 (ClinicalTrials.gov FAQ; Anderson et al., NEJM 2015). The text of the law and the regulation couldn't be fetched automatically, so these two secondary sources, which agree, are what this rule rests on.

**The era rule.** C05 v1.1.0 applies only when `start > 2007-09-27` or `completion >= 2007-12-26`. Otherwise the finding is `not_applicable` (`before_fdaaa`). With neither date recorded, it's `insufficient_data`. For this rule, partial dates are read as the earliest day they could mean, using the registry's own text, so a trial counts as covered only if every reading of its dates says so. ADR-026's latest-day rule still applies to the grace-period arithmetic. Both choices favour precision.

**The reach rule.** The era rule alone moved only 10 trials (flagged 467 → 461). The real cause of the 45% flag rate was reach: only 66 of the 461 flagged trials had a US site, and the registry marked just 6 as FDA-regulated. So C05 also requires a US site, or `oversightModule.isFdaRegulatedDrug` or `isFdaRegulatedDevice` set to true. Otherwise the finding is `not_applicable` (`outside_fdaaa_scope`). A trial with neither never had a duty to post on ClinicalTrials.gov.

Effect on `pmc-rct-2015-2024`, comparing run 7 (v1.0.0) with run 9 (v1.1.0): flagged trials went from 467 to 70. 478 trials are `outside_fdaaa_scope` and 10 are `before_fdaaa`. 21.8% of the trials in scope have no results after the grace period. This answers Q8.

**Turned down, for the era rule:** primary completion on or after 2007-12-26 (drops covered trials that were still running then); the Final Rule date of 2017-01-18 (far narrower than the law). The date 2008-01-18 was proposed first and was wrong: it isn't an FDAAA date at all. **For the reach rule:** keeping non-US trials at `low` severity (the flag would still suggest a duty that doesn't exist); leaving the question for later.

---

### ADR-032 — Changes ADR-020: OpenAlex now charges, so Crossref becomes the main search source
**Status:** active · amends ADR-020

**In short:** OpenAlex searches now cost money, so Sentinel searches free Crossref first and uses OpenAlex only within a daily budget.

ADR-020 assumed OpenAlex's free polite pool, at about 10 requests a second with no daily cap. Checked live on 2026-10-25, OpenAlex now meters use in dollars: about $0.10 a day without a key, and $1 a day with a free one (openalex.org/signup). DOI lookups cost $0 and searches $0.001, so a free key allows about 1,000 searches a day. Adding `mailto` no longer changes anything. Crossref hasn't changed and is free: with `mailto`, its polite pool allows 10 requests a second and 3 at once.

Decisions:
- Crossref's `query.bibliographic` search is the main way S1.9 finds possible matches ("candidates"). OpenAlex title search is a fallback within a daily budget (`SENTINEL_OPENALEX_DAILY_BUDGET_USD`, a speed setting).
- OpenAlex DOI lookups stay unlimited. Each response's `X-RateLimit-Cost-USD` and `-Remaining-USD` headers are recorded. When the budget runs out, a search raises `OpenAlexBudgetExhausted` instead of returning nothing, so running out of money can never make a reference look impossible to match.
- The OpenAlex key goes in the `Authorization` header, never in a URL that could end up in a log.
- Works found by lookup are stored by DOI, PMID or PMCID, and the first source to save a work owns its row. A later source reuses the row without overwriting it, and records with conflicting IDs are never merged (`ingest/papers.py`).

**Turned down:** dropping OpenAlex (loses a second source of metadata); using OpenAlex as an equal search source with no limit (S1.9 would hit the cap within minutes).

---

### ADR-033 — Reference parser: fill a field only when sure, otherwise leave it empty
**Status:** active

**In short:** the parser never guesses, and it's measured against citations whose correct fields are known from XML tags.

`parse/references.py` (parser 1.0.0) follows §9.4 with one stricter rule: every field is either found by a clear rule or left empty (None).
- The year must fall between 1900 and the citing paper's year + 1. That year is passed in, never read from the clock. Years inside DOIs, URLs and "accessed on" dates are ignored. A year in the usual citation position wins, a range uses its first year, and conflicting candidates give None.
- The title and journal are filled in only when both the author list and the citation details (volume, pages and so on) are found.
- DOIs glued to other text, organisation names in the surname position, and titles that look like author lists give None.

One change from §9.4's wording: the title is everything between the authors and the journal, not the longest segment. Subtitles and abbreviations such as "vs." also contain ". ", and the correct titles from the XML tags keep them.

Measured on 7,627 `mixed-citation` strings whose XML tags give the correct answer, the share of filled fields that were right was: first author 99.2%, year 100%, title 99.3%, journal 99.6%. On the 22,019 untagged strings it's actually meant for, it fills the first author in 92.6%, the year in 97.3% and the title in 78.9%. On the 50 hand-checked strings: 0 fields wrong or invented, and 88.2% of the true fields filled.

**Turned down:** a machine-learning or CRF parser (§9.4 says rule-based); the "longest segment" title rule (it cuts off subtitles).

---

### ADR-034 — How references are matched, the safety rules, the evidence kept, and how the 90% target is measured
**Status:** active

**In short:** references go through five matching stages, most reliable first; extra rules reject doubtful matches; on the official sample, 94.4% match.

`resolve/cascade.py` runs §9.5's five stages: exact DOI, exact PMID, exact title, similar title plus author and year, and very similar title alone. Works that aren't stored yet are fetched: a DOI from Crossref and then OpenAlex, a PMID from OpenAlex (both free), and titles through a Crossref bibliographic search and then an OpenAlex search within budget (ADR-032). A tie at any stage gives `ambiguous`. If the OpenAlex budget runs out, the run stops; it never marks a reference unresolved because of that.

Extra safety rules beyond §9.5 (both cost only recall):
- At stages 3 and 5, a candidate is rejected if its year (beyond the allowed tolerance) or its first author contradicts the reference. Authors are compared by the name parts they share, so "al shahi salman" and "salman" count as compatible.
- Stage 5 needs a title of at least 4 words.

**Evidence:** a new column, `reference.resolution_evidence` (migration `80034d5e56ce`), holds the stages tried, near-misses, lookups and parser version. For DOI and PMID matches it also holds how well the cited title agrees with the matched title. Low agreement means the cited ID points to a work with a different title. That's left for C01 to deal with, and the ID match stands. Records that a source returns with no title aren't stored, but are noted as "registered". Searches that a service rejects (a 4xx error) are recorded and skipped.

Parser 1.1.0 (updating ADR-033's version) handles "et al" glued to initials, Harvard style ("Author, 2017. Title"), author lists ending in ":", hyphenated initials, and APA style with no journal. Precision on tagged strings is unchanged (title 99.2%, journal 99.6%). Untagged references whose fields came from an older parser version are parsed again; fields that came from XML tags never are.

**How the ≥90% target is measured:** §9.10 defines it on references from structured reference lists (`sentinel resolve --sample 1000 --structured`). On a seeded sample (seed 20260922) on 2026-10-25: **944 of 1,000 matched (94.4%)**, 1 ambiguous, 55 unmatched. By method: DOI 897, PMID 29, exact title 15, title plus author plus year 3, title only 1. For comparison, a sample of 1,000 drawn from all kinds of reference matches **88.7%**. Unstructured strings match 85.7%, and at least 68 of the 110 misses are grey literature (guidelines, agency reports, websites, books).

**Precision:** no title-stage match that could be checked was wrong. The three apparent disagreements were references whose DOIs are malformed in the source XML. A random read of title matches found none wrong.

**Turned down:** judging the target on the all-reference sample (it measures how much grey literature there is as much as how well matching works); loosening the author rule to win back about 2 references with odd author data in Crossref.

---

### ADR-035 — C01: how unmatched references are sorted, one flag per unverifiable reference, and safeguards against flagging real works
**Status:** active

**In short:** C01 flags only references that look like journal articles and have no plausible match, one flag each, and adds a summary per paper.

C01 (`checks/c01_existence.py`, v1.0.0) is pure. Using the stored matching result and its trail (ADR-034), it puts each reference in one class:
- **resolved** or **ambiguous**: the work exists.
- **doi_registered**: doi.org knows the DOI.
- **nonjournal**: the XML's `publication-type` isn't a journal, or there are clues that it's a book, web page, guideline, report, conference item, preprint, or has an organisation as author. Clues are looked for outside the journal name, because words like "Proceedings" and "Association" appear in real journal names.
- **near_match**: a recorded candidate looks like this work.
- **unparseable**: the title, first author, year or journal is missing.
- **journal_like**: everything else. Only this class is flagged.

The doi.org check (§9.5) runs during matching, because checks can't use the network. Its result is stored in the trail.

**Findings** (your decision): one `flagged`/`medium` finding for each journal-like reference, worded "Reference N could not be verified: no matching record in Crossref or OpenAlex[; its DOI is not registered at doi.org]", with its own evidence. Plus one summary finding per paper, with the counts and the class of every unmatched reference. The summary is `clean`, or `insufficient_data` when the paper has no references, some are still waiting to be matched, or none could be parsed.

**Safeguards against flagging real works** (they cost only recall):
- *near_match:* a candidate with the same first author, a year within ±1, and title similarity of at least `SENTINEL_C01_NEAR_MATCH_MIN` (0.6); or any candidate from the same year with similarity of at least `SENTINEL_C01_SAME_TITLE_MIN` (0.85). Both are settings with no default (ADR-022). Cases found in real data: Wu & McGoogan (*JAMA* 2020) just missed the stage-4 threshold; a 1985 paper's candidate had the first author "cn"; and "Emmanuel K" is "Kongo E" with the names swapped.
- The near-miss evidence now keeps every candidate with the same author, not just the top 3.

**Fixture:** 48 real references, each labelled by reading it, before looking at C01's output. All 48 agree, including 32 non-journal items (none flagged) and 3 journal articles that aren't indexed anywhere (flagged).

Paper report cards (`sentinel report <paper-id|doi>`) show each check's findings from its latest run, plus the cards of linked trials (§9.8).

**Turned down:** one finding per paper (separate problems would collapse into one line); a finding for every reference (about 38,700 rows per run); flagging journal-like references whose candidates suggest the work exists.

---

### ADR-036 — C02: the timing bands from §9.7, plus a low band for "retracted and replaced"
**Status:** active

**In short:** how serious a citation to retracted work is depends on when it was cited; works that were retracted and replaced with a corrected version get a gentler flag.

C02 (`checks/c02_retraction.py`, v1.0.0) applies ADR-017's three timing bands, with G = `C02_GRACE_MONTHS` (6). Time is counted in whole months from the *earliest* retraction notice to the citing paper's Europe PMC `firstPublicationDate`:
- more than G months after the retraction → high;
- 0 to G months after → low, "in press";
- on or before the retraction date → low.

Expressions of concern are medium, whatever the timing. Corrections are never flagged.

New, found in real data: Retraction Watch marks 927 retractions as "Retract and Replace" (the journal pulled a flawed version and published a corrected one), and Cochrane withdrawals appear as "Withdrawn as Out of Date". In this corpus, 3 of the 9 "cited after retraction" cases were of this kind: PREDIMED (*NEJM* 2013), a favipiravir COVID-19 study, and a Cochrane review. Flagging them high would mislead. So (your decision) a retraction where every notice gives one of those reasons gets its own band: **low**, worded "retracted and replaced or withdrawn as out of date; the current version should be checked". A retraction later reversed by a reinstatement isn't flagged. An unknown citing or retraction date gives `insufficient_data`.

Matching uses both the reference's own DOI/PMID and those of the paper it was matched to, so C02 works before matching is finished. Output follows ADR-035: one finding per affected reference, plus a summary per paper.

**Fixture:** 12 real corpus papers (13 references with notices), with the expected bands written from the notices and dates before C02 was run. The corpus has no real "in press" case, so that band and the edge rules (boundaries, reinstatement, earliest notice, unknown dates) are tested with made-up inputs. In the corpus, 6 references in 5 papers were cited more than 6 months after a plain retraction.

**Turned down:** applying §9.7's bands exactly as written (a third of the high flags would mislead); not flagging replaced works at all (that would lose the signal that the cited version isn't the current one).

---

### ADR-037 — C03: full text cached on disk, statistics only from body paragraphs, and rules to avoid false alarms
**Status:** active

**In short:** C03 recomputes each p-value from its test statistic, allows for rounding and common reporting habits, and found 16 small inconsistencies in the corpus.

**Full text** (your decision): each corpus paper's JATS XML (the standard XML format for journal articles) is cached under `SENTINEL_FULLTEXT_CACHE_DIR` (`data/fulltext/`, which git ignores; 999 papers, 108 MB). Body text isn't stored in the database. `sentinel extract stats` (which can resume after interruption) reads only body paragraphs. It skips tables, figure captions, the abstract (which repeats the results) and the reference list. Each test it finds is stored, with its top-level section, in `stat_test`, and `paper.fulltext_status` is set to `'full'` once the body has been examined. A paper's `stat_test` rows are derived data, and extracting again replaces them. Phase 2 can reuse the cache.

**Parser** (`parse/statistics.py`): it finds t, F, χ² (written as χ², χ2, X2 or chi-square), r and z, each with the p-value next to it. `t` and `r` must be lowercase, and `F` uppercase. **A z next to a rank test (Wilcoxon, Mann–Whitney, Kruskal–Wallis, Friedman, or U/V/W/T/H in the same sentence) is skipped.** In real data that z was an effect size, and the p-value came from the exact test. Before this rule, one paper got 7 false "gross" flags.

**C03** (`checks/c03_statcheck.py`, v1.0.0), following §9.7:
- t, r and z are treated as two-tailed; F and χ² are never halved.
- Rounding is respected on both sides. A statistic printed as 2.10 could be anything from 2.095 to 2.105, and p is compared at the number of decimals printed. The comparison sign (=, <, >) is respected.
- A result that is consistent as a one-tailed test isn't flagged (ADR-009).
- Inconsistent → low. Inconsistent in a way that changes significance at α (`C03_ALPHA`) → high (a "gross" error).
- Two common reporting habits count as consistent: "p = .001" used to mean ".001 or smaller", and "p < .000" meaning "rounds to .000". These removed 10 of 26 flags, all of them unremarkable. Accepting truncation (.0177 printed as .017) was tried and dropped: at two decimals it would accept .03 for anything up to .0399, including §9.7's own example of an inconsistent result.

Output follows ADR-035: one finding per inconsistent test, plus a summary per paper. `not_applicable` means no test that can be rechecked was found; `insufficient_data` means the body wasn't examined.

**Corpus (run 15):** 332 tests in 32 papers. 967 papers report none, which is expected for RCT reports, since they mostly give confidence intervals. 16 low-severity flags in 7 papers, none gross. Each flagged statistic was read in its sentence. Examples: "χ2(3) = 5.60, p = 0.905" (should be .133), "t (168.810) = 0.018, p = 0.86" (should be .986), "F(1, 112) = 0.64, p = 0.524" (should be .425).

**Turned down:** storing body text now (it would get ahead of Phase 2's design for text passages); extracting statistics with no cache (every fix to the extractor would mean downloading 1,000 papers again); checking tables (cells split across lines pair statistics with the wrong p-value).

---

### ADR-038 — C04: the GRIM test and when it applies, proven by fixtures; no means pulled from papers in Phase 1
**Status:** active

**In short:** GRIM works and is tested, but Phase 1 doesn't yet pull means out of papers, so it doesn't run on the corpus.

C04 (`checks/c04_grim.py`, v1.0.0) checks means given to it as a `MeanContext`: the mean as printed, N, whether the measure takes whole-number values, and the number of items per participant. **Nothing pulls means out of the corpus in Phase 1 (your decision).** Papers rarely say whether a measure takes whole-number values (a mean age could come from whole years or from dates of birth), so guessing would hurt precision. As ADR-021 expected, every corpus paper is `not_applicable` (run 16: 1,000 of 1,000), and C04 is proven by fixtures.

**When it applies:** all of these must be true, or the result is `not_applicable` with a reason code:
- the measure takes whole-number values (unknown counts as no);
- N is known and at most `C04_MAX_N`;
- at least `C04_MIN_DECIMALS` decimals are printed;
- N × items < 10^decimals. Beyond that, every mean is possible. This is a mathematical limit added to §9.7's conditions.

Each condition is tested both ways.

**Arithmetic:** whole-number totals on both sides of mean × N × items are tried, not just §9.7's `round(M × N)`. So a mean that sits on a rounding boundary (7/8, printed as 0.87 or 0.88) is never misjudged. The results were checked against a brute-force search over every possible total for 2,000 random cases.

**Output** follows ADR-035: a `medium` flag for each impossible mean ("Mean 3.45 for X can't arise from 18 whole-number values: no total divided by 18 rounds to it"), plus a summary per paper.

**Revisit:** in Phase 2, once tables are parsed. A cautious extractor would need N and a clear sign of a whole-number scale next to each mean.

**Turned down:** a text extractor now (it would find almost nothing in RCTs, needs new tables, and risks precision).

---

### ADR-039 — Stage timings in `stage_run`; `stats` and `export` read from runs
**Status:** active

**In short:** every stage now records how long it took, so `stats` can report speed alongside flag rates, and `export` writes findings to CSV or JSON safely.

§9.8 asks for `sentinel stats` to show the time taken by each stage, built in from the start, but only check runs recorded any timing. **Your decision:** a new `stage_run` table (migration `b41d7c2e9a10`). `ingest`, `resolve` and `extract stats` each write one row: stage, source, corpus, start, end, status (`done`, `failed`, or `stopped` when the OpenAlex budget runs out), number of items, and counts. The row is written at the start, so a killed run still shows when it began. One-off lookups (`--doi`, `--nct`) aren't recorded.

Check runs keep their timing in `check_run`, and its `counts` field now also holds:
- each check's wall-clock time, plus the time taken to load inputs;
- a **snapshot of the matching cascade** over the run's papers: counts by status, by matching method, and by which search found the title matches.

The snapshot is needed because matching again overwrites the `reference` table, so the ratios a run saw can't be rebuilt later.

`sentinel stats --run N [--format json]` shows:
- each check's flag rate: subjects with at least one flag, divided by subjects the check applied to (those where not every finding is `not_applicable`);
- each check's counts by status, and its flags by severity;
- the cascade snapshot;
- the total time of that corpus's stage runs, plus corpus-wide stage runs, that started before the check run.

Runs from before this ADR show "not recorded" rather than zeros.

`sentinel export --run N --format csv|json --out PATH` writes one row per finding, with the subject's IDs. Summaries can quote text from papers, which is untrusted. In CSV, a cell that starts with `= + - @`, a tab or a carriage return gets a leading apostrophe, so a spreadsheet can't run it as a formula. JSON keeps values exactly as they are.

**Turned down:**
- Timing kept only in `check_run.counts`: there would be no times for ingest or resolve, and S1.15's 36,900-reference resolve would go unmeasured.
- Rebuilding cascade ratios from `reference` when `stats` runs: the numbers would be wrong after any `--retry`.

---

### ADR-040 — The §9.9 fixture set: frozen check inputs and exact expected findings
**Status:** active

**In short:** 46 real, hand-checked examples. The tests fail if any check's output differs from the expected answer in any way.

`tests/fixtures/set/` holds 46 real items: 35 papers and 11 trials. Each file holds the exact input the checks receive, frozen from the database by `set/freeze.py`, plus hand-written `expected` findings and `notes`. `test_fixture_set.py` runs all five checks with the thresholds in `.env.example`, so changing a threshold there forces the fixtures to be checked again. It requires the findings to equal `expected` exactly: check ID, status, severity, which reference or statistic each finding is about, and C02's band. Any extra or missing finding fails the test.

How the expected answers were made:
- Every flag (6 from C01, 11 from C02, 16 from C03, 3 from C05) was checked by hand and has a written `why`:
  - C01: Crossref and OpenAlex searched by hand.
  - C02: the notice, its DOI and the month count worked out again.
  - C03: p recomputed with scipy for every statistic value that rounds to the printed one, and the sentence read in the full text.
  - C05: registry dates worked out again.
- Summaries and the classes of unmatched references were reviewed. The script that writes the fixtures refuses any flag without a written reason.

**What the set covers from §9.9:**
- C02: 9 papers, covering all three timing bands, plus retracted-and-replaced and an expression of concern.
- C03: 7 papers with hand-checked inconsistencies.
- C01: 5 papers with books, reports and package inserts, none flagged.
- 8 clean papers with no flags.
- C05: 3 overdue trials, 3 with results posted, and 5 out of scope (Phase 1, observational, outside FDAAA, before FDAAA, terminated).
- Edge cases: first authors with non-English characters, 27 DOIs with a trailing full stop, and references with no year.
- The four earlier false positives: C03's Wilcoxon Z, `p = .001` and `p < .000`, plus this session's three C01 cases.

The corpus has no real "retracted while in press" citation, so one was found outside it. A Europe PMC `CITES:` search found PMC7052980, published 52 days after a work it cites was retracted. It was added as its own one-paper corpus, `fixtures-in-press` (query `PMCID:PMC7052980`), using the normal `corpus select`, `ingest`, `extract stats` and `resolve` commands, so the main corpus is untouched.

C04 is `not_applicable` on every paper, as ADR-038 expects. Its made-up test cases stay in `test_c04.py`.

Building the set found **3 C01 false positives and 1 wording bug**. All are fixed and kept as fixtures; see the failure log.
- C01 1.0.1: a journal field that names a handbook or encyclopedia counts as non-journal.
- `normalize_title` decodes HTML entities.
- C02 1.0.1: "1 month", not "1 months".

**Turned down:**
- Made-up inputs for the set: §9.9 asks for real papers.
- Expected answers for flags only: an unexpected summary status must fail too.
- Storing only reference IDs and loading the rest from the database: tests never touch live data.

---

### ADR-041 — Titles keep their subtitles, and a new stage 3b matches titles that differ only by a subtitle
**Status:** active

**In short:** Sentinel was dropping Crossref subtitles, which caused false "could not be verified" flags. Fixing it cut C01 flags from 347 to 272.

Checking C01's full-corpus flags by hand (run 18) showed that Crossref keeps a subtitle in a separate `subtitle` field, and `CrossrefClient.normalise` stored only `title`. So a reference citing "Preventing abuse during pregnancy: an assessment and intervention protocol" was compared with "Preventing Abuse During Pregnancy", scored below every threshold, and was flagged as having no matching record, even though the work was stored. 64 of the 347 flags had this cause.

Decision:
- `paper.title` from Crossref is now `title: subtitle`, which is how references cite it. The 2,689 stored Crossref rows with a subtitle were rebuilt from their stored `raw` data, each checked through `PaperRecord`. Nothing had to be downloaded again.
- New cascade **stage 3b** (method `title_main`, confidence 0.85): one title equals the other up to its first colon (`main_title`, at least two words). The first author and year must *agree*, not just not contradict, because a subtitle can be all that separates two papers in a series. Two such candidates give `ambiguous`.
- `local_title_candidates` now also fetches stored titles that extend the reference's title, and titles equal to its main part. A title and its longer version can score below the trigram similarity floor, so trigram search alone wouldn't find them.
- Only the references the fix could change were matched again: 223 of the 4,309 unmatched or ambiguous ones, each against stored works, with no new searches. 217 matched and 6 became ambiguous. All 41 `title_main` matches were read: 40 are the cited work, and one is Crossref's record for that article's graphical-abstract figure.

C01 flags fell from 347 to 272, and flagged papers from 224 to 195. C01's own logic didn't change, so its `check_ver` stays 1.0.1.

**Turned down:**
- Also splitting titles at a full stop. That explains only 4 of the remaining 272 flags, and full stops end title sentences far more often than they start subtitles.
- A second Crossref search, on title plus author, for references still unmatched. It would fix the largest remaining cause, but it changes how candidates are found. Put off to Phase 2 (your decision), where an evaluation set can measure the effect.

---

### ADR-042 — Retry dropped connections and timeouts, not only 429 and 5xx responses
**Status:** active · amends §9.3 · answers Q9

**In short:** a network error that is probably temporary is now retried in the same way as a 5xx response, so one dropped connection no longer stops a long run.

In S1.15, Crossref reset a connection after 18,300 of 35,670 references (`httpx.ReadError`, WinError 10054), and the run stopped, because §9.3 said to retry "429 and 5xx responses only". Phase 2 starts with bulk fetches (S2.2), where a stopped run costs a human restart.

Decision:
- `RateLimitedHTTP` also retries `httpx.TimeoutException`, `httpx.NetworkError` (resets, refused connections) and `httpx.RemoteProtocolError` (the server closed the connection without answering). They're listed in `TRANSIENT_TRANSPORT_ERRORS` in `ingest/http.py`.
- Same exponential backoff and the same cap, `SENTINEL_HTTP_MAX_RETRIES`. When the retries run out, the original error is raised, so the run still fails loudly and resumes as before.
- Errors in the request itself are never retried: `UnsupportedProtocol`, `LocalProtocolError` and `ProxyError` would just send the same broken request again. A 4xx response other than 429 is still returned at once.
- Each retry is logged with the error's class name (`http_retry`, field `error`).

Tests in `test_http.py`: five kinds of transport error are retried and then succeed, the cap re-raises the original error, and two request errors aren't retried.

**Turned down:**
- Leaving §9.3 as it was. Resuming loses only the batch in progress, but S2.2 makes tens of thousands of requests.
- Retrying every `httpx.TransportError`. That includes errors that a retry can't fix.

---

### ADR-043 — Local Postgres moves to a pgvector container; MLflow keeps its data in the same server
**Status:** active

**In short:** Phase 2 needs pgvector, and the Windows Postgres 17 service doesn't have it. The database now runs in Docker, and MLflow stores its runs there too.

pgvector isn't shipped with the Windows installer, and building it needs the Visual Studio build tools. Phases 4 and 5 use Docker Compose anyway (§13).

Decision (your choice):
- `docker/compose.yaml` runs `pgvector/pgvector:0.8.6-pg17`. It's the same major Postgres version as before, and the tag was chosen over 0.8.7, which was released the day of the move. It listens on **127.0.0.1:5434**, because another local Postgres server already holds 5433. The data lives in the named volume `sentinel_pgdata`. URLs use `127.0.0.1`, not `localhost`, because the container doesn't listen on IPv6.
- `docker/postgres/init.sh` creates the `sentinel` role, which **is not a superuser**, and its three databases: `sentinel`, `sentinel_test` and `mlflow`. pgvector isn't a trusted extension, so a non-superuser can't create it. The superuser installs it in `template1`, so every database created afterwards already has it. Migrations say `CREATE EXTENSION IF NOT EXISTS vector` and never drop it, just as the first migration does with `pg_trgm`.
- The Phase 1 database was copied with the Postgres 17 `pg_dump` and `pg_restore` (198 MB dump). Every table's row count and the Alembic head (`b41d7c2e9a10`) match the old server exactly. The full test suite passes against the container (700 tests), and `sentinel stats --run 19` matches the metrics ledger. The old Windows database is untouched, as a fallback.
- The container passwords are new random values, `POSTGRES_PASSWORD` and `APP_DB_PASSWORD` in `.env`. Their names aren't `SENTINEL_`-prefixed, so `Settings` ignores them. This also retires the placeholder password left over from Phase 1.
- **MLflow** 3.17 is an optional extra (`pip install -e ".[ml]"`), so the Phase 1 pipeline doesn't load it. `dotenv run -- mlflow server` reads `MLFLOW_BACKEND_STORE_URI` (the `mlflow` database) and `MLFLOW_ARTIFACTS_DESTINATION` (`data/mlartifacts/`, gitignored) from `.env`, so the password never appears on a command line. A smoke run logged a metric and an artifact, then was purged. Sentinel's own MLflow setting is added in S2.6, alongside the code that reads it (§7).

**Turned down:**
- Building pgvector for the Windows service: it needs build tools and admin rights, and has to be repeated for every Postgres upgrade.
- Making `sentinel` a superuser so migrations can create pgvector themselves: the application role doesn't need that power.
- A SQLite file for MLflow: the model registry works on either, but Postgres is already running and keeps everything in one backup.

---

### ADR-044 — The Phase 2 schema: §10.2's sketch with versioned passages, one row per vector, and link tables
**Status:** active · finalises §10.2

**In short:** the tables follow §10.2's sketch, with five changes (your choice) that keep evaluation labels valid and let two models be compared.

Migration `6bb29bc195ff` adds `passage`, `passage_embedding`, `citation_context`, `citation_context_reference`, `claim`, `eval_item` and `eval_item_gold_passage`. Changes from the sketch:
1. **Vectors have their own table**, `passage_embedding(passage_id, model, embedding vector(768))`, keyed on both columns. The sketch's single `embedding` column couldn't hold two encoders' vectors, and the model registry plans to compare them. The HNSW index (cosine) covers the whole table. Searches filter on `model`, and pgvector 0.8's iterative index scans stop that filter from shortening the results. MedCPT and SPECTER2 both make 768-number vectors; a model of another width needs a migration.
2. **Passages carry `splitter_ver`**, unique on `(paper_id, splitter_ver, ordinal)`. A new splitter adds rows alongside the old ones, so passage IDs that gold labels point at never change meaning.
3. **A citation context can cite several references** through `citation_context_reference`, because "[3–5]" is one sentence citing three works. Contexts are unique on `(citing_paper_id, extractor_ver, ordinal)` so re-runs don't duplicate them, and they keep the `marker` as printed.
4. **Every evaluation item records its text's licence** (`license`, not null). Open-access papers include non-commercial licences, so this decides what may be exported.
5. **Gold passages are a link table, not a `BIGINT[]`.** This one wasn't in the list you agreed to; it serves item 2. An array can't have a foreign key, so deleting a passage would silently break gold labels. `eval_item_gold_passage` refers to `passage` with `ON DELETE RESTRICT`, so such a delete fails.

Also: `claim` is unique on `(citation_context_id, extractor, extractor_ver)`, and the new tables that receive rows over time have `created_at`. pgvector's SQLAlchemy type comes from the `pgvector` Python package (0.5.1), which registers `vector` for reflection, so `alembic check` reports no drift.

Tests (`test_migrations.py`): up, down, up; the index, keys and foreign-key rules exist; and, on real rows, a vector round-trips and the nearest-neighbour query returns the right passage, a second model's vector is a separate row, a "[3–5]" context links to three references, deleting a gold passage fails, an item with no licence fails, and duplicate embeddings and contexts are refused. Applied to the working database; the extension is left in place on downgrade, as with `pg_trgm` (ADR-043).

**Turned down:**
- One `vector` column per model on `passage`: every new candidate model would need a migration.
- A vector column with no fixed width: HNSW indexes need a fixed width.
- CHECK constraints on `section`, `set_name`, `split` and `label`: Phase 1 validates such values in Pydantic, not in the database, and the value lists may still change during Phase 2.

---

### ADR-045 — Full text from Europe PMC's open-access records only, recorded per work in `text_availability`
**Status:** active · answers Q2

**In short:** each work's text is looked up in Europe PMC. Open-access full text is cached on disk, and every other work keeps its abstract. **22.6% of resolved references cite a work whose full text Sentinel holds**, so that is the most C09 can check against full text.

How it works (`ingest/fulltext.py`, `sentinel ingest fulltext`, `sentinel corpus coverage`):
- The works are the corpus's papers plus every work their references resolved to: 33,664 works, 32,689 of them cited.
- Europe PMC is searched 25 works at a time, by quoted DOI. A work the DOI search misses is tried by PMID (source MED). When several records match one work, the preference order is open access with a PMCID, then any PMCID, then PubMed's record.
- **Full text** means the record is open access (`isOpenAccess` Y, with a PMCID) and its JATS XML has body paragraphs. The XML goes into `SENTINEL_FULLTEXT_CACHE_DIR`, the cache ADR-037 started, so nothing is downloaded twice.
- Otherwise the work keeps its **abstract**: Europe PMC's, else the one in its stored Crossref record, else OpenAlex's inverted index, rebuilt into text. Markup is stripped, including markup some sources escape, while real "<" signs stay ("18.5<BMI<25"). Placeholder text such as "Abstract is missing." isn't counted.
- Results go to a new table, `text_availability` (migration `8d0bd1c5a522`). It holds one row per work with the lookup key, PMCID, open-access and PMC flags, the licence as Europe PMC states it, `fulltext` (`full`, `abstract` or `none`), any fetch error, and the abstract with its source.
- `paper` stays as its first source wrote it (ADR-032), apart from `fulltext_status`, which is only ever raised. That's because C03 relies on 'full' meaning "the body was examined" (ADR-037).

Results on `pmc-rct-2015-2024` (stage run 16):
- Corpus papers: 999 full text, 1 abstract only (PMC6561645, whose XML still returns 500).
- Cited works: 7,437 full text (22.8%), 23,008 abstract only, 2,244 neither.
- References: 7,825 of 34,593 resolved references (**22.6%**) cite a work with full text, which is 20.2% of all 38,685 references. 92.8% of resolved references cite a work with at least an abstract.
- The median corpus paper has full text for 19.5% of its resolved references (interquartile range 10.7–29.6%).
- Coverage depends on the cited work's age: before 2000, 0.2%; 2000–09, 4.8%; 2010–14, 19.8%; 2015 on, 37.8%.
- Another 5,480 cited works are in PMC but not open access, so no XML is served for them.
- Licences of the cited full text: CC BY 4,842; CC BY-NC 1,138; CC BY-NC-ND 734; not stated 512; CC BY-NC-SA 143; CC0 62; CC BY-ND 5; CC BY-SA 1. About a third are non-commercial or no-derivatives, so they matter for anything exported (ADR-044's licence column).
- 51 open-access records (0.6% of 8,478 attempts) answered 500 on every retry. Each is recorded as `http_500` and kept as an abstract, which understates coverage by at most 0.15 points. A re-run retries them.
- Cost: no money, 8,427 XML files (1.1 GB, gitignored), and about 3.3 hours of fetching. The stage row shows 14 hours because the machine slept for 10.7 of them.

**Turned down:**
- Writing Europe PMC's PMCID, open-access flag and licence into `paper`: it breaks the first-source rule (ADR-032), and a Crossref row's `license` is the publisher's text-mining URL, a different thing.
- Asking for XML for every record with a PMCID: Europe PMC answers 500 for the ones that aren't open access, after five retries each.
- Measuring Q2 on 100 papers, as the old open question suggested: the whole corpus took one run.

---

### ADR-0NN — template
**Status:** active

*The background in one or two sentences. What was decided. Why. What was turned down, and why not.*

---

## Model registry

Every model in the system: what it does, where it runs, why it was chosen, and how it scored.

| Role | Model | Where it runs | Why chosen | Main metric | Status |
|---|---|---|---|---|---|
| Dense retrieval | *TBD: MedCPT vs SPECTER2 vs a general model* | TEI container | Comparison pending, week 4 | nDCG@10 | not selected |
| Reranker | *TBD: bge-reranker-v2-m3 vs MedCPT cross-encoder* | TEI container | Comparison pending, week 5 | nDCG@10 improvement | not selected |
| Claim entity recognition (NER) | *TBD: GLiNER-biomed vs a fine-tuned encoder* | local | Pending, week 6 | entity F1 | not selected |
| Outcome equivalence | *TBD* | local | Pending, week 7 | macro F1 | not selected |
| Entailment, zero-shot | *TBD: an NLI head* | local | Baseline, week 7 | macro F1 | not selected |
| Entailment, fine-tuned | *TBD* | local | Week 8 | macro F1 | not selected |
| Judge | *API model* | LiteLLM router | Teacher for distillation, week 8 | macro F1, $ per 1,000 | not selected |
| Distilled verdict model | *trained from the judge* | local | Week 10 | share of the judge's F1 kept, cost ratio | not built |
| Tabular baseline | Gradient boosting | local | Honest comparison point | PR-AUC | not built |
| Vision, for figures | *TBD: a vision-language model* | API | Week 13 | accuracy of numbers read from figures | not selected |

**How models will be chosen, decided in advance so results can't be explained away afterwards:**
- Retrieval and reranking: nDCG@10 on the held-out query set. Ties are broken by speed.
- Classifiers: macro F1 on the real held-out set, not the generated one. Ties are broken by calibration error.
- Judge: macro F1 first, cost second. It's the teacher, so quality matters most.
- Distilled model: must keep at least 85% of the teacher's F1. Below that, the distillation failed, and it gets reported that way.

### Where things live

| Item | Path or identifier |
|---|---|
| MLflow tracking | `http://127.0.0.1:5000` (`dotenv run -- mlflow server`); runs in the `mlflow` database, artifacts in `data/mlartifacts/` (ADR-043) |
| Model registry | MLflow, tagged by stage |
| Fixture corpus | `tests/fixtures/` |
| Generated evaluation set | *TBD* |
| Hand-labelled held-out set | *TBD* |
| Evaluation reports | `docs/evals/` |
| Config | `.env`, `src/sentinel/config.py` |
| Full-text cache | `data/fulltext/` (`SENTINEL_FULLTEXT_CACHE_DIR`): JATS XML by PMCID, 8,427 files, 1.1 GB (ADR-037, ADR-045) |

### Graveyard

*Models that were tried and dropped. Keep them here: "I tried X and it did Y worse" is a stronger interview answer than never having tried.*

| Model | Role | Why dropped | Number |
|---|---|---|---|
| — | — | — | — |

---

## Experiment log

Add a row whenever you get a number. Bad numbers especially: they're the raw material for the post-mortem.

| Date | ID | Question | Setup | Result | Verdict |
|---|---|---|---|---|---|
| — | E001 | *example: does reranking beat fusion alone?* | *1k queries, held out* | *nDCG@10 0.61 → 0.68* | *keep* |

---

## Metrics ledger

The headline numbers at the end of each phase. These become the evaluation report.

| Date | Phase | Metric | Value | Notes |
|---|---|---|---|---|
| 2026-10-28 | 1 | Match rate, structured sample (§9.10) | 94.4% (944/1,000) | S1.9 sample, seed 20260922 |
| 2026-10-28 | 1 | Match rate, all corpus references | 89.4% matched, 10.1% unmatched, 0.5% ambiguous | 38,685 references; check run 19 |
| 2026-10-28 | 1 | How references were matched | DOI 84.2%, exact title 13.3%, PMID 1.3%, title+author+year 1.0%, title without subtitle 0.1%, title only <0.1% | Share of the 34,593 matched |
| 2026-10-28 | 1 | Where title matches were found | Crossref 3,846, stored works 659, OpenAlex 510 | 5,015 title matches |
| 2026-10-28 | 1 | Cost per 1,000 references matched | $0.048 of OpenAlex searches | $1.71 over 35,670 references; Crossref and doi.org are free |
| 2026-10-28 | 1 | Time to match the full corpus | 2 h 41 min | Runs 11–14; about 220 references a minute |
| 2026-10-28 | 1 | Time to run all five checks | 6.1 s | 1,000 papers, 1,034 trials; 3.9 s of it loading inputs |
| 2026-10-28 | 1 | Report card from a DOI | 4.62–4.75 s | Five runs, idle machine; under the 5 s target (§9.10) |
| 2026-10-28 | 1 | Flag rate, C01 | 19.5% of papers (195/1,000); 272 references | 8 of 20 sampled flags were correct (95% CI 22–61%), so this is an upper bound |
| 2026-10-28 | 1 | Flag rate, C02 | 2.3% of papers (23/1,000); 25 citations | All 25 checked by hand: right notice and band |
| 2026-10-28 | 1 | Flag rate, C03 | 21.2% of papers it applies to (7/33); 16 low, 0 gross | Each read in its sentence (S1.12) |
| 2026-10-28 | 1 | Flag rate, C04 | does not apply (0/1,000) | No mean extraction in Phase 1 (ADR-038) |
| 2026-10-28 | 1 | Flag rate, C05 | 21.6% of corpus trials in scope (69/320) | 10 sampled, all agree with the registry record |
| 2026-10-28 | 1 | False positives on clean fixtures | 0 | 8 clean papers, `test_fixture_set.py` |
| 2026-11-01 | 2 | Cited-source full-text coverage (Q2) | 22.6% of resolved references (7,825/34,593); 20.2% of all references | ADR-045; stage run 16 |
| 2026-11-01 | 2 | Cited works with full text / abstract only / neither | 7,437 / 23,008 / 2,244 of 32,689 | 92.8% of resolved references have at least an abstract |
| 2026-11-01 | 2 | Full-text coverage by cited work's year | <2000 0.2%; 2000–09 4.8%; 2010–14 19.8%; 2015+ 37.8% | Older evidence is mostly abstract only |
| 2026-11-01 | 2 | Full-text fetch | 8,427 XML files, 1.1 GB; 51 persistent 500s (0.6%) | About 3.3 h of fetching; $0 |

**Metrics to track from week one, not work out afterwards:**
- Cascade ratios: how many items enter each stage
- Cost per thousand items, per stage
- Median (p50) and 95th-percentile (p95) time per stage
- Match rate, by method
- Flag rate per check
- False positive rate on clean fixtures

---

## Failure log

What broke, what was surprising, and what was got wrong. Write it the day it happens.

This section becomes deliverable five, the post-mortem. Most candidates ship a README and stop; a post-mortem is what convinces a hiring manager you've shipped real work before.

### Template

**Date · one-line title**
What happened. What you expected instead. The root cause. What changed as a result. Whether a fixture or test now covers it.

**2026-11-01 · Europe PMC answers 500, not 404, for full text it won't serve**
Asking for `fullTextXML` for a PMC record that isn't open access returns HTTP 500 with a short error body. The client expected 404 for "not available", and since 500 is retried, each such request would have spent five retries and about 30 seconds before failing. Found by testing three non-open-access PMCIDs before the run. The fetch now asks only for open-access records (ADR-045), and `EuropePMC.fulltext_xml` documents it. PMC6561645's long-running 500 may be the same thing, since it is the one corpus paper Europe PMC won't serve. `test_fulltext.py` covers it.

**2026-11-01 · The machine slept for ten hours in the middle of the full-text run**
The log stops at 09:17 UTC and resumes at 19:59. The first request after waking hit a dropped connection (`httpx.ReadError`), which was retried under ADR-042 and the run finished normally: the failure that stopped S1.15's run, handled. The stage row's 14-hour duration includes the sleep, so wall time per stage overstates work when a laptop sleeps.

**2026-11-01 · Some sources escape their markup inside abstracts**
Spot-checking stored abstracts mid-run found Crossref abstracts reading "<em>Introduction:</em><strong>…", because the source had escaped the tags (`&lt;em&gt;`), so they reappeared after unescaping. Removing every tag-like string after unescaping would also delete real text, since Europe PMC abstracts contain "18.5<BMI<25". The fix removes only known HTML tag names after unescaping, and drops placeholders such as "Abstract is missing.". The 16 affected stored abstracts were cleaned in place, each validated through `TextAvailability`; 3 placeholders now count as no abstract. Tests in `test_fulltext.py` use both real cases.

**2026-10-31 · Three surprises moving the database into Docker**
- Port 5433 was already taken by another Postgres server on this machine, so Docker refused to start the container. It now uses 5434.
- pgvector isn't a trusted extension, so the plan to let migrations create it as the database owner would have failed in S2.1. The superuser installs it in `template1` instead (ADR-043).
- MLflow printed an emoji when a run ended and crashed on the Windows console's cp1252 encoding (`UnicodeEncodeError`), after the run had been logged. Running with `PYTHONIOENCODING=utf-8` fixes it. S2.6's code that logs to MLflow must not depend on the console's encoding.

**2026-10-18 · The ClinicalTrials.gov OpenAPI URL in ADR-019 returns 404**
`/api/oas/v2/ctg-oas-v2.yaml` no longer exists; the spec is now served as YAML at `/api/oas/v2`. Found while checking endpoints before S1.4. Fixed in `config.py` and `.env.example`. Lesson: a URL written from memory or copied from an old document goes stale, so check each one live.

**2026-10-28 · Most of C01's full-corpus flags were works that exist**
The fixture set had shown C01 flagging only works that aren't indexed, so corpus flags were sampled before their rate was published. Of 20 sampled from run 18, 6 were in Crossref. Four of those had one cause: Sentinel stored Crossref titles without their subtitles. That was fixed (ADR-041), and C01's flags fell from 347 to 272. A fresh sample of 20 from run 19 still found 12 works that exist, from seven smaller causes (see the README's section on C01's limits). The biggest is that Crossref's search on the whole reference string misses works that a search on title plus author would find. Lesson: a hand-picked fixture set doesn't show how a check behaves across the corpus; checking a sample of corpus flags is what showed it.

**2026-10-27 · A reset connection stopped the corpus matching run**
After 18,300 of 35,670 references ($0.895 of OpenAlex spent), Crossref reset a connection (`httpx.ReadError`, WinError 10054), and `sentinel resolve` exited with an error. `RateLimitedHTTP` retries only 429 and 5xx responses, as §9.3 says, so a network error isn't retried at all. Only the batch in progress was lost: finished batches were already saved, stage run 11 was marked `failed`, and a re-run picked up the 17,370 references still waiting. Retrying network errors too (resets, timeouts) would change a documented rule, so it's left as an open question (Q9) rather than changed.

**2026-10-27 · A report card took 10 seconds, not under 5**
Checking item 4 of §9.10 timed `sentinel report <doi>` at about 10 s. Building the card took 0.1 s; the rest was start-up. `sentinel.cli` imported every client, the matcher and the check registry, and C03 imported scipy as soon as it loaded, which alone took 4.4 s. Now scipy loads only when C03 first computes a p-value, and `cli.py` imports clients, the matcher and the checks inside the commands that use them. The card now takes 4.6–4.8 s measured while a matching run was going in the background, and 4.62–4.75 s over five runs on an idle machine (2026-10-28). The margin is thin: about 0.3 s is the card itself, and the rest is SQLAlchemy, pydantic-settings, structlog, typer and the Postgres driver loading, so going faster would mean restructuring around those libraries. Tests that replaced `cli.run_checks` now replace `sentinel.checks.runner.run_checks`.

**2026-10-27 · Building the fixture set found three C01 false positives**
Each flag was searched for by hand in Crossref and OpenAlex before being accepted, and three turned out to be real works:
- **Davidson 1989, "Handb. Neuropsychol.":** a chapter in a handbook, which the XML labels as a journal article. C01 removes the journal name before looking for book clues, and its list of clues only had "handbook" written out in full. A book named in the journal field now counts as non-journal (C01 1.0.1).
- **Kannaiyan 2022:** Crossref lists the work with the author's names swapped, and its title contains `&amp;`. That HTML entity put an extra "amp" word into `title_norm`, which held the similarity at 0.849, just under the 0.85 same-title rule. `normalize_title` now decodes entities. The 22 stored titles with entities were normalised again, and the reference now scores 0.870, a probable match.
- **Proulx 2017:** matched on 2026-10-25, before the glued-text fix (committed 2026-10-26). Matched again now, it finds OpenAlex's record by exact title.

152 references came out unmatched or ambiguous before that fix. The 19 in fixture papers were retried now, and 1 changed; the other 133 were to be retried in S1.15. Separately, C02 printed "(1 months before publication)". This was found on the real in-press case and fixed in C02 1.0.1.

**2026-10-27 · TEST_DATABASE_URL in .env broke every command**
In S1.6 I assumed pydantic-settings ignores `.env` keys without the `SENTINEL_` prefix. It doesn't. With `extra="forbid"`, `TEST_DATABASE_URL` stopped the settings from loading, so every `sentinel` command and many tests failed as soon as the test database was set up. `config.py` now passes only `SENTINEL_` keys from `.env` to validation, so other tools' keys are ignored and a misspelt `SENTINEL_` key still fails. Tests are in `test_config.py`. With the test database in place, the full suite runs: 598 passed, 0 skipped.

**2026-10-26 · C03's first pass over the corpus flagged things no reviewer would**
Before the precision rules, one paper got 7 "gross" z-flags such as "Z = 0.777, p = 0.002" inside Wilcoxon results, where Z was an effect size and p came from the exact V test. Another 10 flags were "p = .001" used to mean ".001 or smaller", and "p < .000". Each was caught by reading the sentence behind every flag before trusting the count. Rules were added (ADR-037). A third rule (accepting truncation) was tried and taken out when it let §9.7's own example of an inconsistent result pass. The run made with the old logic (run 14) was deleted, so check version 1.0.0 refers to one set of rules only.

**2026-10-25 · Housekeeping before S1.12**
- Text from tagged citations no longer runs neighbouring elements together; 7,180 stored strings were rebuilt from the saved XML without downloading again.
- Downloading again no longer wipes fields the parser filled in: a field the XML supplies replaces the stored one, and a missing one keeps it.
- DOIs with a link stuck to them are cleaned (1 stored DOI fixed and matched again).
- Title agreement was filled in for 1,454 older DOI/PMID matches; 36 of 1,569 are below 0.6.
- Tests read `TEST_DATABASE_URL` from `.env`.
- Fixture sources, redactions and licences are listed in `tests/fixtures/README.md`. The Retraction Watch licence was unconfirmed, because neither Crossref's repository nor its documentation names one. *Resolved 2026-10-29:* Crossref's announcement of the data in its API says Crossref metadata is "freely available to reuse without a license" and asks for a citation in published work. The 20-row sample stays, with that citation.

**2026-10-25 · A wrong corpus label made commands quietly do nothing**
`SENTINEL_DEFAULT_CORPUS_LABEL` was still `dev-sample`, so `sentinel resolve` reported "0 references to resolve", and `sentinel check --checks C01` saved an empty run as if it had succeeded. Now both fail and list the known corpus labels, and the runner refuses to save a paper-check run with no papers. Tests are in `test_cli.py`. The `.env` default is set to `pmc-rct-2015-2024`.

**2026-10-25 · Single bad records stopped whole matching runs**
First, OpenAlex returns HTTP 400 when a title filter contains `?` or `*`, which it treats as wildcards ("…what is needed?"), and that one reference stopped the sample run after 701 references. Second, a DOI whose Crossref record has no title failed `PaperRecord` validation and stopped the structured run. Fixes: remove `?` and `*` from OpenAlex title filters; treat a 4xx error on a search as "no candidates from this source" (and record it); note records with no title as "registered" without storing them. Each fix has a test. Lesson: a problem with one external record must fail that record, not the whole run.

**2026-10-25 · Some citations give a DOI that belongs to a different paper**
In the structured sample, a reference titled "Effects of caffeine on exercise performance in sedentary females" (2010) gives a DOI that belongs to "Effects of caffeine on time trial performance in sedentary men" (2012). Stage 1 trusts DOIs (§9.5), so the match goes to the DOI's paper. Title agreement is now recorded in the evidence for every DOI/PMID match, so C01 can bring these cases to light.

**2026-10-25 · Text from tagged citations runs identifiers together**
In S1.6, `raw_string` for a `mixed-citation` with child tags joins neighbouring elements with no space ("…1636–16442716813510.1007/…"). The citations have no whitespace there, and `jats._text` just joins the pieces. It doesn't affect parsing, because those references already have fields taken from the tags, and the parser only runs on untagged ones. But it makes the strings hard to read when shown as C01 evidence. A spacing fix in `jats.py` plus downloading again would clean it up. Not done in S1.8. *(Later fixed; see "Housekeeping before S1.12".)*

**2026-10-25 · OpenAlex went from free to charging by the dollar**
ADR-020's speed estimate (40,000 references in about an hour at 10 requests a second) assumed OpenAlex was free. Its response headers now include `X-RateLimit-Limit-USD: 0.1`, and a search costs $0.001, so without a key only about 100 searches a day are possible. Found while checking endpoints before S1.7. ADR-032 makes Crossref the main source and puts OpenAlex searches behind a budget. `test_lookups.py` tests the guard.

**2026-10-24 · Europe PMC's references API quietly leaves out unmatched references**
I expected the structured reference list to be the paper's whole bibliography. Instead it returns only the entries Europe PMC matched to its own records: 49 of 54 for PMC11662519, and every one it returned had a PMID. That would have hidden every case C01 is meant to find. Caught by comparing it with the full-text `<ref-list>`. ADR-030 switches to the XML. `test_europepmc.py` checks the full counts (54/59/47).

**2026-10-18 · ADR-018's removal list missed some personal data**
The live v2 format also has PI names in `responsibleParty` and contact emails in `resultsSection.moreInfoModule.pointOfContact`, and real records fill them in. ADR-018 listed only the contacts module. ADR-025 extends the list. `test_clinicaltrials.py` checks every path on real records and on a made-up record that has all five.

---

## Open questions

| Question | Why it matters | Answer needed by | Status |
|---|---|---|---|
| Which starting corpus? | Affects how fixtures are designed and whether prevalence can be compared with published figures | Week 1 | **answered: ADR-021** |
| Should `config.py` have defaults, or require every setting? | Decides whether `.env.example` is just documentation or required setup | Week 1 | **answered: ADR-022** |
| Is a GPU available, or should one be rented? | Decides whether encoders and fine-tuning run locally or through an API | Week 6 | open. Not needed until Phase 3; renting by the hour would probably cost under $100 in total. Tracked as Q1 |
| What share of *cited* sources have full text available? | Limits how many claims can be checked against their source. This is different from coverage of the citing papers, which is about 100% by design | Week 4 | **answered: ADR-045** (whole corpus, not 100 papers). Tracked as Q2 |
| Publish findings about individual papers, or only totals? | Wrongly accusing someone of making up a reference would do serious harm | Week 10 | open. Current leaning: rule-based checks may name papers, model-based checks report rates only. Tracked as Q4 |
| Q1: Is a GPU available? | Decides whether encoders and training run locally or through an API | Before S2.4 | open. The development machine has no NVIDIA GPU (checked 2026-10-31), so local serving means CPU |
| Q2: What share of cited sources have open full text? | Limits how much C09 can check | Measured in S2.2 | **answered: ADR-045.** 22.6% of resolved references cite a work with full text; 92.8% at least an abstract |
| Q3: What exact Europe PMC query picks the corpus? | Decides what the working corpus is | S1.6 | **answered: ADR-028** |
| Q4: Publish findings about individual papers, or only totals? | Wrongly accusing someone of making up a reference would do serious harm | Before S5.8 | open |
| Q5: Where can earlier versions of registered outcomes come from? The public v2 API has no history endpoint. Options: the website's change-history tab (check the terms of use); a third-party dataset that rebuilds the history (check the licence and access); or the current record only, which supports only the weaker claim "the registry and the paper don't match" rather than "the outcome was switched". | Decides what C06 can honestly claim | Before S3.9, ideally by the end of Phase 1 | open |
| Q6: Which outside labelled dataset, and is its licence compatible? | Whether the external evaluation is valid | Before S2.8 | open |
| Q7: Which LLM for extraction and judging, and what's the spending cap? | Cost and reproducibility | Before S2.7 | open |
| Q8: Should C05 cover only trials the US law could plausibly apply to (a US site or an FDA-regulated product)? 461 of 1,034 corpus trials are flagged, 393 of them not industry-funded and many entirely outside the US. | Precision of C05's headline rate (ground rule 4) | Before the S1.14 fixtures | **answered: ADR-031** |
| Q9: Should `RateLimitedHTTP` also retry network errors (connection reset, timeout), not only 429/5xx responses? §9.3 says "429 and 5xx responses only", and a reset Crossref connection stopped the S1.15 matching run. | One dropped connection stops a long run. Resuming loses only the batch in progress, but someone has to restart the run | Before the Phase 2 bulk runs | **answered: ADR-042** |

---

## Glossary

Medical and statistical terms, since the project covers two fields.

**GRIM** (Granularity-Related Inconsistency of Means): if N people each give a whole-number answer, their mean must be some whole number divided by N. If a reported mean can't be written that way, it can't have come from the stated data.

**Outcome switching:** a trial registers one main (primary) outcome but publishes a different one. It happens in roughly 31% of trials, and is linked to reported effects about 16% larger.

**Quotation error, or citation distortion:** the cited source is real, but doesn't support the claim it's cited for. This happens in about 17% of quotations in medicine, and about half of those are major.

**Expression of concern:** a journal's notice that a paper may be unreliable, short of fully retracting it.

**Entailment, or NLI (natural language inference):** deciding whether text A supports, contradicts, or says nothing about text B. It's the basic building block for checking whether a source supports a claim.

**Calibration:** whether a model's stated confidence matches how often it's actually right. A model that says "90% sure" should be right 90% of the time.

**Distillation:** training a small model to copy a large model's answers, so it behaves similarly at lower cost.

**Reciprocal rank fusion:** combining the rankings from several search methods by adding up 1 ÷ rank for each item.

**Cascade:** sending work through a series of steps that get more expensive, passing on to the next step only what earlier steps couldn't settle.
