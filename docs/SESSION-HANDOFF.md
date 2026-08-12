# Session handoff — read this first

**Updated:** 12 August 2026
**Branch this was written on:** `claude/strategy-shift-review-roadmap-i9zkwe` (restarted from `main` after PR #11 merged)
**`main` is at:** `b89eb06`

Purpose: let a new session pick up without re-deriving anything. Companion documents are
`docs/NEXT-STEPS-v2.md` (the reasoning, §11–14) and `docs/Watcher_v2_Roadmap.pdf` (the scored plan).

---

## 1. Verified state of `main` right now

Measured by running it, not read off a document.

| | |
|---|---|
| Tests | **101 passing** (was 86 before this session) |
| Python files | 79 |
| Backend modules | 10, each with the outside world behind a swappable seam |
| DB tables | 11, migration written |
| Eval runner | **on `main`**, with a CI gate |
| Recorded baseline | **87.5%** intent accuracy on Haiku, gate fails on a >2pp drop |
| Default branch | `main` |
| Python | 3.12 everywhere (`requires-python >=3.12`, ruff `py312`, mypy `3.12`, CI `3.12`) |

**The product gap, unchanged:** the pipeline can listen and file, but it cannot reply.
`orchestration/worker.py` has exactly three outcomes — `AUTO_ROUTE`, `CONTROL_PING`,
`INBOX_REVIEW` — and all three mean "put this somewhere". Adding a fourth that means
**answer the customer** is the project.

---

## 2. Roadmap status

Numbering matches `docs/Watcher_v2_Roadmap.pdf`.

### Track 0 — today

| # | Item | Status |
|---|---|---|
| 0.1 | Set default branch to `main` | **DONE** — remote HEAD is `refs/heads/main` |
| 0.2 | Remove the client name from the golden set and fixtures | **DONE** — replaced with invented placeholders; eval still 87.5% |
| 0.3 | Decide the receptionist intent vocabulary | **DONE** — `packages/intents`, 19 intents, 38 tests |
| 0.4 | Merge the eval branch | **DONE** — PR #10, 101 tests, gate passing |
| 0.5 | Delete the stale `nifty-johnson` branch | **DONE** |

### Everything else

Track 1: 1.1 de-WhatsApp the core (**NOT STARTED** — `test_boundary.py` defines done) ·
1.2 port the four scaffold files (**DONE** — see §8) · 1.3 Python 3.12→3.13 (**NOT STARTED**)
Track 2: 2.1 conversations/tasks/slot filling · 2.2 reply path · 2.3 autonomy gate ·
2.4 knowledge base · 2.5 prompt v2 + golden set
Track 3: 3.1 `PropertySystemPort` + first adapter · 3.2 end to end on a real number
Parallel: P1 pick first client · P2 read PMS API docs · P3 file Meta verification ·
P4 Graphify as a build aid (optional)

**Remaining: ~14 engineering days.** Demo ~2 weeks, pilot ~3, phone ~4, at six days a week.

---

## 3. Do these next, in this order

> **Superseded by §8.** All of Track 0 is now done, and so is 1.2. The current list is at the end
> of §8; this section is kept as written so the original reasoning is still legible.

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

1. **`test_boundary.py` does not catch the bug it exists to prevent.** It bans the strings
   `whatsapp`, `twilio` and so on. The actual leak is `wa_message_id` / `wa_chat_id`, and
   `whatsapp` never matches `wa_`. **Add the `wa_` prefix when porting it in 1.2**, or 1.1 can
   silently regress.
2. **`test_envelope.py` contradicts `test_boundary.py`.** It caps replies at three quick-reply
   buttons — a *WhatsApp* limit sitting in the channel-neutral core, which is the exact mistake
   the boundary test exists to catch. Decide: cap in the WhatsApp adapter, or accept the core is
   permanently limited to the most restrictive channel.
3. **`test_autonomy.py` needs something that does not exist.** It relies on `identity_verified`.
   The repo does identity *matching* ("same person as this record"), not *verification* ("has
   proved who they are"). Different thing; needs the verification-codes table.
4. **`test_task.py` presumes the taxonomy decision.** It uses `booking_enquiry` and
   `availability_check`, neither of which is among the six locked intents. Hence 0.3 first.
5. **The delivery path is write-only.** `destinations/delivery.py` exposes exactly one operation,
   `WebhookTransport.post()`. Item 3.1 needs a **new read port**, not a tweak.
6. **But `crm_cache` is already the right shape** — `external_record_id`, `last_synced_at`,
   `source_destination_id`, per-tenant. The sync-and-cache pattern was designed for.
7. **Mixed-language accuracy is 0.0%.** Per-language: `ar` 100%, `en` 100%, `mixed` 0%. Only 8
   golden examples, so probably one case — but it is the one category at zero. Watch it when the
   set grows to ~50 in 2.5.
8. **The golden set still names a real client** (`packages/eval/golden/golden_set.jsonl`), in a
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
python3 -m pip install mypy==1.11.2      # NOT `pip install mypy` — see below
ruff check . && ruff format --check . && python3 -m mypy && python3 -m pytest
python3 -m packages.eval \
  --golden   packages/eval/golden/golden_set.jsonl \
  --fixtures packages/eval/fixtures/recorded_haiku.jsonl \
  --baseline packages/eval/baseline.json \
  --out-dir  eval-out
```

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

## 8. Session 2 — 0.2, 0.3 and 1.2

Branch `claude/roadmap-handoff-setup-1faxg7`. **221 passing, 0 xfailed** (was 101). Ruff clean,
strict mypy clean on 92 files, eval gate still 87.5%.

**0.2 — done.** `Northwind Residences` / `Riverside Quarter` / `Riverside Quarter` are gone from the golden set and the
fixtures, replaced with invented placeholders in the same style as the fictional Acme Trading
already there. Both files were rewritten together because the recorded predictor keys on message
text. No scored field moved, so the baseline still holds. New test
`test_every_golden_message_has_a_recorded_prediction` makes a one-sided edit fail a test rather
than the runner. **Still open:** whether to rewrite git history — the old strings remain in it.

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

### Do these next

1. **1.1 — de-WhatsApp the core.** Now has an executable definition of done: empty `KNOWN_LEAKS`.
   The scaffold's envelope is the target shape, and it is already in the repo to copy from.
2. **2.1 — conversations, tasks, slot filling.** The state machine is in; what is left is
   persistence, the conversations table, and the verification-codes table trap #3 needs.
3. **2.2 / 2.3 — wire it up.** The envelope, the adapters and `decide_autonomy` all exist; what
   is missing is the composer, the send-out, and a fourth outcome in `orchestration/worker.py`.
4. **Decide on the history rewrite** left open by 0.2.
5. **Decide what happens to `amahmoudosman96-lgtm-V2-scaffold`.** Its core is ported; the rest
   (`receptionist.py`, `tools/registry.py`, `understanding.py`, the SQL migration, its own eval)
   is not, and it is a second full tree that will drift. Either mine it deliberately or close it.

### Two environment mismatches spotted

The batch-2 artifacts were built on **Python 3.10** (`schema.cpython-310.pyc`) with **pytest
9.1.1**. The repo is 3.12 everywhere and CI pins pytest 8.3.3. A green local run on that setup
is not evidence of a green CI run. Worth reconciling before 1.3 moves the baseline to 3.13.
