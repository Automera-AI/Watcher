"""Loading the golden set and recorded prediction fixtures (addendum §13).

A golden line is the input message plus its known-correct ``expected`` label (validated against the
locked ``ClassificationResult`` schema by ``apps/api/tests/test_golden_set.py``). A fixture line is
a *recorded* model output for the same message — keyed by message text so the CI gate is
deterministic and needs no live key (D13-a). ``predicted: null`` records an unclear/invalid run.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from apps.api.schemas.classification import ClassificationResult
from apps.api.schemas.enums import Language


@dataclass(frozen=True, slots=True)
class EvalCase:
    """One golden example: the input message and its known-correct label."""

    message: str
    sender_phone: str | None
    sender_name: str | None
    expected: ClassificationResult

    @property
    def language(self) -> Language:
        """Expected content language — the grouping key for the per-language metric."""
        return self.expected.language


@dataclass(frozen=True, slots=True)
class CasePrediction:
    """A golden case paired with the model's predicted label (``None`` = unclear/invalid run)."""

    case: EvalCase
    predicted: ClassificationResult | None


def _nonblank_lines(path: Path) -> Iterator[str]:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            yield line


def load_golden(path: Path) -> list[EvalCase]:
    """Parse the golden JSONL into validated cases (raises if a label drifts from the schema)."""
    cases: list[EvalCase] = []
    for line in _nonblank_lines(path):
        record = json.loads(line)
        cases.append(
            EvalCase(
                message=record["message"],
                sender_phone=record.get("sender_phone"),
                sender_name=record.get("sender_name"),
                expected=ClassificationResult.model_validate(record["expected"]),
            )
        )
    if not cases:
        raise ValueError(f"golden set is empty: {path}")
    return cases


def load_fixtures(path: Path) -> dict[str, ClassificationResult | None]:
    """Map each recorded message → its predicted label (or ``None`` for an unclear/invalid run)."""
    fixtures: dict[str, ClassificationResult | None] = {}
    for line in _nonblank_lines(path):
        record = json.loads(line)
        predicted = record.get("predicted")
        fixtures[record["message"]] = (
            ClassificationResult.model_validate(predicted) if predicted is not None else None
        )
    return fixtures
