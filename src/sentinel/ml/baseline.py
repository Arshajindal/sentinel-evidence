"""The tabular baseline: gradient boosting on metadata alone (§10.1 item 8, S2.9, ADR-057).

It predicts C09's labels (supported, contradicted, insufficient) for a
citation from `features.py`'s metadata, never from the text. It is the number
the Phase 3 judges must beat on the same labels, and if it comes close, that
is a finding to report.

No citation has a real label until S3.4's hand-labelled holdout, so the
model is built and checked now and trained on those labels then. Each
labelled set is scored by cross-validation:
- **Outer folds** (OUTER_FOLDS) split by citing paper, never by row, so a
  paper's citations are never on both sides.
- **Inside each training fold,** the model is wrapped in Platt scaling
  (`CalibratedClassifierCV`, sigmoid). Each calibrator is fitted on inner
  folds (INNER_FOLDS, also by citing paper) that its booster didn't train on.
- **Every labelled citation** gets one out-of-fold prediction, uncalibrated
  and calibrated.
- **Measures:** macro F1, accuracy, log loss, PR-AUC per class and for "not
  supported", and the expected calibration error (ECE) before and after.

A final model is then fitted on every label, and SHAP explains it.

The settings below are fixed, not tuned. With a few hundred labels, tuning
would fit noise, so the trees are small and heavily regularised. A
placebo run (random labels on real citations) checks the plumbing end to
end: it should score at chance.
"""

from __future__ import annotations

import csv
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict

from sentinel.ml.features import FEATURES_VER, CitationFeatures, feature_group, feature_names, to_matrix

# Bump on any change to the settings or the procedure below.
BASELINE_VER = "1.0.0"
LABELS = ("supported", "contradicted", "insufficient")
LGBM_PARAMS = {
    "n_estimators": 200, "learning_rate": 0.05, "num_leaves": 7, "max_depth": 3,
    "min_child_samples": 10, "subsample": 0.8, "subsample_freq": 1, "colsample_bytree": 0.8,
    "reg_lambda": 1.0, "n_jobs": 1, "verbose": -1,
}
OUTER_FOLDS = 5
INNER_FOLDS = 3
ECE_BINS = 10


class LabelRow(BaseModel):
    """One labelled citation: a context and one reference it cites."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    citation_context_id: int
    reference_id: int
    label: Literal[LABELS]  # type: ignore[valid-type]


def load_labels(path: Path) -> list[LabelRow]:
    """A CSV with columns citation_context_id, reference_id, label. Every row
    is validated; a repeated citation is refused."""
    with path.open(newline="", encoding="utf-8") as f:
        rows = [LabelRow.model_validate(r) for r in csv.DictReader(f)]
    keys = [(r.citation_context_id, r.reference_id) for r in rows]
    if len(set(keys)) != len(keys):
        raise ValueError("a citation is labelled more than once")
    return rows


def placebo_labels(features: Sequence[CitationFeatures], n: int, seed: int) -> list[LabelRow]:
    """N citations drawn at random, each with a label drawn at random. Nothing
    can predict these, so a model that beats chance on them has a leak."""
    if not 0 < n <= len(features):
        raise ValueError(f"placebo size must be between 1 and {len(features)}")
    rng = np.random.default_rng(seed)
    picked = rng.choice(len(features), size=n, replace=False)
    labels = rng.integers(0, len(LABELS), size=n)
    return [LabelRow(citation_context_id=features[i].citation_context_id,
                     reference_id=features[i].reference_id, label=LABELS[k])
            for i, k in zip(sorted(picked), labels, strict=True)]


@dataclass
class Dataset:
    x: np.ndarray
    y: np.ndarray  # index into LABELS
    groups: np.ndarray  # citing paper ID
    keys: list[tuple[int, int]]  # (citation_context_id, reference_id)


def assemble(features: Sequence[CitationFeatures], labels: Sequence[LabelRow]) -> Dataset:
    """The labelled citations' feature rows. A label for a citation with no
    feature row (another corpus, an old context version) is an error."""
    by_key = {(f.citation_context_id, f.reference_id): f for f in features}
    missing = [(r.citation_context_id, r.reference_id) for r in labels
               if (r.citation_context_id, r.reference_id) not in by_key]
    if missing:
        raise ValueError(f"{len(missing)} labelled citations have no feature row, e.g. {missing[:3]}")
    rows = [by_key[(r.citation_context_id, r.reference_id)] for r in labels]
    return Dataset(x=to_matrix(rows), y=np.array([LABELS.index(r.label) for r in labels]),
                   groups=np.array([f.citing_paper_id for f in rows]),
                   keys=[(r.citation_context_id, r.reference_id) for r in labels])


# --- models -------------------------------------------------------------------------

def _booster(seed: int):
    from lightgbm import LGBMClassifier

    return LGBMClassifier(**LGBM_PARAMS, random_state=seed)


def _folds(n_splits: int, y: np.ndarray, groups: np.ndarray, seed: int) -> list[tuple[np.ndarray, np.ndarray]]:
    """Folds by group, stratified by label as far as groups allow. Fewer
    groups than splits gives fewer folds."""
    from sklearn.model_selection import StratifiedGroupKFold

    k = min(n_splits, len(set(groups.tolist())))
    if k < 2:
        raise ValueError("cross-validation needs labels from at least two citing papers")
    return list(StratifiedGroupKFold(n_splits=k, shuffle=True, random_state=seed)
                .split(np.zeros(len(y)), y, groups))


def _calibrated(seed: int, x: np.ndarray, y: np.ndarray, groups: np.ndarray):
    from sklearn.calibration import CalibratedClassifierCV

    model = CalibratedClassifierCV(_booster(seed), method="sigmoid",
                                   cv=_folds(INNER_FOLDS, y, groups, seed))
    return model.fit(x, y)


def _proba(model, x: np.ndarray) -> np.ndarray:
    """Probabilities for all three labels, in LABELS order, even if a fold
    trained without one of them (that label gets 0)."""
    out = np.zeros((len(x), len(LABELS)))
    out[:, list(model.classes_)] = model.predict_proba(x)
    return out


@dataclass
class CrossValidation:
    raw: np.ndarray  # out-of-fold probabilities, uncalibrated
    calibrated: np.ndarray  # out-of-fold probabilities after Platt scaling
    folds: int
    metrics: dict[str, float] = field(default_factory=dict)


def cross_validate(data: Dataset, seed: int) -> CrossValidation:
    raw = np.zeros((len(data.y), len(LABELS)))
    cal = np.zeros_like(raw)
    folds = _folds(OUTER_FOLDS, data.y, data.groups, seed)
    for train, test in folds:
        if set(data.groups[train]) & set(data.groups[test]):
            raise AssertionError("a citing paper is on both sides of a fold")
        raw[test] = _proba(_booster(seed).fit(data.x[train], data.y[train]), data.x[test])
        cal[test] = _proba(_calibrated(seed, data.x[train], data.y[train], data.groups[train]), data.x[test])
    result = CrossValidation(raw=raw, calibrated=cal, folds=len(folds))
    result.metrics = {**{f"raw_{k}": v for k, v in evaluate(data.y, raw).items()},
                      **evaluate(data.y, cal)}
    return result


def fit_final(data: Dataset, seed: int):
    """The booster on every label (what SHAP explains) and its calibrated
    form (what would make predictions)."""
    return _booster(seed).fit(data.x, data.y), _calibrated(seed, data.x, data.y, data.groups)


# --- measures -------------------------------------------------------------------------

def ece(probs: np.ndarray, y: np.ndarray, bins: int = ECE_BINS) -> float:
    """Expected calibration error of the top label: in equal-width bins of
    confidence, the gap between confidence and accuracy, weighted by size."""
    conf = probs.max(axis=1)
    right = probs.argmax(axis=1) == y
    edges = np.linspace(0, 1, bins + 1)
    total = 0.0
    for lo, hi in zip(edges[:-1], edges[1:], strict=True):
        inside = (conf > lo) & (conf <= hi) if lo > 0 else (conf >= lo) & (conf <= hi)
        if inside.any():
            total += inside.mean() * abs(conf[inside].mean() - right[inside].mean())
    return float(total)


def evaluate(y: np.ndarray, probs: np.ndarray) -> dict[str, float]:
    from sklearn.metrics import accuracy_score, average_precision_score, f1_score, log_loss

    pred = probs.argmax(axis=1)
    out = {"macro_f1": float(f1_score(y, pred, average="macro", labels=list(range(len(LABELS))),
                                      zero_division=0)),
           "accuracy": float(accuracy_score(y, pred)),
           "log_loss": float(log_loss(y, np.clip(probs, 1e-9, 1), labels=list(range(len(LABELS))))),
           "ece": ece(probs, y)}
    for k, name in enumerate(LABELS):
        positive = y == k
        out[f"pr_auc_{name}"] = (float(average_precision_score(positive, probs[:, k]))
                                 if positive.any() else math.nan)
    not_supported = y != LABELS.index("supported")
    out["pr_auc_not_supported"] = (float(average_precision_score(not_supported, 1 - probs[:, 0]))
                                   if not_supported.any() else math.nan)
    return out


def chance(y: np.ndarray) -> dict[str, float]:
    """What a model that knows only the label shares would score. Macro F1
    for always predicting the commonest label, and for guessing at random
    in proportion to the shares (each label's F1 is then its share, so the
    mean is 1/3 whatever the shares). Each PR-AUC's chance is its prevalence."""
    shares = np.bincount(y, minlength=len(LABELS)) / len(y)
    top = shares.argmax()
    f1_top = 2 * shares[top] / (1 + shares[top])
    out = {"chance_macro_f1": float(f1_top / len(LABELS)),
           "chance_macro_f1_guessing": float(shares[shares > 0].sum() / len(LABELS))}
    for k, name in enumerate(LABELS):
        out[f"chance_pr_auc_{name}"] = float(shares[k])
    out["chance_pr_auc_not_supported"] = float(1 - shares[0])
    return out


# --- explanations ---------------------------------------------------------------------

def shap_summary(booster, x: np.ndarray) -> dict:
    """Mean |SHAP| per feature and label, from the final booster. One-hot
    columns are summed per row first, so a category counts as one feature."""
    import shap

    values = shap.TreeExplainer(booster).shap_values(x)
    values = np.stack(values, axis=-1) if isinstance(values, list) else np.asarray(values)
    if values.ndim == 2:  # one output: a booster that saw a single label
        values = values[..., None]
    names = feature_names()
    groups: dict[str, list[int]] = {}
    for i, name in enumerate(names):
        groups.setdefault(feature_group(name), []).append(i)
    classes = [LABELS[c] for c in booster.classes_]
    by_feature = {}
    for g, cols in groups.items():
        summed = values[:, cols, :].sum(axis=1)  # rows x labels
        by_feature[g] = {classes[k]: float(np.abs(summed[:, k]).mean()) for k in range(summed.shape[1])}
    order = sorted(by_feature, key=lambda g: -sum(by_feature[g].values()))
    return {"order": order, "mean_abs_shap": {g: by_feature[g] for g in order}}


# --- reports ----------------------------------------------------------------------------

# The reference palette's light surface, ink and first two categorical slots.
_SURFACE, _INK, _INK_2, _GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
_SERIES = ("#2a78d6", "#eb6834")


def _axes(ax) -> None:
    ax.set_facecolor(_SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(_GRID)
    ax.tick_params(colors=_INK_2, labelsize=9)
    ax.grid(color=_GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def plot_shap(summary: dict, path: Path, title: str) -> None:
    """Horizontal bars of mean |SHAP|, summed over labels, largest at top."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    order = summary["order"]
    totals = [sum(summary["mean_abs_shap"][g].values()) for g in order]
    fig, ax = plt.subplots(figsize=(7, 0.42 * len(order) + 1.4), facecolor=_SURFACE)
    _axes(ax)
    ax.grid(axis="y", visible=False)
    ax.barh(range(len(order)), totals, height=0.6, color=_SERIES[0])
    ax.set_yticks(range(len(order)), order, color=_INK)
    ax.invert_yaxis()
    ax.set_xlabel("mean |SHAP| in log-odds, summed over the three labels", color=_INK_2, fontsize=9)
    ax.set_title(title, loc="left", color=_INK, fontsize=11)
    fig.tight_layout()
    fig.savefig(path, dpi=150, facecolor=_SURFACE)
    plt.close(fig)


def reliability(probs: np.ndarray, y: np.ndarray, bins: int = ECE_BINS) -> list[dict]:
    """Per confidence bin: mean confidence, accuracy and count (top label)."""
    conf = probs.max(axis=1)
    right = probs.argmax(axis=1) == y
    edges = np.linspace(0, 1, bins + 1)
    out = []
    for lo, hi in zip(edges[:-1], edges[1:], strict=True):
        inside = (conf > lo) & (conf <= hi) if lo > 0 else (conf >= lo) & (conf <= hi)
        if inside.any():
            out.append({"confidence": float(conf[inside].mean()), "accuracy": float(right[inside].mean()),
                        "n": int(inside.sum())})
    return out


def plot_reliability(cv: CrossValidation, y: np.ndarray, path: Path, title: str) -> None:
    """Accuracy against confidence for the out-of-fold predictions, before
    and after calibration, with the diagonal a perfectly calibrated model
    would follow."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(5.2, 5.3), facecolor=_SURFACE)
    _axes(ax)
    ax.plot([0, 1], [0, 1], color=_INK_2, linewidth=1, linestyle=(0, (4, 3)))
    for (name, probs), color in zip((("uncalibrated", cv.raw), ("calibrated", cv.calibrated)), _SERIES,
                                    strict=True):
        pts = reliability(probs, y)
        xs, ys = [p["confidence"] for p in pts], [p["accuracy"] for p in pts]
        ax.plot(xs, ys, color=color, linewidth=2, label=f"{name}, ECE {ece(probs, y):.3f}")
        # A bin's marker area follows its share of the citations, so a bin of
        # one doesn't look as weighty as a bin of a hundred.
        ax.scatter(xs, ys, s=[40 + 360 * p["n"] / len(y) for p in pts], color=color,
                   edgecolors=_SURFACE, linewidths=1.5, zorder=3)
        if pts:
            ax.annotate(name, (xs[-1], ys[-1]), xytext=(8, 6), textcoords="offset points",
                        color=_INK_2, fontsize=9, va="bottom")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1.04)
    ax.set_xlabel("confidence in the top label (marker area: citations in the bin)", color=_INK_2,
                  fontsize=9)
    ax.set_ylabel("share right", color=_INK_2, fontsize=9)
    ax.set_title(title, loc="left", color=_INK, fontsize=11)
    ax.legend(frameon=False, fontsize=9, labelcolor=_INK, loc="upper left")
    fig.tight_layout()
    fig.savefig(path, dpi=150, facecolor=_SURFACE)
    plt.close(fig)


@dataclass
class BaselineRun:
    data: Dataset
    cv: CrossValidation
    shap: dict
    booster: object
    calibrated: object
    params: dict


def run(features: Sequence[CitationFeatures], labels: Sequence[LabelRow], *, seed: int,
        label_source: str) -> BaselineRun:
    """Cross-validate, then fit the final model and explain it."""
    data = assemble(features, labels)
    cv = cross_validate(data, seed)
    cv.metrics.update(chance(data.y))
    booster, calibrated = fit_final(data, seed)
    params = {"baseline_ver": BASELINE_VER, "features_ver": FEATURES_VER, "labels": label_source,
              "n_labelled": len(labels), "citing_papers": len(set(data.groups.tolist())),
              "outer_folds": cv.folds, "inner_folds": INNER_FOLDS, "calibration": "sigmoid (Platt)",
              "seed": seed, **{f"lgbm_{k}": v for k, v in LGBM_PARAMS.items()},
              **{f"n_{name}": int((data.y == k).sum()) for k, name in enumerate(LABELS)}}
    return BaselineRun(data=data, cv=cv, shap=shap_summary(booster, data.x), booster=booster,
                       calibrated=calibrated, params=params)


def save(result: BaselineRun, out_dir: Path, *, title: str) -> list[Path]:
    """The run's files: metrics and settings, SHAP values, out-of-fold
    predictions, and the two plots."""
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {"metrics.json": out_dir / "metrics.json", "shap.json": out_dir / "shap.json",
             "oof.csv": out_dir / "oof.csv", "shap.png": out_dir / "shap.png",
             "reliability.png": out_dir / "reliability.png"}
    files["metrics.json"].write_text(json.dumps({"params": result.params, "metrics": result.cv.metrics,
                                                 "reliability": {
                                                     "uncalibrated": reliability(result.cv.raw, result.data.y),
                                                     "calibrated": reliability(result.cv.calibrated,
                                                                               result.data.y)}},
                                                indent=2), encoding="utf-8")
    files["shap.json"].write_text(json.dumps(result.shap, indent=2), encoding="utf-8")
    with files["oof.csv"].open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["citation_context_id", "reference_id", "citing_paper_id", "label",
                    *[f"raw_{n}" for n in LABELS], *[f"calibrated_{n}" for n in LABELS]])
        for i, (ctx, ref) in enumerate(result.data.keys):
            w.writerow([ctx, ref, int(result.data.groups[i]), LABELS[result.data.y[i]],
                        *[f"{p:.6f}" for p in result.cv.raw[i]], *[f"{p:.6f}" for p in result.cv.calibrated[i]]])
    plot_shap(result.shap, files["shap.png"], f"{title}: what the baseline uses")
    plot_reliability(result.cv, result.data.y, files["reliability.png"], f"{title}: reliability")
    return list(files.values())


def log_to_mlflow(result: BaselineRun, files: list[Path], *, tracking_uri: str, run_name: str,
                  tags: dict[str, str], experiment: str = "tabular-baseline") -> str:
    """One MLflow run: settings as params, measures as metrics, the files
    and the calibrated model as artifacts. Nothing is registered."""
    import sys

    import mlflow
    import mlflow.sklearn

    for stream in (sys.stdout, sys.stderr):  # cp1252 consoles (see eval.retrieval)
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment(experiment)
    with mlflow.start_run(run_name=run_name) as active:
        mlflow.set_tags(tags)
        mlflow.log_params(result.params)
        mlflow.log_metrics({k: v for k, v in result.cv.metrics.items() if not math.isnan(v)})
        for path in files:
            mlflow.log_artifact(str(path))
        mlflow.sklearn.log_model(result.calibrated, name="model", skops_trusted_types=SKOPS_TRUSTED)
        return active.info.run_id


# MLflow saves scikit-learn models with skops, which loads only the types it
# is told to trust. These are the calibrated baseline's own parts.
SKOPS_TRUSTED = ["collections.OrderedDict", "lightgbm.basic.Booster", "lightgbm.sklearn.LGBMClassifier",
                 "sklearn.calibration._CalibratedClassifier", "sklearn.calibration._SigmoidCalibration"]
