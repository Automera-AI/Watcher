"""Command-line runner + CI gate for the evals (addendum §12/§13, demo step 9).

Two sets, one entry point, because they answer two different questions about the same system.

**The classifier eval** scores one message at a time::

    python -m packages.eval \
        --golden packages/eval/golden/golden_set.jsonl \
        --fixtures packages/eval/fixtures/recorded_haiku.jsonl \
        --baseline packages/eval/baseline.json \
        --out-dir eval-out

Runs every golden example through the recorded fixtures (deterministic, no live key — D13-a),
computes the five metrics, writes ``report.json`` + ``report.html``, and prints a summary. When
``--baseline`` is given it enforces the §12 gate: exit non-zero if overall intent accuracy drops
more than ``--max-drop`` (default 0.02 = 2pp) below the recorded baseline.

**The journey eval** scores whole conversations against the client's own diary::

    python -m packages.eval \
        --journeys packages/eval/golden/clinics_journeys.jsonl \
        --diary    packages/eval/fixtures/clinic_diary.json \
        --out-dir  eval-out

Every failure the booking journey is about happens between messages, which is exactly what a
per-message metric cannot see. It runs deterministically on the labels written into the journey
file; with ``--fixtures`` it uses recorded classifier output instead and measures the model and
the conversation together. It exits non-zero on any failure that is not a declared known gap.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import TYPE_CHECKING

from packages.eval.cases import load_fixtures, load_golden
from packages.eval.metrics import EvalReport, evaluate_report
from packages.eval.predictors import RecordedPredictor, run_eval
from packages.eval.report import write_html, write_json

if TYPE_CHECKING:  # imported for types only — see `_run_journey_eval` for why not at runtime
    from packages.eval.journeys import JourneyReport, TurnLabel


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m packages.eval", description=__doc__)
    parser.add_argument("--golden", type=Path, default=None, help="Golden set JSONL.")
    parser.add_argument(
        "--journeys", type=Path, default=None, help="Journey set JSONL (demo step 9)."
    )
    parser.add_argument(
        "--diary", type=Path, default=None, help="Clinic diary fixture the journeys run against."
    )
    parser.add_argument(
        "--vertical", default="clinics", help="Vocabulary the journeys are scored against."
    )
    parser.add_argument(
        "--fixtures",
        type=Path,
        default=None,
        help="Recorded predictions JSONL. Required for the classifier eval; optional for "
        "journeys, where it replaces the written labels with what the model actually said.",
    )
    parser.add_argument(
        "--model", default=None, help="Model label for the report (defaults to the baseline's)."
    )
    parser.add_argument(
        "--out-dir", type=Path, default=None, help="Write report.json + report.html here."
    )
    parser.add_argument(
        "--baseline", type=Path, default=None, help="Baseline JSON; enables the §12 accuracy gate."
    )
    parser.add_argument(
        "--max-drop",
        type=float,
        default=0.02,
        help="Max allowed overall-accuracy drop vs baseline before the gate fails (default 2pp).",
    )
    return parser.parse_args(argv)


def _print_summary(report: EvalReport) -> None:
    print(f"model:                  {report.model}")
    print(f"examples:               {report.total}")
    print(f"overall intent acc:     {report.overall_intent_accuracy:.1%}")
    print(f"unclear rate:           {report.unclear_rate:.1%}")
    print(f"brier score:            {report.brier_score:.4f}")
    print("per-language accuracy:")
    for lang, acc in report.per_language_accuracy.items():
        print(f"  {lang:<6}                {acc:.1%}")


def _check_gate(report: EvalReport, baseline_path: Path, max_drop: float) -> int:
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    baseline_acc = float(baseline["overall_intent_accuracy"])
    drop = baseline_acc - report.overall_intent_accuracy
    print(f"baseline intent acc:    {baseline_acc:.1%}  (max drop {max_drop:.1%})")
    if drop > max_drop:
        print(f"::error::eval gate FAILED — accuracy dropped {drop:.1%} (> {max_drop:.1%})")
        return 1
    print("eval gate PASSED")
    return 0


def _print_journeys(report: JourneyReport) -> None:
    print(f"labels:                 {report.label_source}")
    print(f"diary:                  {report.diary}")
    print(f"journeys:               {report.passed}/{report.total}")
    print(f"turns:                  {report.turns_passed}/{report.turns}")
    for outcome in report.outcomes:
        gap = outcome.case.known_gap
        if outcome.ok and gap is None:
            continue
        if outcome.ok and gap is not None:
            print(f"  CLOSED  {outcome.case.id}: passes now — drop its known_gap")
            continue
        marker = "gap" if gap is not None else "FAIL"
        print(f"  {marker:<7} {outcome.case.id} — {outcome.case.title}")
        for turn in outcome.turns:
            for failure in turn.failures:
                print(f"            turn {turn.index} ({turn.kind}): {failure}")
                print(f"              said: {turn.text[:120]!r}")
        if gap is not None:
            print(f"            known gap: {gap}")


def _run_journey_eval(args: argparse.Namespace) -> int:
    # Imported here rather than at module scope, and the reason is the CI job next door: the
    # classifier gate runs on pydantic alone (D13-a — deterministic, no live key, minimal install),
    # while a journey drives the real receptionist and therefore the whole application. A
    # module-level import would make the classifier gate depend on rapidfuzz, PyYAML and
    # SQLAlchemy to replay a JSONL file it could always read on its own.

    from packages.eval.journeys import FixtureDiary, TurnLabel, load_journeys, run_journeys
    from packages.intents.schema import vocabulary_for

    if args.diary is None:
        raise SystemExit("--journeys needs --diary: a journey has to run against a real diary")
    labels: dict[str, TurnLabel] | None = None
    if args.fixtures is not None:
        labels = {
            message: TurnLabel.from_result(result)
            for message, result in load_fixtures(args.fixtures).items()
            if result is not None
        }
    report = run_journeys(
        load_journeys(args.journeys),
        FixtureDiary.from_path(args.diary),
        vocabulary=vocabulary_for(args.vertical),
        labels=labels,
        diary_name=args.diary.name,
    )
    if args.out_dir is not None:
        args.out_dir.mkdir(parents=True, exist_ok=True)
        (args.out_dir / "journeys.json").write_text(
            json.dumps(report.as_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
        )
    _print_journeys(report)
    if report.passed < report.total:
        print(f"::error::journey eval FAILED — {report.total - report.passed} journey(s) broke")
        return 1
    print("journey eval PASSED")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if (args.golden is None) == (args.journeys is None):
        raise SystemExit("pass exactly one of --golden (classifier eval) or --journeys")
    if args.journeys is not None:
        return _run_journey_eval(args)
    if args.fixtures is None:
        raise SystemExit("--golden needs --fixtures")

    cases = load_golden(args.golden)
    predictor = RecordedPredictor.from_path(args.fixtures)
    model = args.model or "recorded"
    if args.baseline is not None and args.model is None:
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        model = baseline.get("model", model)

    report = evaluate_report(run_eval(cases, predictor), model=model)

    if args.out_dir is not None:
        args.out_dir.mkdir(parents=True, exist_ok=True)
        write_json(report, args.out_dir / "report.json")
        write_html(report, args.out_dir / "report.html")

    _print_summary(report)
    if args.baseline is not None:
        return _check_gate(report, args.baseline, args.max_drop)
    return 0
