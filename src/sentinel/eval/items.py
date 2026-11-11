"""What every evaluation set shares: the validated `eval_item` record and the
split rule (§10.2, ADR-044, ADR-049, ADR-051)."""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SPLITS = (("train", 0.6), ("val", 0.8), ("test", 1.0))  # cumulative shares


class EvalItemRecord(BaseModel):
    """One row of `eval_item` with its gold passages (ADR-044)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    set_name: Literal["synthetic", "external", "holdout"]
    split: Literal["train", "val", "test"]
    claim_text: str = Field(min_length=1)
    source_paper_id: int
    label: Literal["supported", "contradicted", "insufficient"]
    corruption_type: str | None = None
    license: str = Field(min_length=1)
    origin: dict
    gold_passage_ids: list[int]

    @model_validator(mode="after")
    def _gold(self) -> EvalItemRecord:
        if (self.label == "insufficient") != (not self.gold_passage_ids):
            raise ValueError("supported/contradicted items need gold passages; insufficient ones have none")
        if (self.set_name == "synthetic") != (self.corruption_type is not None):
            raise ValueError("synthetic items need a corruption type (\"none\" if intact); others have none")
        return self


def hash_split(key: str) -> str:
    """train, val or test (60/20/20) from a hash of `key`, which carries the
    seed and the unit that must not cross splits (a source paper or group)."""
    value = int(hashlib.sha256(key.encode()).hexdigest()[:12], 16) / 16 ** 12
    return next(name for name, upto in SPLITS if value < upto)
