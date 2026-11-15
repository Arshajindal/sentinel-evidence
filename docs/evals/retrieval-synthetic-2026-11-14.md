# Retrieval on full text, with the synthetic set (S2.6 follow-up)

**Date:** 2026-11-14 · **Decisions:** ADR-055 in `PROGRESS.md` · **MLflow:** experiment `retrieval-ablation`, 7 runs with `dataset = synthetic-rules` · **Rankings:** `data/evals/retrieval-ablation-synthetic-20261114T233915Z.json` (not in git; regenerate with `sentinel eval retrieval --set synthetic --corpus pmc-rct-2015-2024`)

## In short

- ADR-050 kept the reranker on SciFact's val alone: test showed no gain. It asked for a re-measure on full-text passages. Here it is.
- **The reranker helps on full text, and both splits agree:** +0.069 nDCG@10 over fusion on val, +0.067 on test.
- **Dot product beats cosine again** for dense search alone, on both splits.
- **BM25 alone is the best row:** 0.919 on val, against fusion plus rerank's 0.900. The set's gold passages are defined by repeating the claim's numbers, which is exactly what BM25 matches. ADR-051 predicted this. The set isn't used to choose components, and the pipeline doesn't change.
- **Equal-weight fusion is the weak stage** when one ranker is much worse. Weighting it is a Phase 3 candidate, to be measured on real citations.

## Setup

| | |
|---|---|
| Queries | The synthetic set's intact pairs (ADR-051, `corruption_type = none`): 390 in val and 370 in test, from 450 source papers. Train isn't used. Corrupted items are left out: their gold is the same passages under an altered claim. |
| Claim | A sentence from the source paper's abstract that pins its result to at least two numbers |
| Gold | The body's results passages that hold every one of those numbers: 1 to 3 per claim, about 1.2 on average |
| Pool | The source paper's own passages, abstract left out (the claim is copied from it). Pools average 26 passages (median 24, maximum 80). This is C09's case: searching inside the work a citation cites. |
| Stack | Exactly ADR-050's: BM25 (k1 1.5, b 0.75), MedCPT dense by dot product and by cosine, RRF with k = 60 at depth 50, and the MedCPT cross-encoder over the fused list |
| Measures | nDCG@10 (binary) and Recall@50 per split; paired bootstrap over claims, 10,000 resamples, seed 20260922 |

**One consequence of small pools.** Only 23 of the 760 pools are bigger than the depth of 50. For the other 97%, every stage's list holds the whole body, and the "fusion + rerank" row is the cross-encoder ranking every passage of the paper. Recall@50 is near 1.000 for every row, so it says nothing here.

## Results

| Row | val nDCG@10 | val Recall@50 | test nDCG@10 | test Recall@50 |
|---|---:|---:|---:|---:|
| BM25 | **0.919** | 1.000 | **0.916** | 1.000 |
| Dense, dot | 0.671 | 1.000 | 0.691 | 0.997 |
| Fusion, dot | 0.831 | 1.000 | 0.827 | 0.998 |
| Fusion + rerank, dot | 0.900 | 1.000 | 0.895 | 0.998 |
| Dense, cosine | 0.642 | 1.000 | 0.646 | 0.997 |
| Fusion, cosine | 0.807 | 1.000 | 0.809 | 0.998 |
| Fusion + rerank, cosine | 0.900 | 1.000 | 0.895 | 0.998 |

The two reranked rows have the same top 10 for all 760 claims. Their full lists differ for 20 claims, all in pools bigger than 50.

**Paired differences in nDCG@10** (first row minus second; 95% intervals; "better/worse" counts claims):

| Comparison | val | test |
|---|---|---|
| Rerank: fusion + rerank vs fusion (dot) | +0.069 [+0.044, +0.094]; 130 better, 50 worse | +0.067 [+0.044, +0.090]; 112 better, 43 worse |
| Rerank: fusion + rerank vs fusion (cosine) | +0.093 [+0.068, +0.118]; 132 better, 37 worse | +0.085 [+0.063, +0.108]; 122 better, 36 worse |
| Fusion + rerank vs BM25 (dot) | −0.020 [−0.040, +0.000]; −2.1% [−4.3%, +0.0%]; 42 better, 59 worse | −0.022 [−0.040, −0.004]; −2.4% [−4.3%, −0.5%]; 38 better, 58 worse |
| Fusion vs BM25 (dot) | −0.089 [−0.110, −0.068]; 30 better, 126 worse | −0.089 [−0.110, −0.070]; 22 better, 114 worse |
| Dense vs BM25 (dot) | −0.248 [−0.280, −0.216]; 29 better, 222 worse | −0.225 [−0.255, −0.196]; 14 better, 205 worse |
| Fusion vs dense (dot) | +0.159 [+0.139, +0.180]; 207 better, 15 worse | +0.136 [+0.118, +0.155]; 189 better, 8 worse |
| Dot vs cosine, dense | +0.030 [+0.007, +0.051]; 127 better, 93 worse | +0.045 [+0.022, +0.067]; 137 better, 71 worse |
| Dot vs cosine, fusion | +0.023 [+0.005, +0.042] | +0.018 [+0.001, +0.034] |
| Dot vs cosine, fusion + rerank | 0 on every claim | 0 on every claim |

## What the numbers say

**The reranker earns its place on full text.** On SciFact's abstracts, its val gain (+0.094) vanished on test (−0.006), where the fused order was already strong. Inside a paper's body, both splits show about +0.07, with intervals well clear of zero. Most of a body's passages are on the paper's topic, so it takes reading the claim against each passage to find the right one. Word overlap and a single vector do this less well.

**Dense search is weak here, and BM25 is strong, for one reason.** Every claim pins its result to numbers, and the gold passages are by definition the ones that repeat them. BM25 indexes those numbers as words, so a passage that repeats "23.4" and "61%" scores high. MedCPT's encoders turn the claim into one vector, in which numbers count for little. Dense search alone misses badly (0.671). Fusing it with BM25 at equal weight drags BM25 down from 0.919 to 0.831. The cross-encoder wins most of that back, but not all of it.

**Where the reranker loses to BM25** (117 claims across both splits), its top passage was gold in 16; there it lost further down the list. Of the other 101:
- 46 were discussion passages, 40 results, 11 methods, 4 elsewhere;
- 80 lacked at least one of the claim's numbers as written. The cross-encoder chose a passage on the claim's topic, not the one that reports its numbers.
- 21 held every number but weren't gold: 13 discussion passages restating the result, 5 methods, 2 results and 1 introduction. Gold covers results passages only, so some of these "losses" may be fine answers.

"Holds the number" is a substring test, so it's approximate: "28" also matches inside "1280".

**Why this doesn't change the pipeline.** The set was built so that the matching passage shares the claim's numbers (ADR-051's limits say it flatters keyword search). On SciFact, whose claims annotators wrote in their own words, the full pipeline beats BM25 by +0.132 on val. Real citing sentences restate what they cite in their own words more often than they copy its numbers. Choosing BM25 alone from this set would mean choosing on the set's construction. What does carry over is narrower: when a claim does give numbers, the passage that repeats them matters, and equal-weight fusion under-uses that. S3.4's hand-labelled citations can settle it.

**§10.3's sanity check** expects fusion plus rerank to beat BM25 by 15–30%. Here it is −2.1% on val and −2.4% on test, outside the band downwards. That comes from the set's construction, not from leakage: nothing is fitted, and the claim's own abstract passage is excluded from the pool.

## Cost

| | |
|---|---|
| Wall time | 97 min for 760 claims × 2 metrics. The dot pass took about 96 min; the cosine pass reused its cross-encoder scores. |
| Per claim | About 7.5 s, nearly all of it the cross-encoder: 19,756 pairs, about 26 per claim, at about 3.4 pairs a second |
| Searching | BM25 and exact dense search are each well under a second for a single paper |

The rate matches ADR-050's 3.9 pairs a second with the CPU free; other light work was running. For C09, the cost of reranking scales with the size of the cited work, not with depth: a typical body costs about 7 s on this CPU.

## Checks

- Every pool passage is in the BM25 index (11,848 passages).
- BM25 returned fewer passages than the pool for 90 claims. In every case the missing passages share no term with the claim, and no gold passage was among them.
- No dense, fused or reranked list came back short of its pool.

## Limits

- **The claims are the sources' own abstract sentences.** Retrieval here measures finding the body's restatement of an abstract, which is easier and more lexical than finding the evidence for a citing author's paraphrase.
- **Gold covers results passages only.** A discussion passage that restates every number isn't gold, so every row is slightly under-credited, the cross-encoder more than BM25.
- **With pools under the depth, the stack's later stages are tested less.** For 97% of claims, fusion and depth decide nothing: the reranker sees the whole body.
- **One source paper can give up to three claims,** so claims aren't fully independent. The bootstrap resamples claims, not papers, and its intervals may be a little narrow.
