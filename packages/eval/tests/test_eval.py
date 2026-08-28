"""Eval harness tests: loading, metrics math, the CLI gate, and golden/fixture coverage."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from apps.api.classifier.prompt import PROMPT_VERSION, build_system_prompt, prompt_fingerprint
from apps.api.schemas.classification import ClassificationResult
from apps.api.schemas.enums import IntentType, Language

from packages.eval.cases import CasePrediction, EvalCase, load_fixtures, load_golden
from packages.eval.cli import main
from packages.eval.metrics import evaluate_report
from packages.eval.predictors import RecordedPredictor, run_eval
from packages.intents.schema import shipped_vocabularies

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "golden/golden_set.jsonl"
FIXTURES = ROOT / "fixtures/recorded_haiku.jsonl"
BASELINE = ROOT / "baseline.json"
CLINIC_GOLDEN = ROOT / "golden/clinics_golden_set.jsonl"


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
    assert len(cases) == 56  # 50 + 6 Franco-Arabic cases added in 2.7's re-record
    assert all(isinstance(c.expected, ClassificationResult) for c in cases)


def test_clinic_golden_set_covers_the_demo_prompt_boundaries() -> None:
    cases = load_golden(CLINIC_GOLDEN)
    intents = {case.expected.intent.value for case in cases}
    languages = {case.expected.language.value for case in cases}

    assert len(cases) >= 16
    assert {
        "greeting",
        "thanks_closing",
        "availability_check",
        "price_enquiry",
        "service_question",
        "clinical_question",
        "clinical_urgent",
        "directions",
        "package_terms_question",
    } <= intents
    assert {"en", "ar", "mixed"} <= languages


def test_franco_arabic_with_one_borrowed_word_is_labelled_arabic() -> None:
    case = next(
        case for case in load_golden(CLINIC_GOLDEN) if case.message == "3ayza a3raf el se3r please"
    )

    assert case.expected.language is Language.AR


def test_every_golden_message_has_a_recorded_fixture() -> None:
    fixtures = load_fixtures(FIXTURES)
    for case in load_golden(GOLDEN):
        assert case.message in fixtures, f"missing fixture for: {case.message!r}"


def test_overall_accuracy_counts_intent_hits() -> None:
    pairs = [
        CasePrediction(
            case=_case(IntentType.AVAILABILITY_CHECK),
            predicted=_result(IntentType.AVAILABILITY_CHECK, conf=0.9),
        ),
        CasePrediction(
            case=_case(IntentType.MAINTENANCE_ISSUE),
            predicted=_result(IntentType.AVAILABILITY_CHECK, conf=0.8),
        ),
    ]
    report = evaluate_report(pairs, model="t")
    assert report.overall_intent_accuracy == 0.5
    assert report.confusion_matrix["maintenance_issue"]["availability_check"] == 1


def test_none_prediction_is_unclear_and_counts_in_rate() -> None:
    pairs = [CasePrediction(case=_case(IntentType.AVAILABILITY_CHECK), predicted=None)]
    report = evaluate_report(pairs, model="t")
    assert report.unclear_rate == 1.0
    assert report.overall_intent_accuracy == 0.0
    assert report.confusion_matrix["availability_check"]["unclear"] == 1


def test_per_language_accuracy_groups_by_expected_language() -> None:
    pairs = [
        CasePrediction(
            case=_case(IntentType.AVAILABILITY_CHECK, language=Language.AR),
            predicted=_result(IntentType.AVAILABILITY_CHECK, conf=0.9),
        ),
        CasePrediction(
            case=_case(IntentType.AVAILABILITY_CHECK, language=Language.AR),
            predicted=_result(IntentType.SPAM, conf=0.9),
        ),
        CasePrediction(
            case=_case(IntentType.AVAILABILITY_CHECK, language=Language.EN),
            predicted=_result(IntentType.AVAILABILITY_CHECK, conf=0.9),
        ),
    ]
    report = evaluate_report(pairs, model="t")
    assert report.per_language_accuracy["ar"] == 0.5
    assert report.per_language_accuracy["en"] == 1.0


def test_brier_score_rewards_calibrated_confidence() -> None:
    confident_right = evaluate_report(
        [
            CasePrediction(
                case=_case(IntentType.AVAILABILITY_CHECK),
                predicted=_result(IntentType.AVAILABILITY_CHECK, conf=1.0),
            )
        ],
        model="t",
    ).brier_score
    confident_wrong = evaluate_report(
        [
            CasePrediction(
                case=_case(IntentType.AVAILABILITY_CHECK),
                predicted=_result(IntentType.SPAM, conf=1.0),
            )
        ],
        model="t",
    ).brier_score
    assert confident_right == 0.0
    assert confident_wrong == pytest.approx(1.0)


def test_recorded_predictor_raises_on_unknown_message() -> None:
    predictor = RecordedPredictor({"known": None})
    with pytest.raises(KeyError):
        predictor.predict(_case(IntentType.AVAILABILITY_CHECK))  # message "m" is not recorded


def test_baseline_matches_recorded_run() -> None:
    report = evaluate_report(
        run_eval(load_golden(GOLDEN), RecordedPredictor.from_path(FIXTURES)),
        model="baseline-check",
    )
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    # baseline.json states the human-facing 2dp accuracy (55/56 is not a clean decimal, unlike the
    # 50-case set's 0.88). Rounding the recorded run to that precision still fails loudly on a
    # fabricated or stale baseline — the guard this test exists to be — while letting the file carry
    # the number a person reads. The gate's 2pp tolerance, not this test, is the real margin.
    assert round(report.overall_intent_accuracy, 2) == pytest.approx(
        baseline["overall_intent_accuracy"]
    )


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
    # A perfect baseline against a real run that misses at least one case is a drop; `--max-drop 0`
    # asserts the gate rejects any drop at all. Pinned to 0 rather than the default 2pp because the
    # v3 model now scores ~98%, so the gap from a 1.0 baseline is under 2pp — the old default would
    # let this pass and stop testing the fail path.
    code = main(
        [
            "--golden",
            str(GOLDEN),
            "--fixtures",
            str(FIXTURES),
            "--baseline",
            str(inflated),
            "--max-drop",
            "0.0",
        ]
    )
    assert code == 1


def test_fixture_recorder_uses_and_records_the_selected_clinic_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts import record_fixtures

    seen: dict[str, str] = {}

    class Provider:
        model_id = "recording-model"

        def complete_json(self, _value: object) -> dict[str, object]:
            return {
                "intent": "greeting",
                "summary_one_line": "Patient says hello",
                "language": "en",
                "confidence_overall": 0.99,
                "confidence_intent": 0.99,
                "confidence_person": 0.1,
                "confidence_company": 0.1,
            }

    def provider(_settings: object, _model_id: str, *, system_prompt: str) -> Provider:
        seen["prompt"] = system_prompt
        return Provider()

    golden = tmp_path / "golden.jsonl"
    golden.write_text(
        CLINIC_GOLDEN.read_text(encoding="utf-8").splitlines()[0] + "\n", encoding="utf-8"
    )
    output = tmp_path / "recorded.jsonl"
    monkeypatch.setattr(record_fixtures, "build_provider", provider)

    code = record_fixtures.main(
        [
            "--golden",
            str(golden),
            "--out",
            str(output),
            "--tenant-vertical",
            "clinics",
        ]
    )

    clinic_prompt = build_system_prompt(shipped_vocabularies()["clinics"])
    metadata = json.loads(output.with_suffix(".meta.json").read_text(encoding="utf-8"))
    assert code == 0
    assert seen["prompt"] == clinic_prompt
    assert metadata == {
        "model": "claude-haiku-4-5",
        "tenant_vertical": "clinics",
        "prompt_version": PROMPT_VERSION,
        "system_prompt_fingerprint": prompt_fingerprint(clinic_prompt),
        "golden_set_size": 1,
    }
