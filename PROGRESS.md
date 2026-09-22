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
**Week:** 0 (not started)
**Last worked:** —

**What works:** nothing yet.

**What's broken:** nothing yet.

**Next three things:**
1. Repo scaffold, `pyproject.toml`, Alembic initialised
2. Initial migration with the Phase 1 schema
3. Crossref harvester, resumable, with watermarks

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

---

## Open questions

| Question | Why it matters | Resolve by | Status |
|---|---|---|---|
| Which starting corpus? | Affects fixture design and prevalence comparability | Week 1 | open |
| GPU available? | Determines local vs API serving for encoders | Week 4 | open |
| Full-text coverage rate on the chosen corpus? | Caps the addressable share of claim grounding | Week 4 | open |
| Publish individual findings, or aggregate only? | A false fabrication accusation is a serious harm | Week 13 | open |
| Does `config.py` carry defaults, or require every variable? | Decides whether `.env.example` is documentation or mandatory setup | Week 1 | open |

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
