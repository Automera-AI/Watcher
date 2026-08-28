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
> **The booking journey does not exist yet.** Nada can greet, close, answer from the knowledge base and route clinical questions to a person. She *cannot* check availability, quote a price from the catalogue, hold a slot, or create an appointment. Steps 3–6 below are the whole transactional core; as of 28 August the catalogue and diary are in the database (steps 3–4, §12) and nothing in a conversation reads them yet.
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
| 5 | Slot extraction | ⬜ **not started** — next; blocks everything multi-turn |
| 6 | Booking tools + atomic confirm | ⬜ **not started** — the demo's core |
| 7 | Clinical screening gate | ⬜ **not started** |
| 8 | Client pack | ⬜ **not started** |
| 9 | Journey evals | ⬜ **not started** |
| 10 | Deploy + rehearse | ⬜ **not started** |

**5 of 10 steps complete.** Steps 5 and 6 are the remaining transactional behaviour: nothing yet
reads the imported catalogue in a conversation.

### Commits

| SHA | Summary |
|---|---|
| `9451ab7` | Clinics vertical; stop escalating greetings; remove false success |
| `52a1a7f` | Clinic vocabulary v2.1; emergency recall; tenant urgent contact |
| `eacdb6a` | Tenant conversation copy |
| `22cf9db` | Tenant owns emergency wording; clinic routes to its doctor |
| `24cd98c` | This handoff |

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
2. **Steps 5–6** — slot extraction, then booking tools reading the catalogue steps 3–4 now persist. The idempotency key on (tenant, conversation, slot) has its column and its uniqueness constraint already (§12); what does not exist is anything that writes one. **This is what is left of the demo's transactional core.**
3. **`worker.py:410` still passes `{}`** for extracted slots. Nothing multi-turn works until Step 5.
4. **Set the environment variables in §6** on both Render services.
5. **Steps 7–10** — screening gate, client pack, journey evals, rehearsal on the live number.

### Before the demo — strongly advised

6. **Dialogue-state rule is unimplemented.** The vocabulary header specifies that short replies ("تمام", "أيوه", "لا") are resolved against an active pending question *before* the flat vocabulary is consulted. Not expressible in YAML; needs runtime work. **"تمام" meaning *yes, book it* versus *thanks, goodbye* is the most likely live failure on demo day.**
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
