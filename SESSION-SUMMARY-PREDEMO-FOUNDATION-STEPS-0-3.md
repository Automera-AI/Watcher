# Session Summary & Handoff — Pre-Demo Foundation (Steps 0–3)

**Plan:** `Watcher_DermaClub_LOCKED_PreDemo_Plan.md` (LOCKED)
**Scope this session:** Step 0, Step 1, Step 2, Step 3 only. Steps 4 and 5 intentionally NOT started.
**Prompt override:** Only Section 13's implementation batching was overridden (Steps 0–3 in one branch/session). All technical requirements, constraints, regressions, and deferred items in the locked plan remain authoritative.

---

## Branch / commit

| | |
|---|---|
| **Base SHA** | `afcb93c90021e9737ae02caf8fe55aaf6606dc33` (verified `origin/main`; matches plan baseline) |
| **Branch** | `claude/predemo-foundation` (fresh from base; not an old branch) |
| **Commit SHA** | `d4122abe696d90c942c35edbb9f8b67b666402f6` |
| **Pushed** | yes → `origin/claude/predemo-foundation` |

No PR opened (not requested).

---

## Files changed (9)

| File | Step | What |
|---|---|---|
| `apps/api/orchestration/composition.py` | 1 | Split terminal capabilities vs runtime tools; fix registration decision + startup diagnostic |
| `apps/api/tests/test_composition.py` *(new)* | 1 | Registration tests (all four wired; non-clinic none; startup line) |
| `packages/intents/verticals/clinics.yaml` | 2 | `max_clarifying_turns` 2 → 5 |
| `apps/api/tests/test_booking_journey.py` | 2 | Progress-not-cut-off + non-progress-still-hands-off regressions; Primelase fixture |
| `apps/api/conversations/slots.py` | 3 | `strip_unsupported_temporal_slots` provenance guard + `_message_states_date` |
| `apps/api/orchestration/worker.py` | 3 | Apply guard after normalisation, before task-state update |
| `packages/eval/journeys.py` | 3 | Apply the same guard in the eval path |
| `apps/api/tests/test_slots.py` | 3 | Guard unit tests |
| `apps/api/tests/test_orchestration.py` | 3 | Journey B worker-level regression |

---

## Step 1 — Repair clinic tool registration

**Problem.** `build_consumer` gated clinic wiring on `_CLINIC_TOOLS <= {intent.terminal_tool …}` where `_CLINIC_TOOLS` included `hold_slot`. No intent ever declares `hold_slot` as a terminal tool (it is an internal booking op run at the read-back), so the subset test could never be true → `configure_clinic()` never ran → real availability fell through the receptionist to `_UNBUILT_TEXT`.

**Fix.**
- `_CLINIC_TERMINAL_CAPABILITIES = {check_availability, quote_price, confirm_booking}` — decides whether the clinic flow is supported.
- `_CLINIC_RUNTIME_TOOLS = {check_availability, quote_price, hold_slot, confirm_booking}` — what `configure_clinic` registers and what completeness is checked against.
- Registration decision now uses terminal capabilities. Startup diagnostic reports `clinic_tools_registered` = `clinic_flow_supported and not missing_clinic_tools`, and `missing_clinic_tools` against the four runtime tools. A clinic deploy logs `clinic_tools_registered=True missing_clinic_tools=[]`; a holiday-home deploy honestly logs `False`.

**Note:** `validate_registry()` (which would flag `hold_slot` as an undeclared registry entry) is referenced only in a comment and is **not called at runtime**, so registering `hold_slot` trips nothing.

**Tests:** `apps/api/tests/test_composition.py` — hold_slot-not-terminal assertion, all four registered for clinics, none for holiday_homes, startup line asserts `True`/`[]`.

---

## Step 2 — Demo-safe clarification limit

**Problem.** Clinic `max_clarifying_turns: 2` handed off during normal slot-filling: `عايزة احجز → ask service`, `service → ask branch`, `branch → ask date` fired the budget (turns_taken=2 ≥ 2) one turn before the diary was consulted.

**Turn-budget arithmetic** (per-task `replies_sent`, verified in `db/orchestration_repo.begin`):

| turn | reply | turns_taken | guard (≥ max) |
|---|---|---|---|
| عايزة احجز | ask service | 0 | |
| برايم ليز جلسة واحدة | ask branch | 1 | |
| المعادي | ask date | 2 | **old max=2 handed off here** |
| بكرة | offer times (availability) | 3 | |
| (pick time) | read-back (confirm) | 4 | |
| أيوه | book (agreement path, ungated) | — | |

So the full journey needs the guard to stay open through `turns_taken=4` → **max ≥ 5**.

**Fix.** `clinics.yaml` `max_clarifying_turns: 5`. This is the schema ceiling (`Field(ge=1, le=5)`), and it is clinic-specific (holiday-home default stays `3`). Every reply is still counted, so repeated non-progress still reaches the hand-off boundary — just later.

**Deliberately NOT done:** the post-demo `question_key` / progress-persistence architecture. This is interim debt (plan §5, §18).

**Tests:** `test_normal_booking_progress_is_not_cut_off_at_the_date_step` (reaches real 17:00/18:00 offer, no hand-off) and `test_repeated_non_progress_still_reaches_the_handoff_boundary` (asks at 4, hands off at 5).

---

## Step 3 — Block fabricated date/time slots

**Problem.** The live classifier emitted a `requested_date` the patient never wrote; `normalise_slots` resolves an invented value exactly like a real one, so it reached the scheduler.

**Fix.** New `strip_unsupported_temporal_slots(resolved, message, *, today, …)` in `slots.py`, applied **after normalisation and before task-state updates**:
- Keep `requested_date` only if the current message deterministically resolves to the same date (`_message_states_date`, reusing `parse_date`).
- Keep `requested_time` only if `parse_time(message)` equals the value.
- Otherwise drop. Non-temporal slots (service/branch) are untouched — the **active task remains authoritative** for previously established facts. No generalized service/branch provenance (plan §6, §11).

**Parser reuse detail.** `parse_date` recognises relative days (`بكرة`) only as a *whole value*, so a date inside a sentence (`… في المعادي بكرة`) or glued to punctuation (`بكرة؟`) would be missed. `_message_states_date` therefore scans word-token windows of size 1–3 (`_WORD = [^\W_]+`, which strips punctuation) with the same parser. This covers multi-word forms (`بعد بكرة`, `يوم الأربع`, `day after tomorrow`) and was required to keep the eval journeys green.

**Wiring.** Applied in `worker._converse` (real path, uses `turn.text` + tenant clock) and `packages/eval/journeys.py` (recorded-classification path). Applied in the worker rather than inside `receptionist.handle` deliberately: many `handle`-level unit tests legitimately pre-inject `requested_date` via `extracted_slots`, and the guard belongs on the real message the classifier saw.

**Tests:** `test_slots.py` unit tests (drop fabricated; keep standalone / in-sentence / multi-word / punctuated; drop wrong-day; time kept vs dropped; service/branch untouched; empty message). `test_orchestration.py::test_a_fabricated_date_never_reaches_task_state_and_the_missing_day_is_asked` — Journey B: active service+branch task + `المواعيد المتاحة ايه` → service/branch preserved, no date invented, asks for day (`kind == "ask"`), no hand-off.

---

## Required regressions (plan) — status

- **Step 2 script** (`عايزة احجز / برايم ليز جلسة واحدة / المعادي / بكرة`) reaches real availability without hand-off → ✅ `test_normal_booking_progress_is_not_cut_off_at_the_date_step`
- **Step 3 script** (`المواعيد المتاحة ايه` on active service+branch) preserves values, invents no date, asks for the day, no hand-off → ✅ `test_a_fabricated_date_never_reaches_task_state_and_the_missing_day_is_asked`
- Preserved unchanged: clinical screening, stale-offer expiry, selected-time revalidation, booking idempotency → ✅ existing suites pass unchanged.

---

## Verification results

| Check | Command | Result |
|---|---|---|
| Focused | `pytest test_composition test_slots test_booking_journey test_orchestration::…` | pass |
| Ruff (changed files) | `ruff check` + `ruff format --check` | clean |
| mypy strict | `mypy apps/api packages/eval packages/intents` | **Success: no issues found in 165 source files** |
| Full suite | `pytest apps/api packages/eval packages/intents` | **999 passed, 12 skipped** |

### Environment note for the reviewer
- Project requires **Python ≥ 3.13**. System default here is 3.11; a venv was built with `python3.13` (`.venv/`).
- mypy strict needs **`types-PyYAML`** installed (the `yaml` import-untyped error on `packages/intents/schema.py:540` is **pre-existing** on the base branch; CI supplies the stub).

---

## Known pre-existing / intentional debt (NOT introduced this session)

1. **Ruff format drift on two untouched files** — `apps/api/tests/test_autonomy.py` and `apps/api/tests/test_boundary.py` are flagged by `ruff format --check` in this sandbox (newer local ruff than the repo pins). They are **not** in this diff and were left untouched.
2. **Time-guard breadth (intentional pre-demo debt).** `parse_time` searches for any number in the message, so a fabricated `requested_time` could in principle validate against an unrelated bare digit in the same turn (e.g. a numeric session count like `6 جلسات`). The scripted demo flow uses `جلسة واحدة` (a word, not a digit) and does not hit this; generalized provenance is deferred (plan §11).
3. **Clarification limit is a flat bump**, not progress-aware accounting (plan §5 post-demo debt).

---

## Explicitly NOT done (per prompt / plan)

Step 4 (Arabic fallback completion) and Step 5 (fact-locked renderer) not started. No renderer, no generative response code, no Arabic copy overhaul beyond what these fixes needed (none was needed), no service-family availability, no generalized `ليزر` changes, no classifier redesign, no queue serialization, no DB schema changes, no unrelated refactors.

---

## Handoff — suggested next steps

1. **Codex Session 1 (review, read-only)** per plan §13: verify clinic tools really registered; natural booking reaches availability and booking; fabricated date/time cannot reach task state; clinical safety unchanged; booking idempotency unchanged. Focus areas above map directly to the added tests.
2. On approval + deploy, confirm the live startup line: `TENANT_VERTICAL=clinics`, `clinic_tools_registered=True`, `missing_clinic_tools=[]` (plan §4 release-blocking; §14).
3. Then proceed to **Step 4** (deterministic Egyptian-Arabic fallback) and **Step 5** (fact-locked renderer) in a fresh session.
