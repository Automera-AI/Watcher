# Session handoff — DermaClub, steps 8–10

**Date:** 28 August 2026
**Branch:** `claude/dermaclub-booking-completion-vaivp2`
**Previous handoffs:** `SESSION-HANDOFF-DERMACLUB-2026-08-27.md` (steps 0–4, decisions 1–11) and
the 28 August one (steps 5–7, decisions 12–14). Both remain the reference for everything they
cover; nothing in either is reopened here.
**Demo:** Tuesday 1 September 2026, 15:00–17:00 Africa/Cairo
**Tests:** 959 passed, 2 skipped. Ruff clean. **mypy clean** — including `test_main.py:166`, which
no longer reports. Coverage 96.2% (gate 95%).

---

> ## ⚠️ Read this first
>
> **The demo script in both previous handoffs cannot run.** The workbook holds **exactly one slot
> per service, branch and day** — 672 slots, 14 branches, 8 services a day each — so
> `check_availability` never offers a choice. "I can offer 11:00 / 16:00 / 18:00" is a unit-test
> fixture. A facial at Maadi on Wednesday 2 September is **19:00**, so the patient's second line is
> `الساعة ٧`, not `الساعة ٦`. §5 has what is open where.
>
> **Two defects were found by running the journey end to end, and both are fixed.** A patient was
> handed to a person on the very next question after a successful booking, and the two turns at the
> centre of the booking were composed in English with no configuration behind them.
>
> **Steps 8 and 9 are done. Step 10 is prepared but unexecuted** — it needs Render, the database
> and the live number, none of which a repository session can reach. `DERMACLUB-DEPLOY-RUNBOOK-2026-09-01.md`
> is the runbook, and every command in it was rehearsed locally first.

---

## 1. Status

| Step | Scope | Status |
|---|---|---|
| 0–7 | Through the clinical screening gate | ✅ done (previous handoffs) |
| — | Arabic aliases | ✅ **drafted, reviewed by the clinic, folded in** — still not imported (decision 12) |
| 8 | Client pack | ✅ **done** |
| 9 | Journey evals | ✅ **done** |
| 10 | Deploy + rehearse | ⚠️ **runbook written and locally rehearsed; nothing applied on Render** |

### Commits

| SHA | Summary |
|---|---|
| `5801ec7` | The Arabic alias draft, the review page, and `check_alias_resolution.py` |
| `2a1f521` | Journey evals; the clarifying-turn budget fix they found |
| `be06e29` | The read-back and confirmation are the tenant's words; the price-template check |
| — | This handoff, the client pack and the deploy runbook |

---

## 2. The Arabic aliases — drafted, reviewed, folded in

`docs/dermaclub-aliases-draft.csv` is the data; `DERMACLUB-ARABIC-ALIASES-DRAFT-2026-08-28.md` is
the review document; `DermaClub_Aliases_DRAFT_2026-08-28.xlsx` is the two columns ready to paste
into the client's own workbook. **Nothing is imported and the client's file is untouched** —
decision 12 puts this data in their workbook, not in this repository.

132 aliases over 48 rows. Measured with `scripts/check_alias_resolution.py` over the demo's own
phrases:

```
today                    23 phrases: 2 resolved, 0 ask, 21 reach nothing
with the reviewed list   23 phrases: 22 resolved, 1 asks, 0 reach nothing
```

The one that asks is a bare `ليزر`, which reaches nine packages. That is the right answer there.

### 2.1 The clinic's review, 28 August

Five comment threads on the review page, all on services that were blocked or flagged. All applied.

* **DT020 / DT023** (Q1, the two 12-session half-body packages at 17,800 and 14,300). The names the
  clinic gave say the same thing for both, and one phrase — `ليزر 12 جلسة نص الجسم` — was given to
  each of them, which an import refuses. **The client settled it by dropping DT020: it carries no
  Arabic name at all**, and its row stays in the list so the decision is visible rather than
  reading as an omission. Every Arabic half-body phrasing now reaches DT023 outright, with no
  clarifying question; DT020 still answers to its English catalogue name and stays bookable.
* **DT024 / DT027** (Q2, the two 4-session maintenance packages) — answered cleanly. Every name the
  clinic gave DT027 says full body; none of DT024's do. Both resolve outright.
* **DT006 Skin Boosters** (Q3a) — `حقن ترطيب ونضارة البشرة` and `معززات البشرة`, kept alongside the
  transliterations because patients type both.

**Still open: DT013, the Biostimulator.** It carries a transliteration and nothing else, so a
patient using the clinic's word for it reaches nothing. The two body-shaping pairs (DT032/DT035,
DT033/DT034) drew no comment and keep the draft's one-distinguishing-word approach.

`scripts/check_alias_resolution.py` is the tool to run on whatever the client sends back. It
answers the question the importer does not: a catalogue can import perfectly and still leave a
patient's words reaching nothing, because resolution happens a turn later, in front of them. It
caught one real problem in the draft — two spellings of `أبريلين` that fold to the same string,
which the importer counts as a name reaching one row twice and refuses the file for.

---

## 3. Step 8 — the client pack

`docs/DERMACLUB-CLIENT-PACK-2026-09-01.md`, and a published page for the client to read.

Six things Nada does, four she refuses, the eleven disclosures, the data behind the demo, the five
things we need back from the clinic, and what the demo is *not*. Written for people who are not
engineers, and it says out loud that the screening lists and the emergency copy are an engineer's
draft awaiting their medical lead.

---

## 4. Step 9 — journey evals, and the two defects they found

`packages/eval/journeys.py`, ten journeys in `golden/clinics_journeys.jsonl`, run by
`python -m packages.eval --journeys … --diary …`.

The classifier eval scores one message at a time. Every failure the booking journey is about
happens *between* messages, so this runs whole conversations through the real receptionist and the
real tools and scores what the patient would have received on each turn.

**It runs against the client's own diary**, not an invented one: `fixtures/clinic_diary.json` is
two branches of one day cut out of the committed workbook by the importer, and
`test_clinic_workbook_integration.py` fails if the two drift apart. That choice is the point, and
it paid immediately — the one-slot-per-service-branch-day fact at the top of this document is what
it found first.

Ten journeys: the booking end to end, a treatment whose one slot is taken, a pregnancy disclosure
on the turn that would have confirmed, an injectables booking that must reach a clinician, a slot
taken between the read-back and the yes, `تمام` away from a read-back, a price that must carry its
session count, look-alike packages that must be asked about, the question after the booking, and
one declared `known_gap`.

### 4.1 The clarifying-turn budget counted finished work

`SqlAlchemyConversationStore.begin` fell back to counting *every* reply in the conversation when no
task was in flight. A booking costs three replies, `max_clarifying_turns` is 2, so the patient's
next question — anything needing a clarifying turn — was handed to a person immediately, on a
conversation that had just gone perfectly. One number in a room full of people would have hit this
within ten minutes.

Fixed: with nothing in flight, nothing has been spent. Pinned by a test on the store itself and by
the journey that found it.

### 4.2 The declared gap

`the_closing_quotes_the_reference`. The booking reference lives on the task, and the task is
finished by the time the patient says thank you, so `closing_booking_confirmed` — the client's own
"تم تأكيد حجزك ✅ رقم الحجز: …" — renders its generic form instead. Reaching it means putting the
reference on the *conversation*, which is a change to the conversation store and its port, and not
demo-week work.

It runs, it is reported, it does not fail the build, and the runner says so if it ever starts
passing. **The booking turn itself does quote the reference** (§5) — this is only the goodbye
after it.

---

## 5. Step 10 — prepared, not executed

`docs/DERMACLUB-DEPLOY-RUNBOOK-2026-09-01.md`. Merge and deploy, apply 007 then 008, set the
environment, import the workbook, verify, rehearse. Everything that needs Render, the database or
the live number is **not done and cannot be done from here**.

What *was* done: the whole path rehearsed locally — the real workbook imported through the real
`SqlAlchemyClinicRepository` into a throwaway database, and the booking conversation run through
the real receptionist against it, with the drafted aliases and the drafted Arabic copy in place.

```
patient  عايزة أحجز فاشيال في المعادي بكرة
Nada     متاح عندنا 19:00 لـ Facial في فرع Maadi يوم Wednesday 02 September. تحبي أحجزلك إمتى؟
patient  الساعة ٧
Nada     تأكيد الحجز: فاشيال، المعادي، Wednesday 02 September، 19:00 — صح كده؟
patient  تمام
Nada     تم الحجز ✅ رقم الحجز: DC-0266. مستنينك في الفرع.
```

### 5.1 What is open on Wednesday 2 September

| Branch | Open |
|---|---|
| Maadi | 11:00 Primelase single · 13:00 Primelase 12 · 16:00 Body Shaping · 18:00 Basic Facial · **19:00 Facial** |
| New Cairo | 12:00 Peeling · 16:00 Botox · 17:00 Botox + Lip Booster · 18:00 Filler · 19:00 Filler 1 Syringe |
| Nasr City | 12:00 Cool Shaping · 13:00 CoolShape · 16:00 Basic Facial · **17:00 Facial** · 18:00 Medical Facial + Dermapen · 19:00 Peeling |

### 5.2 Two more environment variables than the last handoff listed

The previous handoff's "these five templates are the only conversation copy still to be written"
counted the tools and missed the receptionist. `TENANT_CONFIRM_READ_BACK` and
`TENANT_BOOKING_CONFIRMED` are new, and without them two of the booking's three turns come out in
English. All seven values, drafted and rendered through the real tools, are in §3.2 of the runbook.

Three rules attached to them, all discovered by rendering rather than by reading:

* **`{price}` already carries the currency** — a template using `{currency}` too prints it twice.
* **`{sessions}` renders an English phrase** and cannot go in an Arabic sentence; `{session_count}`
  is the bare number, now passed alongside it. A price template must carry one of the two, and the
  process **refuses to start** otherwise — one Primelase session is 3,100 and six are 15,000.
* **`{values}`, not `{details}`, in the read-back.** `{details}` carries English slot labels.

---

## 6. What the client will still see in English

Not a bug, not fixed by the aliases, and worth saying before they ask:

1. **Treatment and branch names** — `Facial`, `Maadi` — because that is what the workbook holds.
   Aliases make a patient's Arabic *resolve*; they do not translate the reply. A display-name
   column would.
2. **Dates** — `Wednesday 02 September`, formatted in English with no per-language setting behind
   it. Proper localisation, after the demo.

The read-back reads better than the offer because it echoes the patient's own words rather than the
catalogue's.

---

## 7. Open items

### Before the demo — required

1. **Step 10 itself.** Migrations 007 and 008, the environment, the import, the rehearsal.
2. **The aliases into the client's workbook.** The list is reviewed (§2.1); it still has to go into
   their file and be re-imported. Nothing Arabic resolves until then; the English names still work.
   One question is open — what they call a Biostimulator (DT013).
3. **The medical lead's approval** of the screening categories, the eleven disclosures and the
   emergency wording. Unchanged, and still unsigned.
4. **Re-script the demo around 19:00** (or Nasr City's 17:00). See the warning at the top.

### Before the demo — strongly advised

5. **DT026 quotes as one session.** "Laser Hair Removal Super Annual Unlimited Sessions" is 18,700
   EGP and its session count imports as 1, because the number is only in the name and there is no
   number in "Unlimited". It therefore quotes as one session — conservative and wrong, in the one
   way `quoting.always_state` is about. The fix is a catalogue decision (what should an unlimited
   package quote as?), not code. Do not probe it on the day.
6. **Multi-intent decomposition** and **the general dialogue-state rule** are still unbuilt,
   unchanged from the previous handoff.

### After the demo

Unchanged, plus:

7. **The booking reference on the conversation**, which closes the declared journey gap (§4.2).
8. **Display names and date localisation** (§6).
9. **Regenerate `packages/eval/fixtures/clinic_diary.json`** whenever the client sends a new
   workbook — the integration test fails if it goes stale, but only where `openpyxl` is installed.

---

## 8. Verification

```bash
uv venv --python 3.13 .venv && uv pip install -e ".[dev]"
uv pip install ruff==0.6.9 mypy==1.11.2 types-PyYAML==6.0.12.20240917 openpyxl

.venv/bin/python -m pytest apps/api/tests packages     # 959 passed, 2 skipped
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/mypy                                          # clean

.venv/bin/python -m packages.eval \
  --journeys packages/eval/golden/clinics_journeys.jsonl \
  --diary    packages/eval/fixtures/clinic_diary.json    # journeys 9/9, 1 known gap

.venv/bin/python scripts/check_alias_resolution.py \
  docs/DermaClub_Availability_DEMO_2026-08-26_1.xlsx --timezone Africa/Cairo \
  --draft docs/dermaclub-aliases-draft.csv               # 17 resolved, 1 asks, 0 nothing
```

`openpyxl` is an operator dependency: with it, the workbook integration tests run; without it they
skip, as they do in CI.

---

## 9. Suggested opening prompt for the next session

> Read `docs/SESSION-HANDOFF-DERMACLUB-2026-08-28-STEPS-8-10.md` and the two handoffs it points at.
> Steps 0–9 are done; step 10 is written up in `docs/DERMACLUB-DEPLOY-RUNBOOK-2026-09-01.md` and
> unexecuted because it needs Render and the live number.
>
> Do not re-litigate decision 11 (the emergency reply never directs a patient to public emergency
> services) or decision 13 (the 15-minute buffer applies only to bookings this system made).
>
> Work the runbook in order with Render access: merge and deploy, apply 007 then 008, set the
> environment on **both** services, import the workbook, then run the two off-line checks in §5.1
> and the live script in §6. Remember the diary holds one slot per service, branch and day — book
> the facial at Maadi at **19:00**, not 18:00.
