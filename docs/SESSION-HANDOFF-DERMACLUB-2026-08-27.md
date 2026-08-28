# Session handoff — DermaClub clinic vertical

**Date:** 27 August 2026
**Branch:** `claude/demo-derma-clinic-readiness-9nat71`
**PR:** [Automera-AI/Watcher#33](https://github.com/Automera-AI/Watcher/pull/33)
**Demo:** Tuesday 1 September 2026, 15:00–17:00 Africa/Cairo
**Tests:** 594 passed, 2 skipped. Ruff clean. mypy clean apart from one pre-existing error in `test_main.py` that is also on `main`.

---

> ## ⚠️ Read this first: scope
>
> **This is demo-scope work, not a product.** Everything in this branch exists to get one scripted WhatsApp conversation working in front of one client on 1 September. It is deliberately narrow.
>
> **The booking journey now exists** (steps 5–7, §13, 28 August). Nada can check availability, quote from the catalogue, hold a slot, write an appointment and quote its reference, and a clinical disclosure stops her doing any of it. Two things stand between that and a working demo and neither is code: the environment variables in §6 and §13.4 are unset on Render, and **no Arabic service or branch name resolves** because the workbook is entirely in English and has no aliases column (§13.5).
>
> **Substantial work follows the demo**, whether or not the client signs — see §9. Nothing here should be read as production-ready for a live clinic: the clinical block lists are an unsigned draft, voice notes are unbuilt, Salesforce is not connected, and human-ownership states are specified but not implemented.

---

## 1. Status

Steps 0–2 of the ten-step demo plan, plus the safety work the client review surfaced.

| Step | Scope | Status |
|---|---|---|
| 0 | Remove false success | ✅ **done** |
| 1 | Clinic taxonomy (36 intents) | ✅ **done** — client-reviewed |
| 2 | Greeting + closing tools | ✅ **done** |
| — | Tenant copy drafted and verified | ✅ **done** — clinical sign-off pending (§7) |
| 3 | Clinic schemas + migration 008 | ✅ **done** — 28 Aug, see §12 |
| 4 | Workbook importer | ✅ **done** — 28 Aug, see §12 |
| 5 | Slot extraction | ✅ **done** — 28 Aug, see §13 |
| 6 | Booking tools + atomic confirm | ✅ **done** — 28 Aug, see §13 |
| 7 | Clinical screening gate | ✅ **done** — 28 Aug, see §13 |
| 8 | Client pack | ⬜ **not started** |
| 9 | Journey evals | ⬜ **not started** |
| 10 | Deploy + rehearse | ⬜ **not started** |

**8 of 10 steps complete.** The booking journey exists end to end: a patient can be greeted, quoted,
offered real times, read back to, and given a durable reference — and a clinical disclosure stops
all of it. What is left is packaging, evals and the rehearsal, plus everything in §7 and §9.

### Commits

| SHA | Summary |
|---|---|
| `9451ab7` | Clinics vertical; stop escalating greetings; remove false success |
| `52a1a7f` | Clinic vocabulary v2.1; emergency recall; tenant urgent contact |
| `eacdb6a` | Tenant conversation copy |
| `22cf9db` | Tenant owns emergency wording; clinic routes to its doctor |
| `24cd98c` | This handoff |
| `0b08676` | Clinic schemas, migration 008, workbook importer (steps 3–4) |
| `0013892` | Slot extraction; `TENANT_VERTICAL` (step 5) |
| `86f27c9` | Booking journey: availability, quotes, holds, atomic confirm (step 6) |
| `a08ea8b` | Clinical screening gate (step 7) |

---

## 2. The two bugs that mattered

**Every greeting escalated.** `"hi"` had two routes and both ended in *"Let me connect you with someone who can help"*: classified `unclear`, whose ceiling is `hand_off`, so `decide_autonomy` fetched a person before confidence was consulted; or classified `general_info`, whose `answer_from_knowledge` searched the facts table for "hi", found nothing, and fell through `on_no_knowledge` to the same sentence. `"شكراً"` did the same.

**Every unbuilt tool claimed success.** Terminal tools with no implementation answered `"All set! I've noted everything down."` — telling someone who just asked for an appointment that they had one, when nothing was written anywhere.

Both fixed and verified against the clinic vocabulary:

```
greeting        أهلاً بحضرتك في ديرما كلوب 👋 أنا ندى، المساعدة الافتراضية…
greeting+name   أهلاً بحضرتك يا رنا في ديرما كلوب 👋 …
closing         شكراً لتواصلك مع ديرما كلوب 💜 …
closing+booking تم تأكيد حجزك ✅ رقم الحجز: DC-0042 …   (unreachable until Step 6)
clinical_*      → handoff, at 0.99 confidence
unbuilt tool    → handoff, never "All set"
```

---

## 3. Decisions taken with the client

| # | Decision |
|---|---|
| 1 | Same-day booking dropped for the demo |
| 2 | Workbook is the source of truth for hours (11:00–20:00, not 12:00–19:00) |
| 3 | Workbook authoritative on the 15-min buffer; the 62 back-to-back pairs stand |
| 4 | DT029 Primelase 6-Sessions → **15,000 EGP** (forced on import; the 26 Aug workbook now reads 15,000 itself — see §12) |
| 5 | All 14 branches in the demo, including the 5 placeholders |
| 6 | Retail removed from the catalogue; 35 treatment services, IDs DT001–DT035 |
| 7 | Voice notes cut from the demo (`Transcriber` is an empty Protocol — unbuilt, not unverified) |
| 8 | Salesforce post-demo; workbook now, behind the same provider-neutral interface |
| 9 | Suitability PDF ships as an unsigned draft; client provides the signed one post-signature |
| 10 | Nada greets by name — client confirmed this is required |
| 11 | **Never direct a patient to public emergency services. The clinic's doctor only.** |

### Decision 11 in detail

The clinic vocabulary's own spec says the emergency reply provides the clinician's number and alerts them. The first implementation appended the clinic contact and kept *"call your local emergency number"* underneath, on the reasoning that a clinic number must never displace emergency services.

That reasoning was wrong for this vertical. **Telling a patient to call an ambulance is itself a triage judgement** — deciding this is an ambulance case rather than a call to the clinician who performed the procedure — and triage is the one thing a clinic receptionist may never make.

It remains correct for holiday homes: a guest smelling gas needs the fire service and no operator substitutes for one. Both are right for their vertical, so **the wording is per tenant** (`TENANT_EMERGENCY_REPLY`), not global. One invariant is asserted across all four code branches: every reply promises a person is being alerted now.

> ⚠️ **Do not revert this to the additive form.** A future session reading `EMERGENCY_REPLY` in isolation will be tempted to "restore" the emergency-services line for the clinic. It is a deliberate client decision, recorded here.

---

## 4. Architecture notes

### Safety floors are per vertical

`MUST_HAND_OFF` / `MUST_VERIFY` / `MONEY_INTENTS` were module-level frozensets naming holiday-home intents, and `Vocabulary` *required* every name in them to exist in the file — a clinic vocabulary could not be written without declaring `extend_stay`, `owner_enquiry`, `access_code_request`. Now `SAFETY`, keyed by vertical, in `packages/intents/schema.py`.

`must_verify` names **only intents that disclose or mutate patient data without a person in the loop** — currently `appointment_lookup_status` and `arrival_late_no_show`. It must *not* include hand-off intents: requiring proof of identity before a hand-off means an unverified patient cannot reach a human at all. Verification belongs inside the human workflow.

### `IntentType` is the union across verticals

`apps/api/schemas/enums.py`. It types the classifier's structured output and ships once; a vocabulary is data a tenant picks. An intent a tenant's vocabulary does not declare is unknown to `decide_autonomy`, which hands off — a cross-vertical leak fails safe.

**Adding an intent to a vocabulary requires adding it here too**, or `build_system_prompt` raises `TaxonomyDrift` at import.

### Emergency matching: verbs, not nouns

Keying triggers on the personal-report **verb** rather than the topic **noun** separates a real report from an ordinary question. `جلدي اتحرق` ("my skin got burned") matches; `الليزر بيحرق الجلد؟` ("does laser burn skin?") does not.

| trigger style | recall | false positives |
|---|---|---|
| short topic nouns | 10/15 | 1/9 |
| long compound phrases | 4/15 | 0/9 |
| **verb-keyed (shipped)** | **15/15** | **0/10** |

The "every declared trigger fires" test passed throughout the 4/15 regression — a vocabulary can declare only long sentences, satisfy that check completely, and still miss every real message. `test_emergency.py` now pins both directions using messages that appear **nowhere in the YAML**. Keep it that way.

### Tenant copy

`ConversationCopy` in `apps/api/conversations/tools.py`, wired via `configure_conversation_copy` in the composition root. Four optional fields, all environment configuration.

`closing_booking_confirmed` is **the one piece of copy that can lie** — it states an appointment exists. The booking reference is its precondition, not a slot to fill: no reference, or a template that will not take one, and the generic closing is sent. It is currently unreachable by construction because nothing supplies a reference until Step 6.

A copy typo degrades rather than raising: `str.format` on a mistyped placeholder would throw `KeyError` mid-conversation and lose the customer's reply.

---

## 5. Source data — validated, ready to import

`DermaClub_Availability_DEMO_2026-08-26.xlsx` (latest upload):

- **14 branches** — 5 real, 4 given, 5 placeholder
- **35 services**, IDs DT001–DT035, retail removed, EGP
- **672 slots** — 31 Aug–6 Sep, Fri 4 Sep correctly absent, 15:00 break held out
- 407 Open / 265 Booked; **0 overlaps**, all slot IDs unique, all 265 booking refs unique, every service name resolves

Deviations to apply on import:

1. **DT029 Primelase 6-Sessions reads 1,500 EGP; force 15,000.** Single session is 3,100 and the 12-session is 16,350. *Update, 28 Aug: the workbook now holds 15,000 itself. The importer's correction is conditional and reports that it had nothing to do rather than re-forcing (§12).*
2. **62 adjacent slot pairs have a 0-minute gap** — every 60-minute service in an hourly grid. Workbook is authoritative; enforce the 15-min buffer only on *new* bookings.
3. **Ambiguous service names** need a canonical ID + alias map or Nada will loop against the 2-turn limit: "Basic Facial" and "Facial" are both 750/45min; three different 12-session laser packages all cost 16,350; "Body Shaping" (400) and "PowerShape 4 Sessions" (4,000) are the same modality in the suitability PDF.
4. Read Me still says "Services (Treatment and Retail)" though retail is gone. Cosmetic.

### T&C — a free win

The `facts` table and `answer_from_knowledge` already work end to end. Load the 15 laser-package clauses as tenant facts and `package_terms_question` answers them with **no new code**. Note the unresolved contradiction between clause 1 (no refunds) and clause 11 (medical refund exception) — only matters if asked.

---

## 6. Environment variables to set on Render

Not yet set — deliberately held so a redeploy did not land mid-work. **All values below are drafted and verified through the real code path**; none require further authoring.

| Variable | Value |
|---|---|
| `TENANT_TIMEZONE` | `Africa/Cairo` ← **currently defaults to `Asia/Dubai`** |
| `TENANT_URGENT_CONTACT` | `+2010978…6232` — the demo number (full value in the session thread and in Render). Swap for the dermatologist post-signature. |
| `TENANT_EMERGENCY_REPLY` | §6.1 below |
| `TENANT_GREETING_OPENING` | §6.2 |
| `TENANT_GREETING_OPENING_NAMED` | §6.2 |
| `TENANT_CLOSING` | §6.2 |
| `TENANT_CLOSING_BOOKING_CONFIRMED` | §6.2 |

Also confirm the LLM API key is set on **both** the API and worker services.

### 6.1 `TENANT_EMERGENCY_REPLY` — drafted, needs clinical sign-off

Implements decision 11: names the doctor, promises the alert, and mentions **no** ambulance or emergency service. `{contact}` is substituted from `TENANT_URGENT_CONTACT` in both paragraphs.

```
دي حالة طارئة ومحتاجة تدخل طبي فوري. برجاء الاتصال حالاً بالطبيب المسؤول في ديرما كلوب على {contact}. أنا بابلغه دلوقتي وهيتواصل مع حضرتك في أسرع وقت.

This is an emergency and needs immediate medical attention. Please call the DermaClub doctor now on {contact}. I am alerting them right now and they will contact you as soon as possible.
```

Verified through `emergency_reply()`: contact substituted in both languages, no unrendered placeholder, no reference to ambulance / emergency services / 123 / إسعاف / الطوارئ, and the "a person is being alerted" invariant holds.

> ⚠️ **This is clinical safety copy written by an engineer, not a clinician.** It is fit for the demo. Before go-live the clinic's medical lead should approve the exact wording, alongside the suitability block lists in the same unsigned state.

### 6.2 Conversation copy — client-supplied, verified

```
TENANT_GREETING_OPENING
أهلاً بحضرتك في ديرما كلوب 👋 أنا ندى، المساعدة الافتراضية. أقدر أساعدك في الخدمات والأسعار والمواعيد والحجز. أساعدك إزاي؟

TENANT_GREETING_OPENING_NAMED
أهلاً بحضرتك يا {customer_name} في ديرما كلوب 👋 أنا ندى، المساعدة الافتراضية. أقدر أساعدك في الخدمات والأسعار والمواعيد والحجز. أساعدك إزاي؟

TENANT_CLOSING
شكراً لتواصلك مع ديرما كلوب 💜 لو احتجت أي مساعدة تانية، أنا تحت أمرك. يومك جميل.

TENANT_CLOSING_BOOKING_CONFIRMED
تم تأكيد حجزك ✅ رقم الحجز: {booking_reference}. شكراً لتواصلك مع ديرما كلوب، ونشوفك على خير.
```

The named opening is the plain opening with `يا {customer_name}` after `بحضرتك`. Verified against an Arabic name, a Latin name, and no name — no unrendered placeholder in any variant.

---

## 7. Open items

Nothing here is blocked on the client. Every item is engineering work.

### Before the demo — required

1. **Migration 007 is unapplied** (006 is the deployed head). Apply it, then 008 (written, §12). Render access is granted.
2. ~~**Steps 5–6**~~ — done, §13. ~~**`worker.py` passes `{}`**~~ — done.
3. **Arabic aliases for services and branches.** The single biggest demo risk now. See §13.5: the mechanism is complete and the data does not exist, so today "عايزة أحجز فاشيال في المعادي" resolves to nothing and Nada asks a question she cannot be answered. Two columns in the client's own workbook, then a re-import.
4. **Set the environment variables in §6 and §13.4** on both Render services. `TENANT_VERTICAL=clinics` is new and **mandatory** — without it the clinic taxonomy is shipped and reachable by nothing.
5. **Steps 8–10** — client pack, journey evals, rehearsal on the live number.

### Before the demo — strongly advised

6. ~~**Dialogue-state rule is unimplemented.**~~ Implemented for the one case the demo needs (§13.3): a short reply is read against an outstanding read-back before the classified intent may switch tasks, so "تمام" mid-booking books and "تمام" elsewhere still closes. The *general* form — every pending question, not just the confirmation — is still unbuilt.
7. **Multi-intent decomposition** — also specified in the vocabulary header, also unimplemented. A message asking price *and* nearest branch will only get one answered.

### Accepted for the demo, not fixed

8. **Five `act` intents have no data behind them** and are hand-offs in the shipped vocabulary: practitioner, promotions, retail/voucher, stock, orders. Safe, but noisy if the client probes them.
9. **`test_main.py:166` mypy error** — pre-existing on `main`, not introduced here.
10. **The handoff and the vocabulary name the client.** The repo is private, so this is not a public leak, but it is inconsistent with `test_no_client_name.py`'s intent. The *code* is clean — only this document and the demo config carry the name. Revisit before the repo is ever made public.

---

## 8. Demo-day traps

1. **Same-day is dropped**, and the demo runs 15:00–17:00 with the 15:00 hour held out as a break. If the client asks "احجزيلي النهاردة" Nada will not offer anything. **Script around Wed 2 September.**
2. **`Transcriber` is unimplemented.** A voice note produces no text. Cut from the demo — do not let it be tried live.
3. **The booking journey does not exist yet.** Steps 5 and 6 are the schedule risk; everything before them is roughly a day each. If time is lost, the fallback is a narrower scripted journey, not a half-built booking path.
4. **`TENANT_TIMEZONE` defaults to `Asia/Dubai`** — an hour off Cairo, which shifts the night-window emergency trigger.

---

## 9. After the demo

Deferred by explicit decision, not oversight. None of it is started.

| Area | Work |
|---|---|
| **Clinical sign-off** | Suitability block lists and the emergency copy in §6.1 are unsigned drafts. The clinic's medical lead must approve both before any real patient reaches the number. |
| **Salesforce** | Contact match/create, appointment object, duplicate prevention, daily per-branch Excel digest by email. The `imported_catalogue` adapter already defines the interface it must implement. |
| **Voice notes** | `Transcriber` is an empty Protocol. Needs a concrete ASR implementation, then inbound voice → transcript → normal receptionist path proven on the live number. |
| **Photos** | Secure intake and authorised staff review. Explicitly no automated diagnosis, and never a claim that a doctor reviewed an image before a real acknowledgement. |
| **Human ownership** | `bot_active` / `handoff_requested` / `human_owned` / `closed` are specified but not implemented. While a human owns a conversation, bot replies must stop — with one idempotent confirmation exception for a staff-created booking. |
| **Dialogue state & multi-intent** | Items 6 and 7 above, properly rather than worked around. |
| **Real client data** | Branch list, practitioner roster, promotions, retail and stock all replace demo fixtures. Five of the 14 branches are currently placeholders. |
| **SMS fallback** | Discussed for a later version; no provider selected. |
| **Second vertical** | The `SAFETY`-per-vertical and `IntentType`-union work makes dental additive rather than a rewrite. Untested until someone tries it. |

---

## 10. Verification commands

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

The project requires Python 3.13; the container default is 3.11.

---

## 11. Suggested opening prompt for the next session

> Read `docs/SESSION-HANDOFF-DERMACLUB-2026-08-27.md` and PR #33. This is demo-scope work for 1 September and only 3 of 10 steps are done — the booking journey does not exist yet.
>
> Do not re-litigate the decisions in §3, in particular decision 11: the emergency reply must never direct a patient to public emergency services.
>
> Continue at Step 3: clinic domain schemas (Branch, Service, AvailabilitySlot, Booking, BookingReference), tenant-scoped with RLS, as migration 008 chained after 007. Verify the deployed alembic head on Render first and apply 007 — it is unapplied. Then Step 4, the workbook importer, applying the three deviations in §5. Steps 5 and 6 are the schedule risk; get to them early.


---

## 12. Steps 3 and 4 — implemented 28 August

Branch `claude/review-handoff-gug2om`. Nothing in §3's decisions was reopened.

### Step 3 — schemas and migration 008

`clinic_branches`, `clinic_services`, `clinic_availability_slots`, `clinic_bookings`, tenant-scoped
with RLS enabled, forced and policied exactly as 004/005/006 do it, chained after 007. Domain
objects in `apps/api/core/clinic.py`; ORM rows in `apps/api/db/models.py`.

Three schema decisions worth knowing before step 6 builds on them:

- **`clinic_bookings` carries three uniqueness constraints.** One appointment per `(tenant, slot)`;
  one `(tenant, reference)`; one `(tenant, idempotency_key)`, the key built from tenant,
  conversation and slot by `core.clinic.booking_idempotency_key`. Imported rows carry no key —
  NULLs do not collide — which is why the per-slot constraint is the one that actually prevents a
  double booking.
- **`held_until` / `held_by_conversation_id` are in 008 already**, unwritten, so step 6's
  `hold_slot` does not need a migration of its own.
- **`BookingReference` is a value object, not a table.** The prefix is a parameter: it is the
  clinic's initials and belongs with tenant configuration. Imported references are kept verbatim as
  the clinic wrote them; the import reports the highest serial already taken (`DC-0265`) so step 6
  issues after it.

Migration 008 was applied and rolled back on SQLite and rendered for Postgres (`--sql`, four
policies). Note that `alembic upgrade head` cannot run end-to-end on SQLite — migration 002 drops a
constraint, which SQLite has no ALTER for. That is pre-existing.

### Step 4 — the importer

Validation and every decision live in `apps/api/clinic/importer.py` (pure, tested);
`scripts/import_clinic_workbook.py` only reads the `.xlsx` with `openpyxl`, which stays an
operator's dependency, not the application's. `apps/api/db/clinic_repo.py` persists a plan.

The three deviations of §5, as implemented:

1. **The DT029 correction is conditional.** It fires only if the cell still reads the value the
   client confirmed was wrong; if the file already holds 15,000 it says so and changes nothing;
   if it holds some third value the import *fails* rather than overwriting a number nobody has
   looked at. The 26 August workbook is in the second state.
2. **The 15-minute buffer is not an import rule.** Back-to-back pairs are counted and reported,
   never rejected. The buffer is step 6's, for new bookings only.
3. **Ambiguous names.** A name reaching two service codes is an error the import refuses on; two
   services a patient cannot tell apart (same price, duration and quantity) are a warning naming
   both, because that fix is a catalogue decision.

Overlapping slots in one branch are an error, with `--allow-overlaps` if the clinic ever says it
runs two rooms at once. A re-import upserts on the clinic's own keys, deactivates (never deletes)
branches and services the workbook has dropped, and **never reopens a slot held by a booking this
system made** — the workbook is authoritative for the clinic's diary, not for appointments made
after it was exported.

### The 26 August workbook, through the real code path

    14 branches, 35 services, 672 slots, 265 bookings; 0 retail rows skipped;
    62 back-to-back pairs; 0 errors, 6 warnings
    slots by date: 31 Aug–3 Sep, 5–6 Sep, 112 each (Fri 4 Sep absent)
    slots by status: booked=265, open=407
    highest booking reference already taken: DC-0265

Every number in §5 reproduces. The six warnings are the look-alike service groups (three at
750/45min, three 12-session packages at 16,350, and three pairs) plus DT026, whose name states
unlimited sessions and so has no countable quantity to quote. Branch provenance reads 5 real
example, 4 given, 5 placeholder — the Read Me's "nine are placeholders" folds the four given in;
the flag follows the column's literal value and §3 decision 5.

The workbook itself is not committed on this branch (it arrives via PR #34, into `docs/`). The
tests use invented data and pin the client file's real header row.

### Verification

    727 passed, 2 skipped. Ruff clean. mypy clean apart from the pre-existing `test_main.py:166`.
    Coverage 96.4% (gate 95%).

### Still not done, and unchanged by this

Steps 5–10, the environment variables in §6, applying 007 and 008 on Render, and everything in §9.
No conversation path reads any of these tables yet: `check_availability`, `quote_price`,
`hold_slot` and `confirm_booking` remain unbuilt, and an unbuilt tool still hands off.

---

## 13. Steps 5, 6 and 7 — implemented 28 August

Branch `claude/review-handoff-gug2om`. Nothing in §3's decisions was reopened. **903 passed, 2
skipped. Ruff clean. mypy clean apart from the pre-existing `test_main.py:166`. Coverage 95.9%
(gate 95%).**

### 13.1 Step 5 — slot extraction, and a gap it uncovered

The model now emits `extracted_slots` alongside the label, and `apps/api/conversations/slots.py`
decides which keys are real and what their values mean. `<today>` on the tenant's clock is rendered
into the user turn so the model has a calendar to resolve against. Prompt version **v4**; the intent
catalogue now names each intent's slots, which was previously on the prompt's own "deliberately not
here" list — the docstring says why that reason does not reach slot *names*.

Three rules, and the third is the one that matters. Only slots the chosen intent declares. Only
values that are values (`null`, `unknown` and friends are absences several models write out). And a
date that cannot be pinned to one day is **dropped, never guessed** — a dropped date costs a
clarifying question, a wrong one books a patient into a day they never chose and confirms it.

**The gap found on the way.** Nothing could select the clinic vocabulary. Every runtime caller went
through `default_vocabulary()`, which reads one file — `intents.yaml`, the holiday-home vertical. The
clinic taxonomy was shipped, validated, client-reviewed and reachable by nothing: the classifier
described holiday-home intents to the model and `decide_autonomy` looked up ceilings in the wrong
file. **`TENANT_VERTICAL=clinics` is now mandatory for this deploy.** An unknown vertical is refused
at startup rather than falling back.

### 13.2 Step 6 — the booking journey

`check_availability`, `quote_price`, `hold_slot` and `confirm_booking`, against the imported
catalogue through a `ClinicDirectory` port. The write side never decides anything it can lose a race
on: `hold_slot` is a conditional `UPDATE` reading its own row count, and `confirm_booking` inserts
against the `(tenant, slot)` and `(tenant, idempotency_key)` constraints and treats the integrity
error as an answer. Two people messaging about the last 18:00 is not hypothetical on a demo where one
number is passed round a room.

The journey, in the shape the demo will run:

```
patient  عايزة أحجز فاشيال في المعادي بكرة
Nada     I can offer 11:00 / 16:00 / 18:00 …          ← from the diary, on this turn
patient  الساعة ٦
Nada     Just to confirm: … Wednesday 02 September, 18:00?   ← slot held while they answer
patient  تمام
Nada     That's booked. Your reference is DC-0266.    ← the appointment now exists
```

**The time is offered, never asked for.** "Which time would you like?" cannot keep the vocabulary's
*never offer a slot the scheduling system did not return*, so `requested_time` and `requested_date`
are now required slots on the booking and availability intents (vocabulary 2.2.0) and the ask turn
for the time is a real availability call.

### 13.3 The two things that made the journey unreachable

Neither was in the tools, and both would have been found on demo day.

**Nothing ever agreed to anything.** `Task.confirmed` was a set only ever *emptied* — `absorb`
discarded from it and nothing added. An intent declaring `confirm_before_acting` read a detail back,
was told "أيوه", and read it back again until `max_clarifying_turns` fetched a person. No task with a
confirmable slot could reach `execute` at all, so `confirm_booking` would have been unreachable even
once built. The read-back now covers everything outstanding in **one** message: four confirmations at
one per turn do not fit inside `max_clarifying_turns: 2`.

**And "تمام" ended the conversation.** Classified flat it is `thanks_closing` — right most of the
time, wrong by one word mid-booking: the task was abandoned and Nada said goodbye to somebody about
to have an appointment. This was §7 item 6, "the most likely live failure on demo day". A short reply
is now read against a read-back that is genuinely outstanding, *before* the classified intent may
switch tasks. Narrow on purpose: away from a pending read-back, "تمام" still closes a conversation.

### 13.4 New environment variables

| Variable | Value | Notes |
|---|---|---|
| `TENANT_VERTICAL` | `clinics` | **Mandatory.** Without it the clinic taxonomy is unreachable. |
| `TENANT_BOOKING_REFERENCE_PREFIX` | `DC` | The letters in `DC-0042`. Defaults to `WB`, which is wrong for this client. |
| `TENANT_AVAILABILITY_OFFER` | Arabic, `{times} {service} {branch} {date}` | Unset → neutral English. |
| `TENANT_AVAILABILITY_NONE` | Arabic, `{service} {branch} {date}` | |
| `TENANT_PRICE_QUOTE` | Arabic, `{service} {price} {currency} {sessions}` | **Must keep `{sessions}`** — see below. |
| `TENANT_CHOOSE_ONE` | Arabic, `{options}` | |
| `TENANT_BOOKING_TAKEN` | Arabic | Said when a slot goes between the offer and the yes. |

`TENANT_PRICE_QUOTE` is the one with a rule rather than a preference attached. `quoting.always_state`
requires the currency, the session count and the package scope; a template that drops `{sessions}`
turns "15,000 EGP for six" into a number meaning a fifth as much treatment. **These five are the only
copy still to be written in Arabic** — everything else was drafted in the previous session (§6.2).

### 13.5 ⚠️ No Arabic name resolves yet — the biggest remaining demo risk

The workbook is entirely in English: branches are `Maadi`, `New Cairo`, `Nasr City`; services are
`Basic Facial`, `Primelase Laser Package - 6 Sessions`. There is **no aliases column**. Nothing in
the code transliterates or translates — deliberately, because a guess about a place name or a
treatment name in shared source is a guess nobody clinical has read. The consequence is concrete:

> "عايزة أحجز فاشيال في المعادي بكرة" resolves to **no service and no branch** today.

The mechanism is complete on both sides. `Service.aliases` already existed; `Branch.aliases` was
added (folded into migration 008, which is unapplied, rather than a 009 for one column on a table no
deployment has). The importer reads an `Aliases` / `Alias` / `Arabic` column on both sheets.

**What is needed:** two columns in the client's own workbook, then a re-import. Putting them there
rather than in a file in this repository is deliberate — it is the clinic's vocabulary for its own
treatments, and decision 2 already makes the workbook the source of truth. A starter list should be
drafted and sent for review, not invented and shipped.

### 13.6 Step 7 — the clinical screening gate

`apps/api/core/screening.py`, driven by a new `screening:` block in the clinic vocabulary (2.3.0).
Two halves:

* **`screened_categories: [Injectables, Skin]`** — a clinician approves these whatever the patient
  says. Filler is a medical procedure; a receptionist taking that booking unsupervised is the
  clinic's licence, not a UX preference. **Laser is deliberately not gated by category** — routine
  hair-removal booking is what the demo is, and its contraindications are disclosures rather than
  properties of the treatment.
* **11 disclosure triggers** — pregnancy, breastfeeding, isotretinoin, anticoagulants, active
  infection, cancer treatment, autoimmune conditions, implanted devices, epilepsy, anaesthetic
  allergy, keloid scarring. Matched on the personal-report form, the same principle the emergency
  triggers use: "أنا حامل" is a disclosure and "الليزر ينفع للحوامل؟" is a question about the world.

A block hands off and **says nothing clinical** — no reassurance, no explanation, no follow-up
question. Naming the disclosure back at the patient is the receptionist stating a clinical fact about
them, and asking a follow-up implies the answer would change the outcome. Both are the
medical-history interview `clinical_question` forbids. What the patient already told us is kept, so
whoever takes over does not start from nothing.

The gate runs on **every turn** of a booking, not once at the start: a patient thinks of the thing
while answering a question about something else, which is why the test that matters is the disclosure
arriving on the turn that would otherwise have confirmed the appointment.

> ⚠️ **The contents are an unsigned clinical draft written by an engineer**, in the same state as the
> emergency reply (§6.1) and the suitability block lists. Fit for a demo; the clinic's medical lead
> must approve the category list and every trigger before a real patient reaches the number. A term
> that is not in the list is a term nobody has written down, **not** a term somebody has cleared.
> What is *structural* — that a match ends autonomy — is not a draft.

### 13.7 Two decisions taken here that the client has not confirmed

1. **The 15-minute buffer is checked against bookings *this system made*, never against the imported
   diary.** Decisions 2 and 3 pull in opposite directions: the workbook is authoritative for the
   diary including its 62 back-to-back pairs, and the buffer constrains new bookings. Applied against
   the imported rows as well it would refuse most of the 407 open slots — in an hourly grid nearly
   every one is adjacent to a booked one — and the clinic would be told its own diary is invalid.
   This is the only reading that leaves the demo bookable. **Put it to the client.**
2. **`Injectables` and `Skin` are the screened categories, and `Laser` is not.** Reasoned above and
   defensible, and still a clinical judgement made by an engineer. **Put it to the medical lead**
   alongside the trigger list.

### 13.8 Also changed

* **Slots are stored and queried in UTC.** SQLite compares the wall clock as text and Postgres
  compares instants, and only one of those puts a 00:30 Cairo slot on the right day. The divergence
  was removed rather than tested around.
* `packages/intents/compile.py` now compiles the vertical vocabularies too, so a deployed process
  loads `clinics` from JSON a build proved valid rather than parsing YAML in the process that has to
  answer a patient.
* A typing regression in `test_clinic_importer.py` from the step 3–4 commit is fixed; mypy is back to
  the single pre-existing error.

### 13.9 Still not done

Steps 8–10, everything in §9, the environment variables above, applying 007 and 008 on Render, and
the Arabic aliases in §13.5. Multi-intent decomposition (§7 item 7) is still unbuilt: a message asking
price *and* nearest branch still gets one answered.
