"""The five Phase-1 eval metrics over predicted-vs-expected labels (addendum §12/§13).

1. overall intent accuracy      — the headline number the CI gate guards (>2pp drop fails, §12);
2. per-field accuracy           — intent / language / record-type (categorical) + person/company
                                  presence, so a regression localizes to a field;
3. confidence calibration       — Brier score + reliability buckets (is a 0.9 actually right ~90%?);
4. per-language accuracy        — Arabic must not silently lag English (AGENTS.md, §15);
5. intent confusion matrix      — which intents get mistaken for which.

A ``None`` prediction (schema-invalid twice → unclear, §8) counts as predicted intent ``unclear``
with confidence 0.0.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from apps.api.schemas.classification import ClassificationResult
from apps.api.schemas.enums import IntentType

from packages.eval.cases import CasePrediction

# Reliability buckets keyed to the locked confidence bands (DECISIONS.md): LOW / MEDIUM / HIGH plus
# a very-high tail so an over-confident model stands out.
_CALIBRATION_EDGES: tuple[float, ...] = (0.0, 0.5, 0.85, 0.95, 1.0001)

_CATEGORICAL_FIELDS: tuple[str, ...] = ("intent", "language", "suggested_record_type")
_PRESENCE_FIELDS: tuple[str, ...] = ("person_name", "company_name")


def _predicted_intent(predicted: ClassificationResult | None) -> IntentType:
    return predicted.intent if predicted is not None else IntentType.UNCLEAR


def _predicted_confidence(predicted: ClassificationResult | None) -> float:
    return predicted.confidence_overall if predicted is not None else 0.0


def _is_intent_correct(pair: CasePrediction) -> bool:
    return _predicted_intent(pair.predicted) == pair.case.expected.intent


@dataclass(frozen=True, slots=True)
class CalibrationBucket:
    """One reliability bucket: how the model's stated confidence tracks its actual hit rate."""

    lower: float
    upper: float
    count: int
    mean_confidence: float
    accuracy: float


@dataclass(frozen=True, slots=True)
class EvalReport:
    """The computed metrics for one eval run, ready to serialize to JSON/HTML."""

    model: str
    total: int
    overall_intent_accuracy: float
    unclear_rate: float
    per_field_accuracy: dict[str, float]
    per_language_accuracy: dict[str, float]
    confusion_matrix: dict[str, dict[str, int]]
    brier_score: float
    calibration: list[CalibrationBucket] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "model": self.model,
            "total": self.total,
            "overall_intent_accuracy": round(self.overall_intent_accuracy, 4),
            "unclear_rate": round(self.unclear_rate, 4),
            "per_field_accuracy": {k: round(v, 4) for k, v in self.per_field_accuracy.items()},
            "per_language_accuracy": {
                k: round(v, 4) for k, v in self.per_language_accuracy.items()
            },
            "confusion_matrix": self.confusion_matrix,
            "brier_score": round(self.brier_score, 4),
            "calibration": [
                {
                    "range": f"[{b.lower:.2f}, {b.upper:.2f})",
                    "count": b.count,
                    "mean_confidence": round(b.mean_confidence, 4),
                    "accuracy": round(b.accuracy, 4),
                }
                for b in self.calibration
            ],
        }


def _field_value(result: ClassificationResult | None, name: str) -> object:
    return getattr(result, name) if result is not None else None


def _per_field_accuracy(pairs: list[CasePrediction]) -> dict[str, float]:
    scores: dict[str, float] = {}
    for name in _CATEGORICAL_FIELDS:
        hits = sum(
            _field_value(p.predicted, name) == _field_value(p.case.expected, name) for p in pairs
        )
        scores[name] = hits / len(pairs)
    for name in _PRESENCE_FIELDS:
        hits = sum(
            bool(_field_value(p.predicted, name)) == bool(_field_value(p.case.expected, name))
            for p in pairs
        )
        scores[f"{name}_present"] = hits / len(pairs)
    return scores


def _per_language_accuracy(pairs: list[CasePrediction]) -> dict[str, float]:
    by_lang: dict[str, list[CasePrediction]] = {}
    for pair in pairs:
        by_lang.setdefault(pair.case.language.value, []).append(pair)
    return {
        lang: sum(_is_intent_correct(p) for p in group) / len(group)
        for lang, group in sorted(by_lang.items())
    }


def _confusion_matrix(pairs: list[CasePrediction]) -> dict[str, dict[str, int]]:
    intents = [i.value for i in IntentType]
    matrix: dict[str, dict[str, int]] = {exp: {pred: 0 for pred in intents} for exp in intents}
    for pair in pairs:
        matrix[pair.case.expected.intent.value][_predicted_intent(pair.predicted).value] += 1
    return matrix


def _brier_score(pairs: list[CasePrediction]) -> float:
    """Mean squared error between stated confidence and correctness (0 = perfect, lower better)."""
    return sum(
        (_predicted_confidence(p.predicted) - float(_is_intent_correct(p))) ** 2 for p in pairs
    ) / len(pairs)


def _calibration(pairs: list[CasePrediction]) -> list[CalibrationBucket]:
    buckets: list[CalibrationBucket] = []
    for lower, upper in zip(_CALIBRATION_EDGES[:-1], _CALIBRATION_EDGES[1:], strict=True):
        in_bucket = [p for p in pairs if lower <= _predicted_confidence(p.predicted) < upper]
        if not in_bucket:
            continue
        mean_conf = sum(_predicted_confidence(p.predicted) for p in in_bucket) / len(in_bucket)
        accuracy = sum(_is_intent_correct(p) for p in in_bucket) / len(in_bucket)
        buckets.append(
            CalibrationBucket(
                lower=lower,
                upper=min(upper, 1.0),
                count=len(in_bucket),
                mean_confidence=mean_conf,
                accuracy=accuracy,
            )
        )
    return buckets


def evaluate_report(pairs: list[CasePrediction], *, model: str) -> EvalReport:
    """Compute all five metrics for a completed run. ``pairs`` must be non-empty."""
    if not pairs:
        raise ValueError("cannot evaluate an empty run")
    total = len(pairs)
    return EvalReport(
        model=model,
        total=total,
        overall_intent_accuracy=sum(_is_intent_correct(p) for p in pairs) / total,
        unclear_rate=sum(p.predicted is None for p in pairs) / total,
        per_field_accuracy=_per_field_accuracy(pairs),
        per_language_accuracy=_per_language_accuracy(pairs),
        confusion_matrix=_confusion_matrix(pairs),
        brier_score=_brier_score(pairs),
        calibration=_calibration(pairs),
    )
