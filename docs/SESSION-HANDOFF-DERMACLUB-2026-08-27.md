# Session handoff — DermaClub clinic vertical

**Date:** 27 August 2026
**Branch:** `claude/demo-derma-clinic-readiness-9nat71`
**PR:** [Automera-AI/Watcher#33](https://github.com/Automera-AI/Watcher/pull/33)
**Demo:** Tuesday 1 September 2026, 15:00–17:00 Africa/Cairo
**State:** 594 passed, 2 skipped. Ruff clean. mypy clean apart from one pre-existing error in `test_main.py` that is also on `main`.

---

## 1. What this session did

Steps 0–2 of the demo plan, plus the safety work the client review surfaced. **No booking capability yet** — this session built the vertical, the two ends of a conversation, and the clinical routing everything else hangs off.

| Step | Status |
|---|---|
| 0 — Remove false success | ✅ done |
| 1 — Clinic taxonomy | ✅ done (36 intents, client-reviewed) |
| 2 — Greeting + closing tools | ✅ done |
| 3 — Clinic schemas + migration 008 | ⬜ next |
| 4 — Workbook importer | ⬜ next |
| 5 — Slot extraction | ⬜ |
| 6 — Booking tools + atomic confirm | ⬜ |
| 7 — Clinical screening gate | ⬜ |
| 8 — Client pack | ⬜ |
| 9 — Journey evals | ⬜ |
| 10 — Deploy + rehearse | ⬜ |

### Commits

| SHA | Summary |
|---|---|
| `9451ab7` | Clinics vertical; stop escalating greetings; remove false success |
| `52a1a7f` | Clinic vocabulary v2.1; emergency recall; tenant urgent contact |
| `eacdb6a` | Tenant conversation copy |
| `22cf9db` | Tenant owns emergency wording; clinic routes to its doctor |

---

## 2. The two bugs that mattered

**Every greeting escalated.** `"hi"` had two routes and both ended in *"Let me connect you with someone who can help"*: classified `unclear`, whose ceiling is `hand_off`, so `decide_autonomy` fetched a person before confidence was consulted; or classified `general_info`, whose `answer_from_knowledge` searched the facts table for "hi", found nothing, and fell through `on_no_knowledge` to the same sentence. `"شكراً"` did the same.

**Every unbuilt tool claimed success.** Terminal tools with no implementation answered `"All set! I've noted everything down."` — telling someone who just asked for an appointment that they had one, when nothing was written anywhere.

Both fixed. Verified against the clinic vocabulary:

```
greeting        أهلاً بحضرتك في ديرما كلوب 👋 أنا ندى، المساعدة الافتراضية…
greeting+name   أهلاً بحضرتك يا رنا في ديرما كلوب 👋 …
closing         شكراً لتواصلك مع ديرما كلوب 💜 …
closing+booking تم تأكيد حجزك ✅ رقم الحجز: DC-0042 …
clinical_*      → handoff, at 0.99 confidence
```

---

## 3. Decisions taken this session

| # | Decision |
|---|---|
| 1 | Same-day booking dropped for the demo |
| 2 | Workbook is the source of truth for hours (11:00–20:00, not 12:00–19:00) |
| 3 | Workbook authoritative on the 15-min buffer; the 62 back-to-back pairs stand |
| 4 | DT029 Primelase 6-Sessions → **15,000 EGP** (file still reads 1,500; forced on import) |
| 5 | All 14 branches in the demo, including the 5 placeholders |
| 6 | Retail removed from the catalogue; 35 treatment services, IDs DT001–DT035 |
| 7 | Voice notes cut from the demo (`Transcriber` is an empty Protocol — unbuilt, not unverified) |
| 8 | Salesforce post-demo; workbook now, same provider-neutral interface |
| 9 | Suitability PDF ships as unsigned draft; client provides the real one post-signature |
| 10 | Nada greets by name — client confirmed this is necessary |
| 11 | **Never direct a patient to public emergency services. The clinic's doctor only.** |

### Decision 11 in detail

The clinic vocabulary's own spec says the emergency reply provides the clinician's number and alerts them. The first implementation appended the clinic contact and kept *"call your local emergency number"* underneath, on the reasoning that a clinic number must never displace emergency services.

That reasoning was wrong for this vertical. **Telling a patient to call an ambulance is itself a triage judgement** — deciding this is an ambulance case rather than a call to the clinician who performed the procedure — and triage is the one thing a clinic receptionist may never make.

It remains correct for holiday homes: a guest smelling gas needs the fire service and no operator substitutes for one. Both are right for their vertical, so **the wording is now per tenant** (`TENANT_EMERGENCY_REPLY`), not global. One invariant is asserted across all four branches: every reply promises a person is being alerted now.

> ⚠️ **Do not revert this to the additive form.** A future session reading `EMERGENCY_REPLY` in isolation will be tempted to "restore" the emergency-services line for the clinic. It is a deliberate client decision, recorded here.

---

## 4. Architecture notes for the next session

### Safety floors are per vertical

`MUST_HAND_OFF` / `MUST_VERIFY` / `MONEY_INTENTS` were module-level frozensets naming holiday-home intents, and `Vocabulary` *required* every name in them to exist in the file — a clinic vocabulary could not be written without declaring `extend_stay`, `owner_enquiry`, `access_code_request`. Now `SAFETY`, keyed by vertical, in `packages/intents/schema.py`.

`must_verify` names **only intents that disclose or mutate patient data without a person in the loop** — currently `appointment_lookup_status` and `arrival_late_no_show`. It must *not* include hand-off intents: requiring proof of identity before a hand-off means an unverified patient cannot reach a human at all. Verification belongs inside the human workflow.

### `IntentType` is the union across verticals

`apps/api/schemas/enums.py`. It types the classifier's structured output and ships once; a vocabulary is data a tenant picks. An intent a tenant's vocabulary does not declare is unknown to `decide_autonomy`, which hands off — a cross-vertical leak fails safe.

**Adding an intent to a vocabulary requires adding it here too**, or `build_system_prompt` raises `TaxonomyDrift` at import.

### Emergency matching: verbs, not nouns

Keying triggers on the personal-report **verb** rather than the topic **noun** is what separates a real report from an ordinary question. `جلدي اتحرق` ("my skin got burned") matches; `الليزر بيحرق الجلد؟` ("does laser burn skin?") does not.

| trigger style | recall | false positives |
|---|---|---|
| short topic nouns | 10/15 | 1/9 |
| long compound phrases | 4/15 | 0/9 |
| **verb-keyed (shipped)** | **15/15** | **0/10** |

The "every declared trigger fires" test passed throughout the 4/15 regression — a vocabulary can declare only long sentences, satisfy that check completely, and still miss every real message. `test_emergency.py` now pins both directions using messages that appear **nowhere in the YAML**. Keep it that way.

### Tenant copy

`ConversationCopy` in `apps/api/conversations/tools.py`, wired via `configure_conversation_copy` in the composition root. Four optional fields, all environment configuration, none committed.

`closing_booking_confirmed` is **the one piece of copy that can lie** — it states an appointment exists. The booking reference is its precondition, not a slot to fill: no reference, or a template that will not take one, and the generic closing is sent. It is currently unreachable by construction because nothing supplies a reference yet.

A copy typo degrades rather than raising: `str.format` on a mistyped placeholder would throw `KeyError` mid-conversation and lose the customer's reply.

---

## 5. Source data — validated, ready to import

`DermaClub_Availability_DEMO_2026-08-26.xlsx` (latest upload):

- **14 branches** — 5 real, 4 given, 5 placeholder
- **35 services**, IDs DT001–DT035, retail removed, EGP
- **672 slots** — 31 Aug–6 Sep, Fri 4 Sep correctly absent, 15:00 break held out
- 407 Open / 265 Booked; **0 overlaps**, all slot IDs unique, all 265 booking refs unique, every service name resolves

Known deviations to apply on import:

1. **DT029 Primelase 6-Sessions reads 1,500 EGP; force 15,000.** Single session is 3,100 and the 12-session is 16,350.
2. **62 adjacent slot pairs have a 0-minute gap** — every 60-minute service in an hourly grid. Workbook is authoritative; enforce the 15-min buffer only on *new* bookings.
3. **Ambiguous service names** need a canonical ID + alias map or Nada will loop against the 2-turn limit: "Basic Facial" and "Facial" are both 750/45min; three different 12-session laser packages all cost 16,350; "Body Shaping" (400) and "PowerShape 4 Sessions" (4,000) are the same modality in the suitability PDF.
4. Read Me still says "Services (Treatment and Retail)" though retail is gone. Cosmetic.

### T&C — a free win

The `facts` table and `answer_from_knowledge` already work end to end. Load the 15 laser-package clauses as tenant facts and `package_terms_question` answers them with **no new code**. Note the unresolved contradiction between clause 1 (no refunds) and clause 11 (medical refund exception) — only matters if asked.

---

## 6. Environment variables to set on Render

Not yet set — deliberately held so a redeploy did not land mid-work.

| Variable | Value |
|---|---|
| `TENANT_TIMEZONE` | `Africa/Cairo` ← **currently defaults to `Asia/Dubai`** |
| `TENANT_URGENT_CONTACT` | `+201097876232` (demo only; swap for the dermatologist post-signature) |
| `TENANT_EMERGENCY_REPLY` | **needs drafting — see §7** |
| `TENANT_GREETING_OPENING` | `أهلاً بحضرتك في ديرما كلوب 👋 أنا ندى، المساعدة الافتراضية. أقدر أساعدك في الخدمات والأسعار والمواعيد والحجز. أساعدك إزاي؟` |
| `TENANT_GREETING_OPENING_NAMED` | client to confirm; tested with `أهلاً بحضرتك يا {customer_name} في ديرما كلوب 👋 …` |
| `TENANT_CLOSING` | `شكراً لتواصلك مع ديرما كلوب 💜 لو احتجت أي مساعدة تانية، أنا تحت أمرك. يومك جميل.` |
| `TENANT_CLOSING_BOOKING_CONFIRMED` | `تم تأكيد حجزك ✅ رقم الحجز: {booking_reference}. شكراً لتواصلك مع ديرما كلوب، ونشوفك على خير.` |

Also confirm the LLM API key is set on **both** the API and worker services.

---

## 7. Open items

### Needs the client

1. **`TENANT_EMERGENCY_REPLY` copy.** Decision 11 forbids the default. Needs bilingual wording that names the doctor's number, promises the alert, and does **not** mention ambulance or emergency services. This is safety copy for a clinic — it should be written and approved by the client, not drafted here. Until it is set, the default (which mentions emergency services) is what fires.
2. **`TENANT_GREETING_OPENING_NAMED`** — confirm the Arabic phrasing of the named variant.

### Engineering, no input needed

3. **Migration 007 is unapplied** (006 is the deployed head). Apply before 008 goes near anything. Render access is granted.
4. **Steps 3–6** — clinic schemas, migration 008, workbook importer, slot extraction, booking tools with an idempotency key on (tenant, conversation, slot).
5. **`worker.py:410` still passes `{}`** for extracted slots. Nothing multi-turn works until Step 5.
6. **Dialogue-state rule is unimplemented.** The vocabulary header specifies that short replies ("تمام", "أيوه", "لا") are resolved against an active pending question *before* the flat vocabulary is consulted. Not expressible in YAML; needs runtime work. **"تمام" meaning *yes, book it* versus *thanks, goodbye* is the most likely live failure on demo day.**
7. **Multi-intent decomposition** — also specified in the header, also unimplemented.
8. **Five `act` intents have no data behind them** and are hand-offs in the shipped vocabulary: practitioner, promotions, retail/voucher, stock, orders. Safe, but noisy if the client probes them.

### Known cosmetic

9. `test_main.py:166` mypy error — pre-existing on `main`, not introduced here.

---

## 8. Demo-day traps

1. **Same-day is dropped**, and the demo runs 15:00–17:00 with the 15:00 hour held out as a break. If the client asks "احجزيلي النهاردة" Nada will not offer anything. **Script around Wed 2 September.**
2. **`Transcriber` is unimplemented.** A voice note produces no text. Cut from the demo — do not let it be tried live.
3. **The booking journey does not exist yet.** Steps 5 and 6 are the schedule risk; everything before them is roughly a day each. If time is lost, the fallback is a narrower scripted journey, not a half-built booking path.
4. **`TENANT_TIMEZONE` defaults to `Asia/Dubai`** — an hour off Cairo, which shifts the night-window emergency trigger.

---

## 9. Verification commands

```bash
uv venv --python 3.13 .venv && uv pip install -e ".[dev]"
uv pip install ruff==0.6.9 mypy==1.11.2 types-PyYAML==6.0.12.20240917

.venv/bin/python -m pytest apps/api/tests packages     # 594 passed, 2 skipped
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/mypy                                          # 1 pre-existing error

.venv/bin/python -m packages.intents \
  packages/intents/intents.yaml \
  packages/intents/verticals/clinics.yaml \
  packages/intents/clients/*.yaml
```

Note the project requires Python 3.13; the container default is 3.11.

---

## 10. Suggested opening prompt for the next session

> Read `docs/SESSION-HANDOFF-DERMACLUB-2026-08-27.md` and PR #33. The clinic vertical, greeting flow and clinical routing are done and merged to the branch. Do not re-litigate the decisions in §3 — in particular §3 decision 11, that the emergency reply must never direct a patient to public emergency services.
>
> Continue at Step 3: clinic domain schemas (Branch, Service, AvailabilitySlot, Booking, BookingReference), tenant-scoped with RLS, as migration 008 chained after 007. Verify the deployed alembic head on Render first and apply 007 — it is unapplied. Then Step 4, the workbook importer, applying the three deviations in §5.
