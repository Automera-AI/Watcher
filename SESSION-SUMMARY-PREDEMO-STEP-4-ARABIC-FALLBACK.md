# Session Summary & Handoff — Pre-Demo Step 4 (Deterministic Arabic Fallback)

**Plan:** `Watcher_DermaClub_LOCKED_PreDemo_Plan.md` (LOCKED) — §7 (Step 4)
**Scope this session:** Step 4 only. Steps 1–3 already merged to `main`; Step 5 (fact-locked renderer) intentionally **not** started.
**Prompt override:** Only Section 13's implementation batching was overridden (Step 4 alone, in one branch/session). All technical requirements, constraints, regressions and deferred items in the locked plan remain authoritative.

---

## Branch / commit

| | |
|---|---|
| **Base SHA** | `3d98a66856cf472b67840ee16d9673357dd7823c` (current `origin/main`; Steps 1–3 merged. The plan's original `afcb93c…` baseline has since advanced — this is the live main.) |
| **Branch** | `claude/predemo-arabic-fallback-5ixo7c` (fresh from base; not an old branch) |
| **Commit SHA** | `4ab85bab6490904d56048e3d3e264f79b3a7ca00` |
| **Pushed** | yes → `origin/claude/predemo-arabic-fallback-5ixo7c` |

No PR opened (not requested).

> **Branch-name note.** The task prompt named `claude/predemo-arabic-fallback`; the session's designated branch carries the `-5ixo7c` suffix and the environment binds pushes to it. Content is identical — flag if the bare name is required.

---

## The problem Step 4 closes

The constrained renderer (Step 5) is only safe if what it falls back to is safe and presentation-ready. The primary DermaClub Arabic booking path must not fall back to English. Before this change, several surfaces on the clinic booking journey and its safety exits were still hard-coded English:

- the **branch** and **date** asks (the two turns between the service and the diary) — generic `"Could you please provide the …?"`
- the **generic hand-off**, the **unbuilt-tool fallback**, and the **read-back-decline** prompt
- the **read-back quick-reply buttons** — `["Yes", "No"]` under an Arabic question
- the availability tools' own English defaults (offer / no-availability / slot-taken)

None of these had a tenant-copy seam, so no configuration could make them Arabic.

---

## Design (two rules, applied per surface)

The guiding constraint: **use the existing `ConversationCopy` seam where practical; configured tenant copy must still override; do not hardcode DermaClub/Nada into shared defaults; do not break the holiday-home vertical.**

1. **Clinic-only surfaces → Egyptian-Arabic in-code defaults.** The four clinic tools are registered only for the clinic vertical (`configure_clinic`), and the branch/date/time slots exist only in the clinic vocabulary, so an Arabic default on these paths can never reach a holiday-home reply. This mirrors the existing precedent (`_ASK_SERVICE_TEXT`, `_CHOOSE_ONE_TEXT` are already Arabic in code). Robust with **zero** configuration.

2. **Shared surfaces → neutral English in-code default + a new `ConversationCopy` seam a clinic sets to Arabic.** Hand-off, unbuilt, read-back-decline and the quick-reply buttons are reached by every vertical, so a global Arabic default would leak Arabic into a holiday-home reply. The default stays English; DermaClub configures Arabic through the same env-var seam every other line already uses.

Hand-off, unbuilt, clinical-block and emergency paths remain **deterministic** — none is routed through generation.

---

## Files changed (8)

| File | What |
|---|---|
| `apps/api/conversations/tools.py` | 8 new `ConversationCopy` fields (`ask_branch`, `ask_date`, `ask_time`, `handoff`, `unbuilt`, `clarify_change`, `confirm_yes`, `confirm_no`); Arabic in-code defaults for availability-offer / no-availability / slot-taken (clinic-only tools) |
| `apps/api/conversations/receptionist.py` | Arabic branch/date/time asks keyed to clinic slots (`_ask_for_slot`, gated by slot-name so holiday-home slots keep the generic prompt); hand-off / unbuilt / read-back-decline read `current_copy()` with English fallback; quick-reply buttons read `confirm_yes`/`confirm_no` |
| `apps/api/core/config.py` | Wired the 8 new `TENANT_*` env vars into `conversation_copy()` |
| `docs/DERMACLUB-DEPLOY-RUNBOOK-2026-09-01.md` | Documented the 8 new env vars (§3.2) with the client's Arabic wording |
| `apps/api/tests/test_booking_journey.py` | Full scripted Arabic journey (zero English system text); no-availability / hand-off / unbuilt / read-back-decline direct tests |
| `apps/api/tests/test_receptionist.py` | Branch/date asks, tenant override, non-clinic-slot keeps generic English, hand-off/unbuilt default-English-else-Arabic |
| `apps/api/tests/test_config.py` | Round-trip of the 8 new fields through `Settings` |
| `apps/api/tests/test_stale_offer_expiry.py` | Updated 2 assertions to the new Arabic no-availability default (`مفيش مواعيد فاضية`) |

---

## Copy surfaces changed — reference table

| Surface | Before | After (unconfigured) | After (DermaClub configured) |
|---|---|---|---|
| Missing service | Arabic (existing) | Arabic | Arabic |
| Missing **branch** | `Could you please provide the branch?` | **Arabic default** | Arabic |
| Missing **date** | `Could you please provide the requested date?` | **Arabic default** | Arabic |
| Missing **time** (if reached) | English generic | **Arabic default** | Arabic |
| Availability offer | English | **Arabic default** | Arabic |
| No availability | English | **Arabic default** | Arabic |
| Slot taken | English | **Arabic default** | Arabic |
| Read-back | English | English | Arabic (`confirm_read_back`) |
| Read-back **buttons** | `Yes` / `No` | `Yes` / `No` | **أيوه / لأ** |
| Read-back **decline** | English | English | **Arabic** (`clarify_change`) |
| Booking confirmation | English | English | Arabic (`booking_confirmed`) |
| **Generic hand-off** | English | English | **Arabic** (`handoff`) |
| **Unbuilt fallback** | English | English | **Arabic** (`unbuilt`) |
| Greeting / closing | English | English | Arabic (`opening`/`closing`) |

`أيوه` / `لأ` buttons are understood on the way back — `conversations/confirmation.py` already reads them as agreement/refusal.

---

## Deterministic journey result (renderer unavailable → deterministic layer only)

```
صباح الخير       → أهلاً بيكي! تحبي أساعدك في ايه؟
عايزة احجز        → تحبي تحجزي أنهي خدمة؟
برايم ليز جلسة واحدة → تحبي تحجزي في أنهي فرع؟
المعادي           → الحجز يكون يوم ايه؟
بكرة             → متاح عندنا 17:00 / 18:00 لـ Primelase Single Session في فرع Maadi يوم Wednesday 02 September. تحبي أحجزلك إمتى؟
الساعة ٥          → تأكيد الحجز: برايم ليز، المعادي، Wednesday 02 September، 17:00 — صح كده؟   [buttons: أيوه / لأ]
أيوه             → تم الحجز ✅ رقم الحجز: DC-0266. مستنينك في الفرع.
```

- **Zero generic English *system* sentences.**
- Exactly **one** durable booking (`DC-0266`); no booking before the explicit `أيوه`.
- The only Latin text is catalogue names (`Primelase Single Session`, `Maadi`) and the English date formatter (`Wednesday 02 September`) — the acknowledged display-name gaps flagged in runbook §5.2 as post-demo, **not** system sentences.

Also directly tested and Arabic: **no availability**, **generic hand-off**, **unbuilt fallback**, read-back decline.

---

## Verification results

| Check | Command | Result |
|---|---|---|
| Focused Step-4 tests | `pytest` (journey + asks + overrides + non-clinic-slot + config round-trip) | pass |
| Ruff | `ruff check` + `ruff format --check` (changed files; full `apps/api packages` check) | clean |
| mypy strict | `mypy` (`apps/api`, `packages/eval`, `packages/intents`) | **Success: no issues found in 165 source files** |
| Full suite | `pytest apps/api packages/eval packages/intents` | **1046 passed, 12 skipped** (baseline 999; +47 new) |

### Environment note for the reviewer
- Project requires **Python ≥ 3.13**. A venv was built with `python3.13` (`.venv/`, git-ignored).
- mypy strict needs **`types-PyYAML`** (the `yaml` import-untyped on `packages/intents/schema.py` is pre-existing on base; CI supplies the stub).

---

## Deploy delta (what the DermaClub deploy must add)

Eight new env vars, documented in the runbook §3.2. The branch/date/time asks are Arabic without them (in-code default); the four **shared** ones are what make the safety exits and buttons Arabic on the clinic:

```
TENANT_ASK_BRANCH, TENANT_ASK_DATE, TENANT_ASK_TIME
TENANT_HANDOFF, TENANT_UNBUILT, TENANT_CLARIFY_CHANGE
TENANT_CONFIRM_YES, TENANT_CONFIRM_NO
```

Add "Arabic fallback env configured" to the plan §14 startup checklist.

---

## Explicitly NOT done (per prompt / plan)

Step 5 (fact-locked renderer) not started. No renderer, no LLM phrasing, no classifier changes, no booking-state redesign, no service-family availability, no generalized multilingual architecture, no DB changes, no price-quote change (out of Step 4 scope; not on the booking journey), no unrelated cleanup.

---

## Handoff — suggested next steps

1. **Codex Session 1 (review, read-only)** per plan §13: confirm the deterministic fallback is Arabic and complete; the four clinic tools still register; clinical/emergency/hand-off/unbuilt paths remain deterministic and out of any generation path; configured tenant copy still overrides.
2. On deploy, set the eight env vars above and confirm the live scripted journey (plan §12 Journey A) is fully Arabic even with the renderer disabled.
3. Then proceed to **Step 5** (fact-locked generative renderer) in a fresh session — the deterministic layer this session built is its fallback.
