"""Eval harness tests: loading, metrics math, the CLI gate, and golden/fixture coverage."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from apps.api.schemas.classification import ClassificationResult
from apps.api.schemas.enums import IntentType, Language

from packages.eval.cases import CasePrediction, EvalCase, load_fixtures, load_golden
from packages.eval.cli import main
from packages.eval.metrics import evaluate_report
from packages.eval.predictors import RecordedPredictor, run_eval

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "golden/golden_set.jsonl"
FIXTURES = ROOT / "fixtures/recorded_haiku.jsonl"
BASELINE = ROOT / "baseline.json"


def _result(
    intent: IntentType, *, conf: float, language: Language = Language.EN
) -> ClassificationResult:
    return ClassificationResult.model_validate(
        {
            "intent": intent.value,
            "summary_one_line": "x",
            "language": language.value,
            "confidence_overall": conf,
            "confidence_intent": conf,
            "confidence_person": conf,
            "confidence_company": conf,
        }
    )


def _case(intent: IntentType, *, language: Language = Language.EN) -> EvalCase:
    return EvalCase(
        message="m",
        sender_phone=None,
        sender_name=None,
        expected=_result(intent, conf=0.9, language=language),
    )


def test_load_golden_validates_against_schema() -> None:
    cases = load_golden(GOLDEN)
    assert len(cases) == 8
    assert all(isinstance(c.expected, ClassificationResult) for c in cases)


def test_every_golden_message_has_a_recorded_fixture() -> None:
    fixtures = load_fixtures(FIXTURES)
    for case in load_golden(GOLDEN):
        assert case.message in fixtures, f"missing fixture for: {case.message!r}"


def test_overall_accuracy_counts_intent_hits() -> None:
    pairs = [
        CasePrediction(
            case=_case(IntentType.NEW_LEAD), predicted=_result(IntentType.NEW_LEAD, conf=0.9)
        ),
        CasePrediction(
            case=_case(IntentType.SUPPORT_ISSUE), predicted=_result(IntentType.NEW_LEAD, conf=0.8)
        ),
    ]
    report = evaluate_report(pairs, model="t")
    assert report.overall_intent_accuracy == 0.5
    assert report.confusion_matrix["support_issue"]["new_lead"] == 1


def test_none_prediction_is_unclear_and_counts_in_rate() -> None:
    pairs = [CasePrediction(case=_case(IntentType.NEW_LEAD), predicted=None)]
    report = evaluate_report(pairs, model="t")
    assert report.unclear_rate == 1.0
    assert report.overall_intent_accuracy == 0.0
    assert report.confusion_matrix["new_lead"]["unclear"] == 1


def test_per_language_accuracy_groups_by_expected_language() -> None:
    pairs = [
        CasePrediction(
            case=_case(IntentType.NEW_LEAD, language=Language.AR),
            predicted=_result(IntentType.NEW_LEAD, conf=0.9),
        ),
        CasePrediction(
            case=_case(IntentType.NEW_LEAD, language=Language.AR),
            predicted=_result(IntentType.SPAM_OR_NOISE, conf=0.9),
        ),
        CasePrediction(
            case=_case(IntentType.NEW_LEAD, language=Language.EN),
            predicted=_result(IntentType.NEW_LEAD, conf=0.9),
        ),
    ]
    report = evaluate_report(pairs, model="t")
    assert report.per_language_accuracy["ar"] == 0.5
    assert report.per_language_accuracy["en"] == 1.0


def test_brier_score_rewards_calibrated_confidence() -> None:
    confident_right = evaluate_report(
        [
            CasePrediction(
                case=_case(IntentType.NEW_LEAD), predicted=_result(IntentType.NEW_LEAD, conf=1.0)
            )
        ],
        model="t",
    ).brier_score
    confident_wrong = evaluate_report(
        [
            CasePrediction(
                case=_case(IntentType.NEW_LEAD),
                predicted=_result(IntentType.SPAM_OR_NOISE, conf=1.0),
            )
        ],
        model="t",
    ).brier_score
    assert confident_right == 0.0
    assert confident_wrong == pytest.approx(1.0)


def test_recorded_predictor_raises_on_unknown_message() -> None:
    predictor = RecordedPredictor({"known": None})
    with pytest.raises(KeyError):
        predictor.predict(_case(IntentType.NEW_LEAD))  # message "m" is not recorded


def test_baseline_matches_recorded_run() -> None:
    report = evaluate_report(
        run_eval(load_golden(GOLDEN), RecordedPredictor.from_path(FIXTURES)),
        model="baseline-check",
    )
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert report.overall_intent_accuracy == pytest.approx(baseline["overall_intent_accuracy"])


def test_cli_gate_passes_on_baseline(tmp_path: Path) -> None:
    code = main(
        [
            "--golden",
            str(GOLDEN),
            "--fixtures",
            str(FIXTURES),
            "--baseline",
            str(BASELINE),
            "--out-dir",
            str(tmp_path),
        ]
    )
    assert code == 0
    assert (tmp_path / "report.json").exists()
    assert (tmp_path / "report.html").exists()


def test_cli_gate_fails_on_accuracy_drop(tmp_path: Path) -> None:
    inflated = tmp_path / "baseline.json"
    inflated.write_text(
        json.dumps({"model": "m", "overall_intent_accuracy": 1.0}), encoding="utf-8"
    )
    code = main(["--golden", str(GOLDEN), "--fixtures", str(FIXTURES), "--baseline", str(inflated)])
    assert code == 1
