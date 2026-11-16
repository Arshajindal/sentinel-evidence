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
**Last worked:** 2026-11-15:
- S2.9, the tabular baseline, built and checked with placebo labels; it trains on S3.4's labels (ADR-057);
- both problems the full claim run found, fixed (ADR-056): contexts 1.1.0 and claim extractor 1.1.0, with 0.63% of claims now invalid.

2026-11-14: the full claim skeleton run (S2.7), reading its failures (failure log), the reranker re-measured on full text (ADR-055), and batch mode on the current ASU gateway (ADR-054). 2026-11-12: S2.7 (claim skeletons; Q7 answered, ADR-053), the end of S2.4's embedding run, and the HNSW index drop (ADR-052). Before that, 2026-11-11: S2.6 (the retrieval ablation, ADR-050), S2.8 (the synthetic set, ADR-051), and the fix and repair of S2.2's abstract cleaner (failure log). 2026-11-10: S2.4 (passage embeddings; Q1 answered), S2.5 (retrieval) and S2.6a (SciFact; Q6 answered).

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
| S2.2 Full-text fetch | done | `ingest/fulltext.py`, `sentinel ingest fulltext`, `sentinel corpus coverage`, migration `8d0bd1c5a522` (ADR-045). All 33,664 works checked in Europe PMC; 8,427 open-access XML files cached. **Q2: 22.6% of resolved references cite a work with full text**, 92.8% at least an abstract. Europe PMC answers 500 for non-open-access full text, so only open-access records are requested. The abstract cleaner cut text after a bare "<"; fixed, and the 5,989 abstracts it changed repaired, on 2026-11-11 (failure log). |
| S2.3 Passages and citation contexts | done | `fulltext/` (body, sentences, passages, contexts, extract), `sentinel extract passages\|contexts` (ADR-046). 290,977 passages from all 31,420 works with text, abstracts included (293,067 after the 2026-11-11 abstract repair). 35,921 contexts; **96.3% of references have a citing context**, linked through the XML ref ids (labels agree for 99.87% of numbered citations). **Contexts 1.1.0, 2026-11-15** (ADR-056): a numeric citation after a full stop and a space now stays with its sentence, and each context stores its sentence's citation spans; 2,421 contexts changed. Passages keep the 1.0.0 rules. |
| S2.4 Embeddings | done |`retrieve/encoders.py`, `retrieve/embed.py`, `sentinel embed passages` (ADR-047). Q1: no GPU, so MedCPT runs in-process with PyTorch on CPU, with the model card's exact title-and-passage input; TEI measured 16x slower. **All 265,424 cited-work passages embedded** (263,440 before the abstract repair), finished 2026-11-12 with no failures; the last of three runs did 115,056, model stored as `ncbi/MedCPT-Article-Encoder@d05a736da4bb`; stored vectors equal the model card's output. Corpus papers' own passages wait for Phase 3. |
| S2.5 BM25, dense, fusion, rerank | done | `retrieve/` (bm25, dense, fusion, search; query and cross encoders), `sentinel retrieve build-bm25` and `sentinel retrieve search` (ADR-048). BM25 index of 265,424 passages, rebuilt after the abstract repair. **The debug command returns the top passages for a claim or a citation, with every stage's rank.** Dot and cosine both implemented; S2.6 decides. |
| S2.6a External set (SciFact) | done | `eval/scifact.py`, `sentinel eval load-scifact` (ADR-049; Q6 answered). Release pinned by SHA-256 and unpacked safely. **1,258 items in our own splits by linked claims and abstracts** (SciFact's split shares 181 of 283 dev abstracts with train). 5,992 passages; every evidence sentence mapped to its gold passages. |
| S2.6 Retrieval ablation | done | `eval/retrieval.py`, `sentinel eval retrieval` and `eval compare` (ADR-050). **Four rows, dot and cosine, in MLflow** over 263 SciFact claims. nDCG@10 on val: BM25 0.763, dense 0.791, fusion 0.801, fusion plus rerank 0.895. Test: 0.822 to 0.865, and there the reranker adds nothing measurable. The first run's cosine rows were cut short by the HNSW index, so dense search is now exact. Dot product chosen; reranker kept on val's evidence. Report in `docs/evals/`. **Re-measured on full text on 2026-11-14** (`--set synthetic`, ADR-055): each of 760 synthetic claims searched its own paper's body. The reranker gained over fusion on both splits (+0.069 val, +0.067 test); dot beat cosine again; BM25 alone scored best (0.919), as the set's number anchors predict. |
| S2.7 Claim skeletons | done | `extract/` (skeleton, prompt, llm, claims), `sentinel extract claims` (ADR-053; Q7 answered: ASU Research Computing's free gateway). Strict JSON schema at temperature 0; valid means grounded: every field is copied from the context, and a broken rule goes back to the model up to twice. **Validation failure rate on 200 contexts:** gemma4-31b-it 11.0% on the first answer, 5.0% after repairs; Qwen-235B 24.0%/2.5%; OLMo-32B 34.5%/8.5%. Gemma chosen: 0 of 20 wrong in a side-by-side reading (Qwen 5, OLMo 3). In MLflow (`claim-extraction`). Then the current gateway and a batch mode (ADR-054). **Full run, 2026-11-14: all 35,921 contexts stored, 33,584 valid skeletons. Of the other 35,701, 14.5% were invalid on the first answer and 6.5% after repairs.** It took 81 min by batch, with none unanswered; the gateway estimates 46.8 kWh. Reading the failures found two problems (failure log): the statement rule rejected exact copies without their citation markers, and at least 993 contexts had the wrong citing sentence. **Both fixed 2026-11-15** (ADR-056): extractor 1.1.0 on contexts 1.1.0, with 31,389 skeletons copied and 4,532 contexts asked again. **0.63% of all 35,921 now invalid** (226), against 6.5%. Reports in `docs/evals/`. |
| S2.8 Synthetic set | done | `eval/synthetic.py`, `eval/items.py`, `sentinel eval make-synthetic` (ADR-051). Rules, no language model. **6,628 items from 2,005 number-anchored abstract–results pairs in 1,176 cited works; every item has a corruption type, and splits are by source paper.** Rebuilt after the abstract repair; the first build had 5,323 items from 1,628 pairs. Spot checks: 48 of 50 items in the first build, and 12 of 13 from repaired abstracts, read as rightly labelled. Reading the drafts found S2.2's abstract cleaner deleting text after a "<" (failure log, now fixed). The reranker was re-measured on its intact pairs on 2026-11-14 (ADR-055). |
| S2.9 Tabular baseline | built; training waits for S3.4 | `ml/` (features, baseline), `sentinel ml features` and `sentinel ml baseline` (ADR-057). Metadata features for all 54,824 citations; LightGBM with Platt scaling, folds by citing paper, SHAP, logged to MLflow (`tabular-baseline`). No real labels until S3.4 (your choice), so it ran on 200 placebo labels. **Macro F1 0.274 against chance 0.194–0.333; ECE 0.253 → 0.019 after calibration.** Over 20 seeds, placebo macro F1 reached at most 0.369, the bar a real run must clear. Report in `docs/evals/`. |

**What works:** the whole Phase 1 pipeline over the 1,000-paper corpus. That means loading data, matching references (89.4% of all references, 94.4% of the structured sample), pulling out statistics, all five checks, `report`, `stats`, `export` and stage timing. 890 tests, all passing, including the database tests and the 46-item fixture set. Phase 2 so far:
- pgvector, the Phase 2 tables and MLflow;
- text availability for every corpus paper and cited work (Q2 measured);
- 293,067 passages and 35,921 citation contexts (contexts 1.1.0, ADR-056);
- MedCPT embeddings for all 265,424 cited-work passages, searched exactly, with no vector index (ADR-052);
- BM25, exact dense search, fusion and reranking;
- SciFact loaded, and the retrieval ablation in MLflow, on SciFact and on full text from the synthetic set (ADR-050, ADR-055);
- the synthetic set, 6,628 items;
- claim skeletons from ASU's gateway for all 35,921 contexts, 35,695 valid (gemma4-31b-it, extractor 1.1.0; ADR-053, ADR-054, ADR-056);
- the tabular baseline's features, training, calibration and SHAP, checked with placebo labels (ADR-057).

**What's broken:**
- Passages are still cut with the splitter's 1.0.0 rules, so a citation printed after a full stop and a space sits at the start of the next sentence there (ADR-056). Only window edges are affected; the next passage version takes the new rules.
- Most of C01's corpus flags are works that do exist: only 8 of 20 sampled flags were correct (see the README's section on C01's limits). So C01's flag rate is published as an upper bound.
- A stage that is force-killed leaves its `stage_run` row saying `running` forever (run 9).
- `stats` counts items only for runs that finished.
- The full text of PMC6561645 still returns an HTTP 500 error from Europe PMC.

**Next three things:**
1. **Close Phase 2:** end-of-phase ledger rows, README pass, and the PR to main. Every §10.3 item is met, except that the baseline is trained only on placebo labels (ADR-057).
2. **Start Phase 3 with S3.1.** Anything that uses claims reads contexts 1.1.0 and claims 1.1.0; the 1.0.0 rows stay only so earlier reports can be reproduced.
3. **When S3.4's labels exist,** train the baseline on them (`sentinel ml baseline --labels`) and report it beside the judges. A macro F1 under about 0.37 is no signal at 200 labels.

**After that:**
- A later claim extractor version could:
  - let a citation's whole parenthesis be left out (24 of the 226 invalid);
  - decide whether a list sentence's part-copy counts (40). Both can be re-checked from the kept answers without new calls (ADR-056).
- Phase 3 needs a plan for reranking cost: about 7.5 s for one paper's body on this CPU, and 13 s for 50 SciFact passages (ADR-050, ADR-055).
- Weighted fusion, or reranking BM25's list, should be measured on S3.4's real citations (ADR-055). Still open from Phase 1: nothing checks for a DOI that points to a paper with a different title, and the title-plus-author Crossref search for C01 (ADR-041) has no session yet.

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
**Status:** active · "body text is not stored in the database" superseded by ADR-046

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

### ADR-046 — Passages and citation contexts: sentence windows in the database, citations linked through the XML
**Status:** active · supersedes ADR-037's "body text is not stored in the database"

**In short:** every work with text is cut into passages of whole sentences, stored in `passage`. Every corpus paper's in-text citations become contexts linked to the references they cite. **96.3% of the corpus's references now have at least one citing context.**

ADR-037 kept body text out of the database because Phase 2's passage design wasn't settled. Retrieval needs the text next to its vectors (§10.2), so passages are stored. The XML stays on disk as the source.

**Passages** (`fulltext/passages.py`, `sentinel extract passages`):
- A full-text work gives its abstract (from `text_availability`) plus its body. An abstract-only work gives its abstract (your choice), which is what C09 falls back on for most citations.
- The body is read from the cached JATS. Tables, figures, footnotes and non-content top-level sections are skipped: references, acknowledgements, funding, declarations, data availability, author biographies, publisher's notes, trial status and the like.
- Sections map to §10.1's labels by `sec-type`, else by title. "Objectives" counts as introduction, protocol headings such as "Assignment of interventions" as methods, "Concluding remarks" as discussion. Anything else is `other`.
- Each section is cut separately into windows of whole sentences. A window closes once it reaches `SENTINEL_PASSAGE_MIN_WORDS` (150) and never grows past `SENTINEL_PASSAGE_MAX_WORDS` (300). The next window repeats the last `SENTINEL_PASSAGE_OVERLAP_SENTENCES` (1) sentences. A short last window joins the one before when they fit, and a sentence longer than the maximum is cut by words.
- The three sizes have no defaults and feed `config_hash` (ADR-022, §7). They also appear in `splitter_ver` (`1.0.0/150-300-1`), so a change of size makes new rows beside the old instead of rewriting them (ADR-044).

**Sentences** (`fulltext/sentences.py`): rule-based. A boundary is a full stop, question mark or exclamation mark, then whitespace, then a capital, digit, bracket or quote.
- It isn't a boundary after an abbreviation ("et al.", "e.g.", "Fig.", "vs.") or an initial.
- A citation right after the stop ("adults.12,13 Next") stays with its sentence.
- When unsure, sentences stay joined: a context one sentence too long is better than one cut mid-claim.

**Contexts** (`fulltext/contexts.py`, `sentinel extract contexts`): each `<xref ref-type="bibr">` is linked to its stored reference through the ref id kept since S1.6 (`reference.raw.id`).
- Citations joined only by ",", ";" or a dash form one context.
- Ranges are expanded in reference-list order in three markups seen in the corpus: two citations joined by a dash; one citation reading "9–13", when its start agrees with the reference's label; and a dash marked up as a citation of its own (`<xref rid="B21">–</xref>`, PMC4750684).
- Author-year citations are never expanded.
- The context is the citing sentence plus one either side, within the paragraph. `marker` keeps the citation as printed.

Results on `pmc-rct-2015-2024`:
- Passages: 290,977 from 31,420 works, every work with text; 398 MB in the database. Median 166 words (5th–95th percentile 150–249), and a median of 28 passages per full-text work.
- Contexts: 49,770 citations in 35,995 groups gave 35,921 contexts in 968 papers. 2,508 ranges were expanded, and 97 citations point at no stored reference.
- 32 papers have no contexts: PMC6561645 has no XML, and 31 have no citation markup.
- **37,243 of 38,685 references (96.3%) have at least one context.**
- Link checks: the linked reference's printed label equals the citation number for 99.87% of single-number citations (23,385 of 23,415); the rest follow publishers' markup that points elsewhere. Ranges link exactly a..b in 99.5% (2,111 of 2,121); most misses are references with no numeric label to compare.
- Citing sentences: median 28 words, 99th percentile 102. 195 are over 120 words, mostly real long sentences such as questionnaire lists.

Three patterns were found in the real output and fixed before it was kept. Each is a test, and the earlier rows were deleted, so version 1.0.0 names one set of rules (as with C03 in ADR-037):
- List items inside a paragraph ran together ("ultrasound.Recent"), making a 365-word sentence.
- A space before the full stop ("countries 20) . While") hid the boundary.
- Panel titles ran into their text ("Research in contextEvidence").

**Turned down:**
- A sentence-splitting library: the repository's parsers are rule-based and tested against real cases (§9.4), and citation spans have to be protected anyway.
- Passages that cross section boundaries: results text would sit beside methods text in one retrieval unit.
- Contexts across paragraph boundaries: a new paragraph usually starts a new point.
- Expanding author-year "ranges": a hyphen there is part of a name.

---

### ADR-047 — Embeddings run in-process with PyTorch on CPU, with MedCPT's exact title-and-passage input
**Status:** active · amends §4 and §10.1 item 4 · answers Q1

**In short:** there's no GPU here (your choice: the CPU here). Measured on this CPU, Text Embeddings Inference (TEI) would have taken about 11 days, while the model card's own PyTorch code takes about 14 hours. So passages are embedded in-process with PyTorch, exactly as the model card does it.

The models, confirmed on Hugging Face: `ncbi/MedCPT-Article-Encoder` (revision `d05a736da4bb`) for passages and `ncbi/MedCPT-Query-Encoder` (`d83a36cc6b8e`) for claims in S2.5. Both are 768-number BERT encoders, public domain as US government works; NCBI asks for a citation (Jin et al., *Bioinformatics* 2023). The article encoder reads a pair, title then text, truncated to 512 tokens, and its vector is the `[CLS]` token's last hidden state, not normalised.

Measured before choosing, on 300 random passages:
- **TEI 1.9.4 (CPU image): 0.3 passages a second.** No ONNX build exists for this model, so TEI fell back to its own CPU backend, limited to 4 inputs per batch. Its vectors equal PyTorch's for the same input (cosine 0.99999), so the problem is speed only.
- **TEI takes single strings, not pairs.** A passage without its title drifts from the model's pair format: mean cosine 0.955, as low as 0.776. "Title [SEP] passage" as one string reaches 0.990, which is close but not exact.
- **The model card's PyTorch code with the true pair: 4.0 a second;** 4.7–4.8 with batches of similar length, since less padding is computed. Thread count made no difference. The real run averaged 5.1 a second.

Decision:
- `retrieve/encoders.py` runs the model card's code: (paper title, passage text) pairs, 512 tokens, `[CLS]`. torch 2.15.0 (CPU) and transformers 5.22.2 are in the `[ml]` extra and load only when an encoder is built.
- `sentinel embed passages` (`retrieve/embed.py`) embeds passages of the current splitter version that have no vector from the model. It walks them in passage ID order and encodes each 256-passage chunk in batches of similar length. Each chunk is written in one transaction with its cursor, so a killed run loses at most one chunk, and a re-run does nothing.
- `passage_embedding.model` is `model@revision` (`ncbi/MedCPT-Article-Encoder@d05a736da4bb`). `SENTINEL_EMBED_ARTICLE_MODEL` and `SENTINEL_EMBED_ARTICLE_REVISION` have no defaults and feed `config_hash` (ADR-022); the batch size is operational.
- Scope (your choice): the works the corpus cites, 263,440 passages, which is what C09 retrieves from. The corpus papers' own 28,326 passages wait for Phase 3 (`--scope all`).
- Stored vectors were checked against the model card's code run separately: cosine 1.0, largest difference 1.4e-6.
- Run time: about 14 hours of awake time; the final count goes in the metrics ledger when it ends. Windows idle sleep was held off for the run's process only (`SetThreadExecutionState`), with no power setting changed.

**For S2.5:** MedCPT is trained for dot-product similarity, but the HNSW index from ADR-044 uses cosine distance. S2.5's dense search should measure both, and add an inner-product index (`vector_ip_ops`) if dot product wins.

**Turned down:**
- TEI on this CPU: 11 days.
- "Title [SEP] passage" through TEI: not the exact input, and still the slow backend.
- Renting a GPU (you chose the CPU here). TEI stays the option if a GPU is used later.
- int8 quantisation: about twice as fast but changes the vectors, and 14 hours didn't need it.

---

### ADR-048 — Retrieval: bm25s BM25, MedCPT dense search, rank fusion, MedCPT cross-encoder, in-process
**Status:** active · implements §10.1 item 5

**In short:** a claim's passages are found by four stages, all on this CPU: BM25 and dense search each return 50, rank fusion merges them, and the cross-encoder rescores the 50 and keeps 5. `sentinel retrieve search` shows every stage's rank for any claim or citation.

How it works (`retrieve/`):
- **BM25** (`bm25.py`) uses the bm25s library (0.3.16, MIT), not Postgres's `ts_rank`, which isn't BM25.
  - It indexes each passage as "title. text": lower-cased words, English stopwords removed, Snowball English stems.
  - `SENTINEL_BM25_K1`=1.5 and `SENTINEL_BM25_B`=0.75 are bm25s's defaults, set explicitly.
  - `sentinel retrieve build-bm25` builds the index from the database and saves it under `data/bm25/` with each row's passage ID and a manifest: corpus, scope, splitter version, k1, b, tokenizer version.
  - Loading refuses an index whose manifest disagrees with the settings, so a stale index can't be used silently.
  - Searching one work's passages keeps the whole index's term statistics.
- **Dense search** (`dense.py`) runs in pgvector over the article encoder's vectors, with the MedCPT query encoder (`d83a36cc6b8e`; 64 tokens, `[CLS]`, as its model card does) for the claim.
  - `SENTINEL_RETRIEVE_DENSE_METRIC` is `dot` (inner product, which MedCPT is trained for) or `cosine` (which the HNSW index from ADR-044 serves). It's set to `dot`, and S2.6 measures both.
  - Restricted to the works a citation cites, Postgres reads those few rows exactly.
- **Fusion** (`fusion.py`) is reciprocal rank fusion with k = `SENTINEL_RETRIEVE_RRF_K` (60). Ties go to the lower passage ID, so results never depend on dictionary order.
- **Rerank** uses the MedCPT cross-encoder (`71caf65d4927`), with (claim, "title. text") pairs, 512 tokens and the logit as score, as its model card does. It scores the fused top `SENTINEL_RETRIEVE_DEPTH` (50) and keeps `SENTINEL_RETRIEVE_KEEP` (5).
- `search.py` keeps every stage's ranks, scores and time on the result, so S2.6 can score BM25, dense, fusion and fusion plus rerank from one search each.
- Models load in-process, as in ADR-047. All ten settings have no defaults and feed `config_hash`.

Checked on real data:
- The BM25 index holds 263,440 cited-work passages: built in 72 s, 194 MB on disk.
- For a citing sentence about convalescent plasma, searched within the cited trial's 27 passages, the five results came from its introduction, abstract and discussion. The stages disagreed: the cross-encoder's first choice was 6th by BM25 and 2nd by dense search, which is the disagreement fusion and reranking are for.
- Latency within one work: BM25 0.01 s, dense 0.13 s. Over the whole index: BM25 0.19 s, dense 0.35 s.
- The cross-encoder scored about 3 pairs a second while the embedding run shared the CPU; it is re-measured with the CPU free.

**For Phase 3:** C09 reranks every citation's candidates. 35,921 contexts with up to 50 candidates each is up to 1.8 million pairs; at 3 a second that is far too slow for one machine. Phase 3 needs a narrower pool (the cited work's passages only, usually under 30), a cheaper reranker, or a GPU (Q1).

**Turned down:**
- Postgres full-text search as "BM25": `ts_rank` isn't BM25 (§10.1).
- A per-work BM25 index built at query time: its term statistics would come from a handful of passages.
- Choosing cosine or dot now: S2.6's ablation measures both.

---

### ADR-049 — SciFact is the external evaluation set, from the authors' release, split by linked claims and abstracts
**Status:** active · answers Q6 · splits §15's S2.8 (the external set loads first, as S2.6a)

**In short:** SciFact (your choice) is loaded as 1,258 claim-and-abstract items with exact gold passages, in our own train/val/test splits. SciFact's own split shares abstracts between train and dev, so it would leak.

SciFact (Wadden et al., EMNLP 2020) has 1,409 claims rewritten from citing sentences in biomedical papers, and 5,183 abstracts whose sentences experts marked as supporting or contradicting them. The claims are close to C09's input, and the marked sentences are gold evidence.

**Licence.** The authors' `LICENSE.md` puts the claims and labels under CC BY 4.0 and the abstracts under ODC-By 1.0: use and redistribution with attribution. The Hugging Face copy says CC BY-NC 2.0. The authors' release is the one used, and each item's `license` says so.

How it's loaded (`eval/scifact.py`, `sentinel eval load-scifact`):
- `SENTINEL_SCIFACT_URL` is the authors' "latest" release, whose content could change, so the download must match `SENTINEL_SCIFACT_SHA256` (`11c62128…`). Both have no default. Neither feeds `config_hash`: they change evaluation results, not findings.
- The archive is unpacked with tarfile's "data" filter, which refuses paths outside the target, links and devices. A test proves it.
- Only train and dev claims are used; test claims have no labels.
- **One item per (claim, cited abstract)**, which is C09's own question:
  - evidence in the abstract gives `supported` or `contradicted` (SciFact never mixes them within an abstract);
  - an abstract the citation named but with no evidence gives `insufficient`, with no gold passage.
- Each abstract becomes a paper (source `scifact`). Its sentences are cut into passages by the corpus's window rules, from SciFact's own sentence list and with the same `splitter_ver`, so every evidence sentence maps to its passages exactly. 0 evidence sentences were unmapped.
- `origin` keeps the claim and abstract IDs, SciFact's own split, the evidence sentence indexes and the release checksum. Loading again adds nothing.

**Splits** (§10.2: by source paper, never by row): SciFact's train and dev share 181 of dev's 283 abstracts, and the shared abstracts hold opposite claims. Train claim 2 ("1 in 5 million in UK have abnormal PrP positivity", contradicted) and dev claim 5 ("1/2000 in UK…", supported) rest on the same sentence. So claims and abstracts linked by any item form groups: 572 of them, the largest holding 9 claims. Each group goes to train, val or test (60/20/20) by a hash seeded with `SENTINEL_RANDOM_SEED`. No abstract and no claim falls in two splits.

Result: 5,183 abstracts, 5,992 passages, 1,258 items, 868 gold links.

| Split | Supported | Contradicted | Insufficient |
|---|---|---|---|
| train | 310 | 167 | 294 |
| val | 98 | 49 | 96 |
| test | 100 | 49 | 95 |

1,259 items were expected; claim 1245 names one abstract twice, and it is counted once.

**For S2.6:** 87% of SciFact abstracts are a single passage, so searching within one cited abstract is usually trivial. The retrieval ablation should search all 5,992 SciFact passages, which is SciFact's standard abstract-retrieval task. SciFact tests abstracts only; full-text retrieval needs the synthetic set (S2.8) or the hand-labelled holdout (S3.4).

**Turned down:**
- The Hugging Face copy: its licence label disagrees with the authors'.
- SciFact's own split: it leaks.
- One item per claim: C09 judges each cited source separately.
- Finding gold passages by text matching: SciFact's sentence indexes map exactly.
- `claims_with_citances.jsonl`, the original citing sentences: closer to C09's real input, and worth trying later as a second query form.

---

### ADR-050 — Retrieval ablation: dot product, exact dense search, and the reranker kept on val's evidence
**Status:** active · implements S2.6 · changes ADR-044 item 1 (iterative scans) and ADR-048 (dense search)

**In short:** the four-row ablation on 263 SciFact claims is in MLflow. Fusion plus rerank scores nDCG@10 0.895 on val against BM25's 0.763. Dense search stays on dot product and is now exact: the HNSW index had cut every cosine search in the first run short. The reranker stays because val shows a clear gain, but test shows none. Full report: `docs/evals/retrieval-ablation-2026-11-11.md`.

How it's measured (`eval/retrieval.py`, `sentinel eval retrieval`):
- Every claim with evidence in val (128) and test (135) searches all 5,992 SciFact passages. Gold is the evidence sentences' passages (ADR-049).
- Each search keeps every stage's list, so it scores BM25, dense, fusion and fusion plus rerank. Dense search runs once by dot product and once by cosine. Settings are ADR-048's, fixed before the run.
- nDCG@10 (binary) and Recall@50 per split. Choices are made on val; test confirms (§14).
- `sentinel eval compare <results> <row> <row>` gives paired bootstrap intervals: 10,000 resamples of claims, seed 20260922.
- The results file in `data/evals/` holds every ranking. The command warns if any row returns fewer than 50 passages.

| Row | val nDCG@10 | test nDCG@10 |
|---|---:|---:|
| BM25 | 0.763 | 0.822 |
| Dense, dot | 0.791 | 0.879 |
| Fusion, dot | 0.801 | 0.871 |
| Fusion + rerank, dot | 0.895 | 0.865 |
| Dense, cosine | 0.770 | 0.849 |

Decided:
1. **Dot product.** It beats cosine on val for dense search (+0.021) and for fusion (+0.013), though both val intervals include zero. Test confirms the dense difference (+0.030 [+0.005, +0.057]). After reranking the two are identical on every claim. `SENTINEL_RETRIEVE_DENSE_METRIC` stays `dot`.
2. **Dense search is exact.**
   - Every candidate's distance is computed in a `MATERIALIZED` CTE and sorted there, so no HNSW index can serve the query.
   - Before, the cosine query went through HNSW, which returns 40 vectors (`hnsw.ef_search`) before the paper filter runs. Every cosine list in the first run was short, and one was empty (failure log, 2026-11-11). This changes ADR-044 item 1: iterative scans are off by default, and even when on they are approximate, with no guarantee under a narrow filter.
   - No inner-product HNSW index is added, although ADR-047 proposed one if dot won: under a filter it would fail the same way.
   - The cosine HNSW index is now unused. Dropping it needs a migration, so that waits for a decision.
   - Measured: one cited work reads only its own rows; the SciFact pool takes 0.1 s; the whole table (29,544 vectors so far) takes 0.15–1.4 s.
3. **Keep the reranker.**
   - On val it adds +0.094 [+0.048, +0.140], better on 45 claims and worse on 14.
   - Test doesn't confirm it: −0.006 [−0.039, +0.026]; 18 better, 19 worse. Test's fused order is already strong (0.871 against val's 0.801).
   - The rule fixed in advance chooses on val. But at about 13 s per claim on this CPU (3.9 pairs/s with the CPU free), Phase 3 should re-measure it on full-text passages before paying for it corpus-wide.
4. **Keep fusion.** Its nDCG@10 is within noise of dense search alone on both splits. It does raise val's Recall@50, the reranker's pool, from 0.992 to 1.000, and BM25 costs 0.01 s.

§10.3's sanity check (fusion plus rerank over BM25):
- val +17.2% [+9.9%, +25.4%], inside 15–30%;
- test +5.1% [+1.2%, +9.6%], below it.

Neither is the outsized gain that signals leakage. Nothing is fitted to SciFact, and no abstract or claim crosses splits.

Checked:
- The rerun reproduced every dot ranking exactly (263 of 263 claims, four rows).
- No row in the rerun returned fewer than 50 passages.
- A test reproduces the empty cosine result under the old query, with sequential scans and sorts priced out, and passes on the exact query.

**Turned down:**
- Turning on `hnsw.iterative_scan`: it is still approximate, and a narrow filter can still return too few rows.
- Dropping the reranker because of test: that would be choosing on test.
- Pooling val and test to decide: §14 fixes the choice on val.
- Deleting the first run's MLflow runs: they're tagged `status=superseded` with the reason, as the record of what went wrong.

---

### ADR-051 — The synthetic set: number-anchored pairs from full text, corrupted by rule
**Status:** active · implements S2.8 · no language model (your choice; Q7 stays open for S2.7)

**In short:** 5,323 items from 1,628 supported pairs in 1,000 cited works. A pair is a sentence from a work's own abstract, plus the results passages in its body that repeat every number the sentence pins its result to. Four rule-based corruptions turn pairs into contradicted and insufficient items, each recording its type. It costs nothing and is reproducible. The catch is that the claims are the sources' own words, not a citing author's.

How it's made (`eval/synthetic.py`, `sentinel eval make-synthetic`, generator 1.0.0):
- **Sources:**
  - the corpus's cited works with body passages: 7,432;
  - of those, works whose licence allows adapted text: 4,025 (CC BY, BY-SA, BY-NC, BY-NC-SA, CC0, public domain);
  - skipped: NoDerivatives, text-mining-only licences, publisher terms and missing licences.
- **Anchors:** decimals, percentages, and whole numbers of 10 or more that aren't years. P values and "95% CI" don't count, since every results section has them.
- **What makes a claim:**
  - at least two anchors, one of them a decimal or a percentage;
  - 8–60 words;
  - no abstract heading inside it (that's two sentences run together);
  - not an aim ("To assess…").
- **Gold:** the one to three results passages that hold every anchor. A paper gives at most three pairs.
- **Splits:** by source paper, with the seeded hash shared with SciFact (moved to `eval/items.py`), 60/20/20. A swapped source comes from the claim paper's split. `EvalItemRecord` now requires a corruption type on synthetic items and refuses one on any other set.
- **Provenance:** every item's `origin` holds:
  - the generator version and the pair it came from;
  - the claim's paper and abstract passage;
  - the anchors;
  - the raw licence;
  - what changed.
- Running again adds nothing.

| Type | Rule | Label | Items |
|---|---|---|---:|
| `none` | The pair as found | supported, with gold | 1,628 |
| `direction_flipped` | Every direction word swapped (higher/lower, increased/decreased, …); skipped if the sentence negates | contradicted, same gold | 453 |
| `number_changed` | One anchor ×0.5 or ×1.5, to a value the source never prints | contradicted, same gold | 1,567 |
| `population_changed` | One population word swapped (children/adults, men/women, mice → humans, type 1/2 diabetes). Skipped if the source uses any word for the new group, or the claim gives the group's count or share | insufficient | 47 |
| `source_swapped` | The claim paired with another cited work that doesn't repeat its anchors; one cited by the same corpus paper where possible (1,517), else random (111) | insufficient | 1,628 |

| Split | Supported | Contradicted | Insufficient |
|---|---:|---:|---:|
| train | 1,022 | 1,273 | 1,048 |
| val | 317 | 407 | 330 |
| test | 289 | 340 | 297 |

**Spot check.** These are Claude's readings, not human labels.
- **Two drafts of the set were read and deleted.** Reading 62 items of the first and 15 population swaps of the second found:
  - claims merged across two sentences: the abstract bug in the failure log, 2026-11-11;
  - aims taken as findings;
  - one heading the stripper missed ("Result");
  - population swaps on sample-composition sentences: "33.7% … were women" became "were men", which contradicts rather than leaving the claim unsupported.

  Each became a rule above.
- **In the final set, 48 of 50 items (10 per type, seed 99) read as rightly labelled.**
  - One number change altered a dilution ("1:500" to "1:750"), which is arguably insufficient rather than contradicted.
  - One intact pair's abstract restates its body loosely.
  - Two co-cited swaps whose source could have restated the claim were checked against the source's passages, and neither did.
  - Population swaps are right but often absurd ("men with uterine fibroids"), so they're easy.
- A person should read a sample before Phase 3 trains on the set.

**Limits:**
- The claim is the source's own sentence. Retrieval on this set flatters BM25, and verdicts are easier than on real citations, which is why §14 reports synthetic and real numbers together. Retrieval should search body passages only: the claim's abstract passage is `origin.claim_passage_id`.
- An intact pair assumes the abstract agrees with the body.
- A changed number can be any anchor, including an SD, a CI bound or a design value. `number_from` and `number_to` allow error analysis by kind.
- 47 population items are too few to report alone.
- Abstracts damaged by S2.2's cleaner give fewer pairs, though none merged now (0 of 1,628). Regenerate after the fix.
- 2 source papers share a title with a SciFact abstract, so they should be excluded when one set trains and the other tests.
- The set's cited works will also be the sources of S3.4's real items. Phase 3 must keep a work's synthetic items out of training when that work is in the holdout.

**Turned down:**
- A language model writing paraphrased claims and corruptions. It needs Q7 and a budget, and it's circular if the same model later judges. It can be added later as a second set over the same pairs.
- Citation contexts as claims. They're the real input, but nothing says which are supported; that's S3.4's hand labelling.
- SciFact's supported claims as the source: abstracts only, the gap this set fills.
- Only random swapped sources: too easy, since co-cited works share a topic.
- Every licence: NoDerivatives forbids adapted text, and text-mining licences don't clearly allow it.

---

### ADR-052 — Drop the unused cosine HNSW index on passage vectors
**Status:** active · changes ADR-044 item 1 · follows ADR-050

**In short:** migration `d1219fa0eeed` drops `passage_embedding_hnsw`. Since ADR-050, dense search is exact, so no query used the index, but it took about 1 GB once all 271,416 vectors were in (839 MB at 214,728) and added work to every insert.

- **Why no query used it:** dense search scores its candidates in a materialised CTE, limited to the cited works being searched (ADR-050). An HNSW index can't serve that query, and under a narrow filter it cut result lists short.
- **The downgrade** rebuilds the same index (cosine operators). On a full table that takes minutes.
- **Applied after S2.4's embedding run finished,** so the drop's table lock never paused the run. With all 271,416 vectors in, the table and its indexes went from 2,195 MB to 1,134 MB; `alembic check` finds no difference.

**Turned down:**
- Keeping it for Phase 5's 10,000-paper run. If approximate search over the whole corpus is needed then, an index is one migration away, and that index should be measured then.
- An inner-product HNSW index now, as ADR-047 had planned if dot product won. Under a filter it fails the same way.

---

### ADR-053 — Q7: open-weight models on ASU's free gateway; claim skeletons validated by grounding
**Status:** active · implements S2.7 · answers Q7 for extraction (the judge's model is decided at S3.3) · changes §6's "LiteLLM router"

**In short:** claim skeletons come from gemma4-31b-it on ASU Research Computing's free, on-premises gateway. It was chosen from three models run on the same 200 citation contexts. Its validation failure rate is 11.0% on the first answer and 5.0% after up to two repairs. In a reading of 20 contexts it got none wrong. Report: `docs/evals/claim-extraction-pilot-2026-11-12.md`.

**The model source (your choice):**
- ASU Research Computing's gateway (`https://openai.rc.asu.edu/v1`; key from the Voyager portal, in `.env` as `SENTINEL_LLM_API_KEY`).
- It costs nothing, and every model runs on ASU hardware, so prompts never reach a commercial provider. It keeps request metadata for 180 days, not prompt text.
- The paper text sent is open access and public.
- A Claude pilot, about $3 with a capped full run, was turned down in favour of free.

**The gateway differs from its docs** (checked live on 2026-11-12):
- The model list has no capability fields.
- There are no `x-obleth-*` headers.
- Answers come through a LiteLLM 1.83.7 proxy over vLLM 0.26.0.
- Strict JSON-schema output and temperature 0 work as documented.

Because of this:
- each run records the deployment ID (`x-litellm-model-id`) and server build (`system_fingerprint`) in its `stage_run` counts;
- strict output was checked per model by running it.

**The client:** the project's own HTTP client, not a new SDK. `RateLimitedHTTP` gained `post`, with the same retry rules (ADR-042). A 400 marks only that context as refused; any other 4xx stops the run. No `From` header with the contact email is sent. Settings:
- `SENTINEL_LLM_BASE_URL`, `SENTINEL_LLM_WORKERS` (4, as the gateway's docs advise), `SENTINEL_LLM_TIMEOUT_S` and `SENTINEL_RATE_LLM_RPS` are operational, with defaults.
- The model has no setting. It's a required `--model` option, and every row's `extractor` is `llm:<model>`.

**The skeleton** (`extract/skeleton.py`, extractor 1.0.0):
- **Fields:** kind (finding, background, method, other), statement, population, intervention, comparator, outcome, effect, n and direction (increase, decrease, no_effect, unspecified).
- **The gateway's strict schema** comes from the same Pydantic model, so the shape is fixed and validity is about grounding.
- **Grounding rules:**
  - the statement is copied from the citing sentence;
  - the other text fields are copied from the context, with case, dashes, quotes and spacing ignored;
  - n is a number written there;
  - a finding has a direction;
  - a non-finding has no finding fields.
- **Repairs:** an invalid answer goes back with the broken rules, up to two times. What still fails is stored as `valid = false` with no skeleton, so no unvalidated output is written.
- **The citation marker is named in the prompt,** because one sentence can cite several groups for different claims.
- **Paper text is untrusted.** It goes only inside tags, the system message says so, and the reply is only validated, never acted on.

**The pilot** (`sentinel extract claims --model M --sample 200`; MLflow experiment `claim-extraction`):

| Model | Invalid, first answer | After repairs | Spot check: right / partly / wrong / invalid | s / call |
|---|---:|---:|---|---:|
| gemma4-31b-it | 11.0% | 5.0% | 14 / 6 / 0 / 0 | 6.3 |
| qwen3-235b-a22b-instruct-2507 | 24.0% | 2.5% | 10 / 5 / 5 / 0 | 9.4 |
| olmo3-32b-instruct | 34.5% | 8.5% | 13 / 2 / 3 / 2 | 2.9 |

The spot check is Claude's reading, not human labels.

**Why Gemma:**
- Qwen has the lowest final failure rate, but it makes findings out of speculation and out of the citing paper's own results. That is the worst error for C09, which would then check a claim nobody made.
- OLMo marks real findings as background, so C09 would skip them.

**Limits:**
- **All three borrow fields from neighbouring sentences,** mostly the citing study's population. Grounding to the citing sentence alone would catch that, but it would also reject comparators named in the sentence before, so that trade-off is for a later version to measure.
- **Field accuracy isn't measured.** S3.4's hand-labelled set should measure it.
- **The full run over 35,921 contexts hasn't been done.** At 4 workers it takes about 20 hours of a shared, free service, and the gateway's docs suggest batches for runs that size.

**Turned down:**
- **Claude through the Anthropic SDK:** costs money, and the user chose free.
- **A local model on this CPU:** hundreds of hours.
- **The LiteLLM library from §6:** with one gateway, its routing adds a dependency for nothing (the gateway runs LiteLLM itself).
- **Shape-only validation:** under a strict schema it would report a failure rate near zero and say nothing about invented text.

---

### ADR-054 — The current ASU gateway, and batch mode for large extraction runs
**Status:** active · follows ADR-053, whose gateway notes describe 2026-11-12

**In short:** a new Voyager key (2026-11-14) reaches the gateway the docs describe. Gemma answers there from the same server build as in the pilot, so the pilot stands and the extractor stays at 1.0.0. Large runs now go through the gateway's batch API, which uses only capacity interactive users aren't using.

**What the current gateway adds:**
- the model list has capability fields (`supports_response_schema`, `context_window`, `quantization`, add-ons);
- responses carry `x-obleth-*` headers;
- usage includes the gateway's own estimate of energy (`energy_wh`) and CO2 (`co2_g`).

**Gemma there:** bf16, no add-ons, server build `vllm-0.26.0-tp4-24487d56`, the same as the pilot's. The old LiteLLM deployment ID no longer exists.

**Provenance:** each call records the server build, plus `x-obleth-model-variant` for a model family, or `x-litellm-model-id` on a legacy key.

**Add-ons ("boons"):** direct calls send `x-obleth-boons: off`, as the docs advise for reproducible research. Batch lines can't carry headers, but Gemma has no add-ons.

**Why the version doesn't change:** the prompt, schema, rules, model and server build are all the same as the pilot's. The add-on header changes nothing for a model that has none.

**Batch mode** (`sentinel extract claims --batch`):
- The requests are the same as direct calls (`request_body` is shared).
- 5,000 contexts per batch (about 21 MB), up to 8 batches at once (the gateway allows 10 per key), each with a 24-hour window.
- Each repair round is a new, smaller batch.
- A context with an error line, or one whose batch expired, gets no row, so the next run asks again.
- If the run stops early, its running batches are cancelled. A hard kill can't do that, so they would run to the end unread.

**Checked live:**
- A 10-request probe finished in 30 s, with 8 of 10 answers valid at once.
- A 20-context batch run took 3 batches (a first round and two repair rounds): 18 valid at once, 2 invalid, both with the statement taken from outside the citing sentence.

**Energy:**
- The gateway's estimate for Gemma is about 2 Wh and 0.66 g CO2 per call: 46.8 Wh and 15.8 g over the smoke run's 24 calls.
- On that estimate, the full run of about 35,700 contexts (1.2 calls each) is roughly 80 kWh and 27 kg CO2. Each run logs its total.

**Turned down:**
- **Direct calls for the full run.** The gateway shares capacity fairly between keys, but its docs ask for batches for runs nobody is waiting on.
- **One 154 MB batch file.** Smaller batches save progress as they finish, and a failure loses less.

---

### ADR-055 — Retrieval on full text: the reranker confirmed, and BM25 strongest on number-anchored claims
**Status:** active · answers ADR-050 item 3's re-measure · uses ADR-051's set · the pipeline doesn't change

**In short:** each of the synthetic set's 760 intact val and test pairs searched the body of its own source paper, the way C09 will search a cited work. The reranker beat fusion by nDCG@10 +0.069 on val, and test confirmed it (+0.067). So ADR-050's choice holds on full text, and dot product again beat cosine. BM25 alone scored best, 0.919 on val against 0.900. The set rewards repeated numbers by construction (ADR-051), so it isn't used to choose components. Full report: `docs/evals/retrieval-synthetic-2026-11-14.md`.

**How it's measured** (`sentinel eval retrieval --set synthetic --corpus pmc-rct-2015-2024`):
- **Queries:** the intact pairs (`corruption_type = none`): 390 in val and 370 in test, from 450 source papers. The claim is the abstract sentence. Gold is the results passages that hold every anchor, about 1.2 per claim. Corrupted items are left out, since their gold is the same passages under a changed claim.
- **Pool:** the source paper's own passages, with the abstract left out, since the claim is copied from it (ADR-051). Pools average 26 passages (median 24, maximum 80). Only 23 of 760 are bigger than the depth of 50. So for 97% of claims the fused list is the whole body, and "fusion + rerank" is the cross-encoder ranking every passage.
- **Code:**
  - `Retriever.search` and `dense_search` take `skip_sections`; a passage with no section is never skipped.
  - `Query.paper_ids` gives each query its own pool.
  - A short list now means fewer results than min(depth, pool size).
  - `eval compare` reloads synthetic queries from the results file's parameters.
- **Settings:** ADR-048's, the same as ADR-050's run.
- **Results:** `data/evals/retrieval-ablation-synthetic-20261114T233915Z.json`, and MLflow `retrieval-ablation`, 7 runs with `dataset = synthetic-rules`.

| Row | val nDCG@10 | test nDCG@10 |
|---|---:|---:|
| BM25 | 0.919 | 0.916 |
| Dense, dot | 0.671 | 0.691 |
| Fusion, dot | 0.831 | 0.827 |
| Fusion + rerank, dot | 0.900 | 0.895 |
| Dense, cosine | 0.642 | 0.646 |
| Fusion, cosine | 0.807 | 0.809 |
| Fusion + rerank, cosine | 0.900 | 0.895 |

Recall@50 is 0.997–1.000 for every row. With pools this small it says nothing.

| Paired bootstrap (10,000 resamples, seed 20260922) | val | test |
|---|---|---|
| Fusion + rerank against fusion, dot | +0.069 [+0.044, +0.094]; 130 better, 50 worse | +0.067 [+0.044, +0.090]; 112 better, 43 worse |
| Fusion + rerank against BM25 | −0.020 [−0.040, +0.000] | −0.022 [−0.040, −0.004] |
| Fusion against BM25 | −0.089 [−0.110, −0.068] | −0.089 [−0.110, −0.070] |
| Dense: dot against cosine | +0.030 [+0.007, +0.051] | +0.045 [+0.022, +0.067] |

**Decided:**
1. **Keep the reranker.** ADR-050 kept it on val alone and asked for a re-measure on full text. On full text both splits show a gain of about 0.07, unlike SciFact's test.
2. **Dot product, confirmed.** For dense search alone, dot beats cosine on both splits. After reranking the two are identical, because both rerank the same whole-body pool.
3. **Don't switch to BM25 alone, and don't choose components on this set.**
   - The set's gold is defined by shared numbers. BM25 matches numbers exactly; the encoders don't.
   - Where the reranker lost to BM25 (117 claims), its top passage was gold in 16. Of the other 101, 80 were missing at least one of the claim's numbers. The cross-encoder found a passage on the topic, not the one with the numbers.
   - On SciFact, whose claims annotators wrote in their own words, the full pipeline beats BM25 by +0.132 on val. Real citing sentences paraphrase too.
   - Whether numbers deserve more weight is for S3.4's real citations to decide.
4. **Equal-weight fusion is the weak stage when one ranker is much worse.** It pulls BM25's 0.919 down to 0.831, and the reranker wins most of that back. Weighting the fusion, or reranking BM25's list, is a Phase 3 candidate. Measure it on real citations; don't tune it on this set.

§10.3's sanity check, the gain of fusion plus rerank over BM25: −2.1% on val [−4.3%, +0.0%] and −2.4% on test [−4.3%, −0.5%]. That is outside the 15–30% band, downwards. This is the set's construction at work; there's no sign of leakage.

**Cost:** 97 min for 760 claims, about 7.5 s per claim. Nearly all of it is the cross-encoder: 19,756 pairs, about 26 per claim. The cosine pass reused the dot pass's scores.

**Checked:**
- Every pool passage is in the BM25 index.
- BM25 returned fewer passages than the pool for 90 claims. In every case the missing passages share no term with the claim, and no gold passage was lost.
- No dense, fused or reranked list came back short.

**Turned down:**
- **BM25 alone, on this set's evidence.** The set's construction favours it (ADR-051's limits).
- **Scoring the corrupted items too.** They would repeat the intact pairs' gold under altered claims.
- **Leaving the abstract in the pool.** The claim would find its own sentence.
- **One pool of all 450 papers' bodies.** C09 searches the works a citation cites, so a single paper is the case that matters.

---

### ADR-056 — Contexts 1.1.0 and claim extractor 1.1.0: the citing-sentence fix, a marker-tolerant statement rule, and copying unchanged skeletons
**Status:** active · fixes both 2026-11-14 failure-log entries · amends ADR-046 (sentences, contexts) and ADR-053 (validity) · passages stay at 1.0.0

**In short:** the splitter now keeps a numeric citation printed after the full stop and a space with the sentence before it, and contexts 1.1.0 record where each citation sits in the sentence. A claim's statement may now leave those citations out, as the prompt asks. Valid 1.0.0 skeletons were copied where the model's input hadn't changed, and the other 4,532 contexts were asked again. **0.63% of the 35,921 contexts are now invalid, against 6.5%.** Report: `docs/evals/claim-extraction-1.1.0-2026-11-15.md`.

**Sentences** (`fulltext/sentences.py`): `split(..., cites_across_space=True)` gives rules 1.1.0, and `False` gives 1.0.0's exactly.
- A numeric citation after the end punctuation stays with the sentence before, even after a space ("removal. 15 However").
- The citations after it may be spaced ("16 , 17"), with up to three joining characters ("], [").
- Closing brackets after the last one go with it.
- An author-year citation after a space still starts the next sentence, where it is usually the subject.
- When the next character isn't a capital, sentences stay joined, as before.

**Contexts 1.1.0** (`fulltext/contexts.py`; migration `e5a7c3d91b20`):
- sentences are cut with rules 1.1.0;
- `citation_context.sentence_cites` holds every citation group in the sentence, linked or not, as [start, end] offsets, widened to brackets that enclose them exactly. It is NULL on 1.0.0 rows.
- **Result:** 2,421 of 35,921 contexts changed, in 161 papers (the failure log's lower bound was 993):
  - 958 had the wrong sentence;
  - 944 had two sentences run together behind a spaced citation list;
  - the rest gained a lost trailing marker or a changed neighbour.
- Citing sentences that start with their own marker fell from 962 to 4, all author-year subjects. Marker-only sentences fell from 208 to 0.
- Citations, groups, ranges and links are unchanged, with 96.3% of references still having a context.
- `context_coverage` now counts only the current version.

**Passages stay at 1.0.0.** `make_passages` passes `cites_across_space=False`, so a rebuild gives the stored text. That text feeds 265,424 vectors (about 14 h of CPU), the BM25 index and the synthetic set's gold passages. The new rules would change 858 of the 13,690 corpus-paper paragraphs that contain a citation, moving a marker between neighbouring sentences. Within a passage that matters only at window edges. The next passage version takes the new rules.

**Claim extractor 1.1.0** (`extract/skeleton.py`, `extract/claims.py`):
- **The statement rule:** the statement must read off the citing sentence as one run, but any citation group in `sentence_cites` may be left out. Where one is, spacing is ignored. Nothing else is excused. It accepts everything 1.0.0 accepted.
- **Why:** the prompt has always said "copied exactly. Leave out the citation marker." 1.0.0's rule rejected exactly that whenever the marker sat inside the statement.
- **Invalid answers are kept:** `claim.answer` holds the last answer as text, and `claim.errors` the rules it broke. Valid rows keep only the skeleton, and nothing reads `answer` as one.
- The prompt, schema, model (gemma4-31b-it), temperature and repairs are 1.0.0's.

**Copying unchanged skeletons** (`carry_over`, `sentinel extract claims-carry --model … --from-ver 1.0.0`):
- **When a skeleton is copied:** a 1.0.0 skeleton is copied to the 1.1.0 context with the same paper, ordinal, sentence, passage, marker, section and cited references, so the model would be asked the same request.
- **Conditions:** only valid skeletons are copied, each is checked against the 1.1.0 rules first, and no context that already has a 1.1.0 claim is touched.
- **Result:** 31,389 copied, and none failed the check.
- **Asked again:** the other 4,532, by batch in 56 minutes. That was 5,276 calls, none unanswered, and 10.5 kWh by the gateway's estimate.

| | Contexts | Invalid after repairs |
|---|---:|---:|
| Copied from 1.0.0 | 31,389 | 0 |
| Text changed, asked again | 2,421 | 16 (0.7%) |
| Unchanged, invalid under 1.0.0, asked again | 2,111 | 210 (9.9%) |
| All | 35,921 | 226 (0.63%; 95% CI 0.55–0.72%) |

**Checks:**
- 90.1% of the contexts that were invalid only under 1.0.0's rule are now valid. E010's re-ask of 20 had predicted 18 of 20.
- Among the changed contexts valid under both versions:
  - where the citing sentence changed, 539 of 1,941 claims (28%) changed kind;
  - where only a neighbour changed, 10 of 244 (4%) did.
  - In 8 of the 539 read side by side, the 1.0.0 claim described the next sentence or the bare marker, and the 1.1.0 claim the right sentence.

**What's left:** 226 invalid.
- 135 have a field reworded rather than copied, mostly the outcome.
- 90 break the statement rule:
  - 40 select this citation's part of a list sentence;
  - 50 edit or run on;
  - 24 of the 90 would pass if a citation's whole parenthesis could be left out (year-only markup: "(Peacock et al. <xref>2013</xref>)").
- 1 is not JSON.
- The answers are kept for a later rule.

**Turned down:**
- **Asking all 35,921 again.** About 47 kWh by the full run's rate, to replace 31,389 skeletons that pass the same rules from the same request. The cost is a mixed set: 1.1.0's first-answer failure rate over every context isn't measured.
- **Fixing passages now.** It would mean rebuilding and re-embedding passages and remaking the BM25 index and the synthetic set, for a change that only moves markers at window edges.
- **Stripping markers by pattern instead of storing spans.** The XML knows exactly where each citation is; patterns would miss publisher variants and strip real numbers.
- **Accepting part-copies of list sentences.** It may be the right reading for one citation, but it is a looser rule that needs its own measurement.
- **Deleting the 1.0.0 contexts and claims.** Earlier reports and the pilot's Qwen and OLMo rows depend on them.

---

### ADR-057 — The tabular baseline: built and checked in Phase 2, trained on S3.4's labels
**Status:** active · S2.9 · moves part of §10.3's exit criterion ("trained and calibrated, and its SHAP plot is saved") to Phase 3

**In short:** the baseline predicts C09's labels (supported, contradicted, insufficient) from citation metadata alone, as §10.1 item 8 asks. No real citation has a label until S3.4's hand-labelled holdout, and no other set can stand in (your choice, 2026-11-15). So S2.9 builds:
- the feature table;
- training with grouped cross-validation, Platt scaling, SHAP and MLflow logging;
- a placebo check that the whole path works on real citations.

It is trained when S3.4's labels exist. Report: `docs/evals/tabular-baseline-2026-11-15.md`.

**Why not train now:**
- **The synthetic set's labels** are random corruptions within each pair, and its "citing" side is the cited work itself. Metadata such as shared authors or the year gap would only reveal source swaps.
- **SciFact** has labels but no metadata to join, and no citing papers.
- **A proxy label from C01 or C02** isn't the label the judges predict.

**Features** (`ml/features.py`, version 1.0.0, `sentinel ml features`). One row per citation: a context from contexts 1.1.0 and one reference it cites. That makes 54,824 rows from 35,921 contexts in 968 papers.
- **The citing paper:** year, venue (as a count of corpus papers), reference count, and its trial's sponsor class.
- **The cited work:** year, venue (as a count of corpus references), match status, text held, and its most serious notice.
- **The pair:** year gap, shared authors (surname and first initial, counted), section, and co-cited references.
- **Encoding:** unknown numbers stay NaN, never zero. Categories have fixed vocabularies plus "unknown", so the encoding doesn't depend on which citations are labelled.
- **Nothing comes from the text, a model, or a check's verdict.**

**Model** (`ml/baseline.py`, version 1.0.0, `sentinel ml baseline --labels FILE | --placebo N`):
- LightGBM with fixed, conservative settings (200 trees, depth 3, 7 leaves, at least 10 per leaf), not tuned;
- Platt scaling (`CalibratedClassifierCV`, sigmoid), fitted on inner folds its booster didn't train on;
- 5 outer and 3 inner folds, all split by citing paper, with the separation asserted in code;
- **measures:** macro F1, accuracy, log loss, PR-AUC per label and for "not supported", and top-label ECE before and after calibration, each beside its chance level;
- **SHAP** on a final model fitted on every label, with one-hot columns summed so a category counts once;
- **logging:** metrics, SHAP values, out-of-fold predictions and two plots, written to `data/ml/` and logged to MLflow (`tabular-baseline`) with the calibrated model. skops trusts five named types. Nothing is registered.

**Placebo check:** 200 real citations with random labels; MLflow run `170f7b32`.
- macro F1 0.274, against 0.194 for always the commonest label and 1/3 for guessing;
- PR-AUC for "not supported" 0.675, against a prevalence of 0.680;
- ECE 0.253 uncalibrated, 0.019 calibrated.

Over 20 seeds, macro F1 was 0.217–0.352 (5th–95th percentile, maximum 0.369). **So at 200 labels, the baseline must clear about 0.37 before it shows signal.** On a planted signal in the tests it reaches 0.928, and SHAP ranks the planted features first.

**When S3.4's labels exist:** train with `sentinel ml baseline --labels <csv>`. The CSV has `citation_context_id` (contexts 1.1.0), `reference_id` and `label`. Report the result beside the judges on the same 200 items. If it comes close, that is a finding (§10.1 item 8). S3.4 may replace the CSV with a loader from its own tables.

**Known asymmetry:** S3.4's items will be chosen by active learning. The baseline is trained on them by cross-validation and the judges aren't, which favours the baseline, so "the pipeline beats the baseline" is a conservative claim.

**Turned down:**
- **Training on the synthetic set to meet the criterion literally.** The number would describe how the set was built.
- **Deferring S2.9 entirely.** The features and pipeline don't depend on labels, and the placebo check catches plumbing faults now.
- **One category per venue, or LightGBM's own categorical handling.** It's too many levels for a few hundred labels. One-hot over small fixed vocabularies keeps SHAP simple too.
- **Tuning hyperparameters.** With 200 labels it would fit noise.
- **Isotonic calibration.** It needs more data than Platt scaling; S3.7 compares the two on real labels.
- **A binary label.** The judges predict three labels. "Not supported" is reported as a PR-AUC for the registry's metric.

---

### ADR-0NN — template
**Status:** active

*The background in one or two sentences. What was decided. Why. What was turned down, and why not.*

---

## Model registry

Every model in the system: what it does, where it runs, why it was chosen, and how it scored.

| Role | Model | Where it runs | Why chosen | Main metric | Status |
|---|---|---|---|---|---|
| Dense retrieval | MedCPT article encoder (`d05a736da4bb`) for passages, query encoder (`d83a36cc6b8e`) for claims; SPECTER2 or a general model as the comparison | in-process PyTorch, CPU (ADR-047) | Named in §10.1; dot product beat cosine on val (ADR-050) | nDCG@10 on SciFact: 0.791 val, 0.879 test (dense alone); on the synthetic set's full text, 0.671 and 0.691 (ADR-055) | in use; no comparison model tried yet |
| Reranker | MedCPT cross-encoder (`71caf65d4927`); bge-reranker-v2-m3 as the comparison | in-process PyTorch, CPU (ADR-048) | Named in §10.1; kept on val's gain (ADR-050), confirmed on full text (ADR-055) | nDCG@10 gain over fusion: SciFact +0.094 val, −0.006 test; synthetic full text +0.069 val, +0.067 test | in use; no comparison model tried yet |
| Claim entity recognition (NER) | *TBD: GLiNER-biomed vs a fine-tuned encoder* | local | Pending, week 6 | entity F1 | not selected |
| Outcome equivalence | *TBD* | local | Pending, week 7 | macro F1 | not selected |
| Entailment, zero-shot | *TBD: an NLI head* | local | Baseline, week 7 | macro F1 | not selected |
| Entailment, fine-tuned | *TBD* | local | Week 8 | macro F1 | not selected |
| Claim skeleton extraction | gemma4-31b-it (server build `vllm-0.26.0-tp4-24487d56`; the pilot ran on legacy deployment `dca36210…`) | ASU Research Computing gateway, free (ADR-053, ADR-054) | Fewest wrong skeletons of three in a 20-context reading; second-lowest failure rate | validation failure rate: 11.0% first answer, 5.0% after repairs (pilot, 200); 14.5% and 6.5% (the other 35,701); extractor 1.1.0: 0.63% of all 35,921 invalid after repairs (ADR-056) | in use, extractor 1.1.0 on contexts 1.1.0 since 2026-11-15 |
| Judge | *TBD at S3.3; the ASU gateway first (ADR-053)* | ASU Research Computing gateway | Teacher for distillation, week 8 | macro F1, cost per 1,000 | not selected |
| Distilled verdict model | *trained from the judge* | local | Week 10 | share of the judge's F1 kept, cost ratio | not built |
| Tabular baseline | LightGBM 4.7.0 with Platt scaling, on metadata only (features 1.0.0, baseline 1.0.0) | local, CPU (ADR-057) | Honest comparison point | macro F1 beside the judges; PR-AUC for "not supported". Placebo, 200 random labels: macro F1 0.274, PR-AUC 0.675 at prevalence 0.680 | built; trains on S3.4's labels |
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
| External evaluation set | SciFact: `eval_item` rows with `origin.dataset = scifact`; release in `data/external/scifact/` (ADR-049) |
| Generated evaluation set | The synthetic set: `eval_item` rows with `origin.dataset = synthetic-rules`, made from stored passages by `sentinel eval make-synthetic` (ADR-051) |
| Hand-labelled held-out set | *TBD* |
| Evaluation reports | `docs/evals/`: `retrieval-ablation-2026-11-11.md` (ADR-050), `claim-extraction-pilot-2026-11-12.md` (ADR-053), `claim-extraction-full-2026-11-14.md` (ADR-054), `retrieval-synthetic-2026-11-14.md` (ADR-055), `claim-extraction-1.1.0-2026-11-15.md` (ADR-056), `tabular-baseline-2026-11-15.md` (ADR-057) |
| Tabular baseline runs | `data/ml/baseline-<labelled\|placebo>-<UTC stamp>/` (metrics, SHAP, out-of-fold predictions, plots); MLflow experiment `tabular-baseline`, one run per training run, with the calibrated model (ADR-057) |
| Claim skeletons | `claim` rows, extractor `llm:<model>`, version 1.1.0 on contexts 1.1.0 (1.0.0 rows kept for the earlier reports); an invalid row's last answer in `claim.answer`; MLflow experiment `claim-extraction`, one run per model run (ADR-053, ADR-056) |
| Ablation results | `data/evals/retrieval-ablation-<UTC stamp>.json` (SciFact) and `retrieval-ablation-synthetic-<UTC stamp>.json`, every ranking; MLflow experiment `retrieval-ablation`, one run per row |
| Config | `.env`, `src/sentinel/config.py` |
| BM25 index | `data/bm25/<corpus>-<scope>-<splitter>/` with `manifest.json` (ADR-048) |
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
| 2026-11-11 | E001 | Four-row ablation, dot and cosine (first run) | 263 SciFact claims, all 5,992 passages | Cosine dense lists cut short by HNSW (median 24 of 50) | discarded; dense search made exact |
| 2026-11-11 | E002 | Four-row ablation, dot and cosine (rerun) | Same; exact dense search | nDCG@10 val/test: BM25 0.763/0.822, dense 0.791/0.879, fusion 0.801/0.871, + rerank 0.895/0.865 | dot rows identical to E001 |
| 2026-11-11 | E003 | Does reranking beat fusion alone? | E002, paired bootstrap | val +0.094 [+0.048, +0.140]; test −0.006 [−0.039, +0.026] | keep (val decides); re-measure on full text |
| 2026-11-11 | E004 | Dot or cosine? | E002, paired bootstrap | Dense: val +0.021 [−0.007, +0.051], test +0.030 [+0.005, +0.057]; same after rerank | dot |
| 2026-11-12 | E005 | Which gateway model extracts claim skeletons? | 200 citation contexts (seed 20260922), 3 models, temperature 0, strict schema, 2 repairs | Invalid first/final: gemma 11.0%/5.0%, Qwen-235B 24.0%/2.5%, OLMo-32B 34.5%/8.5%; spot check of 20 wrong: 0, 5, 3 | gemma4-31b-it (ADR-053) |
| 2026-11-14 | E006 | Does the pilot's failure rate hold over every context? | The other 35,701 contexts; gemma4-31b-it by batch; same prompt, rules and server build | Invalid first/final 14.5%/6.5% (pilot 11.0%/5.0%); repairs fixed 40% of statement failures and nearly all others | holds, inside the pilot's intervals; reading the failures found the S2.3 splitter bug |
| 2026-11-14 | E007 | Four-row ablation on full text | 760 intact synthetic pairs (val 390, test 370), each searching its own paper's body, abstract left out; ADR-048's settings | nDCG@10 val/test: BM25 0.919/0.916, dense 0.671/0.691, fusion 0.831/0.827, + rerank 0.900/0.895 | BM25 favoured by the set's number anchors (ADR-051) |
| 2026-11-14 | E008 | Does reranking beat fusion on full text? | E007, paired bootstrap | val +0.069 [+0.044, +0.094]; test +0.067 [+0.044, +0.090] | keep the reranker: test now agrees (ADR-055) |
| 2026-11-14 | E009 | Does the full pipeline beat BM25 on number-anchored claims? | E007, paired bootstrap; top passages read where the reranker lost | val −0.020 [−0.040, +0.000]; test −0.022 [−0.040, −0.004]; 80 of 101 non-gold top passages lacked a claim number | no change: an artefact of the set; weighted fusion to be measured on real citations |
| 2026-11-14 | E010 | Why do statement failures survive two repairs? | 20 invalid contexts with words in the sentence (seed "11"), asked again directly, nothing stored; 59 calls, 136.8 Wh | 18 copied the sentence exactly but without its citation markers; 1 valid this time; 1 effect rule | the rule, not the model: a marker-tolerant rule in extractor 1.1.0 (built 2026-11-15, ADR-056) |
| 2026-11-15 | E011 | Does the splitter fix change which sentence a context reads? | Contexts 1.1.0 built in memory, then stored, against 1.0.0 by paper and ordinal | 2,421 changed in 161 papers: 958 had the wrong sentence, 944 were two sentences run together; starts-with-marker 962 → 4, marker-only 208 → 0 | contexts 1.1.0 (ADR-056); passages kept at 1.0.0 |
| 2026-11-15 | E012 | Do the fixes bring the claim failure rate down as predicted? | 31,389 valid skeletons copied where the input was unchanged; 4,532 asked by batch with extractor 1.1.0 | 226 of 35,921 invalid (0.63% [0.55, 0.72]); previously-invalid unchanged contexts 90.1% valid (E010 predicted 18 of 20); kind changed for 28% of claims whose sentence changed, 4% where only a neighbour did | extractor 1.1.0 in use (ADR-056) |
| 2026-11-15 | E013 | Does the baseline's pipeline run end to end on real citations? | 200 citations from 183 papers, random labels (seed 20260922); 5 outer folds by citing paper, Platt on 3 inner | Macro F1 0.274 (chance 0.194–0.333); PR-AUC not supported 0.675 at prevalence 0.680; ECE 0.253 → 0.019 | pipeline works; MLflow run `170f7b32` (ADR-057) |
| 2026-11-15 | E014 | How high can a model with no signal score at 200 labels? | E013 over 20 seeds, cross-validation only | Macro F1 mean 0.278, 5th–95th 0.217–0.352, max 0.369; calibrated ECE up to 0.143 | a real baseline must clear about 0.37 |

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
| 2026-11-02 | 2 | Passages | 290,977 from 31,420 works; median 166 words | Splitter 1.0.0/150-300-1 (ADR-046); 398 MB |
| 2026-11-02 | 2 | Citation contexts | 35,921 in 968 papers; 96.3% of references have one | Labels agree for 99.87% of numbered citations; ranges 99.5% exact |
| 2026-11-10 | 2 | Passages to embed (MedCPT article encoder) | 265,424 cited-work passages, all embedded by 2026-11-12, none failed | ADR-047; 263,440 before the abstract repair; plus 5,992 SciFact passages |
| 2026-11-10 | 2 | Embedding throughput, CPU | 5.1 passages/s; about 14 h | TEI on the same CPU: 0.3/s |
| 2026-11-10 | 2 | BM25 index | 263,440 passages; 72 s; 194 MB | ADR-048 |
| 2026-11-10 | 2 | Retrieval latency, one cited work | BM25 0.01 s; dense 0.13 s | Whole index: 0.19 s and 0.35 s |
| 2026-11-10 | 2 | Cross-encoder throughput, CPU | 3 pairs/s | Too slow to rerank 50 per citation corpus-wide (ADR-048) |
| 2026-11-10 | 2 | External evaluation set (SciFact) | 1,258 items: 508 supported, 265 contradicted, 485 insufficient; 868 gold passage links | ADR-049; own splits, 60/20/20 by linked group |
| 2026-11-11 | 2 | Retrieval nDCG@10, fusion + rerank (SciFact) | val 0.895, test 0.865 | ADR-050; 128 and 135 claims, all 5,992 passages |
| 2026-11-11 | 2 | Retrieval nDCG@10, BM25 (SciFact) | val 0.763, test 0.822 | Recall@50 0.971 and 0.970 |
| 2026-11-11 | 2 | Gain of fusion + rerank over BM25 (§10.3: 15–30%) | val +17.2% [+9.9%, +25.4%]; test +5.1% [+1.2%, +9.6%] | Paired bootstrap; no sign of leakage |
| 2026-11-11 | 2 | Cross-encoder throughput, CPU free | 3.9 pairs/s; about 13 s per claim for 50 pairs | Replaces the 3 pairs/s measured while sharing the CPU |
| 2026-11-11 | 2 | Retrieval ablation run time | 62.8 min for 263 claims × 2 metrics | 14,866 cross-encoder pairs, cached across metrics |
| 2026-11-11 | 2 | Synthetic evaluation set | 5,323 items: 1,628 supported, 2,020 contradicted, 1,675 insufficient; 4,327 gold passage links | ADR-051; 1,628 pairs from 1,000 of 4,025 cited works with an adaptable licence; about a minute to build |
| 2026-11-11 | 2 | Synthetic items by corruption type | none 1,628; number changed 1,567; direction flipped 453; population changed 47; source swapped 1,628 | Swapped sources: 1,517 co-cited, 111 random |
| 2026-11-11 | 2 | Synthetic labels judged right in a spot check | 48 of 50 (10 per type, seed 99) | Claude's reading, not human labels |
| 2026-11-11 | 2 | Abstracts damaged by S2.2's cleaner | 4,233 of 36,418 works show the signature (a lower bound) | Failure log, 2026-11-11; not yet repaired |
| 2026-11-11 | 2 | Abstracts changed by the cleaner fix | 5,989 of 30,661 Europe PMC abstracts (19.5%); median 376 characters restored, none shortened | All 30,661 fetched again: same record, same source text; 0 left with the signature |
| 2026-11-11 | 2 | Passages after the abstract repair | 293,067 (7,287 abstract passages replaced by 9,377); 265,424 in the cited scope | 4,000 vectors went with their passages and were queued again; all 5,989 works match a fresh build |
| 2026-11-11 | 2 | BM25 index, rebuilt | 265,424 passages; 81 s; 196 MB | After the abstract repair |
| 2026-11-11 | 2 | Synthetic evaluation set, rebuilt | 6,628 items: 2,005 supported, 2,558 contradicted, 2,065 insufficient; 5,423 gold passage links | ADR-051's rules; 2,005 pairs from 1,176 works; replaces the 5,323-item build |
| 2026-11-11 | 2 | Synthetic items by corruption type, rebuilt | none 2,005; number changed 1,934; direction flipped 624; population changed 60; source swapped 2,005 | Swapped sources: 1,850 co-cited, 155 random |
| 2026-11-11 | 2 | Synthetic labels judged right, items from repaired abstracts | 12 of 13 | Claude's reading; the miss changed a range bound ("1000-1499 g") |
| 2026-11-12 | 2 | Claim skeleton validation failure rate (S2.7) | gemma4-31b-it: 11.0% first answer, 5.0% after two repairs | ADR-053; 200 contexts, seed 20260922; Qwen-235B 24.0%/2.5%, OLMo-32B 34.5%/8.5% |
| 2026-11-12 | 2 | Claim skeletons judged right in a spot check | gemma 14 right, 6 partly, 0 wrong of 20 | Claude's reading; Qwen 5 wrong, OLMo 3 wrong and 2 invalid |
| 2026-11-12 | 2 | Vector table after dropping the HNSW index | 1,134 MB, down from 2,195 MB | ADR-052; 271,416 vectors, exact search only |
| 2026-11-14 | 2 | Gateway energy estimate per claim call | about 2 Wh and 0.66 g CO2 (46.8 Wh, 15.8 g over 24 calls) | ADR-054; the gateway's own figures for gemma4-31b-it; full run about 80 kWh by this estimate |
| 2026-11-14 | 2 | Batch mode, live check | 10-request probe in 30 s; 20 contexts in 3 batches, 18 valid at once, 2 invalid | ADR-054 |
| 2026-11-12 | 2 | Claim extraction speed and size | 6.3 s per call, 1.17 calls and 823 + 111 tokens per context; about 0.5 contexts/s at 4 workers | ASU gateway, free; a full run of 35,921 contexts is about 20 h |
| 2026-11-14 | 2 | Claim skeleton validation failure rate, full run (S2.7) | 14.5% first answer [14.1, 14.8]; 6.5% after two repairs [6.3, 6.8] | 35,701 contexts, stage run 39; with the pilot, all 35,921 stored, 33,584 valid |
| 2026-11-14 | 2 | Claim extraction by batch | 81 min; 43,462 calls in 24 batches; about 7.3 contexts/s; 0 unanswered | ADR-054; 856 + 118 tokens and 1.22 calls per context; 5 answers cut off |
| 2026-11-14 | 2 | Gateway energy estimate, full claim run | 46.8 kWh, 15.8 kg CO2; 1.08 Wh per call | The gateway's figures; ADR-054 projected 80 kWh from the smoke run's 1.95 Wh per call |
| 2026-11-14 | 2 | Citation contexts with the wrong citing sentence | at least 993 of 35,921 (2.8%), in 77 papers | Failure log, 2026-11-14; 856 of their claims passed validation |
| 2026-11-14 | 2 | Retrieval nDCG@10, fusion + rerank (synthetic, own paper's body) | val 0.900, test 0.895 | ADR-055; 390 and 370 intact pairs; pools of 26 passages on average |
| 2026-11-14 | 2 | Retrieval nDCG@10, BM25 (synthetic) | val 0.919, test 0.916 | The set's gold repeats the claim's numbers (ADR-051) |
| 2026-11-14 | 2 | Reranker gain over fusion, full text | val +0.069 [+0.044, +0.094]; test +0.067 [+0.044, +0.090] | Paired bootstrap; answers ADR-050's re-measure |
| 2026-11-14 | 2 | Gain of fusion + rerank over BM25 (§10.3: 15–30%), synthetic | val −2.1% [−4.3%, +0.0%]; test −2.4% [−4.3%, −0.5%] | Below the band because of the set's construction; no sign of leakage |
| 2026-11-14 | 2 | Synthetic retrieval ablation run time | 97 min for 760 claims; 19,756 cross-encoder pairs | About 7.5 s per claim, nearly all reranking (3.4 pairs/s) |
| 2026-11-15 | 2 | Citation contexts changed by the splitter fix | 2,421 of 35,921 (6.7%), in 161 papers | ADR-056; replaces the "at least 993" lower bound; stage run 40 |
| 2026-11-15 | 2 | Claim skeleton validation failure rate, extractor 1.1.0 | 0.63% of all 35,921 after repairs [0.55, 0.72]; 35,695 valid | ADR-056; 31,389 copied from 1.0.0, 4,532 asked again (stage runs 43, 44); first-answer rate over all contexts not measured |
| 2026-11-15 | 2 | Tabular baseline feature table | 54,824 citations from 35,921 contexts in 968 papers; cited year known for 98.5%, text held for 89.9%, shared authors for 89.0% | ADR-057; built in about 9 s |
| 2026-11-15 | 2 | Tabular baseline, placebo labels (200) | macro F1 0.274 (20-seed range up to 0.369); ECE 0.253 → 0.019 after Platt scaling | ADR-057; no real labels until S3.4; 29 s per run |
| 2026-11-15 | 2 | Claim re-ask by batch | 56 min; 5,276 calls in 3 batches; 0 unanswered; 10.5 kWh, 3.6 kg CO2 (2.0 Wh per call) | The gateway's figures; asking all 35,921 again would have been about 47 kWh |

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

**2026-11-14 · Most "invalid" claim skeletons were exact copies with the citation markers left out**
After repairs, 2,180 of the full run's 2,325 invalid contexts still broke the statement rule. That rule wants the words of the citing sentence copied exactly, compared as one run of text. Invalid answers aren't stored, so 20 invalid contexts were asked again, with nothing written:
- 18 of the 20 failed the statement rule again;
- in all 18, the model had copied the sentence, or part of it, exactly except for the citation markers ("Halse et al. [60] noted" became "Halse et al. noted");
- two repair messages saying the statement wasn't copied exactly changed nothing.

I expected this rule to catch paraphrase and text from other sentences. It also catches the one edit that changes no meaning, and the "6.5% invalid" reported for S2.7 is mostly that. ADR-054's live check described its two invalid answers as statements "taken from outside the citing sentence". That was my reading of the rule's code name, not of the answers.

Not fixed yet. A fix needs:
- extractor 1.1.0, which also accepts a statement that matches the sentence with its citation markers removed;
- keeping invalid answers, so a later rule can re-check them without new calls;
- asking again for the invalid contexts.

That goes beyond this session (your choice). The pattern suggests roughly 1–2% would stay invalid.

Lesson: read a sample of what a validator rejects before reporting its rejection rate as the model's failure rate.

*Fixed 2026-11-15* (ADR-056). Building the fix showed the root cause. The prompt has always said "copied exactly. Leave out the citation marker." The rule required one exact run of the sentence. So the rule rejected exactly what the prompt asked for whenever the marker sat inside the statement. The repair message ("not copied exactly") couldn't resolve that contradiction, which is why two repairs changed nothing.
- **Extractor 1.1.0** lets the statement leave out any citation group stored in the context's `sentence_cites`.
- **It keeps invalid answers** in `claim.answer`.
- **It copied 31,389 valid skeletons** where the input was unchanged.
- **4,532 contexts were asked again.**
- **Result:** 0.63% of the 35,921 are invalid. Of the unchanged contexts that failed only 1.0.0's rule, 90.1% are now valid, as E010's 18 of 20 predicted.
- **Tests:** `test_claims.py` has both shapes from E010.

Second lesson: when a validator checks a prompt's output, read the prompt and the rule side by side.

**2026-11-14 · About 1 citation context in 36 has the wrong citing sentence**
I compared the 2,337 contexts whose claim skeleton failed validation in the full S2.7 run with the valid ones. Very short "sentences" were ten times commoner among the failures. Reading them showed 92 whose citing sentence was only the citation marker: "16 , 17".

The cause is S2.3's sentence splitter (ADR-046). A citation stays with the sentence before it only when at most two joining characters (commas, dashes, brackets) separate it from the full stop. Some publishers put a space there: "…until drain removal. 15 However, …". The boundary then falls before the citation, so the citation starts the next sentence. At the end of a paragraph, it stands alone as the whole "sentence". The 2026-11-02 fix covered a space before the full stop ("countries 20) ."), not after it. The splitter's tests have no citation after a full stop and a space.

Three patterns find at least 993 of the 35,921 contexts (2.8%), in 77 of the 968 papers with contexts:
- 209 where the sentence is the marker alone;
- the rest where the sentence starts with the marker, bare or in brackets, followed by a capital letter.

**Their claims:**
- 137 are invalid;
- 856 are valid but describe the sentence after the one the citation supports.

Grounding can't catch this. It checks that the statement is copied from the sentence it was given, not that this is the right sentence. Nothing downstream uses claims yet.

Not fixed yet. A fix means four steps:
- allowing whitespace between the punctuation and a citation that follows it;
- a new context extractor version;
- rebuilding the contexts;
- asking again for the claims of the contexts that change.

That goes beyond this session (your choice).

Lesson: a validity rate measures whether an answer fits its input, not whether the input is right. Reading the failures found an upstream bug that the 856 valid rows were hiding.

*Fixed 2026-11-15* (ADR-056). Splitter rules 1.1.0 keep a numeric citation after a space with the sentence before it, and contexts 1.1.0 were built with them.
- **More contexts changed than the lower bound:** 2,421, in 161 papers, against "at least 993".
- **958 had the wrong sentence.**
- **944 more were two sentences run together.** A spaced citation list ("(QoL).3, 4, 5, 6 Psychological …") failed the old two-character glue rule, so the boundary was never found. The three patterns above couldn't see this, because the sentence still contained its marker.
- **Their claims were asked again.** Where the citing sentence changed, 28% of claims changed kind, against 4% where only a neighbour changed.
- **Tests:** `test_passages_contexts.py` now has the spaced superscript, the paragraph-end marker, the spaced list, a bracket after the stop, the author-year subject and the lowercase join.
- **Passages keep the 1.0.0 rules** until their next version.

**2026-11-11 · The abstract cleaner deleted text after a literal "<" in about 12% of abstracts**
Reading S2.8's first synthetic items found claims that ran two sentences together: "…in the BSB group (P Conclusion Our BSB could be…". The cause is `clean_markup` (S2.2), which removes tags with `<[^>]+>` before unescaping. Europe PMC's `abstractText` mixes real tags (`<h4>Conclusion</h4>`) with unescaped less-than signs (`P<0.001`). So everything from `<0.001).` to the next tag's `>` went, the heading's opening tag included. The 2026-11-01 fix covered escaped tags after unescaping, not this. Its test case, "18.5<BMI<25", has no tag after the "<", so the bug never showed.

Abstract passages come from these cleaned abstracts, and body passages from the JATS, which is unaffected.
- 4,233 of 36,418 works' abstract passages show the "P Conclusion" signature.
- Losses with no heading after them leave no signature, so the true count is higher.
- No results passage shows it (0 of 48,709).

The raw `abstractText` wasn't kept, so a repair means fetching again, then replacing those abstract passages and their vectors. Not fixed yet (your choice: its own session next). S2.8 keeps merged sentences out of its claims. Lesson: when testing a rule that must not delete something, include the case where the thing to keep comes before something the rule should delete.

*Fixed the same day.* `clean_markup` now removes only tag-shaped markup: "<" or "</", a name, optional attributes, then ">". A "<" followed by a digit, a space or "=" stays text. `test_fulltext.py` has the BSB case. The repair:
- **Text.** All 30,661 Europe PMC abstracts were fetched again with the same lookups. Every one came back as the same record, and the old cleaner reproduced every stored abstract exactly, so the source text hadn't changed. The fixed cleaner changed 5,989 (19.5%) and shortened none, restoring a median of 376 characters. They include 4,229 of the 4,231 with the signature; the other two are real text ("…reference group Conclusions"). Each update went through `TextAvailability`. Crossref's stored abstracts have no bare "<", and OpenAlex's never pass through this code.
- **Passages.** Each changed work's abstract passages were replaced under the same splitter version: 7,287 deleted, 4,000 of them with vectors that went with them, and 9,377 inserted. Body passages kept their IDs and vectors; in 576 works their ordinals moved. No gold label pointed at a replaced passage. Every repaired work matches a fresh build from its abstract and JATS, and a second pass finds nothing out of date. The embedding run was paused for the swap and resumed.
- **Downstream.** The corpus BM25 index was rebuilt. The synthetic set was deleted and built again: 6,628 items from 2,005 pairs (was 5,323 from 1,628), because sentences that had lost their numbers now qualify. ADR-051's counts describe the first build.

The fetch and repair were one-off scripts, not kept in the repo. One SciFact abstract shows the same damage ("(P Conclusions. —The…") in SciFact's own release; that text is SciFact's and was left alone.

**2026-11-11 · The HNSW index cut every cosine search in the first ablation short**
The first full ablation run (68.6 min) printed a plausible table, with cosine a little behind dot product. Checking how many results each row returned showed every cosine dense list had fewer than 50: median 24, at most 40, and one empty. `EXPLAIN` showed why. The cosine query was served by the HNSW index, which returns its 40 nearest vectors (`hnsw.ef_search`) and only then applies the SciFact filter. With 23,552 corpus vectors in the same table, most of those 40 were other papers' passages. Dot product has no HNSW index, so its searches were exact and its rows were right. ADR-044 had assumed pgvector 0.8's iterative scans would prevent this, but they are off by default and nothing turned them on. Dense search is now exact by construction (ADR-050), a test reproduces the empty result under the old query, and the ablation now warns when any row returns fewer than 50. The configured metric was always `dot`, so no stored result was affected. Lesson: a table of averages can look right while every row underneath it is broken; check the shape of the outputs, not just the means.

**2026-11-10 · My estimate for CPU embedding was off by a factor of 50**
Before Q1 was answered, I guessed CPU embedding with TEI would take "a few hours". Measured, TEI's CPU backend did 0.3 passages a second, which would have been 11 days. The decision (your choice: "CPU here, measured first") asked for a measurement before the full run, and that is what caught it: the model card's PyTorch code does 5 a second, and the run takes about 14 hours. The same test showed TEI can't take MedCPT's title-and-passage pair, which the documents had assumed away. Both are recorded in ADR-047. Lesson: time a sample before quoting a duration.

**2026-11-02 · Sentence boundaries hidden by markup, found by reading the output**
The first full context run looked fine in aggregate (96.2% of references with a context), but the longest "sentences" were 365 words. Reading them showed three ways JATS hides a boundary: list items inside a paragraph rendered with no space ("ultrasound.Recent use"), a space between a citation and its full stop ("countries 20) . While"), and panel titles running into their text ("Research in contextEvidence"). A separate check of linked references against printed citation numbers found a fourth problem: some publishers mark up the dash of a range as its own citation, so "20–27" linked only 20, 21 and 27. All four are fixed and tested, and the rows made before the fixes were deleted. As with C03's precision rules, aggregate numbers didn't show any of this; reading the longest and the mismatched cases did.

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
| Is a GPU available, or should one be rented? | Decides whether encoders and fine-tuning run locally or through an API | Week 6 | **answered: ADR-047.** No GPU; embeddings run on this CPU with PyTorch. Training in Phase 3 may still need one. Tracked as Q1 |
| What share of *cited* sources have full text available? | Limits how many claims can be checked against their source. This is different from coverage of the citing papers, which is about 100% by design | Week 4 | **answered: ADR-045** (whole corpus, not 100 papers). Tracked as Q2 |
| Publish findings about individual papers, or only totals? | Wrongly accusing someone of making up a reference would do serious harm | Week 10 | open. Current leaning: rule-based checks may name papers, model-based checks report rates only. Tracked as Q4 |
| Q1: Is a GPU available? | Decides whether encoders and training run locally or through an API | Before S2.4 | **answered: ADR-047.** No GPU: MedCPT runs in-process on this CPU (5 passages a second); TEI measured 16x slower |
| Q2: What share of cited sources have open full text? | Limits how much C09 can check | Measured in S2.2 | **answered: ADR-045.** 22.6% of resolved references cite a work with full text; 92.8% at least an abstract |
| Q3: What exact Europe PMC query picks the corpus? | Decides what the working corpus is | S1.6 | **answered: ADR-028** |
| Q4: Publish findings about individual papers, or only totals? | Wrongly accusing someone of making up a reference would do serious harm | Before S5.8 | open |
| Q5: Where can earlier versions of registered outcomes come from? The public v2 API has no history endpoint. Options: the website's change-history tab (check the terms of use); a third-party dataset that rebuilds the history (check the licence and access); or the current record only, which supports only the weaker claim "the registry and the paper don't match" rather than "the outcome was switched". | Decides what C06 can honestly claim | Before S3.9, ideally by the end of Phase 1 | open |
| Q6: Which outside labelled dataset, and is its licence compatible? | Whether the external evaluation is valid | Before S2.8 | **answered: ADR-049.** SciFact from the authors' release: claims CC BY 4.0, abstracts ODC-By 1.0 |
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
