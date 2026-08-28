# DermaClub — conversation-layer diagnosis

**Date:** 28 August 2026
**Branch:** `claude/dermaclub-conversational-layer-7ygmkl`
**Status:** diagnosis + reproduction only. No behavioural fix implemented.
**Inputs:** the deploy handoff (steps 8–10), the Monday salvage plan, and a WhatsApp screenshot
showing the same English line repeated after a working greeting.

This document records what was found, **how** it was found (runtime evidence, not code reading
alone), and what it means for the salvage plan. It supersedes the "missing tools / wrong vertical"
reading of the screenshot with a measured account of what the deployed code actually does.

---

## 0. TL;DR

- The screenshot's repeated line — *"Let me check that with the team and come straight back to
  you."* — is `_UNBUILT_TEXT`, a hardcoded fallback. **It is produced only when the four clinic
  tools are absent from the running process** (i.e. the worker loaded the holiday-homes vocabulary,
  not `clinics`). With the clinic tools registered — the confirmed live state — it is **unreachable
  for a WhatsApp patient** (proven, §5).
- With the tools registered, the *real* current behaviour on the screenshot flow is different and
  was reproduced live:
  - **Turn 1** (branch + day, no service) → the receptionist asks for the service in **hardcoded
    English**: `"Could you please provide the service?"`.
  - **Turn 2** (a service that resolves, e.g. `فاشيال`) → a **real, workbook-backed offer**
    (`19:00 for Facial at Maadi…`). The machinery downstream of the ask works.
  - **Turn 2 with the broad word `ليزر`** → a silent `HANDOFF_TEXT`, because `ليزر` resolves to no
    catalogue row **in the diary fixture** (the Primelase rows carry `برايم ليز…` aliases, not the
    bare word `ليزر`).
- Context is **preserved** across turns (branch + date carried); the live classifier is
  context-aware and labelled confidently. The "context is lost between turns" premise did **not**
  reproduce on this flow.
- **First divergence / recommended first fix:** localise the one hardcoded missing-slot ask in
  `apps/api/conversations/receptionist.py` to a context-aware Arabic question. Bounded to one branch
  + one copy field.

---

## 1. What was taken as given (not re-litigated)

- The live Render **worker** has `TENANT_VERTICAL=clinics` (independently confirmed by the client).
- The `clinics` vocabulary declares the four clinic tools, so `configure_clinic()` registers
  `check_availability`, `quote_price`, `hold_slot`, `confirm_booking`.

Because of this, "wrong vertical / clinic tools missing" was **not** assumed as the cause. The task
was to find how the bad behaviour arises **with the tools registered**, using runtime evidence.

---

## 2. Method — everything done to reach the findings

1. **Traced the message path** end to end from the code (webhook → queue → worker → classifier →
   receptionist → sender), and identified every site that emits `_UNBUILT_TEXT` / `HANDOFF_TEXT`
   and every `TENANT_*` copy field. (Files in §3.)
2. **Task 0 — startup diagnostic (already shipped on this branch).** Added a one-line startup log in
   `build_consumer` (the wiring both the API and the arq worker share) reporting the deployed git
   SHA, the selected vertical, and the registered tool names. This is the live check that
   distinguishes "clinic tools registered" from "not". No secrets, no DB round-trip.
3. **Read the governing intent definitions** in `packages/intents/verticals/clinics.yaml` for the
   intents these messages can hit (`availability_check`, `booking_enquiry`, `service_question`,
   `price_enquiry`, `appointment_lookup_status`, `arrival_late_no_show`) — required slots, terminal
   tool, `max_autonomy`, `needs_verified_identity`.
4. **Ran the real classifier + real receptionist live.** Using an API key supplied for the session
   (used in-process only; **must be rotated**), built the production two-tier classifier
   (`claude-haiku-4-5` → `claude-sonnet-5`, clinics prompt v5) via `build_classifier`, and drove the
   screenshot conversation turn by turn — accumulating history exactly as the worker does — through
   the real `receptionist.handle`, the real shipped tools, and the client's diary fixture
   (`packages/eval/fixtures/clinic_diary.json`, tenant clock 2026-09-01 Africa/Cairo). Captured, per
   turn: intent, confidences, raw + normalised slots, terminal tool, registry presence, autonomy,
   next step, and the exact response string + task status.
5. **Ran two variants** of the third turn: `ليزر` (broad) and `فاشيال` (concrete).
6. **Deterministic probes (no key):** `resolve_service(...)` / `resolve_branch(...)` against the
   diary to prove exactly why `ليزر` hands off; and an enumeration of every clinic intent whose
   terminal tool the receptionist does not directly handle, cross-checked against `decide_autonomy`,
   to prove `_UNBUILT_TEXT` is unreachable for an anonymous patient once the tools are registered.
7. **Pinned the failure** with a regression test that replays the flow with the live-captured labels
   and needs no key (§7).
8. Ran ruff, mypy, and the full test suite after each change.

---

## 3. The message path (what produces each reply)

```
webhook  apps/api/ingestion/router.py: receive()  → 200 immediately, enqueues id only
worker   apps/api/worker.py: consume_message() → orchestration/worker.py: Orchestrator.process()
           emergency check → classify (Haiku→Sonnet, WITH history) → _converse()
receptn  apps/api/conversations/receptionist.py: handle()  → tool calls → OutboundAction
send     apps/api/channels/whatsapp.py: WhatsAppSender.send()
```

- **No generative model in the response path.** The only LLM call is classification (one intent +
  copied slot strings). Every patient sentence is a `TENANT_*` template or a hardcoded string.
- **Response strings:**
  - Tenant copy: `TENANT_GREETING_OPENING[_NAMED]`, `TENANT_CLOSING[_BOOKING_CONFIRMED]`,
    `TENANT_AVAILABILITY_OFFER/_NONE`, `TENANT_PRICE_QUOTE`, `TENANT_CHOOSE_ONE`,
    `TENANT_BOOKING_TAKEN`, `TENANT_CONFIRM_READ_BACK`, `TENANT_BOOKING_CONFIRMED`.
  - Hardcoded English (language-blind): `HANDOFF_TEXT` (`receptionist.py:70`), `_UNBUILT_TEXT`
    (`:118`), `"Could you please provide the {slot}?"` (`:250`), `"Sorry — which detail should I
    change?"` (`:207`), `"Thanks — I've noted that down."` (`:277`).
- **Tool registration gate:** `apps/api/orchestration/composition.py:105` registers the four clinic
  tools only when the loaded vocabulary declares them — i.e. when `TENANT_VERTICAL=clinics`.

---

## 4. Live runtime trace (tools registered; diary fixture; today = 2026-09-02 = "tomorrow")

`REGISTRY` after clinic tools installed:
`answer_from_knowledge, check_availability, close_conversation, confirm_booking, greet,
handoff_to_human, hold_slot, quote_price, take_message`.

### Screenshot flow, third turn = `ليزر`

| turn | message | intent (live) | slots | terminal_tool | in REGISTRY | autonomy | response |
|---|---|---|---|---|---|---|---|
| 0 | `مساء الخير` | `greeting` 0.98 | `{}` | `greet` | yes | act | greeting (`say`) |
| 1 | `عايزة احجز بكرة في المعادي ايه المتاح؟` | `availability_check` 0.94 | `{branch:المعادي, requested_date:2026-09-02}` — **no service** | `check_availability` | yes | act | **`ask`: "Could you please provide the service?"** |
| 2 | `ليزر` | `booking_enquiry` 0.94 | `{service:ليزر, branch:المعادي, requested_date:2026-09-02}` | `confirm_booking` | yes | act | **`handoff`: "Let me connect you with someone who can help."** |

### Same flow, third turn = `فاشيال` (a service that resolves)

| turn | message | intent (live) | slots | response |
|---|---|---|---|---|
| 2 | `فاشيال` | `booking_enquiry` 0.95 | `{service:فاشيال, branch:المعادي, requested_date:2026-09-02}` | **`ask`: "I can offer 19:00 for Facial at Maadi on Wednesday 02 September. Which would you like?"** |

Observations proven by the trace:
- Turn 0/1 never emit `_UNBUILT_TEXT`. Turn 1 is a hardcoded **English** ask; the tool is never
  called because `service` is a required slot and is missing.
- **Context is preserved:** at turn 2 the model re-supplied `branch` + `requested_date` from history
  and added the service. The task also carried them (`status=collecting` after turn 1).
- A **resolvable** service reaches a **real, workbook-backed offer** (`19:00`, matching §9.1 of the
  deploy handoff). The machinery downstream of the missing-service ask works.
- (Greeting/offer read English in the harness only because the probe did not set the Arabic
  `TENANT_*` copy; production uses the Arabic templates. Treatment/branch/date names stay English —
  a pre-existing known gap.)

---

## 5. Root cause

### 5.1 Primary — the missing-service ask is hardcoded English (turn 1)

`apps/api/conversations/receptionist.py`, `handle()`, the `step == "ask"` branch:

```python
return OutboundAction(kind="ask",
    text=f"Could you please provide the {slot.replace('_', ' ')}?"), task
```

For any missing required slot other than the appointment time, this returns a fixed English,
context-blind sentence. It is the **first** point where behaviour diverges from
*understand → preserve context → return a real result*, and it is entirely in our control (no data
dependency).

### 5.2 Why the screenshot's `_UNBUILT_TEXT` cannot be the current state (proven)

`_UNBUILT_TEXT` is emitted only when a chosen intent's terminal tool is **absent from the
registry** (the `handle` catch-all, `_unbuilt()`, and the `tool is None` branches of
`_answer_from_catalogue` / `_book`). Enumerating every clinic intent whose terminal tool the
receptionist does **not** directly handle:

- All `handoff_to_human` intents (`human_request`, `promotion_enquiry`, `modify_appointment`,
  `cancel_appointment`, `clinical_*`, `complaint`, `unclear`, …) → `decide_autonomy` returns
  `hand_off` → `HANDOFF_TEXT`, never `_UNBUILT_TEXT`.
- The only two non-handoff unbuilt tools are `lookup_appointment` (`appointment_lookup_status`) and
  `create_ticket` (`arrival_late_no_show`). **Both declare `needs_verified_identity: true`**, so for
  an anonymous WhatsApp patient `decide_autonomy` hands off **before** the unbuilt branch is
  reached → `HANDOFF_TEXT`.

Therefore, with the four clinic tools registered, **no clinic intent produces `_UNBUILT_TEXT` for an
anonymous patient.** The screenshot's exact string is the fingerprint of the clinic tools being
**unregistered** (holiday-homes vocabulary loaded). *Live confirmation:* the Task 0 startup log
(`clinic_tools_registered=…`, `vertical=…`) or a single fresh WhatsApp message will show the live
worker now producing the English-ask / handoff behaviour above rather than that string.

### 5.3 Secondary — the broad word `ليزر` hands off (turn 2)

Deterministic probe against the diary fixture:

```
resolve_service('ليزر')      → found=None, ambiguous=False, candidates=[]     (reaches nothing)
resolve_service('برايم ليز') → ambiguous, candidates=[Primelase single, 6-session, 12-session]
resolve_service('فاشيال')    → Facial (DT002)
resolve_branch('المعادي')    → Maadi (DC01)
```

The three Primelase rows carry aliases `برايم ليز جلسة واحدة` / `برايمليز…`, **not** the bare word
`ليزر`. So `check_availability._resolve` returns `unknown_service` (no patient-facing text), and
`_offer_times` converts an empty result into a bare `HANDOFF_TEXT`.

**Proven for the diary fixture only.** Against the full production catalogue (35 services), prior
alias-resolution evidence (deploy handoff §5: bare `ليزر` → "1 asks") indicates `ليزر` resolves
**ambiguous** → a "which package?" question, not a hard handoff. Not verified against the live DB
this session.

---

## 6. What is proven vs. unproven

| Claim | Status |
|---|---|
| With tools registered, turn 1 returns hardcoded English `"Could you please provide the service?"` | **Proven** (live trace + deterministic regression) |
| A resolvable service reaches a real workbook-backed offer; context is preserved across turns | **Proven** (live trace) |
| `_UNBUILT_TEXT` is unreachable for an anonymous patient once clinic tools are registered | **Proven** (intent enumeration + `decide_autonomy`) |
| `ليزر` → silent handoff | **Proven for the diary fixture**; production catalogue likely ambiguous → "which package?" (**unproven live**) |
| The screenshot reflects a state where clinic tools were unregistered | **Inferred** from the proven unreachability; confirm via Task 0 log / one live message |
| Live classifier labels for these messages | **Proven** (live model), with mild non-determinism: turn 1 came back `availability_check` once and `booking_enquiry` once — both lack `service`, so turn 1 fails identically either way |

---

## 7. Regression pinned

`packages/eval/tests/test_screenshot_regression.py` — plays the screenshot flow through the real
receptionist + tools against the client diary, using the live-captured labels (no API key). Marked
`xfail(strict=True)`, the repo's idiom for behaviour not yet built: it documents the target, fails
today, and the strict marker forces the marker off when the fix lands.

Per-turn result today:
```
turn 0: say  ok=True   greeting
turn 1: ask  ok=False  "Could you please provide the service?"   ← pinned failure
turn 2: ask  ok=True   "I can offer 19:00 for Facial at Maadi on Wednesday 02 September…"
```

Suite after the change: **956 passed, 11 skipped, 1 xfailed**; ruff + mypy clean.

---

## 8. Impact on the salvage plan

**Confirmed**
- Reply text is templates-or-hardcoded; the missing-service ask is hardcoded English (the target of
  the plan's Task 1 missing-service piece).
- Downstream machinery (offer → hold → confirm) works once a service resolves — reuse it.

**Disproved / weakened**
- *"`_UNBUILT_TEXT` is the current failure"* — disproved for the confirmed live state; it is the
  fingerprint of unregistered tools.
- *"Context is lost across turns / the classifier mislabels fragments and abandons the task"* (the
  premise of the plan's context-carry task) — did **not** reproduce here: the live model preserved
  branch + date + service and classified confidently.

**Minimal correction**
- Promote **Task 1** (Arabic, context-aware missing-slot ask) to first.
- Demote the context-carry task to a smaller, later guard (it protects a case this flow did not hit,
  e.g. a bare `الساعة ٥`, not the screenshot).
- Keep `ليزر` as a **catalogue/alias** question (add a `ليزر` family alias, or script a concrete
  service), **not** a receptionist bug — verify against the live catalogue before touching code.

---

## 9. Recommended first implementation fix (one bounded change)

In `apps/api/conversations/receptionist.py` `handle()`, replace the single hardcoded English
`"Could you please provide the {slot}?"` ask with a **context-aware Arabic question rendered from the
dialogue state** (known branch/date + the missing slot), through the existing `current_copy()` seam
with an Arabic default. Scope: one `step == "ask"` branch + one copy field. It flips turn 1 of the
regression from red to green and touches nothing else. **Not yet implemented** — awaiting go-ahead.

---

## 10. Artifacts produced on this branch

| Item | Where |
|---|---|
| Startup diagnostic (Task 0) | `apps/api/orchestration/composition.py` (`build_consumer`) |
| Screenshot regression (xfail-strict) | `packages/eval/tests/test_screenshot_regression.py` |
| This document | `docs/DERMACLUB-CONVERSATION-DIAGNOSIS-2026-08-28.md` |

## 11. How to reproduce

```bash
# deterministic (no API key): the pinned regression and its per-turn result
.venv/bin/python -m pytest packages/eval/tests/test_screenshot_regression.py -rx -q

# why ليزر hands off in the fixture
.venv/bin/python - <<'PY'
from pathlib import Path
from packages.eval.journeys import FixtureDiary
from apps.api.clinic.catalogue import resolve_service, resolve_branch
d = FixtureDiary.from_path(Path('packages/eval/fixtures/clinic_diary.json'))
for p in ['ليزر','برايم ليز','فاشيال']:
    m = resolve_service(p, d.services)
    print(p, '→ found=', getattr(m.found,'name',None), 'ambiguous=', m.ambiguous)
PY

# full live trace (needs a key; used in-process only, rotate afterwards)
#   builds the production two-tier classifier and drives the real receptionist.
#   See the session record; script lived in the scratchpad and is not committed.
```

> **Security:** the API key used for the live trace was passed in chat and is in the session
> transcript. It was used in-process only and never written to a file or committed. **Rotate it.**
