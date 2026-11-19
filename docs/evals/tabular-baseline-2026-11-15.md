# The tabular baseline: built, checked with placebo labels, trained when S3.4's labels exist (S2.9)

**Date:** 2026-11-15 · **Decisions:** ADR-057 in `PROGRESS.md` · **Code:** `ml/features.py`, `ml/baseline.py`; `sentinel ml features`, `sentinel ml baseline` · **MLflow:** experiment `tabular-baseline`, run `170f7b328cf140c4b8060db546454303` (placebo) · **Files:** `data/ml/baseline-<kind>-<stamp>/` (not in git)

## In short

- **The baseline predicts C09's labels from metadata alone.** The labels are supported, contradicted and insufficient. The metadata is who cites what, where and when; the model never reads the text. It is the number the Phase 3 judges must beat (§10.1 item 8).
- **No citation has a real label until S3.4, so it isn't trained yet (your choice).** Everything else is built:
  - the feature table for all 54,824 citations;
  - gradient boosting, grouped cross-validation, Platt scaling and SHAP;
  - MLflow logging.
  It trains on S3.4's labels with one command.
- **The synthetic set can't stand in for real labels.** Its labels are random corruptions within each pair, and its "citing" side is the cited work itself. Features like shared authors or the year gap would only reveal source swaps.
- **A placebo run checks the plumbing end to end** with 200 real citations and random labels:
  - macro F1 0.274, against 0.194 for always the commonest label and 1/3 for guessing by the shares;
  - PR-AUC for "not supported" 0.675, against a prevalence of 0.680;
  - Platt scaling took ECE from 0.253 to 0.019.
  The model learns nothing, and calibration removes its overconfidence.
- **The bar for S3.4:** over 20 placebo runs of 200 labels, macro F1 ranged up to 0.369 (95th percentile 0.352). A real baseline needs to clear about 0.37 before it shows any signal at all.
- **On a planted signal in the tests,** the pipeline reaches macro F1 0.928 (the ceiling is about 0.93 with 10% of labels flipped), and SHAP ranks the two planted features first.

## The features (`ml/features.py`, version 1.0.0)

One row per citation that C09 will judge: a context from contexts 1.1.0, and one reference it cites. A context citing "[3-5]" gives three rows.

| Feature | From | Known |
|---|---|---:|
| `citing_year` | the citing paper | 100% |
| `citing_venue_papers` | corpus papers in the same venue | 100% |
| `citing_references` | the citing paper's reference count | 100% |
| `citing_sponsor` | its linked trial's lead sponsor class; "none" or "multiple" | 100% |
| `cited_year` | the matched work, else the year parsed from the reference | 98.5% |
| `year_gap` | citing year minus cited year | 98.5% |
| `cited_venue_citations` | corpus references to the same venue | 91.5% |
| `cited_resolution` | matched, unmatched or ambiguous | 100% |
| `cited_text` | full text, abstract only, or none (S2.2) | 89.9% |
| `cited_notice` | most serious notice: retraction, expression of concern, correction | 100% |
| `shared_authors` | authors the two papers share: surname and first initial | 89.0% |
| `section` | the section the citation sits in | 100% |
| `co_cited` | references the same citation cites | 100% |

"Known" is the share of the 54,824 citations with a value. An unknown number stays unknown (NaN), never zero, and each category has an explicit "unknown" column.

**On the corpus:**
- median year gap 6 years (5th–95th percentile 1–24);
- 21.8% of citations point at a work with full text held, and 62.3% at one with an abstract only;
- 47 citations point at a work with a notice: 42 retractions, 2 expressions of concern and 3 corrections;
- where both author lists are known (48,791 citations), 22.7% share at least one author with the cited work.

**Choices:**
- **Venues are counts, not categories.** With a few hundred labels, one category per journal would be noise.
- **Categories have fixed vocabularies,** so the encoding never depends on which citations are labelled.
- **Self-citation is a count of shared authors,** not a yes/no flag. One shared "Lee J" may be two different people; three shared names rarely are. At the level of paper pairs, the most often shared names are prolific trialists (Maksymowych, van der Heijde, Kappos), not surname collisions.
- **Nothing comes from the text, a model or a check's verdict.** C01's and C02's flags aren't used; only the raw notice type is.

## The procedure (`ml/baseline.py`, version 1.0.0)

1. **Outer folds:** 5, split by citing paper (`StratifiedGroupKFold`). No paper's citations are on both sides, and the code asserts it.
2. **Within each training fold:** LightGBM wrapped in Platt scaling (`CalibratedClassifierCV`, sigmoid). Each calibrator is fitted on 3 inner folds, also by citing paper, that its booster didn't train on.
3. **Every labelled citation** gets one out-of-fold prediction, uncalibrated and calibrated.
4. **Measures:**
   - macro F1, accuracy and log loss;
   - PR-AUC per label and for "not supported";
   - top-label ECE in 10 bins, before and after calibration;
   - chance references for each.
5. **A final model** is fitted on every label. SHAP (TreeExplainer) explains its booster. Each category's one-hot columns are summed per citation first, so a category counts as one feature.
6. **Files:** `metrics.json`, `shap.json`, `oof.csv`, `shap.png`, `reliability.png`. MLflow gets these plus the calibrated model, saved by skops with its five types listed as trusted. The model isn't registered.

**Settings:** fixed, not tuned. 200 trees, learning rate 0.05, 7 leaves, depth 3, at least 10 citations per leaf, 80% row and column sampling, L2 penalty 1. Tuning on a few hundred labels would fit noise.

## Placebo runs

**The logged run.** 200 citations from 183 papers, with labels drawn at random (seed 20260922): 64 supported, 82 contradicted, 54 insufficient. It took 29 seconds, building the feature table included.

| Measure | Out of fold | Chance |
|---|---:|---:|
| Macro F1 | 0.274 | 0.194 (always the commonest label), 0.333 (guessing by the shares) |
| Accuracy | 0.405 | 0.410 (the commonest label's share) |
| PR-AUC, not supported | 0.675 | 0.680 (prevalence) |
| Log loss | 1.091 | 1.084 (the shares as probabilities) |
| ECE, uncalibrated | 0.253 | |
| ECE, calibrated | 0.019 | |

The SHAP plot of this run ranks venue counts and reference counts first. That is what an overfitted model leans on when the labels are noise, so a SHAP plot means nothing until the model beats chance.

**The spread over 20 seeds** (scratch run, not logged; 200 labels each):

| Measure | Mean | 5th–95th percentile | Max |
|---|---:|---:|---:|
| Macro F1 | 0.278 | 0.217–0.352 | 0.369 |
| Accuracy | 0.343 | 0.286–0.377 | 0.405 |
| PR-AUC, not supported | 0.653 | 0.595–0.708 | 0.712 |
| ECE, uncalibrated | 0.271 | 0.223–0.315 | 0.361 |
| ECE, calibrated | 0.043 | 0.012–0.093 | 0.143 |

**What S3.4's run should be compared with:** macro F1 above about 0.37, or PR-AUC more than 0.05 above prevalence, before reading anything into the baseline. Below that, the baseline has found nothing, which is itself a result: metadata alone doesn't predict whether a citation is supported.

## Checked

- **Grouping:** the code asserts that no citing paper is on both sides of a fold, and a test checks it.
- **Labels:** a labelled citation with no feature row, a repeated citation or an unknown label is refused.
- **The feature table** was built against a fixture database. That covers a citation with two references, an unmatched reference falling back to its parsed year, a retraction outranking a correction, shared authors across Europe PMC's and Crossref's name shapes, and an old context version being ignored.
- **Planted signal:**
  - macro F1 0.928 and PR-AUC "not supported" 0.964;
  - SHAP ranks `section` and `cited_text` first;
  - Platt scaling moved ECE from 0.062 to 0.087, slightly worse.
- **The logged model** loads back from MLflow and predicts the same probabilities as a refit.

## Limits

- **Not trained on real labels.** Phase 2's "trained and calibrated, with SHAP saved" is met only on placebo labels. The real run moves to Phase 3 (ADR-057).
- **S3.4's 200 items will be chosen by active learning,** where judges are least sure or disagree. So they won't be a random sample of citations.
  - Every model is scored on the same items, so the comparison is fair.
  - But the baseline is trained on those labels by cross-validation, and the judges are not. That favours the baseline, which makes "the pipeline beats the baseline" a conservative claim.
- **Calibration on a few hundred items is noisy.** On placebo labels, calibrated ECE ranged up to 0.143. On the planted signal, Platt scaling made ECE slightly worse. S3.7 compares calibration methods properly.
- **Shared authors use surname and first initial,** so common names can collide. The count, rather than a flag, softens this; it doesn't remove it.
- **Venue counts come from this corpus,** so they describe this corpus's citing habits, not a venue's standing.
- **Features describe contexts 1.1.0.** Labels keyed to 1.0.0 contexts are refused.
