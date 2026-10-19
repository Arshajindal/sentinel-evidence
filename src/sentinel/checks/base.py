"""The check interface (sentinel-project.md §9.6).

Checks are pure: the same input and version always give the same findings.
No network or database calls inside `run()`. Bump `check_ver` on any change
to a check's logic.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar, Literal

from sentinel.checks.context import PaperContext, TrialContext
from sentinel.schemas import Finding


class Check(ABC):
    check_id: ClassVar[str]
    check_ver: ClassVar[str]
    subject: ClassVar[Literal["paper", "trial"]]

    @abstractmethod
    def run(self, ctx: PaperContext | TrialContext) -> list[Finding]: ...
