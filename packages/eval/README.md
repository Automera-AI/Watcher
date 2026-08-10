# Watcher Eval Tool (`packages/eval`)

The classifier eval harness (addendum §12/§13; AI-Engineer role guide §1.5). This folder currently holds
the **golden set**; the runner + reporters are the next Sprint-1 slice.

## Golden set — `golden/golden_set.jsonl`
One JSON object per line: the input message + the known-correct `expected` classification (the flat
`ClassificationResult` schema with the locked taxonomy — DECISIONS.md).

```json
{"message": "...", "sender_phone": "+...", "sender_name": "...",
 "expected": {"intent": "new_lead", "summary_one_line": "...", "language": "en",
              "person_name": "...", "person_appears_to_be": "company_representative",
              "company_name": "...", "company_domain_hint": null, "phone_e164": "+...",
              "suggested_record_type": "contact_under_company",
              "confidence_overall": 0.95, "confidence_intent": 0.95,
              "confidence_person": 0.95, "confidence_company": 0.92}}
```

**Status:** 8 seed examples covering EN / AR / mixed across all 6 intents. Grow to **50** (10 per intent,
4 EN / 4 AR / 2 mixed) for the Phase-1 baseline, then to 150+ from promoted production corrections.

## Runner (`packages/eval`, §13)
Run from the **repo root** (the harness imports the locked Pydantic schemas from `apps/api`):

```bash
python -m packages.eval \
  --golden   packages/eval/golden/golden_set.jsonl \
  --fixtures packages/eval/fixtures/recorded_haiku.jsonl \
  --baseline packages/eval/baseline.json \
  --out-dir  eval-out
```

Runs each example, computes the **five metrics** — overall intent accuracy, per-field accuracy,
confidence calibration (Brier + reliability buckets), per-language accuracy, and the intent confusion
matrix — and writes `eval-out/report.json` + a self-contained `report.html` (no external CDN, per the
no-egress constraint). Module layout: `cases.py` (load) · `predictors.py` (prediction seam) ·
`metrics.py` (the five metrics) · `report.py` (JSON/HTML) · `cli.py` (`python -m packages.eval`).

- **CI mode (deterministic, D13-a):** `RecordedPredictor` replays `fixtures/recorded_haiku.jsonl` —
  recorded model outputs keyed by message text, so the gate needs no live key. With `--baseline`, the
  runner enforces the §12 gate: **exit non-zero if overall intent accuracy drops >2pp** below
  `baseline.json`. Shipping this `pyproject.toml` + the golden set is what flips `eval-gate` in
  `.github/workflows/ci.yml` from self-skip to a real run.
- **Re-recording:** when the prompt/model legitimately improves, re-record the fixtures and bump
  `baseline.json`. The current baseline is **0.875** (one intentional miss in the 8-example seed
  exercises the confusion matrix + calibration).
- **Nightly (next, with the concrete providers):** a live `Predictor` wrapping
  `apps.api.classifier.service.Classifier` over Anthropic + OpenAI runs against the golden set to catch
  silent model drift — it plugs into the same `run_eval` because it satisfies the `Predictor` seam.
