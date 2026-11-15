# Claim skeletons 1.1.0: the citing-sentence fix and the marker-tolerant statement rule (S2.7 follow-up)

**Date:** 2026-11-15 · **Decisions:** ADR-056 in `PROGRESS.md` · **Before:** `claim-extraction-full-2026-11-14.md` and the two failure-log entries of 2026-11-14 · **Stage runs:** 40 (contexts), 43 (carry-over), 44 (re-ask) · **MLflow:** experiment `claim-extraction`, run `dc1bb082bd9b4d379a2ed0946f415ae7` · **Rows:** contexts `extractor_ver = 1.1.0`; claims `llm:gemma4-31b-it`, version 1.1.0

## In short

- **Both problems found in the full run are fixed.** Citation contexts 1.1.0 keep a numeric citation printed after the full stop and a space ("removal. 15 However") with the sentence it supports. Claim extractor 1.1.0 lets the statement leave out citation markers, which the prompt has always asked for.
- **The contexts changed more than the failure log estimated:** 2,421 of 35,921 (6.7%), in 161 papers, against a lower bound of 993.
  - 958 had the wrong citing sentence: either the citation opened the next sentence, or the "sentence" was only the marker.
  - 944 more had two sentences run together, because a spaced citation list ("(QoL).3, 4, 5, 6 Psychological …") hid the boundary.
- **Every context now has a stored claim, and 0.63% are invalid** (226 of 35,921; 95% CI 0.55–0.72%). Under 1.0.0 it was 6.5% (2,337). The failure log predicted roughly 1–2%.
- **Only the 4,532 contexts that needed it were asked again:** 2,421 whose text changed, and 2,111 unchanged but invalid under 1.0.0. The other 31,389 kept their valid 1.0.0 skeletons, and every one of those passes 1.1.0's rules.
- **The diagnostic's prediction held.** Of the 2,111 contexts that were invalid only under 1.0.0's rule, 90.1% are valid now. Asking 20 of them again on 2026-11-14 had predicted 18 of 20.
- **The old claims for changed sentences described the wrong sentence.**
  - Where only a neighbouring sentence changed, 4% of claims changed kind (10 of 244).
  - Where the citing sentence changed, 28% did (539 of 1,941).
- **Passages, vectors, the BM25 index and the synthetic set are untouched.** Passages keep the splitter's 1.0.0 rules.
- **The gateway's own energy estimate:** 10.5 kWh and 3.6 kg CO2. That is about 2.0 Wh per call, nearer the smoke run's rate than the full run's 1.08.

## The splitter and contexts 1.1.0

**The rule** (`fulltext/sentences.py`, `cites_across_space=True`):
- A citation after the end punctuation stays with the sentence before it, even when a space comes first, as long as it is numeric.
- The citations after it may be spaced ("16 , 17"), with up to three joining characters between them ("], [").
- Closing brackets after the last one go with it.
- An author-year citation after a space still starts the next sentence, because there it is usually the subject ("Gorini et al. (2014) investigated").
- When the next character isn't a capital, the sentences stay joined, as before ("level. 11 miRNAs have been").

**The spans:** each context now stores where every citation group in its sentence sits (`sentence_cites`). Linked and unlinked groups are both included, widened to brackets that enclose them exactly. That averages 1.41 groups per sentence, with a maximum of 14. Every context's own marker falls inside one of its spans.

**What changed,** context by context against 1.0.0:

| Change | Contexts |
|---|---:|
| Two sentences that 1.0.0 ran together are now split | 944 |
| The citation opened the next sentence; it now ends its own | 774 |
| The "sentence" was only the marker; it is now the sentence the marker ends | 184 |
| The sentence gained a trailing marker it had lost (for example "(SF‐12). 12") | 166 |
| Other changes at the sentence's edges | 70 |
| Only a neighbouring sentence changed | 283 |
| **Total** | **2,421** |

**All contexts, before and after:**

| | 1.0.0 | 1.1.0 |
|---|---:|---:|
| Citing sentence starts with its own marker | 962 | 4 |
| Citing sentence is only the marker | 208 | 0 |
| Citing sentence under 40 characters | 227 | 21 |
| Citing sentence over 120 words | 195 | 86 |
| Words in the citing sentence, median / 99th percentile | 28 / 102 | 28 / 83 |

The 4 left starting with their marker are author-year subjects, as intended. The new rule joins 4 contexts that 1.0.0 split:
- 2 rightly, at "S.Va.M.A. [36]" and a version number, "3.44.3. [15]";
- 2 at "level. 11 miRNAs", the joined-when-unsure case.

Citations, groups, contexts, ranges and links are identical to 1.0.0: 49,770 citations, 35,995 groups, 35,921 contexts, 2,508 ranges expanded, and 96.3% of references with a context.

**Why passages keep the old rules.** Passages 1.0.0 were cut with the same splitter, and their text feeds 265,424 vectors (about 14 h of CPU), the BM25 index and the synthetic set's gold passages. Under the new rules, 858 of the 13,690 corpus-paper paragraphs that contain a citation would split differently; cited works weren't measured. A citation marker moving between neighbouring sentences changes passage text only at window edges, and retrieval matches the passage, not the sentence. So `make_passages` passes `cites_across_space=False`, and a rebuild still gives the stored text. The new rules can wait for the next passage version.

## Claim extractor 1.1.0

**The rule** (`extract/skeleton.py`, `statement_in_sentence`):
- The statement must read off the citing sentence as one run, as before, except that any of the sentence's citation groups may be left out ("Halse et al. noted" is in "Halse et al. [60] noted").
- Where a marker is left out, spacing is ignored, because the marker's spaces stay behind.
- Nothing else is excused: a paraphrase, a skipped phrase, a dropped comma or a statement stitched from parts still fails.
- 1.1.0 accepts everything 1.0.0 accepted.

**Why it was wrong before:** the prompt has always said "copied exactly. Leave out the citation marker." The 1.0.0 rule required one exact run of the sentence, so it rejected exactly what the prompt asked for whenever the marker sat inside the statement. Reading the prompt alongside the rule would have caught this before the pilot.

**Invalid answers are kept.** `claim.answer` holds an invalid claim's last answer as text, and `claim.errors` the rules it broke. A valid claim keeps only its skeleton. Nothing reads `answer` as a skeleton. It is there so a later rule can re-check failures without asking again.

The prompt, schema, model, temperature and repair limit are 1.0.0's.

## Which contexts were asked again

`sentinel extract claims-carry --model gemma4-31b-it --from-ver 1.0.0` copied a 1.0.0 skeleton to the matching 1.1.0 context only when all of these held:
- **the input is unchanged:** the same paper and ordinal, and the same sentence, passage, marker, section and cited references, so the model would be asked exactly the same thing;
- **the 1.0.0 claim is valid;**
- **the copy passes 1.1.0's rules,** which is checked again before each copy is stored.

That copied 31,389 skeletons, and none failed the check. The other 4,532 contexts were asked by batch (`sentinel extract claims --model gemma4-31b-it --batch`).

| Group | Contexts | Invalid after repairs |
|---|---:|---:|
| Copied from 1.0.0 | 31,389 | 0 |
| Text changed, asked again | 2,421 | 16 (0.7%; 95% CI 0.4–1.1) |
| Unchanged, invalid under 1.0.0, asked again | 2,111 | 210 (9.9%; 8.7–11.3) |
| **All** | **35,921** | **226 (0.63%; 0.55–0.72)** |

**The run:**
- 56 minutes; 5,276 calls in 3 batches (4,532, then 498, then 246); none refused or unanswered;
- 822 prompt and 125 answer tokens per context; 1.16 calls per context; one answer cut off at the limit.

**On the 4,532 asked,** 89.0% were valid on the first answer, 6.0% after repairs, and 5.0% stayed invalid. These contexts were chosen because they had failed before or had changed, so these rates aren't comparable with the full run's 14.5% and 6.5%.

**Rules broken** by the 4,532 asked (a context can break more than one):

| Rule | First answer | After repairs |
|---|---:|---:|
| Outcome not in the context | 143 | 91 |
| Statement not in the citing sentence | 195 | 90 |
| Population not in the context | 59 | 25 |
| Intervention not in the context | 27 | 13 |
| Effect not in the context | 24 | 8 |
| Comparator not in the context | 8 | 2 |
| Finding without a direction | 59 | 0 |
| Finding fields set on a non-finding | 22 | 0 |
| Sample size not in the context | 21 | 0 |
| Not valid JSON | 1 | 1 |

**What the valid skeletons say,** all 35,695:

| Finding | Background | Method | Other |
|---:|---:|---:|---:|
| 15,915 (44.6%) | 11,087 (31.1%) | 8,411 (23.6%) | 282 (0.8%) |

"Other" fell from 1.5% of the full run's valid skeletons. Marker-only "sentences" used to give "other", or a marker copied as the statement.

**Invalid share by section:** discussion 0.8%, introduction 0.8%, results 1.0%, methods 0.2%, other 0.4%.

## What the changed contexts show

Of the 2,421 changed contexts, 2,185 had a valid claim under both versions.
- **Where the citing sentence changed** (1,941), 539 claims (28%) changed kind.
- **Where only a neighbouring sentence changed** (244), 10 did (4%). That is roughly the background rate for asking about the same sentence again with a slightly different passage.

Eight of the 539 were read side by side, the first 8 in `md5("15:" ‖ id)` order. In all 8, the 1.0.0 claim described the sentence after the citation, or the marker itself (statement "22", kind "other"). The 1.1.0 claim describes the sentence the citation ends. One example:
- 1.0.0, "background": "To minimise postoperative complications, surgeons frequently insert hemostatic agents …";
- 1.1.0, "finding": "However, adverse events such as allergic skin reactions and seroma formation have also been described."

## What still fails

**226 contexts.** Grounding of the finding fields now accounts for as many failures as the statement rule.

**Fields not found in the context** (outcome 88, population 24, intervention 13, effect 6, comparator 2, and 2 with two fields): the model reworded or shortened a field. These are the grounding rules working as meant.

**Statement not in the sentence** (90). The kept answers were read:
- **40 use only words from the context, but not as one run.** Most pick out the part of a list sentence that belongs to this citation: "ZIKV infection has also been associated with neurologic disorders [16], sexual transmission [17, 18], …" became "ZIKV infection has also been associated with sexual transmission". That is a fair reading for this citation, but it isn't a copy, so it is rightly reported as not grounded by the current rule.
- **50 have words that aren't in the context.** They include:
  - a dropped letter ("“esarean section");
  - keeping its own marker but dropping the one before ("Cooper et al. [28]" from "Cooper et al. [27], [28]");
  - statements that run on into the next sentence.
- **24 of the 90 would pass if a citation's whole parenthesis could be left out.** Some publishers mark up only the year of an author-year citation ("(Peacock et al. <xref>2013</xref>)"), so the stored span is "2013" and the model's "… these efforts found that …" fails. That is 0.07% of all contexts. The answers are kept, so a later rule can re-check them without asking again.

**Not valid JSON:** 1.

## Energy

| Run | Calls | Wh | g CO2 | Wh per call |
|---|---:|---:|---:|---:|
| Re-ask, 4,532 contexts (stage run 44) | 5,276 | 10,543 | 3,553 | 2.00 |
| For comparison: full run, 2026-11-14 | 43,462 | 46,818 | 15,778 | 1.08 |

These are the gateway's own estimates. The rate per call here is close to the 20-context smoke run's 1.95, not the full run's 1.08. That fits the full run's explanation that full batches share hardware better, but nothing here tests it. Asking every context again would have cost roughly another 47 kWh by the full run's rate.

## Limits

- **The 1.1.0 claim set is mixed.** 31,389 skeletons come from 1.0.0's answers, and 4,532 from new ones. Under 1.0.0, about 8% of valid skeletons were accepted only after a repair round (the full run's share), and which ones wasn't recorded. Some of those first answers would have passed 1.1.0 at once, giving a different, equally valid skeleton. Both pass the same rules. So 1.1.0's first-answer failure rate over every context isn't measured, and isn't claimed.
- **Temperature 0 on vLLM is close to repeatable, not exact.** "Unchanged input" means the same request, not a guaranteed identical answer.
- **Field accuracy is still unmeasured at scale.** This run fixes which sentence is read and how the statement is checked. It doesn't measure whether the fields are right; that waits for S3.4's hand-labelled set.
- **A list sentence's statement for one citation can't be a part-copy.** The rule accepts the whole run or a stretch of it, not a selection. That is 40 of the remaining failures, and it is a design question for a later version.
- **The 2,421 changed contexts have new IDs.** The 1.0.0 contexts and claims stay, including the pilot's Qwen and OLMo rows, so the 2026-11-12 and 2026-11-14 reports can be reproduced. Anything that uses claims reads `extractor_ver = 1.1.0` for both contexts and claims.
