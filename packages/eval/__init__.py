"""Watcher classifier eval harness (addendum §12/§13; AI-Engineer role guide §1.5).

Loads the golden set, gets a predicted ``ClassificationResult`` per example (from recorded
fixtures in CI — D13-a — or a live provider nightly), and computes the five Phase-1 metrics:
overall intent accuracy, per-field accuracy, confidence calibration, per-language accuracy, and the
intent confusion matrix. Reports render to JSON + a self-contained HTML file (no external CDN — the
self-hosted tier forbids egress, AGENTS.md).
"""

from __future__ import annotations

from packages.eval.cases import CasePrediction, EvalCase, load_fixtures, load_golden
from packages.eval.metrics import EvalReport, evaluate_report
from packages.eval.predictors import Predictor, RecordedPredictor, run_eval

__all__ = [
    "CasePrediction",
    "EvalCase",
    "EvalReport",
    "Predictor",
    "RecordedPredictor",
    "evaluate_report",
    "load_fixtures",
    "load_golden",
    "run_eval",
]
