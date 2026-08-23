"""Re-record the eval fixtures by running the classifier over the golden set (roadmap 2.7).

The CI gate replays ``packages/eval/fixtures/recorded_haiku.jsonl`` — recorded model outputs keyed
by message text — so it needs no live key (D13-a). That determinism has a cost: the recorded
outputs are frozen at whatever prompt/model produced them, and the current ones were recorded under
prompt v2. Until they are re-recorded, the gate reports v2's number (0.88) whatever prompt v3 says.
This is the operator-run tool that re-records them — the same category as ``docs/make_roadmap.py``
and ``scripts/measure_prompt.py``: not imported by the app or CI, and needing a key.

    ANTHROPIC_API_KEY=sk-ant-... python scripts/record_fixtures.py \
        --golden   packages/eval/golden/golden_set.jsonl \
        --out      packages/eval/fixtures/recorded_haiku.jsonl

**First pass only, on purpose.** The classifier escalates a low-confidence first pass to a larger
model; this records the *first-pass* result alone (escalation threshold pinned to 0, so it never
escalates). The eval measures whether prompt v3 improved the cheap tier — the thing every inbound
message pays for — and folding the escalation model's quality into the number is exactly how 0.88
stopped meaning anything. The model recorded per line is whatever ``CLASSIFIER_MODEL_FIRST_PASS``
resolves to (default ``claude-haiku-4-5``).

After recording, run ``python -m packages.eval`` against the new fixtures to read the real v3
accuracy, and write that number into ``packages/eval/baseline.json`` (with the model this recorded).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apps.api.classifier.factory import build_provider  # noqa: E402
from apps.api.classifier.service import Classifier  # noqa: E402
from apps.api.classifier.types import ClassificationInput  # noqa: E402
from apps.api.core.config import Settings  # noqa: E402
from apps.api.schemas.enums import MessageType  # noqa: E402
from packages.eval.cases import load_golden  # noqa: E402


def _record_one(classifier: Classifier, message: str, phone: str | None, name: str | None) -> dict:
    outcome = classifier.classify(
        ClassificationInput(
            text=message,
            modality=MessageType.TEXT,
            sender_display_name=name,
            sender_phone=phone,
        )
    )
    predicted = outcome.result.model_dump(mode="json") if outcome.result is not None else None
    return {"message": message, "model": outcome.model_used, "predicted": predicted}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python scripts/record_fixtures.py", description=__doc__)
    parser.add_argument("--golden", type=Path, required=True, help="Golden set JSONL.")
    parser.add_argument("--out", type=Path, required=True, help="Fixtures JSONL to write.")
    args = parser.parse_args(argv)

    settings = Settings()
    model_id = settings.classifier_model_first_pass
    provider = build_provider(settings, model_id)
    # Pin escalation off: a first pass is always "confident enough", so the larger model never runs
    # and every recorded line is the cheap tier's own answer under prompt v3.
    classifier = Classifier(provider, provider, escalation_threshold=0.0)

    cases = load_golden(args.golden)
    lines: list[str] = []
    for i, case in enumerate(cases, 1):
        record = _record_one(classifier, case.message, case.sender_phone, case.sender_name)
        lines.append(json.dumps(record, ensure_ascii=False))
        pred = record["predicted"]
        label = pred["intent"] if pred is not None else "UNCLEAR/invalid"
        print(f"[{i:>2}/{len(cases)}] {label:<20} {case.message[:52]}", file=sys.stderr)

    args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nwrote {len(lines)} fixtures to {args.out} (model {model_id})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
