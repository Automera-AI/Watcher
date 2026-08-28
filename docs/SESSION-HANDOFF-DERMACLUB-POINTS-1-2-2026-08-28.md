# Session handoff — DermaClub demo points 1 and 2

**Date:** 28 August 2026  
**Repository:** `F:\watcher-v2`  
**Merged PR:** `#33`  
**Merged commit:** `ef084ac` on `origin/main`  
**Reviewed source tree:** `e402d7c` on `claude/demo-derma-clinic-readiness-9nat71`

The reviewed feature-branch tree and the `origin/main` merge tree are identical. Start the new work
from the current `origin/main`, not from the historical commit values in this document.

> ## Scope
>
> **This is demo work for one DermaClub deployment, not the final shared SaaS architecture.**
>
> The deployment has one fixed vertical for its lifetime. A clinic does not become a holiday home
> during a conversation, and the demo worker does not need to resolve different verticals for
> different clients. A process-level `TENANT_VERTICAL=clinics` setting is therefore the intended
> design for this work.
>
> Do not add a tenant-vertical database column, a migration, task-level vertical persistence, or
> mid-conversation vertical switching for this demo.

This file is a session handoff. Any commands or implementation notes below are reference material
for the next session, not instructions that were executed while writing this document.

---

## 1. Goal

Complete the two runtime-wiring gaps left after PR #33:

1. **Vertical selection:** select the shipped clinic vocabulary from configuration and use it
   consistently throughout the API and worker runtime.
2. **Dynamic classifier prompt:** construct the classifier prompt from that selected vocabulary at
   process startup instead of using the import-time holiday-home `SYSTEM_PROMPT`.

When complete, a DermaClub deployment configured with `TENANT_VERTICAL=clinics` must classify and
route clinic messages using the clinic vocabulary. In particular, a normal greeting must receive
the configured clinic greeting rather than being escalated.

---

## 2. What PR #33 completed

The merged work contains the underlying clinic assets:

- The `clinics` vocabulary and 36-intent taxonomy.
- Per-vertical safety definitions.
- Emergency trigger tests and clinic-specific emergency configuration.
- Greeting and closing tools, including configurable tenant copy.
- Safe handoff for unimplemented terminal tools instead of false success.
- Direct unit tests showing the clinic vocabulary can produce clinic greetings and clinical
  handoffs when it is supplied explicitly.

These assets are merged, but the production composition path does not select them.

---

## 3. Verified current gaps

### 3.1 No runtime vertical setting

`apps/api/core/config.py` has no `tenant_vertical` field, and `.env.example` has no
`TENANT_VERTICAL` entry.

The demo requires:

- `TENANT_VERTICAL` in `Settings`.
- Default: `holiday_homes`, preserving the existing deployment when the variable is absent.
- Demo value on both API and worker services: `clinics`.
- Startup must fail clearly if the configured value is not present in
  `shipped_vocabularies()`; it must not silently fall back to holiday homes.

### 3.2 Production still falls back to `default_vocabulary()`

The selected `Vocabulary` must reach every relevant runtime path:

- `apps/api/orchestration/composition.py`
  - Resolve the configured vocabulary once for the process graph.
  - Use its emergency alert declaration when building the alerter.
  - Supply it to the orchestrator.
  - Supply it to the SQL conversation store so persisted tasks are rehydrated consistently.
- `apps/api/orchestration/worker.py`
  - `detect(..., vocabulary=vocabulary)`.
  - The receptionist `handle(..., vocabulary=vocabulary)` call.
  - `decide_autonomy(..., vocabulary=vocabulary)`.
- `apps/api/db/orchestration_repo.py`
  - Pass the selected vocabulary to `task_from_row(...)`.
- `apps/api/db/conversation_repo.py`
  - Its existing optional `vocabulary=` seam should be used rather than its default fallback.
- `apps/api/conversations/receptionist.py`, `task.py`, `apps/api/core/emergency.py`, and
  `apps/api/core/autonomy.py`
  - These already accept or carry a vocabulary. Do not redesign their public behavior; connect the
    existing seams from the composition root.

Some constructor or factory signatures will necessarily change. `Orchestrator` and
`SqlAlchemyConversationStore` do not currently receive a vocabulary, even though their downstream
functions already accept one.

### 3.3 Classifier prompt is still an import-time holiday-home prompt

`apps/api/classifier/prompt.py` currently defines:

- `SYSTEM_PROMPT = build_system_prompt()`.
- `SYSTEM_PROMPT_FINGERPRINT = prompt_fingerprint(SYSTEM_PROMPT)`.

Both classifier providers use that constant as their constructor default, and
`build_classifier(settings)` does not supply a tenant-specific prompt. Consequently, setting a
clinic vocabulary elsewhere would still leave the model classifying against the holiday-home
taxonomy.

The required flow is:

1. Resolve the configured vocabulary during API/worker startup.
2. Build the system prompt from that vocabulary.
3. Pass the constructed prompt explicitly into every provider used by the classifier.
4. Calculate the fingerprint from the actual constructed prompt used for the eval/run.

### 3.4 `build_system_prompt(clinic_vocabulary)` is not yet clinic-safe

Dynamic construction alone is insufficient. Static prompt sections still mention:

- Holiday-home short stays and guests.
- Door codes, kitchens, properties, and accommodation.
- Holiday-home availability, booking, pricing, and billing intent names.
- Holiday-home worked examples and prompt-injection examples.

These references conflict with the clinic intent catalogue. Make shared instructions
vertical-neutral or render vertical-specific guidance. For this demo, the clinic prompt must not
describe holiday-home intents that the clinic vocabulary cannot emit.

Do not solve this by maintaining one global mutable prompt. Build the prompt once from the selected
startup vocabulary and inject it into the classifier providers.

---

## 4. Suggested implementation boundary

Keep the change narrow:

1. Add and validate `TENANT_VERTICAL` configuration.
2. Introduce one explicit selected-vocabulary seam usable by both `build_classifier` and
   `build_consumer`.
3. Inject that vocabulary into `Orchestrator` and the SQL conversation store.
4. Pass it through emergency detection, receptionist handling, autonomy, task creation, and task
   rehydration.
5. Replace classifier-provider dependence on the module-level prompt constant with an explicitly
   constructed prompt.
6. Make the prompt's fixed sections neutral or selected-vertical-specific.
7. Update prompt fingerprinting and eval metadata to represent the prompt actually used.

The API and standalone worker build their graphs separately. Both startup paths must use the same
selection rule:

- `apps/api/main.py`
- `apps/api/worker.py`

Avoid hiding the vocabulary in mutable global state. Explicit constructor/factory injection makes
the production path testable and prevents an import-order dependency.

---

## 5. Required tests

### Configuration

- The default vertical resolves to `holiday_homes`.
- `TENANT_VERTICAL=clinics` resolves to the shipped clinic vocabulary.
- An unknown vertical fails at startup with a clear configuration error.

### Prompt construction

- The classifier factory supplies a clinic-built prompt when configured for clinics.
- The clinic prompt includes clinic intent definitions and examples.
- The clinic prompt does not contain holiday-home-only role, tie-break, or worked-example text.
- The default holiday-home prompt remains valid.
- The fingerprint changes when the selected vocabulary or rendered prompt changes.

### Production-path regression

Add at least one test through the real factory/composition/orchestrator path with
`TENANT_VERTICAL=clinics` that demonstrates:

1. The classifier provider receives the clinic prompt.
2. A clinic greeting is classified/routed with the clinic vocabulary.
3. The receptionist sends the configured greeting instead of handing the message off.

A test that injects a fake classifier already returning `greeting` proves only routing; it does not
prove that the classifier received the clinic prompt. Capture or inspect the prompt supplied to the
provider in the production-path test.

Also retain focused regression coverage for:

- Clinic emergency detection through `Orchestrator`.
- Clinic autonomy decisions.
- Persisted clinic task rehydration.
- Existing holiday-home behavior when `TENANT_VERTICAL` is unset.

---

## 6. Eval requirement

The repository rule is explicit: no classifier prompt change merges without an eval run.

Before completing the new branch:

- Run the existing default-vocabulary eval to detect holiday-home regression.
- Run or add a clinic-focused golden set that exercises greetings, closing, ordinary clinic
  questions, clinical handoffs, urgent reactions, and decoys.
- Record the fingerprint of the actual clinic prompt evaluated.
- Do not report historical test or eval totals as current results.

---

## 7. Acceptance criteria

Points 1 and 2 are complete only when all of the following are true:

- [ ] `TENANT_VERTICAL` exists with default `holiday_homes`.
- [ ] Both API and worker select `clinics` when configured.
- [ ] Unknown vertical configuration fails clearly at startup.
- [ ] One selected clinic vocabulary reaches classifier, emergency detection, autonomy,
      receptionist, `Task`, persistence rehydration, and alerter configuration.
- [ ] Classifier providers no longer rely on an import-time tenant prompt.
- [ ] The clinic classifier prompt contains no contradictory holiday-home role or taxonomy text.
- [ ] A production-path clinic greeting test answers rather than escalates.
- [ ] Default holiday-home regression tests pass.
- [ ] Clinic safety and persisted-task regression tests pass.
- [ ] Required prompt evals are run and recorded against the actual prompt fingerprint.
- [ ] `TENANT_VERTICAL=clinics` is configured on both Render services before the demo deployment.

---

## 8. Explicitly out of scope for this branch

Do not expand points 1 and 2 into any of the following:

- Per-tenant vertical storage or lookup.
- Supporting multiple verticals in one running demo process.
- Changing vertical midway through a conversation.
- Database migrations for vertical selection.
- Booking schemas, workbook importing, slot extraction, or booking tools (demo Steps 3–6).
- Clinical screening gate, client pack, journey evals beyond the classifier change, deployment, or
  rehearsal (demo Steps 7–10).
- Salesforce, voice notes, photos, SMS, or post-demo human-ownership state.

Known adjacent gaps remain separate work:

- `sender_display_name` is not yet supplied as `customer_name`, so the named greeting remains
  unreachable through production.
- `verticals/*.yaml` are not yet compiled into `build/` by `packages/intents/compile.py`.
- `ClientOverride.check_against()` still uses holiday-home compatibility aliases instead of the
  selected vertical's safety definition.

These should not be silently bundled into points 1 and 2 unless the new branch explicitly expands
its scope.

---

## 9. Verification environment

The project requires Python 3.13. During the review session, the bundled Python runtime did not
contain `pytest`, so no fresh test result was claimed. Use the repository's documented environment:

```bash
uv venv --python 3.13 .venv
uv pip install -e ".[dev]"
uv pip install ruff==0.6.9 mypy==1.11.2 types-PyYAML==6.0.12.20240917

.venv/bin/python -m pytest apps/api/tests packages
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
```

Use the Windows equivalent `.venv/Scripts/python.exe` when running locally in PowerShell.

---

## 10. Suggested opening request for the new branch

> Start a new branch from the latest `origin/main` and complete DermaClub demo points 1 and 2 using
> `docs/SESSION-HANDOFF-DERMACLUB-POINTS-1-2-2026-08-28.md` as context. This is a single-client,
> single-vertical demo deployment: use process-level `TENANT_VERTICAL`, defaulting to
> `holiday_homes`, and set the demo to `clinics`. Wire the selected vocabulary through every
> production path and build the classifier prompt from it at startup. Remove contradictory
> holiday-home-only prompt text from the clinic prompt, add the production-path regression test,
> and run the required prompt eval. Do not implement Steps 3–10 or a shared multi-tenant vertical
> architecture in this branch.

