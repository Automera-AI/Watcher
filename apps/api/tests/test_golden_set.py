"""Guards the eval golden set: every expected label must validate against the locked schema."""

from __future__ import annotations

import json
from pathlib import Path

from apps.api.schemas.classification import ClassificationResult

GOLDEN_SET = Path(__file__).resolve().parents[3] / "packages/eval/golden/golden_set.jsonl"


def test_golden_set_examples_match_schema() -> None:
    lines = [line for line in GOLDEN_SET.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert lines, "golden set is empty"
    for i, line in enumerate(lines):
        record = json.loads(line)
        assert record["message"], f"example {i} missing message"
        # Raises if the expected label drifts from the locked taxonomy / confidence bounds.
        ClassificationResult.model_validate(record["expected"])
