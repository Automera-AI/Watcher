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

**Status:** **56 examples** across all 19 intents in EN / AR / mixed, including six Franco-Arabic
cases added in the 2.7 re-record. Grow toward 150+ from promoted production corrections.

**No real client names, addresses or numbers in here** (roadmap 0.2). This is a public repo. Every
company, estate and area in the set is invented — Acme Trading, Cedar Realty, Northwind Residences,
Riverside Quarter — and it stays that way when the set grows. `test_golden_set.py` checks the golden
set and the fixtures still agree on message text, so anonymising one file and not the other fails a
test instead of the eval runner.

> Re-recorded live under prompt v3 (roadmap 2.7), so the fixtures are now the model's actual v3
> outputs for the golden messages — no longer the anonymised v2 replay the earlier note described.
> The run reports **0.98** (55/56); the one miss is a borderline general_info-vs-property_question
> case the model labelled property_question at 0.78, below the escalation threshold. Use
> `scripts/record_fixtures.py` to re-record when the prompt or model legitimately changes.

## DermaClub demo golden set

`golden/clinics_golden_set.jsonl` contains 18 synthetic clinic cases covering greetings, closings,
ordinary clinic questions, clinical handoffs, urgent reactions, and decoys in English, Arabic,
Franco-Arabic, and mixed messages. It deliberately has no recorded predictions yet: prompt v4 must
be evaluated with a live model rather than relabelling historical holiday-home fixtures.

```bash
ANTHROPIC_API_KEY=sk-ant-... python scripts/record_fixtures.py \
  --golden packages/eval/golden/clinics_golden_set.jsonl \
  --out packages/eval/fixtures/recorded_clinics_haiku.jsonl \
  --tenant-vertical clinics
```

The recorder writes `recorded_clinics_haiku.meta.json` beside the fixtures with the model, selected
vertical, prompt version, actual rendered-prompt fingerprint, and golden-set size.

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
- **Re-recording:** when the prompt/model legitimately improves, re-record the fixtures with
  `scripts/record_fixtures.py` (needs a live key) and bump `baseline.json`. The current baseline is
  **0.98**, recorded under prompt v3 with `claude-haiku-4-5` as the first pass (roadmap 2.7).
- **Nightly (next, with the concrete providers):** a live `Predictor` wrapping
  `apps.api.classifier.service.Classifier` over Anthropic + OpenAI runs against the golden set to catch
  silent model drift — it plugs into the same `run_eval` because it satisfies the `Predictor` seam.

## Journey evals — `golden/clinics_journeys.jsonl` (demo step 9)

The classifier eval scores one message at a time. Every failure the *booking* journey is about
happens between messages — a detail agreed on one turn and forgotten by the next, a slot offered
and then given away, a "تمام" that ends the conversation instead of finishing it — so the journey
set runs whole conversations and scores what the patient would have received on each turn.

```bash
python -m packages.eval \
  --journeys packages/eval/golden/clinics_journeys.jsonl \
  --diary    packages/eval/fixtures/clinic_diary.json \
  --out-dir  eval-out
```

Ten journeys: the demo booking end to end, a treatment whose one slot is already taken, a pregnancy
disclosure arriving on the turn that would have confirmed the appointment, an injectables booking
that must reach a clinician, a slot taken between the read-back and the yes, a "تمام" away from any
read-back, a price that must carry its session count, three look-alike packages that must be asked
about rather than guessed, and the question a patient asks *after* their booking.

**It runs against the client's own diary.** `fixtures/clinic_diary.json` is two branches of one day
cut out of `docs/DermaClub_Availability_DEMO_2026-08-26_1.xlsx` by the importer, and
`apps/api/tests/test_clinic_workbook_integration.py` fails if the two drift apart. That is not
decoration: the first thing this set established is that the workbook holds **exactly one slot per
service, branch and day**, so the "I can offer 11:00 / 16:00 / 18:00" in the handoffs cannot happen
with this data — the real journey offers the single open time. It also found the clarifying-turn
budget counting a finished booking's replies against the patient's next question, which handed off
the very next message on a conversation that had just gone perfectly.

> The diary fixture carries the **proposed** Arabic aliases (`docs/dermaclub-aliases-draft.csv`).
> A journey that resolves an Arabic name here resolves it in production only once the clinic has
> put those in their own workbook and it has been re-imported.

Two knobs:

- **`--fixtures`** swaps the labels written into the journey file for recorded classifier output,
  so the same journeys measure the model and the conversation together. A message the recording
  does not cover fails that journey rather than falling back to the written label.
- **`known_gap`** on a journey declares behaviour that is not built. It runs, it is reported, and
  it does not fail the build — and if it starts passing the runner says so, because that means the
  flag should come off. One is declared today: the closing after a booking does not repeat the
  reference, because the reference lives on the task and the task is finished by then.

The runner exits non-zero if any journey that is not a declared gap breaks.
