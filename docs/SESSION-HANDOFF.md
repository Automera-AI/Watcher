# Session handoff — read this first

**Updated:** end of session 2, 12 August 2026
**Branch:** `claude/roadmap-handoff-setup-1faxg7` — all work pushed, no PR
**`main` is at:** `b89eb06`, untouched
**Start at §2 for status, §9 for what to do first.**

Purpose: let a new session pick up without re-deriving anything. Companion documents are
`docs/NEXT-STEPS-v2.md` (the reasoning, §11–14) and `docs/Watcher_v2_Roadmap.pdf` (the scored plan).

---

## 1. Verified state right now

Measured by running it, not read off a document. **Work is on the branch, not on `main`.**

| | |
|---|---|
| Branch | `claude/roadmap-handoff-setup-1faxg7` — 6 commits, all pushed |
| `main` | **`b89eb06`, untouched.** No PR opened |
| Tests | **221 passing**, 0 xfailed (was 101 at the start of this session) |
| Python files | 97 (was 79) |
| Lint / types | ruff clean; strict mypy clean on 92 files |
| Recorded baseline | **87.5%** intent accuracy, gate passing |
| Python | 3.12 everywhere |

To get green in a fresh container:

```
pip install pytest pydantic fastapi sqlalchemy rapidfuzz alembic httpx pyyaml
python3 -m pytest            # expect 221 passed
```

**The product gap, unchanged:** the pipeline can listen and file, but it cannot reply.
`orchestration/worker.py` still has exactly three outcomes — `AUTO_ROUTE`, `CONTROL_PING`,
`INBOX_REVIEW` — and all three mean "put this somewhere". Adding a fourth that means
**answer the customer** is still the project. What changed this session is that the pieces a
fourth outcome needs now exist; nothing is wired to it yet.

---

## 2. Roadmap status

Against `docs/Watcher_v2_Roadmap.pdf` (v1.11, unchanged). Days are the roadmap's own estimates.

| # | Item | Days | Status |
|---|---|---|---|
| 0.1 | Set default branch to `main` | 1 min | **DONE** |
| 0.2 | Remove the client name from the golden set | 0.25 | **DONE** — history rewrite still open |
| 0.3 | Decide the receptionist intent vocabulary | 1 hr | **DONE** — 19 intents, 38 tests |
| 0.4 | Merge the eval branch | 0.25 | **DONE** |
| 0.5 | Delete the stale branch | 1 min | **DONE** |
| 1.1 | Stop the core speaking WhatsApp | 1.5 | **NOT STARTED** — next up |
| 1.2 | Port the four kept scaffold files | 1.0 | **DONE** |
| 1.3 | Python 3.12 → 3.13 | 0.5 | NOT STARTED — do after 1.1 |
| 2.1 | Conversations, tasks and slot filling | 2.0 | **PART DONE** — state machine in, persistence not |
| 2.2 | The reply path | 1.5 | **PART DONE** — envelope + adapters in, composer not |
| 2.3 | Autonomy gate | 1.0 | **PART DONE** — `decide_autonomy` in, not wired to the worker |
| 2.4 | Knowledge base | 2.0 | NOT STARTED |
| 2.5 | Prompt v2 + rewrite the golden set | 1.0 | NOT STARTED — unblocked by 0.3 |
| 3.1 | `PropertySystemPort` + first adapter | 2.5 | NOT STARTED — **blocks door codes and all pricing** |
| 3.2 | End to end on a real number | 1.0 | NOT STARTED |
| P1 | Pick the first client | — | Open |
| P2 | Read the PMS API docs, get sandbox keys | 0.5 | Open — start day one |
| P3 | File Meta business verification | 1 hr | Open |
| P4 | Graphify as a build aid | 0.5 | Optional |

**~13 engineering days remaining** by those numbers, but 2.1–2.3 are partly built now, so the
true figure is lower — call it 11 to 12. Track 0 is fully closed.

---

## 3. Do these next, in this order

> **Superseded — see §9.** All of Track 0 is done, and so is 1.2. This section is kept as
> written so the original reasoning stays legible.

1. ~~**0.3 — decide the intent vocabulary.**~~ Done. One founder hour, blocked three items and the
   golden set. Cheapest and most blocking thing on the whole plan.
2. ~~**0.2 — fix the client name.**~~ Done. **The history rewrite is still an open decision** —
   changing the string stopped it being the first thing a prospect reads, nothing more.
3. ~~**0.5 — delete the stale branch.**~~ Done.
4. **1.1 — de-WhatsApp the core.** Still the next thing. Do it before anything else is built on
   the current shape; every week it waits adds call sites. `test_boundary.py` now defines done.

---

## 4. Decisions made this session — do not re-litigate

- **Base360.ai is ruled out as a partner.** Commercial, not technical: its unified-inbox and
  guest-messaging product is substantially Watcher v2's pitch, and its client base is closed to
  us. Recorded in `NEXT-STEPS-v2.md` §14.1.
- **Integration is a vendor-neutral `PropertySystemPort`,** not a Hostaway integration. Hostaway,
  Guesty, Cloudbeds and Mews all publish APIs. The port is the asset; adapters are disposable.
  Pick the first adapter by which client signs first. (§14.2)
- **Knowledge base: facts in the prompt for the demo, Postgres + pgvector for the real thing.**
  Dedicated vector DBs and knowledge graphs both deferred, with reasoning recorded so they are
  not re-argued. The load is ~1,500 fact rows and ~200 prose chunks per client — four orders of
  magnitude below where specialised tooling starts to matter. (§12.5)
- **Most receptionist knowledge is exact facts and belongs in an ordinary table.** Semantic
  search is approximate by design; an approximately-right door code is worse than no answer.
  Live availability is never a knowledge base — it is an API call. (§12.3)
- **Graphify is a build aid, not the product's knowledge base.** It maps a *codebase* for coding
  assistants and its own docs say it is not for runtime querying. Optional, item P4. (§12.1–12.2)
- **Meta verification no longer sets the date.** Starting unverified is allowed, and a
  receptionist mostly replies inside the 24-hour window. **Engineering is the critical path.**

---

## 5. Traps found this session — these will bite

Each of these was found by reading the code, and each contradicts something a document claims.

> **Status after session 2:** 1, 2, 3, 4 and 8 are resolved — see §8. 5, 6, 7 and 9 stand.
> Trap 3 is resolved as a *design*, not as code: the plan is in §8, the build is blocked on 3.1.

1. **[CLOSED]** **`test_boundary.py` does not catch the bug it exists to prevent.** It bans the strings
   `whatsapp`, `twilio` and so on. The actual leak is `wa_message_id` / `wa_chat_id`, and
   `whatsapp` never matches `wa_`. **Add the `wa_` prefix when porting it in 1.2**, or 1.1 can
   silently regress.
2. **[CLOSED]** **`test_envelope.py` contradicts `test_boundary.py`.** It caps replies at three quick-reply
   buttons — a *WhatsApp* limit sitting in the channel-neutral core, which is the exact mistake
   the boundary test exists to catch. Decide: cap in the WhatsApp adapter, or accept the core is
   permanently limited to the most restrictive channel.
3. **[CLOSED]** **`test_autonomy.py` needs something that does not exist.** It relies on `identity_verified`.
   The repo does identity *matching* ("same person as this record"), not *verification* ("has
   proved who they are"). Different thing; needs the verification-codes table.
4. **[CLOSED]** **`test_task.py` presumes the taxonomy decision.** It uses `booking_enquiry` and
   `availability_check`, neither of which is among the six locked intents. Hence 0.3 first.
5. **The delivery path is write-only.** `destinations/delivery.py` exposes exactly one operation,
   `WebhookTransport.post()`. Item 3.1 needs a **new read port**, not a tweak.
6. **But `crm_cache` is already the right shape** — `external_record_id`, `last_synced_at`,
   `source_destination_id`, per-tenant. The sync-and-cache pattern was designed for.
7. **Mixed-language accuracy is 0.0%.** Per-language: `ar` 100%, `en` 100%, `mixed` 0%. Only 8
   golden examples, so probably one case — but it is the one category at zero. Watch it when the
   set grows to ~50 in 2.5.
8. **[CLOSED]** **The golden set still names a real client** (`packages/eval/golden/golden_set.jsonl`), in a
   public repo. That is item 0.2 and PR #10 did not touch it.
9. **Python baseline is 3.12, not 3.10** — several older notes say otherwise. The 3.13 upgrade
   surface was checked and is clean (no removed stdlib, no `datetime.utcnow`). Do it *after* 1.1
   so a breakage has one suspect.

---

## 6. Environment notes for a fresh container

Nothing is pre-installed. To get to a green test run:

```
pip install pytest pydantic fastapi sqlalchemy rapidfuzz alembic httpx pyyaml
python3 -m pytest            # expect 221 passed
```

`pyyaml` is needed by `packages/intents`. It is a build dependency, not a runtime one — the
application loads the compiled JSON and `schema.py` imports yaml lazily so the shipped image
needs no parser. `default_vocabulary()` prefers `build/intents.json` but **ignores it if it is
older than `intents.yaml`**, so editing the vocabulary in development takes effect without
remembering to recompile.

To reproduce the full CI locally:

```
pip install ruff==0.6.9
python3 -m pip install mypy==1.11.2 types-PyYAML==6.0.12.20240917   # NOT `pip install mypy`
ruff check . && ruff format --check . && python3 -m mypy && python3 -m pytest
python3 -m packages.eval \
  --golden   packages/eval/golden/golden_set.jsonl \
  --fixtures packages/eval/fixtures/recorded_haiku.jsonl \
  --baseline packages/eval/baseline.json \
  --out-dir  eval-out
```

**Gotcha, and it cost a red CI run this session:** `pip install ruff==0.6.9` can leave a *newer*
`ruff` earlier on `PATH`, so bare `ruff check .` silently runs the wrong version. 0.15.8 and 0.6.9
disagree — 0.6.9 flags `UP027`, which newer versions removed, and the two format differently. Use
**`python3 -m ruff`**, never bare `ruff`, and check each command's exit code separately:

```
for c in "python3 -m ruff check ." "python3 -m ruff format --check ." "python3 -m mypy" "python3 -m pytest"; do
  $c >/dev/null 2>&1; echo "rc=$? <- $c"
done
```

Chaining these with `&&` hides failures: a failed early command short-circuits the rest, and the
last line you see may be a *later* command's success. That is exactly how a red CI run got
reported as green here.

**Gotcha:** installing mypy as a standalone tool puts it in its own environment where
`pydantic.mypy` is not importable, and it fails with a misleading "No module named 'pydantic'"
plugin error. Install it into the *same* interpreter that has pydantic (`python3 -m pip install`).

`httpx` is required by FastAPI's `TestClient` — without it `test_webhook.py` fails at collection
rather than as a test failure, which looks scarier than it is.

CI: three jobs — `API · lint · types · tests`, `Control page · lint · build` (self-skips until
`apps/control-page/package.json` exists), `Classifier eval gate`. The eval gate also self-skips
if the eval tool or golden set are missing, so **check it actually ran** rather than trusting a
green tick — a real run uploads an `eval-report` artifact.

---

## 7. What shipped this session

Documentation and repo hygiene only. **No product code was written.**

- `docs/NEXT-STEPS-v2.md` — §11 where we stand, §12 knowledge base options, §13 the plan and
  dates, §14 pulling from a PMS. Continues the reconciliation document, which ends at §10.
- `docs/Watcher_v2_Roadmap.pdf` + `docs/make_roadmap.py` — the scored roadmap, regenerable.
- Default branch set to `main` (0.1).
- PR #10 merged — eval runner + job queue (0.4). PR #11 merged — the documents.

Corrections made to earlier documents, in case older copies are still circulating: Meta
verification is no longer the binding constraint; the eval merge was a merge commit rather than
a fast-forward; the Python baseline is 3.12 not 3.10; and Graphify is not Graphiti.

---

## 8. What session 2 did — 0.2, 0.3, 1.2

Branch `claude/roadmap-handoff-setup-1faxg7`. **221 passing, 0 xfailed** (was 101). Ruff clean,
strict mypy clean on 92 files, eval gate still 87.5%.

**0.2 — done.** The client name, and the two London areas that identified it alongside the name,
are gone from the golden set and the fixtures — replaced with invented placeholders in the same
style as the fictional Acme Trading already there. Both files were rewritten together because the
recorded predictor keys on message text. No scored field moved, so the baseline still holds. New
test `test_every_golden_message_has_a_recorded_prediction` makes a one-sided edit fail a test
rather than the runner.

Note this document must not name the strings either, which it briefly did — writing "X is gone"
puts X back. `test_no_client_name_in_the_repo` now covers the whole tree, docs included, so the
same mistake fails a test instead of shipping.

**Still open:** whether to rewrite git history. The old strings remain in four commits.

**0.3 — done.** `packages/intents/`: 18 intents, 80 examples, 5 languages, 6 emergency triggers,
2 client overrides, 37 tests. Adapted to the repo rather than dropped in — package-qualified
imports, `python -m packages.intents[.compile]`, wired into pytest/mypy/CI, `build/` gitignored,
PyYAML build-only via a lazy import.

Two bugs came in with it, both of which had a check that could not fire:

1. **The fire emergency trigger could never match.** `7arі2` carried a Cyrillic `і` (U+0456)
   where the Latin `i` belongs. It passed the validator and all 32 original tests and reached
   `build/intents.json` — verified against the compiled artifact. `EmergencyTrigger` now rejects
   any phrase mixing alphabets.
2. **The Franco-Arabic guard was dead code.** `if latin and ... and not intent.examples` — a
   non-empty `latin` implies non-empty `examples`, so it never ran. Replaced with a real rule:
   languages declare `spoken`, and an acting intent needs at least one spoken example or its
   phone test set in 3.2 is empty. A client on a voice channel may not declare a typed-only
   language either.

**1.2 — done, against the real scaffold.** The four files were not in this repo; they are on
branch `amahmoudosman96-lgtm-V2-scaffold` under `watcher-v2-scaffold/`, which turned out to be a
whole parallel tree carrying the *implementation* as well as the tests — and one that is already
channel-neutral (`channel_thread_id`, not `wa_chat_id`). Its own suite: 14 passing.

All four tests are ported and **live, no xfail**, along with the minimum core they exercise:

| Ported in | What |
|---|---|
| `apps/api/schemas/envelope.py` | `InboundTurn` / `OutboundAction`, minus the button cap |
| `apps/api/channels/` | `base`, `whatsapp` (the cap lives here), `voice` |
| `apps/api/conversations/task.py` | the task state machine, slots read from the vocabulary |
| `apps/api/core/autonomy.py` | `decide_autonomy`, ceiling read from the vocabulary |

This is the scaffold's *core primitives*, not items 2.1–2.3. Still outstanding there:
persistence and the conversations table (2.1), the composer and send-out (2.2), and wiring the
gate into `orchestration/worker.py` (2.3).

**Trap #2 is worse than the handoff recorded, and now demonstrated rather than argued.** The
scaffold's `test_boundary.py` **passes** on `app/core/envelope.py` while that file raises
`"WhatsApp allows at most 3 quick reply buttons"`. Its regex only catches provider names used as
identifiers (`\bwhatsapp\b\s*[.(=]`), so both the string literal and
`Channel = Literal["whatsapp", ...]` slip straight through. The boundary test greenlit the exact
violation the envelope test enforced, in the same tree. **Decision taken: cap in the adapter.**
The core composes freely, `channels/whatsapp.py` truncates to three and reports `truncated`, and
`channels/voice.py` speaks the options instead — the case that proves the point, since on a call
three is as wrong as six.

**Two places the scaffold and the vocabulary disagreed, resolved for the vocabulary:**

1. The scaffold let a **verified guest cancel autonomously** — `cancel_reservation` sat in its
   `REQUIRES_VERIFIED_IDENTITY` set, not its always-human one. A refund is money going backwards.
2. Its always-human list was `{billing_question, owner_enquiry}`; payment questions and
   complaints were absent.

`owner_enquiry` went the other way — the scaffold had it and 0.3 did not, so it was **added to
the vocabulary as `hand_off`** (now 19 intents, 84 examples). Without it, half of "money and
owner matters always reach a person" had nowhere to live. The scaffold's `viewing_request` was
not taken: lettings traffic, not holiday-homes reception.

**Trap #3 stands.** `identity_verified` is a parameter the caller supplies and nothing in this
repo can produce it. `test_the_repo_still_cannot_prove_who_a_sender_is` fails the day that stops
being true.

**`test_boundary.py` is still the one to understand.** 1.1 has not happened, so it carries
`KNOWN_LEAKS`: eight core files and exactly which tokens each still has. New leaks fail at once;
*stale* entries fail too, so 1.1 cannot half-land. The list only shrinks, and empty means done:

```
classifier/prompt.py · control_chat/state.py · core/config.py · db/models.py
db/repository.py · orchestration/queue.py · schemas/enums.py · schemas/message.py
```

`CHANNEL_REGISTRY` sits beside it for the one **permanent** exception — `schemas/envelope.py`
names the closed set of channels, because you cannot have a channel-neutral envelope without a
channel field. A separate test stops that becoming a back door for `wa_`.

### Trap #3 — the door-code decision, taken but not built

Discussed and settled this session. **No code written; it is blocked on 3.1.**

The repo does identity *matching*; it cannot prove anything. The fix is one inversion: **do not
ask a guest for their registered mobile — check the number they are already messaging from.**
Meta has verified they control that WhatsApp account, so it is possession-based proof and costs
the guest nothing. Reservation ref stays as the *lookup key*, never the proof. The Meta profile
**display name is user-set and worthless** — it must not appear in the check at all.

Anything you *ask* for (ref, email, name) is knowledge, and knowledge is forwarded, screenshotted
and sitting on cleaners' booking sheets. Anything you *send to* what is already on file is proof.

**v1 releases a code only when all four hold; anything else hands off:**

1. inbound `channel_identity` matches the booking contact — exact E.164, never fuzzy;
2. the stay is live right now;
3. the property's code is self-limiting (per-booking PIN), or explicitly opted in;
4. sent only to the contact on the booking, never the asking number.

Conditions 2 and 4 are already in the `access_code_request` never-list. **Condition 3 is the one
that is easy to miss:** lock types vary by property, and a static key-box code cannot be
un-leaked — one wrong recipient compromises every future guest until someone physically visits.
So a silent match alone is not sufficient.

**Deferred to before pilot:** the step-up flow (send a one-time code to the contact on file, guest
reads it back). Bookings are roughly half OTA, and relay numbers break condition 1, so this is
about half of all guests. `control_chat/tokens.py` already has the HMAC + TTL machinery.

**Still open:** the booker is not always the arriver. The code goes to whoever booked; the person
at the door may be their spouse.

Full write-up: `/root/.claude/plans/okay-for-3-can-soft-cocoa.md` (not in the repo — copy it in if
it matters).

---

## 9. Start here tomorrow

1. **1.1 — de-WhatsApp the core (1.5 days).** The next real item, and it now has an executable
   definition of done: `KNOWN_LEAKS` in `apps/api/tests/test_boundary.py` empty. Eight files:

   ```
   classifier/prompt.py · control_chat/state.py · core/config.py · db/models.py
   db/repository.py · orchestration/queue.py · schemas/enums.py · schemas/message.py
   ```

   `wa_message_id` → `external_id`, `wa_chat_id` → `thread_id`, add a channel field.
   **`apps/api/schemas/envelope.py` is already the target shape — copy from it.**

2. **P2 — read the PMS API docs, get sandbox keys (0.5 days).** Not engineering, and it gates
   3.1, which in turn gates all pricing, availability and door codes. Cheapest unblock available.

3. **Then 2.1 → 2.2 → 2.3**, which are partly built. What is missing: persistence and the
   conversations table; the composer and send-out; a fourth outcome in `orchestration/worker.py`.

### Decisions still owed by you

- **The git history rewrite** left open by 0.2. The old client name is still in history.
- **What happens to `amahmoudosman96-lgtm-V2-scaffold`.** Its core is ported; `receptionist.py`,
  `tools/registry.py`, `understanding.py`, the SQL migration and its own eval harness are not.
  It is a second full tree that will drift — mine it deliberately or close it. Note
  `understanding.py` is a *third* intent taxonomy, after `IntentType` and the vocabulary.
- **Whether to open a PR** for `claude/roadmap-handoff-setup-1faxg7`. Six commits, none on `main`.

### Environment mismatch worth fixing

The build artifacts uploaded this session were made on **Python 3.10** (`schema.cpython-310.pyc`)
with **pytest 9.1.1**. The repo is 3.12 everywhere and CI pins pytest 8.3.3. A green local run on
that setup is not evidence of a green CI run. Reconcile before 1.3 moves the baseline to 3.13.
