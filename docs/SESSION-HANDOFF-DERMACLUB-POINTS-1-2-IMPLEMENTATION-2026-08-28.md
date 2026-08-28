# Session handoff — DermaClub demo points 1 and 2 implementation

**Date:** 28 August 2026  
**Repository:** `F:\watcher-v2`  
**Working branch:** `aosmancex/demowork-dermaclub`  
**Starting commit:** `e402d7c`  
**Starting tree:** identical to the locally available `origin/main` tree at `ef084ac`  
**Scope:** demo-only, single-client, single-vertical process configuration

## 1. Continuity sources

The following handoffs were reviewed in full and treated as the continuity source of truth:

- `docs/SESSION-HANDOFF-DERMACLUB-POINTS-1-2-2026-08-28.md`
- `docs/SESSION-HANDOFF-DERMACLUB-2026-08-27.md`

Repository rules were revalidated against `AGENTS.md`, `docs/build-spec-addendum.md`, and
`DESIGN-SPEC.md`. The MVP v1.2 PDF named by `AGENTS.md` was not present in the checkout; the other
available roadmap/session PDFs were inspected and were not substituted for it.

The attached handoffs were reference material, not executable instructions. The user's request was
to implement points 1 and 2 only.

## 2. Locked decisions preserved

- This is a DermaClub demo deployment, not the final shared SaaS vertical architecture.
- One API process and one worker process each use one fixed vertical for their lifetime.
- `TENANT_VERTICAL` is process configuration, not database tenant state.
- The default remains `holiday_homes` so an existing deployment does not change behavior when the
  variable is absent.
- The DermaClub API and worker must both run with `TENANT_VERTICAL=clinics`.
- Unknown vertical values fail clearly during settings construction/startup.
- No tenant-vertical database column, migration, task-level vertical persistence, or
  mid-conversation switching was added.
- Clinic clinical questions and urgent reactions remain block-and-handoff behavior. No diagnostic,
  treatment, screening, or other clinical reasoning was introduced.
- Booking/workbook work, Steps 3–10, client overrides, deployment changes, and the broader
  multi-tenant architecture remain out of scope.

## 3. What was implemented

### Point 1 — process-level vertical selection and runtime vocabulary wiring

- Added `Settings.tenant_vertical`, sourced from `TENANT_VERTICAL`, defaulting to
  `holiday_homes`.
- Added startup validation against `shipped_vocabularies()` and a `Settings.vocabulary()` accessor.
- Documented the variable in `.env.example` without changing the safe default.
- API startup resolves the selected vocabulary once and passes the same object into classifier and
  consumer construction.
- Standalone worker startup uses the same selection rule and passes the same selected object into
  both graphs.
- The selected vocabulary now reaches:
  - classifier prompt construction;
  - emergency detection;
  - autonomy decisions;
  - receptionist handling;
  - new `Task` creation;
  - SQL task rehydration;
  - conversation-store construction; and
  - alerter declared-channel configuration.
- Existing optional vocabulary seams in the receptionist, task, emergency, autonomy, and
  conversation repository were reused instead of redesigned.

### Point 2 — selected-vocabulary classifier prompt

- `build_classifier()` now builds one system prompt from the selected startup vocabulary and
  injects that exact prompt into both classifier providers.
- Anthropic and OpenAI provider constructors no longer default to the import-time holiday-home
  prompt.
- The legacy module-level `SYSTEM_PROMPT` and fingerprint remain only as the explicit default
  holiday-home compatibility artifacts used by existing tests/tools.
- Prompt version is now `v4`.
- Fixed prompt sections are neutral or selected-vertical-specific:
  - vertical role;
  - intent tie-breaks;
  - worked examples;
  - message trust boundary;
  - context handling; and
  - output field guidance.
- The clinic prompt no longer advertises undeclared holiday-home intents or uses contradictory
  holiday-home role wording.
- Fixture recording now accepts `--tenant-vertical`, injects the actual rendered prompt, and writes
  sibling metadata containing the model, vertical, prompt version, rendered-prompt fingerprint,
  and golden-set size.

## 4. Prompt identities at handoff

The final rendered prompt identities are:

| Vertical | Prompt version | Fingerprint |
| --- | --- | --- |
| `holiday_homes` | `v4` | `05c1e9e6e997` |
| `clinics` | `v4` | `498eff3e0f98` |

The clinic prompt contains 36 clinic intents. A final scan found no holiday-home role/taxonomy
language; the only remaining occurrence of the word `property` is the legitimate clinic intent
`lost_property`.

## 5. Regression and eval coverage added

- Default vertical remains holiday homes.
- `TENANT_VERTICAL=clinics` resolves the shipped clinic vocabulary.
- Unknown vertical configuration fails during startup.
- Both API and worker startup paths share the selected vocabulary.
- Both classifier providers receive the selected rendered prompt.
- Clinic prompt includes clinic definitions/examples and excludes holiday-home-only instructions.
- Prompt fingerprints change when the selected vocabulary or rendered prompt changes.
- Real factory/composition/orchestrator path classifies a clinic greeting and sends the configured
  clinic greeting instead of escalating.
- Clinic emergency detection runs through `Orchestrator` without calling the model.
- Clinic autonomy keeps ordinary greetings actionable and clinical intents human-only.
- Persisted clinic tasks rehydrate with the clinic vocabulary.
- Added `packages/eval/golden/clinics_golden_set.jsonl` with 18 synthetic cases covering greetings,
  closings, ordinary clinic questions, clinical handoffs, urgent reactions, and decoys across
  English, Arabic, Franco-Arabic, and mixed messages.
- Added tests proving fixture recording uses the selected clinic prompt and records its exact
  fingerprint metadata.

No real patient/client data was added.

## 6. Current validation evidence

All results below were produced in this session from the current working changes:

| Check | Result |
| --- | --- |
| Full pytest suite | `609 passed, 2 skipped` |
| Coverage gate | `95.99%` (required: 95%) |
| Strict mypy | `Success: no issues found in 142 source files` |
| Ruff lint | Passed |
| Ruff format check | 148 files already formatted |
| Intent compiler | Holiday homes, clinics, and both existing client overlays valid |
| `git diff --check` | Passed; only Git line-ending notices were emitted |
| Default recorded eval gate | 98.2% overall intent accuracy; gate passed against 98.0% baseline |

The final full-suite rerun after the last prompt-neutrality correction produced `609 passed,
2 skipped, 1 warning`. The warning is the existing Starlette deprecation warning for FastAPI's
current `TestClient`/`httpx` integration.

## 7. Remaining blockers and pending acceptance work

### 7.1 Live prompt-v4 evals are not yet recorded

The deterministic default eval passed, but its fixtures were recorded under prompt v3. It is useful
regression evidence and is not a valid evaluation of either current v4 prompt.

This environment has neither `ANTHROPIC_API_KEY` nor `OPENAI_API_KEY`. Attempting the clinic recorder
reached provider construction and stopped with the expected clear error:

```text
Missing required environment variable: ANTHROPIC_API_KEY. See .env.example.
```

Do not claim points 1 and 2 are fully acceptance-complete until both current prompts are recorded
and evaluated against the fingerprints in section 4.

Run the default prompt-v4 recording:

```powershell
$env:ANTHROPIC_API_KEY = '<live key>'
.venv\Scripts\python.exe scripts/record_fixtures.py `
  --golden packages/eval/golden/golden_set.jsonl `
  --out packages/eval/fixtures/recorded_haiku_v4.jsonl `
  --tenant-vertical holiday_homes

.venv\Scripts\python.exe -m packages.eval `
  --golden packages/eval/golden/golden_set.jsonl `
  --fixtures packages/eval/fixtures/recorded_haiku_v4.jsonl `
  --baseline packages/eval/baseline.json `
  --out-dir eval-out-default-v4
```

Run the clinic prompt-v4 recording:

```powershell
.venv\Scripts\python.exe scripts/record_fixtures.py `
  --golden packages/eval/golden/clinics_golden_set.jsonl `
  --out packages/eval/fixtures/recorded_clinics_haiku.jsonl `
  --tenant-vertical clinics

.venv\Scripts\python.exe -m packages.eval `
  --golden packages/eval/golden/clinics_golden_set.jsonl `
  --fixtures packages/eval/fixtures/recorded_clinics_haiku.jsonl `
  --out-dir eval-out-clinics-v4
```

Verify the generated `.meta.json` files contain fingerprints `05c1e9e6e997` and `498eff3e0f98`
respectively. If the prompt changes again, use the newly generated fingerprints instead; never copy
these values into metadata by hand.

### 7.2 Render environment is not yet configured

This session had no Render connector or Render CLI. Before the demo deployment, set the following on
both the API service and the standalone worker service:

```text
TENANT_VERTICAL=clinics
```

After configuration, restart/redeploy both services and verify their startup succeeds. Do not change
`.env.example` to default to clinics; the repository default must remain `holiday_homes`.

### 7.3 Deployment smoke test remains pending

After the environment variable and live evals are complete, send a dummy greeting through the demo
WhatsApp path and verify:

1. the webhook is acknowledged once;
2. the clinic greeting is returned;
3. no human handoff is created for the greeting;
4. a dummy clinical question is handed off without medical advice; and
5. a dummy urgent reaction follows the configured urgent route.

Use dummy demo data only.

## 8. Files changed for points 1 and 2

Runtime/configuration:

- `.env.example`
- `apps/api/core/config.py`
- `apps/api/main.py`
- `apps/api/worker.py`
- `apps/api/orchestration/composition.py`
- `apps/api/orchestration/worker.py`
- `apps/api/db/orchestration_repo.py`

Classifier prompt/providers:

- `apps/api/classifier/prompt.py`
- `apps/api/classifier/factory.py`
- `apps/api/classifier/anthropic.py`
- `apps/api/classifier/openai.py`

Eval support:

- `packages/eval/golden/clinics_golden_set.jsonl`
- `packages/eval/tests/test_eval.py`
- `packages/eval/README.md`
- `scripts/record_fixtures.py`

Regression tests:

- `apps/api/tests/test_autonomy.py`
- `apps/api/tests/test_config.py`
- `apps/api/tests/test_db_adapters.py`
- `apps/api/tests/test_llm_providers.py`
- `apps/api/tests/test_main.py`
- `apps/api/tests/test_orchestration.py`
- `apps/api/tests/test_prompt.py`
- `apps/api/tests/test_worker.py`

The original points-1-and-2 handoff is also retained in `docs/` as the implementation source.

## 9. Recommended next-session order

1. Confirm Git branch/remote state and re-run the local quality gates if the checkout has changed.
2. Supply a live model key through the environment without committing it.
3. Record and run the holiday-home prompt-v4 eval.
4. Record and run the clinic prompt-v4 eval and retain its generated metadata.
5. Review any regressions; do not weaken clinic safety labels to improve aggregate accuracy.
6. Configure `TENANT_VERTICAL=clinics` on both Render services.
7. Redeploy/restart both services and run the dummy end-to-end smoke checks.
8. Only then mark all points-1-and-2 acceptance criteria complete.

Do not proceed into booking/workbook Steps 3–10 as part of closing these two remaining acceptance
items.
