"""Command-line runner + CI gate for the classifier eval (addendum §12/§13).

    python -m packages.eval \
        --golden packages/eval/golden/golden_set.jsonl \
        --fixtures packages/eval/fixtures/recorded_haiku.jsonl \
        --baseline packages/eval/baseline.json \
        --out-dir eval-out

Runs every golden example through the recorded fixtures (deterministic, no live key — D13-a),
computes the five metrics, writes ``report.json`` + ``report.html``, and prints a summary. When
``--baseline`` is given it enforces the §12 gate: exit non-zero if overall intent accuracy drops
more than ``--max-drop`` (default 0.02 = 2pp) below the recorded baseline.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from packages.eval.cases import load_golden
from packages.eval.metrics import EvalReport, evaluate_report
from packages.eval.predictors import RecordedPredictor, run_eval
from packages.eval.report import write_html, write_json


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m packages.eval", description=__doc__)
    parser.add_argument("--golden", type=Path, required=True, help="Golden set JSONL.")
    parser.add_argument(
        "--fixtures", type=Path, required=True, help="Recorded predictions JSONL (CI mode)."
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


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
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
