"""Prediction sources for the eval runner (addendum §13).

Two paths share one ``Predictor`` seam:

* ``RecordedPredictor`` replays committed fixtures — deterministic, no live key, the CI gate path
  (D13-a).
* a live path (wrapping ``apps.api.classifier.service.Classifier`` over the concrete
  Anthropic/OpenAI providers) lands with those providers and runs nightly to catch silent model
  drift. It plugs into the same ``run_eval`` because it satisfies ``Predictor``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from apps.api.schemas.classification import ClassificationResult

from packages.eval.cases import CasePrediction, EvalCase, load_fixtures


class Predictor(Protocol):
    """Returns the predicted label for one case, or ``None`` for an unclear/invalid run."""

    def predict(self, case: EvalCase) -> ClassificationResult | None: ...


class RecordedPredictor:
    """Replays recorded model outputs keyed by message text (deterministic CI mode, D13-a)."""

    def __init__(self, fixtures: dict[str, ClassificationResult | None]) -> None:
        self._fixtures = fixtures

    @classmethod
    def from_path(cls, path: Path) -> RecordedPredictor:
        return cls(load_fixtures(path))

    def predict(self, case: EvalCase) -> ClassificationResult | None:
        if case.message not in self._fixtures:
            raise KeyError(f"no recorded prediction for message: {case.message!r}")
        return self._fixtures[case.message]


def run_eval(cases: list[EvalCase], predictor: Predictor) -> list[CasePrediction]:
    """Predict every case, preserving order, into the pairs the metrics module consumes."""
    return [CasePrediction(case=case, predicted=predictor.predict(case)) for case in cases]
