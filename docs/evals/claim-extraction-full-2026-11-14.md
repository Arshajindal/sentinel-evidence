# Claim skeletons, full run (S2.7)

**Date:** 2026-11-14 · **Decisions:** ADR-053 (model, rules), ADR-054 (gateway, batch mode) in `PROGRESS.md` · **Pilot:** `claim-extraction-pilot-2026-11-12.md` · **MLflow:** experiment `claim-extraction`, run "gemma4-31b-it all" · **Stage run:** 39 · **Rows:** `claim`, extractor `llm:gemma4-31b-it`, version 1.0.0

## In short

- gemma4-31b-it extracted skeletons for the 35,701 citation contexts the pilot hadn't covered. It ran through the ASU gateway's batch API in 81 minutes, with 24 batches and 43,462 calls. The gateway refused none and left none unanswered.
- **Validation failure rate:** 14.5% on the first answer (95% CI 14.1–14.8), 6.5% after up to two repairs (6.3–6.8). That is a little above the pilot's 11.0% and 5.0%, and inside the pilot's intervals.
- **All 35,921 contexts now have a stored result:** 33,584 valid skeletons and 2,337 invalid.
- **Almost every remaining failure is the statement rule:** 2,180 of the 2,325 contexts left invalid break it. Repairs fix most other rules but only 40% of statement failures.
- **Most of those are the rule's fault, not the model's.** Asking again for 20 invalid contexts showed what happens in 18 of them: the model copies the sentence exactly but leaves out its citation markers, which the exact-copy rule rejects. A marker-tolerant rule would put the failure rate after repairs at roughly 1–2%. *Fixed 2026-11-15 in extractor 1.1.0: 0.63% invalid. See `claim-extraction-1.1.0-2026-11-15.md`.*
- **Reading the failures found an upstream bug.** At least 993 contexts (2.8%) have the wrong citing sentence, from S2.3's sentence splitter. 856 of them still passed validation (failure log, 2026-11-14). *Fixed 2026-11-15 in contexts 1.1.0: 2,421 contexts changed, more than this lower bound. See `claim-extraction-1.1.0-2026-11-15.md`.*
- **The gateway's own energy estimate:** 46.8 kWh and 15.8 kg CO2. That is about 1.08 Wh per call, half the smoke run's rate, so about 60% of ADR-054's 80 kWh projection.

## Setup

As in the pilot, with these differences:

| | |
|---|---|
| Contexts | All 35,921 of corpus `pmc-rct-2015-2024`, less the 220 the pilot and batch smoke run had already stored |
| Gateway | `https://openai.rc.asu.edu/v1` with a current key (ADR-054), not the legacy LiteLLM proxy. Every call answered from server build `vllm-0.26.0-tp4-24487d56`, the pilot's. |
| Mode | Batch (`--batch`): 5,000 contexts per batch, up to 8 at once, a 24-hour window. Each repair round is a new batch. |
| Command | `sentinel extract claims --model gemma4-31b-it --corpus pmc-rct-2015-2024 --batch` |

## Results

| | Contexts | Share |
|---|---:|---:|
| Valid on the first answer | 30,541 | 85.5% |
| Valid after one or two repairs | 2,835 | 7.9% |
| Still invalid | 2,325 | 6.5% |
| Refused by the gateway (400) or unanswered | 0 | 0% |

| Round | Batches | Requests | Fixed |
|---|---:|---:|---:|
| First answer | 8 | 35,701 | — |
| First repair | 8 | 5,160 | 2,559 (49.6%) |
| Second repair | 8 | 2,601 | 276 (10.6%) |

**Size:** 856 prompt and 118 answer tokens per context, 1.22 calls per context. Five answers were cut off at the 1,024-token limit. The run took 81 minutes of wall time, about 7.3 contexts a second. Direct calls managed about 0.5 at 4 workers.

**Which rules broke:** a context can break more than one.

| Rule | First answer | After repairs |
|---|---:|---:|
| Statement not copied from the citing sentence | 3,626 | 2,180 |
| Finding without a direction | 639 | 0 |
| Outcome not in the context | 520 | 96 |
| Population not in the context | 293 | 29 |
| Finding fields set on a non-finding | 134 | 2 |
| Intervention not in the context | 99 | 17 |
| Effect not in the context | 93 | 11 |
| Sample size not in the context | 79 | 3 |
| Comparator not in the context | 40 | 3 |
| Not valid JSON | 6 | 2 |
| Statement empty | 0 | 4 |
| Intervention too long | 1 | 0 |

The pilot's 200 contexts had no finding without a direction. Here 639 first answers (1.8%) had one, and every one was fixed on repair. At the full run's rate, the pilot would have expected about 4. Seeing none has a chance of about 3%. So the 200 were a little lucky, or something differed. The server build is the same, and the batch lines ask exactly what direct calls ask (`request_body` is shared).

**What the valid skeletons say** (this run's 33,376):

| Finding | Background | Method | Other |
|---:|---:|---:|---:|
| 14,716 (44.1%) | 10,574 (31.7%) | 7,584 (22.7%) | 502 (1.5%) |

**Invalid share by section,** all 35,921 stored claims: introduction 4.5%, other 4.4%, discussion 7.5%, methods 8.0%, results 8.6%.

## Why skeletons fail

Invalid answers aren't stored, only the context and `valid = false`. So the failures were studied from their contexts, and from a sample asked again.

**Wrong citing sentence (an S2.3 bug).**
- Citing sentences under 40 characters are 4.0% of the invalid contexts and 0.4% of the valid ones.
- 209 contexts have a "sentence" that is only the marker ("16 , 17"). In 92 of them the claim is invalid; in 117 it is "valid", with the marker copied as the statement, and 5 of those count as findings.
- Another 784 contexts have the marker at the start of the following sentence ("15 However, …"), so their claim describes the wrong sentence.
- Together that is at least 993 contexts in 77 papers: 137 invalid, 856 valid. The cause and fix are in the failure log (2026-11-14). Their skeletons should be asked for again once contexts are rebuilt.

**Statement failures: faithful copies with the citation markers left out.** Twenty invalid contexts were asked again directly, with the same prompt and repairs; nothing was stored. They were the first 20, in `md5("11:" ‖ id)` order, among those whose citing sentence has words. That took 59 calls and, by the gateway's estimate, 136.8 Wh.
- 1 was valid this time, after one repair.
- 1 broke the effect rule.
- **18 broke the statement rule again after two repairs.** In all 18, the statement is the citing sentence, or a stretch of it, copied exactly except that the citation markers are left out:
  - "Halse et al. [60] noted that…" became "Halse et al. noted that…";
  - "chemotherapy,16,17,18,19,20 offering…" became "chemotherapy, offering…".

  None was a paraphrase, and none came from another sentence. Being told twice that the statement wasn't copied exactly didn't make the model put the markers back.

So most of the 6.5% is the rule, not the model. The rule compares one exact run of text, so a faithful copy without its markers fails. Comparing against the sentence with its citation markers removed would have passed 18 of these 20 (95% CI 70–97%). Applied to the 2,180 contexts left failing the statement rule, that suggests a failure rate after repairs of roughly 1–2%, not 6.5%. This is a rough extrapolation from 20 contexts. ADR-054's live check described its two invalid answers as statements "taken from outside the citing sentence". That was a reading of the rule's code, and this sample doesn't support it.

A fix belongs in extractor 1.1.0:
- also accept the statement when it matches the sentence with citation markers removed, either by pattern or from citation spans stored with each context;
- ask again for the invalid contexts.

The relaxed rule accepts everything 1.0.0 accepted. Not done yet (failure log, 2026-11-14).

## Energy

The gateway reports its own estimate of each call's energy and CO2 in `usage`. These are its figures, not a measurement made here.

| Run | Calls | Wh | g CO2 | Wh per call |
|---|---:|---:|---:|---:|
| Smoke run, 20 contexts by batch (stage run 38) | 24 | 46.8 | 15.8 | 1.95 |
| Full run (stage run 39) | 43,462 | 46,818 | 15,778 | 1.08 |

The two totals share their first three digits by chance: the second run made 1,811 times as many calls. Summing one 5,000-line output file directly gives 1.059 Wh per call. That agrees with the run's total, so the total isn't a counting error. The estimate per call fell at scale, presumably because full batches share the hardware better. ADR-054 projected 80 kWh from the smoke run's rate.

## Limits

- **Provenance in batch mode.** Batch output lines have no response headers, and a batch's status names only the model. So a model family's variant can't be recorded in batch mode. Gemma isn't a family, and every call's server build (`system_fingerprint`) is recorded. A family model run by batch would lose its variant.
- **Invalid answers aren't kept.** Only the rule codes reach the logs and MLflow, so studying failures means asking again. Temperature 0 on vLLM is close to repeatable, not exact. Had the answers been kept, a relaxed rule could re-check them without new calls. Version 1.1.0 should keep them.
- **The spot check is still the pilot's.** Field accuracy at scale waits for S3.4's hand-labelled set.
- **Contexts with the wrong sentence** stay in the table until contexts are rebuilt. Anything that uses claims before then should leave out the 993 found here.
