# Session handoff — DermaClub, steps 8–10

**Date:** 28 August 2026
**Branch:** `claude/dermaclub-booking-completion-vaivp2` — pushed, head `dddda38`, 11 commits,
0 behind `origin/main`, no conflicts expected.
**Demo:** Tuesday 1 September 2026, 15:00–17:00 Africa/Cairo
**Previous handoffs:** `SESSION-HANDOFF-DERMACLUB-2026-08-27.md` (steps 0–4, decisions 1–11) and
the 28 August one (steps 5–7, decisions 12–14). Both remain the reference for what they cover.
Nothing in either is reopened here; two things in both are **corrected** — see §7.1 and §7.7.

**Verification**

| | Local | CI's own pinned environment |
|---|---|---|
| pytest | 965 passed, 2 skipped | 956 passed, 11 skipped |
| coverage (gate 95%) | 96.3% | 96.0% |
| ruff check / format | clean | clean |
| mypy strict | clean | clean |
| classifier eval gate | — | PASSED (98.0% vs baseline, on pydantic alone) |

The 11 CI skips are the workbook tests, which skip without `openpyxl` by design. `test_main.py:166`,
reported as a standing mypy error in both previous handoffs, no longer reports.

---

> ## ⚠️ Read this first
>
> **1. The demo was broken at its second message, and only a live model showed it.** `الساعة ٧`
> classifies as `unclear` — 0.25 on Haiku and **0.3 after escalating to Sonnet 5**, which is more
> certain of it, not less. That abandoned the booking and fetched a person one turn after the
> patient was offered a time. Fixed (§7.3). Running the journeys on recorded classifications took
> them from **5/9 to 9/9**.
>
> **2. The demo script in both previous handoffs cannot run.** The workbook holds **exactly one
> slot per service, branch and day**, so availability never offers a choice. "I can offer 11:00 /
> 16:00 / 18:00" is a unit-test fixture. A facial at Maadi on Wednesday 2 September is **19:00**,
> so the patient's second line is `الساعة ٧`, not `الساعة ٦`. §9.1 has what is open where.
>
> **3. One blocker remains and it is not code.** Migrations 007/008 are unapplied, no environment
> variable is set on either Render service, and nothing is imported. Until §3 is worked, merging
> this branch deploys code that is inert for this client.

---

## 1. Status

| Step | Scope | Status |
|---|---|---|
| 0–7 | Through the clinical screening gate | ✅ done (previous handoffs) |
| — | Arabic aliases | ✅ **reviewed, confirmed, and in the workbook** |
| 8 | Client pack | ✅ **done** |
| 9 | Journey evals | ✅ **done**, and run against a live model |
| 10 | Deploy + rehearse | ⛔ **runbook written and locally rehearsed; nothing applied on Render** |

### Commits

| SHA | Summary |
|---|---|
| `5801ec7` | The Arabic alias draft, the review page, `check_alias_resolution.py` |
| `2a1f521` | Journey evals; the clarifying-turn budget fix they found |
| `be06e29` | The read-back and confirmation become tenant copy; the price-template startup check |
| `c7865fe` | Client pack, deploy runbook, this handoff |
| `65ebeaf` | The clinic's alias review folded in |
| `708b735` | DT020 left unwired, by decision |
| `0c3e7dd` | The Biostimulator's own name |
| `6d4d2b6` | Keep the CI classifier gate off the application's dependencies |
| `a66f1e5` | The confirmed Arabic names go into the demo workbook |
| `af51cef` | **The demo's second turn** — three fixes found by real classifications |
| `dddda38` | This document and the runbook, updated for the live-model run |

---

## 2. Blockers

**One, and it is access rather than engineering.**

| Blocker | What it stops | What unblocks it |
|---|---|---|
| **No Render / database / WhatsApp access from a repository session** | Migrations 007 + 008, every environment variable, the workbook import, the live rehearsal — all of step 10 | Credentials for an operator, or for a session that can reach Render. `docs/DERMACLUB-DEPLOY-RUNBOOK-2026-09-01.md` has every command with its check; roughly 30 minutes |

Nothing else blocks. Everything the clinic owed has been answered, and every named question on the
alias list is closed.

---

## 3. Next steps, in order

Dependency order. Do not reorder 1–4.

1. **Merge this branch and let Render deploy both services.** The API takes messages in; the
   worker answers them. `TENANT_VERTICAL`, the four booking tools, the screening gate and the copy
   variables all arrived after the deployed head.
2. **Apply migrations 007 then 008** — `alembic upgrade head` against the Render `DATABASE_URL`.
   Check `alembic current` reports 008.
3. **Set the environment on *both* services** — runbook §3. `TENANT_VERTICAL=clinics` above all:
   without it a patient asking to book is classified against holiday-home intents.
4. **Import the workbook** — runbook §4. The dry run must print
   `14 branches, 35 services, 672 slots, 265 bookings … 0 errors, 6 warnings`.
5. **Rehearse off the number** — the commands in §10. Seconds, no credentials.
6. **Rehearse on the number** — runbook §6, scripted against Wednesday 2 September.
7. **Re-script the demo around 19:00** (Maadi) or 17:00 (Nasr City). See §9.1.

---

## 4. What the live model changed — the run that mattered

An API key arrived late in the session and bought a measurement nobody had made.

**The clinic taxonomy had never been run against a live model.** The CI gate replays the
*holiday-home* set recorded under an older prompt; the 18 clinic cases deliberately carried no
recordings. Recorded now — `fixtures/recorded_clinics_haiku.jsonl`, prompt **v5**,
`claude-haiku-4-5`:

```
18 examples · overall intent accuracy 100.0% · Brier 0.0145
ar 100%   en 100%   mixed 100%
```

The classifier is not the problem. **The demo's own turns were.** Recorded and replayed through the
journeys they scored **5/9 journeys, 10/17 turns**. Three defects, all fixed and pinned — §7.3,
§7.4, §7.5. On recorded classifications the set now scores **9/9, 17/17**, and that run is a test
(`test_every_journey_survives_what_the_model_actually_says`), so the gap between the labels we
write and the ones the model produces is measured on every commit rather than on demo morning.

**Re-record when the prompt or the model changes:**

```bash
ANTHROPIC_API_KEY=… python scripts/record_fixtures.py \
  --golden packages/eval/golden/clinics_journey_turns.jsonl \
  --out    packages/eval/fixtures/recorded_clinics_journey_haiku.jsonl \
  --tenant-vertical clinics --now 2026-09-01T12:00:00+03:00
```

`--now` is not optional in spirit. Without it the model has no calendar and resolves "بكرة" against
its training cut — `2025-01-10`, a real, parseable, wrong date — and the recording is not the
pipeline production runs. The recorder warns when it is missing, and the sibling `.meta.json`
records which calendar produced each file.

> **The key used in this session must be rotated.** It was passed in chat, so it is in the
> transcript. It was used in-process only and was never written to a file or committed.

---

## 5. The Arabic aliases — done

Reviewed by the clinic on 28 August across five comment threads, all applied, plus a sixth answer
that closed the last question. **The confirmed names are now in the demo workbook itself** —
decision 12 puts this data in the clinic's workbook rather than in code, and the workbook committed
here is that file. 14 branches and 34 of the 35 services; DT020 is blank on the client's
instruction.

Measured with `scripts/check_alias_resolution.py` over the demo's own phrases:

```
the workbook before      24 phrases: 2 resolved, 0 ask, 22 reach nothing
the workbook now         24 phrases: 23 resolved, 1 asks, 0 reach nothing
```

The one that asks is a bare `ليزر`, which reaches nine packages. That is the right answer there.

| Question | Answer |
|---|---|
| **Q1** — the two 12-session half-body packages | The clinic gave one phrase to *both*, which an import refuses. Settled by the client: **DT020 carries no Arabic name at all.** Its row stays in the list so the decision reads as a decision. Every Arabic half-body phrasing reaches DT023 outright; DT020 still answers to its English catalogue name and stays bookable |
| **Q2** — the two 4-session maintenance packages | Answered cleanly. Every name given to DT027 says full body; none of DT024's do. Both resolve outright |
| **Q3** — Skin Boosters and Biostimulator | `حقن ترطيب ونضارة البشرة` / `معززات البشرة` and `محفزات الكولاجين` / `محفز الكولاجين`, each kept beside the transliterations because patients type both |

The two body-shaping pairs (DT032/DT035, DT033/DT034) drew no comment and keep one distinguishing
word each. Worth a look post-demo; not blocking.

**`scripts/check_alias_resolution.py` is the tool to run on whatever the clinic sends back.** It
answers what the importer does not: a catalogue can import perfectly and still leave a patient's
words reaching nothing, because resolution happens a turn later, in front of them.

---

## 6. Steps 8 and 9

**Step 8 — the client pack.** `docs/DERMACLUB-CLIENT-PACK-2026-09-01.md`, plus a published page.
Six things Nada does, four she refuses, the eleven disclosures, the data behind the demo, the five
things needed from the clinic, and what the demo is not. It says plainly that the screening lists
and emergency copy are an engineer's draft awaiting the clinic's medical lead.

**Step 9 — journey evals.** `packages/eval/journeys.py`; ten journeys in
`golden/clinics_journeys.jsonl`; run with `python -m packages.eval --journeys … --diary …`.

The classifier eval scores one message at a time, and every failure the booking journey is about
happens *between* messages. This runs whole conversations through the real receptionist and the
real tools and scores what the patient would have received on each turn.

**It runs against the client's own diary**, not an invented one: `fixtures/clinic_diary.json` is
two branches of one day, cut out of the committed workbook by the importer, with
`test_clinic_workbook_integration.py` failing if the two drift apart. That choice is the point — it
is what found §7.1 within minutes.

Two label sources through one runner: the labels written into the journey file (deterministic, no
key, what CI runs) or recorded classifications (`--fixtures`, the model and the machinery
together). A journey may declare a `known_gap`: it runs, it is reported, it does not fail the
build, and the runner says so if it starts passing.

---

## 7. Failures found — and what was done about each

### 7.1 The diary holds one slot per service, branch and day

**Severity: demo script.** 672 slots ÷ 14 branches ÷ 6 days = 8 services a day each, one slot
apiece. `check_availability` therefore never offers a choice: it offers the single open time, or
says there is none. The "11:00 / 16:00 / 18:00" in both previous handoffs is a unit-test fixture.

**How found:** the journey eval, first run, because its diary is cut from the client's file.
**Remedy applied:** every document corrected; §9.1 lists what is actually open.
**If it bites anyway:** ask for a different service or day — Nada re-offers what is free.

### 7.2 The clarifying-turn budget counted finished work

**Severity: would have broken the demo within ten minutes.** With no task in flight,
`SqlAlchemyConversationStore.begin` counted *every* reply in the conversation. A booking costs three
replies and `max_clarifying_turns` is 2, so the patient's next question — anything needing a
clarifying turn — was handed to a person immediately, on a conversation that had just gone
perfectly.

**How found:** the `a_second_request_after_the_booking` journey.
**Remedy applied:** with nothing in flight, nothing has been spent. Pinned by a test on the store
itself (`test_a_finished_job_leaves_no_turns_for_the_next_one_to_spend`) and by the journey.

### 7.3 `الساعة ٧` is `unclear`, and that abandoned the booking

**Severity: broke the demo at its second message.** Out of context the phrase carries no request,
no treatment and no verb. Haiku says `unclear` at 0.25; escalation to Sonnet 5 says `unclear` at
0.3. `unclear`'s ceiling is a hand-off, and because the intent differed from the task's, the
booking was abandoned first.

**How found:** recording the demo's own messages and replaying them through the journeys.
**Remedy applied:** the dialogue-state rule grew the half it was missing. A read-back takes a yes
or a no; an outstanding *question* takes a value, and the model cannot supply one because it does
not know what was asked. An `unclear` turn is now offered to the slot the task is waiting on,
through the worker's own `normalise_slots`, and **only a message that resolves into that slot** is
treated as an answer. Anything else is still `unclear`, and still a person.

### 7.4 A confidence in a label the model never chose was gating the task

**Severity: the read-back rule ended in the hand-off it exists to prevent.** When the conversation
supplies the intent — `تمام` mid-booking — `decide_autonomy` was still reading the model's
confidence in `thanks_closing`/`unclear` (0.3) and fetching a person.

**How found:** the same run; it was masked because every hand-written label carried 0.95.
**Remedy applied:** an intent that came from conversation state is a fact, not a guess, and is not
gated by a number attached to something else. Nothing else is relaxed — the clinical gate reads
every turn, and a booking still reaches `confirm_booking` only through a read-back the patient
agreed to.

### 7.5 The price question asked instead of quoting

**Severity: demo quality.** `برايم ليز 6 جلسات بكام؟` reaches the model as a service *and* a
quantity — correctly — which left the catalogue lookup holding `برايم ليز`, three packages
differing only by session count, and a clarifying question whose answer the patient had already
given.

**Remedy applied:** the count narrows the candidates. It never picks: a count matching two rows, or
none, or not a number, still asks.

### 7.6 The recorder gave the model no clock

**Severity: every recorded date-bearing fixture was unrepresentative.** `record_fixtures.py` never
passed `local_now`, so `<today>` was absent and the model resolved "بكرة" against its training cut.

**Remedy applied:** `--now`, a warning when it is missing, and the calendar recorded in the sibling
`.meta.json`.

### 7.7 Two of the booking's three turns were English, with no configuration behind them

**Severity: the client would have seen it on the day.** The read-back and the booking confirmation
were composed inside `receptionist.py`. The previous handoff's "these five templates are the only
copy still to be written" counted the tools and missed the receptionist.

**Remedy applied:** `TENANT_CONFIRM_READ_BACK` and `TENANT_BOOKING_CONFIRMED`, read through the same
composition-root seam as the greeting and closing. The read-back offers `{details}` (with English
slot labels, what the default reads) and `{values}` (without them, what an Arabic template wants).

### 7.8 The CI eval gate would have failed on this branch

**Severity: red CI on first run.** Wiring the journey eval into the shared CLI made
`python -m packages.eval --golden …` import the whole receptionist; that job installs pydantic and
nothing else.

**Remedy applied:** the journey imports are lazy. Verified by running the gate's own command against
a virtualenv holding only `pydantic==2.9.2`.

### 7.9 A duplicate alias that folds to one string

**Severity: the import would have refused the file.** Two spellings of `أبريلين` differ only by
hamza, which the matcher folds — the importer counts that as one name reaching one row twice.

**How found:** `check_alias_resolution.py`, on my own draft, before anyone saw it.
**Remedy applied:** removed, and the rule documented in the review note. The overlay now also
dedupes on the normalised form, so re-running `--draft` against the updated workbook reports the
file as it is instead of inventing a collision.

---

## 8. Known failures **not** fixed, and the remedy for each

| # | What | Why it was left | Remedy |
|---|---|---|---|
| 1 | **The goodbye after a booking does not repeat the reference.** `closing_booking_confirmed` renders its generic form | The reference lives on the task, and the task is finished by the time the patient says thank you. Reaching it means putting the reference on the *conversation* — a change to the store and its port | Post-demo. Declared as `known_gap` in the journey set, so it is reported on every run and the runner says so if it starts passing. **The booking turn itself does quote the reference** — only the goodbye does not |
| 2 | **DT026 quotes as one session.** "Super Annual Unlimited Sessions" is 18,700 EGP and imports with `session_count = 1`, because the number is only in the name and "Unlimited" has none | It is a catalogue decision — what *should* an unlimited package quote as? — not a code one | Ask the clinic. Until then, **do not probe it on the day**. The importer already reports it as one of the six warnings |
| 3 | **Treatment names, branch names and dates read in English** inside otherwise Arabic sentences | Aliases make a patient's words *resolve*; they do not translate the reply. Dates are formatted with no per-language setting behind them | Post-demo: a display-name column in the workbook, and a date locale. Say it to the client before they ask — it is already in the client pack |
| 4 | **Multi-intent decomposition is unbuilt.** A message asking price *and* nearest branch gets one answered | Unchanged from both previous handoffs | Post-demo |
| 5 | **The general dialogue-state rule is still partial.** It now covers a pending read-back *and* a pending slot question; a short reply to anything else still falls through to the flat vocabulary | The two cases the demo needs are covered; the general form needs conversation context in the classifier | Post-demo. The proper fix is giving the classifier the previous turn, which is a prompt and pipeline change |
| 6 | **Five `act` intents have no data behind them** — practitioner, promotions, retail/voucher, stock, orders | Accepted for the demo | They hand off safely. Do not probe them; they will be flat |
| 7 | **The screening lists and emergency copy are an engineer's unsigned draft** | Agreed: demo work, the clinic supplies real items post-demo (decision 14) | The clinic's medical lead approves before any real patient reaches the number. Structurally, that a match ends autonomy is not a draft |
| 8 | **`alembic upgrade head` cannot run end-to-end on SQLite** | Pre-existing: migration 002 drops a constraint SQLite has no `ALTER` for | Only affects local experiments. Postgres is fine |

---

## 9. Demo-day traps, and what to do about each

| Symptom on the day | Cause | Remedy |
|---|---|---|
| Every booking hands off | `TENANT_VERTICAL` unset, or set on one service only | Set `clinics` on **both**; redeploy |
| Times an hour out | `TENANT_TIMEZONE` still `Asia/Dubai` | Set `Africa/Cairo` |
| `WB-0266` instead of `DC-0266` | `TENANT_BOOKING_REFERENCE_PREFIX` unset | Set `DC` |
| Nada offers one time, not three | Correct — one slot per service, branch and day (§7.1) | None. Script around it |
| The patient's hour is not the open one | They asked for a time the diary does not hold | Nada re-offers what is free. Costs a turn |
| Arabic reaches no treatment | Aliases not imported | The English names still work. Re-run the import |
| Nada asks which package, repeatedly | The words reach several catalogue rows | Use the full name. `check_alias_resolution.py` shows which words are ambiguous |
| A booking says the slot is taken | It is | Offer another day. The diary is honest |
| Worker silent, API healthy | LLM key set on the API only | Set it on the worker; restart |
| Will not boot, `ConfigError` naming `TENANT_PRICE_QUOTE` | The template dropped `{price}` or its quantity placeholder | Deliberate. Fix the template — a quote without the session count is wrong by a factor of five |

**Rollback** is the previous Render deploy. The migrations need no reverting: 007 and 008 only add
tables and columns nothing older reads.

### 9.1 What is open on Wednesday 2 September

| Branch | Open |
|---|---|
| Maadi | 11:00 Primelase single · 13:00 Primelase 12 · 16:00 Body Shaping · 18:00 Basic Facial · **19:00 Facial** |
| New Cairo | 12:00 Peeling · 16:00 Botox · 17:00 Botox + Lip Booster · 18:00 Filler · 19:00 Filler 1 Syringe |
| Nasr City | 12:00 Cool Shaping · 13:00 CoolShape · 16:00 Basic Facial · **17:00 Facial** · 18:00 Medical Facial + Dermapen · 19:00 Peeling |

`فاشيال` at Maadi is **19:00** → the patient's line is `الساعة ٧`. At Nasr City it is 17:00 →
`الساعة ٥`. A bare hour reads as the afternoon (the clinic opens at 11:00); a weekday name means the
*next* one, never today; same-day is dropped by agreement.

### 9.2 The transcript these settings produce

Run locally against the whole workbook, through the real repository and the real receptionist:

```
patient  عايزة أحجز فاشيال في المعادي بكرة
Nada     متاح عندنا 19:00 لـ Facial في فرع Maadi يوم Wednesday 02 September. تحبي أحجزلك إمتى؟
patient  الساعة ٧
Nada     تأكيد الحجز: فاشيال، المعادي، Wednesday 02 September، 19:00 — صح كده؟
patient  تمام
Nada     تم الحجز ✅ رقم الحجز: DC-0266. مستنينك في الفرع.
```

`Facial`, `Maadi` and the date are the English noted in §8 item 3.

---

## 10. Verification

```bash
uv venv --python 3.13 .venv && uv pip install -e ".[dev]"
uv pip install ruff==0.6.9 mypy==1.11.2 types-PyYAML==6.0.12.20240917 openpyxl

.venv/bin/python -m pytest apps/api/tests packages     # 965 passed, 2 skipped
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/mypy                                          # clean

# the booking conversation on the labels we wrote
.venv/bin/python -m packages.eval \
  --journeys packages/eval/golden/clinics_journeys.jsonl \
  --diary    packages/eval/fixtures/clinic_diary.json            # 9/9, 1 known gap

# …and on what the model actually said
.venv/bin/python -m packages.eval \
  --journeys packages/eval/golden/clinics_journeys.jsonl \
  --diary    packages/eval/fixtures/clinic_diary.json \
  --fixtures packages/eval/fixtures/recorded_clinics_journey_haiku.jsonl   # 9/9

# what a patient's Arabic reaches in the catalogue
.venv/bin/python scripts/check_alias_resolution.py \
  docs/DermaClub_Availability_DEMO_2026-08-26_1.xlsx --timezone Africa/Cairo
                                                        # 23 resolved, 1 asks, 0 nothing
```

`openpyxl` is an operator dependency: with it the workbook tests run, without it they skip, as they
do in CI.

---

## 11. Where things live

| Path | What |
|---|---|
| `docs/DERMACLUB-DEPLOY-RUNBOOK-2026-09-01.md` | **Step 10.** Every command with its check |
| `docs/DERMACLUB-CLIENT-PACK-2026-09-01.md` | The client-facing pack |
| `docs/DERMACLUB-ARABIC-ALIASES-DRAFT-2026-08-28.md` | The alias review document |
| `docs/dermaclub-aliases-draft.csv` | The alias data, machine-readable |
| `docs/DermaClub_Aliases_DRAFT_2026-08-28.xlsx` | The two columns, paste-ready for the clinic |
| `docs/DermaClub_Availability_DEMO_2026-08-26_1.xlsx` | The client's workbook — **now carrying the Aliases columns** |
| `scripts/check_alias_resolution.py` | What a patient's words reach; run on any returned workbook |
| `scripts/record_fixtures.py` | Re-record classifier fixtures (needs a key, and `--now`) |
| `packages/eval/journeys.py` | The journey harness |
| `packages/eval/golden/clinics_journeys.jsonl` | The ten journeys |
| `packages/eval/golden/clinics_journey_turns.jsonl` | The journeys' messages, for recording |
| `packages/eval/fixtures/clinic_diary.json` | Two branches of one day, cut from the workbook |
| `packages/eval/fixtures/recorded_clinics_*.jsonl` | Live classifications, prompt v5, Haiku 4.5 |

---

## 12. Suggested opening prompt for the next session

> Read `docs/SESSION-HANDOFF-DERMACLUB-2026-08-28-STEPS-8-10.md` and the two handoffs it points at.
> Steps 0–9 are done and verified against a live model; step 10 is written up in
> `docs/DERMACLUB-DEPLOY-RUNBOOK-2026-09-01.md` and unexecuted because it needs Render and the live
> number.
>
> Do not re-litigate decision 11 (the emergency reply never directs a patient to public emergency
> services), decision 13 (the 15-minute buffer applies only to bookings this system made), or
> decision 14 (the screening lists ship as demo work).
>
> Work the runbook in order with Render access: merge and deploy, apply 007 then 008, set the
> environment on **both** services, import the workbook, then run the checks in §10 of the handoff
> and the live script in runbook §6.
>
> The diary holds one slot per service, branch and day — book the facial at Maadi at **19:00**, not
> 18:00. Do not probe the unlimited laser package; it quotes as one session.
