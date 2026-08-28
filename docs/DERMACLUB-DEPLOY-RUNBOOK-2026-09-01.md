# Deploy and rehearsal runbook — DermaClub demo, Tuesday 1 September 2026

**Step 10 of the demo plan.** Everything here needs credentials this repository does not hold —
Render, the demo database, the WhatsApp number — so it is written to be executed by an operator who
has them, in order, with the check that proves each step landed.

Every command was run locally against the committed workbook and a throwaway database before it was
written down. What has **not** been done, and cannot be from a repository session, is anything that
touches Render or the live number: §2, §3, §4 and §6 are unexecuted.

---

## 0. What is already true

| | |
|---|---|
| Code | On `claude/dermaclub-booking-completion-vaivp2`. 959 tests pass, ruff and mypy clean, coverage 96.2% |
| Migrations | **007 and 008 are both unapplied.** 006 is the deployed head |
| Environment | None of §3 is set on either service |
| Catalogue | Nothing imported. The tables do not exist until 008 is applied |
| Arabic names | Drafted and sent for review (`docs/DERMACLUB-ARABIC-ALIASES-DRAFT-2026-08-28.md`), **not in the client's workbook** |

The order below is the dependency order. Migrations before the import (the tables have to exist),
`TENANT_VERTICAL` before anything is messaged (without it a patient is classified against
holiday-home intents), and the import before the rehearsal (nothing is bookable until the diary is
in the database).

---

## 1. Merge and deploy the code

Nothing below works against the deployed head as it stands: `TENANT_VERTICAL`,
the four booking tools, the screening gate and the two new copy variables all arrived after it.

```bash
git checkout claude/dermaclub-booking-completion-vaivp2 && git pull
# open the PR, merge, and let Render deploy both services (API and worker)
```

**Check:** Render shows the same commit live on `watcher-api` *and* `watcher-worker`. They are
separate services and the worker is the one that answers messages.

---

## 2. Apply migrations 007 and 008

`alembic upgrade head` from a shell with the Render `DATABASE_URL`:

```bash
DATABASE_URL='postgresql://…' alembic upgrade head
```

007 first, then 008, which is what `head` does. 008 creates `clinic_branches`,
`clinic_services`, `clinic_availability_slots` and `clinic_bookings` with RLS enabled, forced and
policied the way 004–006 do it, and carries `clinic_branches.aliases` and the slot hold columns —
there is no 009 to follow it.

**Check:**

```bash
DATABASE_URL='postgresql://…' alembic current   # → 008
```

> Do not try this on SQLite. `alembic upgrade head` cannot run end to end there — migration 002
> drops a constraint and SQLite has no `ALTER` for it. That is pre-existing and only affects local
> experiments; Postgres is fine.

---

## 3. Set the environment variables

**On both services.** The worker answers the messages; the API takes them in. A variable set on one
of them is a bug that only appears under load.

### 3.1 Mandatory — the deploy is wrong without these

| Variable | Value |
|---|---|
| `TENANT_VERTICAL` | `clinics` |
| `TENANT_TIMEZONE` | `Africa/Cairo` — the default is `Asia/Dubai`, an hour out |
| `TENANT_BOOKING_REFERENCE_PREFIX` | `DC` — the default `WB` would issue `WB-0266` |
| `TENANT_URGENT_CONTACT` | the demo number (in Render and the session thread; swap for the dermatologist after signature) |
| `TENANT_EMERGENCY_REPLY` | §6.1 of `SESSION-HANDOFF-DERMACLUB-2026-08-27.md`, verbatim |
| LLM API key | on **both** services |

### 3.2 The conversation, in the client's own words

The four from the previous session (§6.2 of the 27 August handoff) are unchanged:
`TENANT_GREETING_OPENING`, `TENANT_GREETING_OPENING_NAMED`, `TENANT_CLOSING`,
`TENANT_CLOSING_BOOKING_CONFIRMED`.

These seven are new. The five below the line were specified last session; the last two exist because
running the journey end to end showed the two turns at the centre of the booking still coming out in
English. **Every one of these was rendered through the real tools before being written down** — see
§5 for the transcript they produce.

```
TENANT_AVAILABILITY_OFFER=متاح عندنا {times} لـ {service} في فرع {branch} يوم {date}. تحبي أحجزلك إمتى؟

TENANT_AVAILABILITY_NONE=معلش، مفيش مواعيد فاضية لـ {service} في فرع {branch} يوم {date}. تحبي أشوفلك يوم تاني؟

TENANT_PRICE_QUOTE={service}: {price}. عدد الجلسات: {session_count}. استخدام الباقة خاضع لشروط العيادة المكتوبة.

TENANT_CHOOSE_ONE=تقصدي أنهي واحدة؟ {options}

TENANT_BOOKING_TAKEN=معلش، الميعاد ده اتحجز حالاً. تحبي أشوفلك المتاح؟

TENANT_CONFIRM_READ_BACK=تأكيد الحجز: {values} — صح كده؟

TENANT_BOOKING_CONFIRMED=تم الحجز ✅ رقم الحجز: {booking_reference}. مستنينك في الفرع.
```

Three things about these that are rules rather than preferences:

* **`{price}` already carries the currency.** It renders `15,000 EGP`. A template that also uses
  `{currency}` prints it twice.
* **`{session_count}`, not `{sessions}`.** `{sessions}` renders the English phrase "6 sessions",
  which cannot go inside an Arabic sentence; `{session_count}` is the bare number. A price template
  must carry one of the two and the process **refuses to start** without it — deliberately, because
  one Primelase session is 3,100 EGP and six are 15,000, and a quote that drops the count is wrong
  by a factor of five in the clinic's own voice.
* **`{values}`, not `{details}`, in the read-back.** `{details}` includes English slot labels
  ("service …, branch …"); `{values}` is the same list without them.

**Check:** the service starts. A price template missing its quantity raises `ConfigError` naming
the variable at boot rather than mis-quoting a patient later.

---

## 4. Import the workbook

The catalogue and the diary come into existence here, and not before.

```bash
pip install openpyxl    # operator dependency; nothing shipped imports it

# read it, validate it, write nothing
python scripts/import_clinic_workbook.py \
  docs/DermaClub_Availability_DEMO_2026-08-26_1.xlsx --timezone Africa/Cairo

# …then, once the report is clean
DATABASE_URL='postgresql://…' python scripts/import_clinic_workbook.py \
  docs/DermaClub_Availability_DEMO_2026-08-26_1.xlsx \
  --timezone Africa/Cairo --tenant <TENANT-UUID> --apply
```

**Check** — the dry run prints exactly this, and the numbers are the ones the client signed off:

```
14 branches, 35 services, 672 slots, 265 bookings; 0 retail rows skipped;
62 back-to-back pairs; 0 errors, 6 warnings
slots by status: booked=265, open=407
highest booking reference already taken: DC-0265
```

`--timezone` is required and there is no default: the sheet holds a wall clock and names no zone.
A week of appointments an hour out is not something a demo notices until it is quoting times.

### 4.1 When the aliases come back

The clinic adds an `Aliases` column to the Branches and Services sheets and returns the workbook.
Then, before re-importing:

```bash
python scripts/check_alias_resolution.py <their-workbook>.xlsx --timezone Africa/Cairo
```

It refuses a catalogue whose names collide (which is what the import would do, later and less
helpfully) and prints what each phrase a patient types actually reaches. Re-import with `--apply`
afterwards: a second import upserts on the clinic's own keys and **never reopens a slot a booking
made by this system holds**.

---

## 5. Rehearse before the demo

### 5.1 Off the live number

Both of these run in a checkout, need no credentials, and take seconds:

```bash
# the booking conversation, turn by turn, against the client's own diary
python -m packages.eval \
  --journeys packages/eval/golden/clinics_journeys.jsonl \
  --diary    packages/eval/fixtures/clinic_diary.json

# what a patient's Arabic actually reaches in the catalogue
python scripts/check_alias_resolution.py \
  docs/DermaClub_Availability_DEMO_2026-08-26_1.xlsx --timezone Africa/Cairo \
  --draft docs/dermaclub-aliases-draft.csv
```

Expect `journeys: 9/9`, one declared known gap, and `17 resolved / 1 clarifying question / 0
reaching nothing`.

### 5.2 The transcript these settings produce

Run locally against the whole workbook through the real repository and the real receptionist:

```
patient  عايزة أحجز فاشيال في المعادي بكرة
Nada     متاح عندنا 19:00 لـ Facial في فرع Maadi يوم Wednesday 02 September. تحبي أحجزلك إمتى؟
patient  الساعة ٧
Nada     تأكيد الحجز: فاشيال، المعادي، Wednesday 02 September، 19:00 — صح كده؟
patient  تمام
Nada     تم الحجز ✅ رقم الحجز: DC-0266. مستنينك في الفرع.
```

**Two things in that transcript are still English, and the client will notice both.**

1. **The treatment and branch names** — `Facial`, `Maadi` — because they are what the workbook
   holds. Aliases make Arabic *resolve*; they do not make the reply Arabic. Note that the read-back
   echoes the patient's own words instead, which is why it reads better than the offer.
2. **The date** — `Wednesday 02 September`. Formatted in English by the date formatter, with no
   per-language setting behind it today.

Neither is a bug and neither is fixed by the alias column. If the client wants them in Arabic that
is a display-name column in the workbook and a locale for dates, both after the demo. **Say so
before they ask.**

---

## 6. On the live number

Only after §1–§4. Send from a phone that is not the one running the demo.

1. `السلام عليكم` → the Arabic greeting, no escalation.
2. The booking above, **against Wednesday 2 September**.
3. `جلسة الليزر بكام؟` → a quote carrying its session count.
4. `أنا حامل، ينفع أعمل ليزر؟` → a hand-off that says nothing clinical.
5. From a second phone, race the first for the same slot. One books; the other is told.

**Check:** a `clinic_bookings` row with source `bot`, reference `DC-0266`, and the slot it took now
`booked`.

### 6.1 Book against Wednesday 2 September, and know which time you are being offered

The workbook holds **exactly one slot per service, branch and day** — 672 slots, 14 branches, 8
services a day each. So availability never offers a choice: it offers the one open time, or says
there is nothing. The "11:00 / 16:00 / 18:00" in both session handoffs is a unit-test fixture and
not this diary.

What is actually open on **Wed 2 September**:

| Branch | Open |
|---|---|
| Maadi | 11:00 Primelase single · 13:00 Primelase 12 · 16:00 Body Shaping · 18:00 Basic Facial · **19:00 Facial** |
| New Cairo | 12:00 Peeling · 16:00 Botox · 17:00 Botox + Lip Booster · 18:00 Filler · 19:00 Filler 1 Syringe |
| Nasr City | 12:00 Cool Shaping · 13:00 CoolShape · 16:00 Basic Facial · **17:00 Facial** · 18:00 Medical Facial + Dermapen · 19:00 Peeling |

So `فاشيال` at Maadi is **19:00** and the patient's next line is `الساعة ٧`. At Nasr City it is
17:00 and the line is `الساعة ٥`. Asking for a time that is not the open one is not a failure —
Nada re-offers what is free — but it costs a turn in front of the client.

### 6.2 Do not do these on the day

* **Do not send a voice note.** Speech-to-text is unbuilt; it produces nothing.
* **Do not ask for today.** Same-day is dropped by agreement, and a weekday name resolves to the
  *next* occurrence, never today.
* **Do not probe the unlimited laser package** (DT026, 18,700 EGP). Its session count imports as 1
  because the number is only in its name, so it quotes as "one session" — conservative and wrong.
  A catalogue fix, not a code one.
* **Do not probe practitioner rosters, promotions, retail, stock or order status.** Five intents
  with no data behind them. Safe — they hand off — but flat.

---

## 7. If something is wrong on the day

| Symptom | Cause | Fix |
|---|---|---|
| Every booking hands off | `TENANT_VERTICAL` unset, or set on one service only | Set it on both; redeploy |
| Times an hour out | `TENANT_TIMEZONE` still `Asia/Dubai` | Set `Africa/Cairo` |
| `WB-0266` instead of `DC-0266` | `TENANT_BOOKING_REFERENCE_PREFIX` unset | Set `DC` |
| Nada asks which treatment, endlessly | The patient's word reaches several catalogue rows | Use the full name; §4.1 shows which words are ambiguous |
| Arabic reaches no treatment or branch | Aliases not imported | English service and branch names still work |
| A booking says a slot is taken | It is | Offer another day; the diary is honest |
| Worker silent, API healthy | LLM key set on the API only | Set it on the worker; restart |

**Rollback** is the previous Render deploy. The migrations do not need reverting — 007 and 008 only
add tables and columns nothing older reads.
