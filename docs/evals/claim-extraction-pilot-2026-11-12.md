# Claim skeleton pilot (S2.7)

**Date:** 2026-11-12 · **Decisions:** ADR-053 in `PROGRESS.md` · **MLflow:** experiment `claim-extraction`, one run per model · **Rows:** `claim`, extractors `llm:<model>`, version 1.0.0

## In short

- Three open-weight models on ASU Research Computing's free gateway extracted skeletons from the same 200 citation contexts.
- **The validation failure rate, S2.7's metric:**
  - gemma4-31b-it: 11.0% on the first answer, 5.0% after up to two repairs;
  - qwen3-235b-a22b-instruct-2507: 24.0%, then 2.5%;
  - olmo3-32b-instruct: 34.5%, then 8.5%.
- **Gemma is chosen.** In a side-by-side reading of 20 contexts, it got no skeleton wrong. Qwen got 5 wrong, mostly by calling speculation or the citing paper's own result a finding. OLMo got 3 wrong by calling findings "background", and 2 more were invalid.
- **Every model sometimes borrows a field from a neighbouring sentence,** usually the citing study's own population. The grounding rules allow this, since the field is in the context. It's the main weakness to fix in a later version.
- **Not yet done:** extracting all 35,921 contexts. With direct calls at 4 workers it would take about 20 hours, so it will run through the gateway's batch API (`--batch`, ADR-054).

## Setup

| | |
|---|---|
| Contexts | 200 of the 35,921 citation contexts of corpus `pmc-rct-2015-2024`: the first 200 in `md5("<seed>:" ‖ id)` order, seed 20260922. The same 200 for every model. |
| Gateway | `https://openai.rc.asu.edu/v1`, OpenAI-compatible chat completions. It answered through LiteLLM 1.83.7 over vLLM 0.26.0. Each model ran on one deployment, and its ID and server build are in the run's `stage_run` row. |
| Request | Temperature 0, at most 1,024 answer tokens, `response_format` a strict JSON schema from the `ClaimSkeleton` Pydantic model. The system prompt says the paper text is untrusted. The passage and the citing sentence go inside tags (`extract/prompt.py`). |
| Valid | The answer parses into `ClaimSkeleton`, and it passes the grounding rules (`extract/skeleton.py`): the statement is copied from the citing sentence; population, intervention, comparator, outcome and effect are copied from the context; a sample size is a number written there; a finding has a direction; anything else has no finding fields. Case, dashes, quotes and spacing are ignored. |
| Repair | An invalid answer goes back with the rules it broke, up to two times. What still fails is stored as `valid = false` with no skeleton. |
| Workers | 4 per model, with the three models run at the same time. |

## Results

| Model | Invalid, first answer | Invalid after repairs | Calls | Seconds per call | Tokens per context, prompt / answer |
|---|---:|---:|---:|---:|---:|
| gemma4-31b-it | 22 (11.0%, 95% CI 7.4–16.1) | 10 (5.0%, 2.7–9.0) | 233 | 6.3 | 823 / 111 |
| qwen3-235b-a22b-instruct-2507 | 48 (24.0%, 18.6–30.4) | 5 (2.5%, 1.1–5.7) | 258 | 9.4 | 909 / 138 |
| olmo3-32b-instruct | 69 (34.5%, 28.3–41.3) | 17 (8.5%, 5.4–13.2) | 303 | 2.9 | 1,095 / 152 |

Intervals are Wilson 95% intervals over 200 contexts. Gemma's counts include two contexts from a smoke run with the same code and version. No answer was cut off at the token limit, and the gateway refused none. The token counts are from the runs' own usage records.

**Which rules broke on the first answer:**

| Rule | gemma | qwen | olmo |
|---|---:|---:|---:|
| Statement not copied from the citing sentence | 16 | 18 | 26 |
| Outcome not in the context | 4 | 15 | 18 |
| Population not in the context | 2 | 19 | 14 |
| Effect not in the context | 0 | 6 | 5 |
| Comparator not in the context | 0 | 5 | 5 |
| Intervention not in the context | 0 | 2 | 3 |
| Finding fields set on a non-finding | 0 | 0 | 23 |
| Finding without a direction | 0 | 0 | 1 |

**What the valid skeletons say:**

| Model | Finding | Background | Method | Other |
|---|---:|---:|---:|---:|
| gemma4-31b-it | 86 | 62 | 41 | 1 |
| qwen3-235b-a22b-instruct-2507 | 117 | 40 | 38 | 0 |
| olmo3-32b-instruct | 35 | 117 | 31 | 0 |

The three agree on the kind for 91 of the 200 contexts. Gemma and Qwen agree on 150, Gemma and OLMo on 122.

## Spot check

These are Claude's readings, not human labels. Twenty contexts, the first 20 in `md5("99:" ‖ id)` order among the 200, were read with all three skeletons side by side. A skeleton is **right** if its kind is right and every filled field is correct for the cited claim, with null allowed for what isn't stated. It is **partly right** if the kind is right but a field is borrowed from a neighbouring sentence or put in the wrong slot. It is **wrong** if the kind is wrong, or if it makes a finding out of speculation or the citing paper's own result.

| Model | Right | Partly right | Wrong | Invalid |
|---|---:|---:|---:|---:|
| gemma4-31b-it | 14 | 6 | 0 | 0 |
| qwen3-235b-a22b-instruct-2507 | 10 | 5 | 5 | 0 |
| olmo3-32b-instruct | 13 | 2 | 3 | 2 |

What the misses look like:
- **Qwen calls things findings that aren't.**
  - "compliance … might be poor": speculation, extracted as a finding.
  - "the carryover effect … may distort the results": also extracted as a finding.
  - The citing paper's own nonsignificant sodium result was attributed to the reference it cites for background.
  - "Traumatic injuries represent 10% of the disease burden" got the direction "increase".
- **OLMo calls findings "background",** for example "the pharmacokinetic and safety profiles of serelaxin were not affected in … hepatic impairment".
- **All three borrow fields.**
  - The citing study's "older subjects" or "participants" appears as the cited study's population.
  - A condition ("when bisphosphonate treatment is administered") appears as a comparator.
- **Gemma's six partly right skeletons** each have a borrowed field or a phrase in the wrong slot. Examples are a vaccine antigen as the "intervention", and "weight loss" with direction "decrease".

## Limits

- 200 contexts and a 20-context reading are enough to choose between models, not to estimate field accuracy. Phase 3's hand-labelled set (S3.4) should measure that.
- Grounding catches invented and paraphrased text, not text copied from the wrong sentence. A rule tying population, intervention and outcome to the citing sentence would catch borrowing, but it would also reject a comparator named in the sentence before ("… comparing atorvastatin 80 mg/day with placebo. Atorvastatin lowered …"). A later version should measure that trade-off before choosing.
- "Finding" is broad by design: rates and frequencies count. That is why the models disagree on epidemiological sentences ("represent 10% of the disease burden").
- Temperature 0 on vLLM is close to repeatable but not guaranteed, since batching can change the arithmetic. The rows, not a rerun, are the record.
- The gateway can retire a model. The deployment ID and server build in `stage_run` show which one answered.
