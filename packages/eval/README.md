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

## Runner (next slice — §13)
- `python -m eval --golden golden/golden_set.jsonl --model <id>` → run each example, compute the 5 metrics
  (overall accuracy, per-field, confidence calibration, per-language, confusion matrix), emit HTML + JSON.
- **CI mode:** recorded fixtures (D13-a) so the gate is deterministic and needs no live key; the gate in
  `.github/workflows/ci.yml` activates once this package ships a `pyproject.toml` + the golden set.
- **Nightly:** run against Anthropic + OpenAI to catch silent model drift.
