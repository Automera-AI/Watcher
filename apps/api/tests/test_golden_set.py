"""Guards the eval golden set: every expected label must validate against the locked schema."""

from __future__ import annotations

import json
from pathlib import Path

from apps.api.schemas.classification import ClassificationResult

REPO_ROOT = Path(__file__).resolve().parents[3]
GOLDEN_SET = REPO_ROOT / "packages/eval/golden/golden_set.jsonl"
FIXTURES = REPO_ROOT / "packages/eval/fixtures/recorded_haiku.jsonl"


def _messages(path: Path) -> list[str]:
    return [
        json.loads(line)["message"]
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_golden_set_examples_match_schema() -> None:
    lines = [line for line in GOLDEN_SET.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert lines, "golden set is empty"
    for i, line in enumerate(lines):
        record = json.loads(line)
        assert record["message"], f"example {i} missing message"
        # Raises if the expected label drifts from the locked taxonomy / confidence bounds.
        ClassificationResult.model_validate(record["expected"])


def test_every_golden_message_has_a_recorded_prediction() -> None:
    """The recorded predictor keys on message text, so the two files must agree exactly.

    Edit one and not the other — anonymising a name in the golden set but not the fixtures,
    say — and the eval gate dies with a ``KeyError`` deep in the runner rather than here.
    """
    golden, recorded = _messages(GOLDEN_SET), _messages(FIXTURES)

    assert len(golden) == len(set(golden)), "duplicate messages in the golden set"
    assert set(golden) == set(recorded), (
        "golden set and recorded fixtures disagree on message text.\n"
        f"  only in golden:   {sorted(set(golden) - set(recorded))}\n"
        f"  only in fixtures: {sorted(set(recorded) - set(golden))}"
    )
