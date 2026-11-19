# Retrieval ablation on SciFact (S2.6)

**Date:** 2026-11-11 · **Decisions:** ADR-050 in `PROGRESS.md` · **MLflow:** experiment `retrieval-ablation`, one run per row · **Rankings:** `data/evals/retrieval-ablation-20261111T081044Z.json` (not in git; regenerate with `sentinel eval retrieval`)

## In short

- Fusion plus rerank is the best row on val (nDCG@10 0.895, against BM25's 0.763).
- On test, the reranker adds nothing measurable.
- Dense search stays on dot product.
- A first run had to be thrown away: pgvector's HNSW index had cut every cosine search short.
- The §10.3 sanity check passes on val (+17%) and falls short on test (+5%); neither suggests leakage.

## Setup

| | |
|---|---|
| Queries | The 263 SciFact claims with evidence in our val (128) and test (135) splits (ADR-049). Train claims aren't used: nothing here is trained. |
| Gold | The passages holding a claim's evidence sentences, pooled across the abstracts it cites. |
| Pool | All 5,992 passages of SciFact's 5,183 abstracts, for every query. 87% of abstracts are one passage, so searching within the cited abstract would be trivial. |
| BM25 | bm25s 0.3.16, "title. text", Snowball stems, k1 1.5, b 0.75 |
| Dense | MedCPT query encoder (`d83a36cc6b8e`) against article-encoder vectors (`d05a736da4bb`), exact search in pgvector, by dot product and by cosine |
| Fusion | Reciprocal rank fusion, k = 60, of BM25's 50 and dense search's 50; the top 50 are kept |
| Rerank | MedCPT cross-encoder (`71caf65d4927`) over the fused 50 |
| Measures | nDCG@10 with binary relevance (§14's selection measure) and Recall@50, averaged per split |
| Rule | Fixed before the run (§14): choose by nDCG@10 on val; test confirms |
| Intervals | Paired bootstrap over claims: 10,000 resamples, seed 20260922, 95% percentile intervals (`sentinel eval compare`) |

Every setting was fixed before the run (ADR-048). One search per claim and metric keeps each stage's list, so each search scores four rows.

## Results

| Row | val nDCG@10 | val Recall@50 | test nDCG@10 | test Recall@50 |
|---|---:|---:|---:|---:|
| BM25 | 0.763 | 0.971 | 0.822 | 0.970 |
| Dense, dot | 0.791 | 0.992 | **0.879** | 1.000 |
| Fusion, dot | 0.801 | 1.000 | 0.871 | 1.000 |
| Fusion + rerank, dot | **0.895** | 1.000 | 0.865 | 1.000 |
| Dense, cosine | 0.770 | 1.000 | 0.849 | 1.000 |
| Fusion, cosine | 0.788 | 1.000 | 0.866 | 0.996 |
| Fusion + rerank, cosine | **0.895** | 1.000 | 0.865 | 0.996 |

Rerank reorders the fused 50, so its Recall@50 is fusion's.

**Paired differences in nDCG@10** (first row minus second; 95% intervals; "better/worse" counts claims):

| Comparison | val | test |
|---|---|---|
| Fusion + rerank vs BM25 (dot) | +0.131 [+0.080, +0.182]; +17.2% [+9.9%, +25.4%]; 50 better, 12 worse | +0.042 [+0.010, +0.076]; +5.1% [+1.2%, +9.6%]; 25 better, 11 worse |
| Rerank: fusion + rerank vs fusion (dot) | +0.094 [+0.048, +0.140]; 45 better, 14 worse | −0.006 [−0.039, +0.026]; 18 better, 19 worse |
| Rerank: fusion + rerank vs fusion (cosine) | +0.106 [+0.062, +0.150]; 47 better, 11 worse | −0.002 [−0.032, +0.029]; 16 better, 20 worse |
| Fusion vs dense (dot) | +0.010 [−0.032, +0.050] | −0.008 [−0.039, +0.021] |
| Dense vs BM25 (dot) | +0.028 [−0.031, +0.088] | +0.056 [+0.018, +0.098] |
| Dot vs cosine, dense | +0.021 [−0.007, +0.051] | +0.030 [+0.005, +0.057] |
| Dot vs cosine, fusion | +0.013 [−0.004, +0.031] | +0.005 [−0.012, +0.022] |
| Dot vs cosine, fusion + rerank | 0 on every claim | 0 on every claim |

## What the numbers say

**The reranker helps on val and not on test.**
- On val, it lifts nDCG@10 by 0.094, and the interval is clear of zero.
- On test, it changes nothing measurable.
- Test's first stage is already strong: fusion scores 0.871 there, against 0.801 on val. After reranking, both splits land near 0.87–0.90. That fits a reranker that repairs a weak first-stage order and has little to add to a good one, but 128 and 135 claims can't settle why.
- By the rule fixed in advance, val decides, so the reranker stays.
- It costs about 13 s per claim on this CPU. Before Phase 3 pays that at scale, it should be re-measured on full-text passages (S2.8's set).

**§10.3's sanity check** expects fusion plus rerank to beat BM25 by roughly 15–30% on nDCG@10, and reads a much bigger gain as a sign of leakage.
- Val's +17.2% is inside that range; test's +5.1% is below it.
- Nothing suggests leakage:
  - no model or setting is fitted to SciFact;
  - BM25's term statistics come from passages, which carry no labels;
  - no abstract or claim crosses splits (ADR-049).
- On test, BM25 is already strong (0.822), which leaves less for the rest of the stack to add.

**Fusion adds nothing to dense search's nDCG@10** on either split.
- It does raise val's Recall@50, which is the pool the reranker sees, from 0.992 to 1.000.
- BM25 takes about 0.01 s, so fusion stays.

**Dot product beats cosine** on val for dense search (+0.021) and for fusion (+0.013).
- Both val intervals include zero.
- Test confirms the dense difference (+0.030, interval clear of zero).
- After reranking the two give the same nDCG@10 on every claim. Their fused pools differ (only 141 of 263 reranked top tens are identical), but the gold passages end at the same ranks.
- MedCPT is trained for dot product, and dot is the configured setting.

## The first run, and why it was discarded

The first full run printed a plausible table: cosine was a little behind dot. But every cosine dense list held fewer than 50 passages: a median of 24, at most 40, and one empty. The cause was in the query plan:
1. The cosine query was served by the HNSW index (`vector_cosine_ops`).
2. The index returns its 40 nearest vectors (`hnsw.ef_search`) before any filter runs.
3. With 23,552 corpus vectors in the same table, most of those 40 belonged to other papers, and the SciFact filter then removed them.

Dot product has no HNSW index, so its searches were exact. All 263 of its rankings in all four rows came out identical in the rerun.

| Dense, cosine | val nDCG@10 | val Recall@50 | test nDCG@10 | test Recall@50 |
|---|---:|---:|---:|---:|
| First run, cut short by HNSW | 0.744 | 0.948 | 0.825 | 0.959 |
| Rerun, exact | 0.770 | 1.000 | 0.849 | 1.000 |

The fix, a test that reproduces the failure, and a new warning for short lists are described in ADR-050 and the failure log. The first run's MLflow runs are tagged `status=superseded` with the reason.

## Cost

| | |
|---|---|
| Full ablation | 62.8 min for 263 claims × 2 metrics, on CPU with nothing else running |
| Cross-encoder | 3.9 pairs/s (11,250 pairs in 2,846 s); about 13 s per claim for 50 pairs |
| Pairs scored | 14,866; scores were cached, so the cosine pass rescored only passages missing from the dot pass's pools |
| Dense search | 0.1 s over the SciFact pool; 0.15–1.4 s (warm and cold cache) over all 29,544 vectors now stored, growing with the table |

## Limits

- **Abstracts only.** C09 also searches full-text passages, where choosing the right passage inside a long paper is the hard part. SciFact can't test that; S2.8's synthetic set and S3.4's hand-labelled set will.
- **The pool is all of SciFact.** C09 searches only the cited work's passages, which is usually under 30. That's a different ranking problem, and the reranker's value there is unmeasured.
- **Not comparable with published SciFact retrieval scores.** Only claims with evidence are queries, relevance is per passage, and the splits are ours.
- **Small splits.** With about 130 claims each, differences under about 0.03 nDCG@10 aren't resolved.
- **One model family.** The model registry's comparison models (SPECTER2, bge-reranker-v2-m3) haven't been tried.
