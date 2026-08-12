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
| 0.2 | Remove the client name from the golden set and fixtures | **NOT DONE** — still live in a public repo |
| 0.3 | Decide the receptionist intent vocabulary | **NOT DONE** — blocks 1.2, 2.4, 2.5 |
| 0.4 | Merge the eval branch | **DONE** — PR #10, 101 tests, gate passing |
| 0.5 | Delete the stale `nifty-johnson` branch | **NOT DONE** |

### Everything else — all NOT STARTED

Track 1: 1.1 de-WhatsApp the core · 1.2 port the four scaffold files · 1.3 Python 3.12→3.13
Track 2: 2.1 conversations/tasks/slot filling · 2.2 reply path · 2.3 autonomy gate ·
2.4 knowledge base · 2.5 prompt v2 + golden set
Track 3: 3.1 `PropertySystemPort` + first adapter · 3.2 end to end on a real number
Parallel: P1 pick first client · P2 read PMS API docs · P3 file Meta verification ·
P4 Graphify as a build aid (optional)

**Remaining: ~14 engineering days.** Demo ~2 weeks, pilot ~3, phone ~4, at six days a week.

---

## 3. Do these next, in this order

1. **0.3 — decide the intent vocabulary.** One founder hour. Blocks three items and the golden
   set. Cheapest and most blocking thing on the whole plan. **Do not start 1.2, 2.4 or 2.5
   before this lands.**
2. **0.2 — fix the client name.** Two hours. It is in a public repo. Decide separately whether
   to rewrite history; changing the string now at least stops it being the first thing a
   prospect reads.
3. **0.5 — delete the stale branch.** One minute. Keep `claude/dazzling-gates-iepkxa` if you
   want the history, but it is fully merged now.
4. **1.1 — de-WhatsApp the core.** Do this before anything else is built on the current shape.
   Every week it waits adds call sites.

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
pip install pytest pydantic fastapi sqlalchemy rapidfuzz alembic httpx
python3 -m pytest            # expect 101 passed
```

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
