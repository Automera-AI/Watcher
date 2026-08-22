# Session handoff — read this first

**Updated:** end of session 10, 22 August 2026
**Branch:** `claude/session-handoff-demo-timeline-goqvbe` — branched from `main` at `6affc37`
(PR #23 merged). Not yet merged; push and open a PR before starting a new session on top of it.
**`main` is at:** `6affc37` as of this session's start — PRs #20 (B1+B2+B3), #21 (G3), #22 (docs)
and #23 (CD fix) are all on `main`. This session's commits are B5, on top of that.
**Deployed:** https://watcher-api-lup7.onrender.com — live, serving from `6affc37` (this session's
work is not deployed; see §4)
**Start at §2 for status, §8 for what to do first.**

Purpose: let a new session pick up without re-deriving anything. Companion documents are
`docs/Watcher_v2_Roadmap.pdf` (**v2.10**) and the five specs in `docs/specs/` — why the code is
shaped the way it is.

**This file was stale before this session started, independent of anything B5 touches.** The copy
committed to `main` still read "end of session 9, 16 August 2026" and named `db94f01` as `main` —
the handoff written after session 9's CD fix (PR #23) was apparently never committed, only carried
forward as an upload. This version folds that gap shut: everything session 9 actually left behind,
plus what session 10 (B5) added on top.

Overwrite this file at the end of each session.

---

## 1. Verified state right now

Measured by running it, not read off a document.

| | |
|---|---|
| Branch | `claude/session-handoff-demo-timeline-goqvbe`, based on `main`@`6affc37`, not yet merged |
| Tests | **499 passing**, 2 skipped without a Postgres (was 486 at session start) |
| Lint / types | ruff clean (`ruff check .` — `docs/` is excluded by config); strict mypy clean on 132 source files |
| Recorded baseline | **88%** intent accuracy, gate passing — still v2's number, unchanged this session (see §5) |
| Database | live — Supabase `watcher-prod`, `qjpjxspycuafqqgudsiv`, eu-central-1, PG 17 (unchanged) |
| Service | live — Render `watcher-api`, `srv-da0a81jl550s73d0b1i0`, **plan: `free`** (confirmed via the Render MCP this session — still the B4 cold-start blocker) |
| Redis / worker | **not provisioned** — no Key Value instance, no worker service exist in this Render workspace yet |
| Python | 3.13 |

To get green in a fresh container:

```bash
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python ruff==0.6.9 mypy==1.11.2 pytest==8.3.3 \
  pydantic==2.9.2 pydantic-settings==2.5.2 fastapi==0.115.0 httpx==0.27.2 \
  rapidfuzz==3.10.1 sqlalchemy==2.0.50 alembic==1.13.3 pyyaml==6.0.2 \
  types-PyYAML==6.0.12.20240917 arq
.venv/bin/python -m pytest          # expect 499 passed, 2 skipped
.venv/bin/mypy && .venv/bin/ruff check . && .venv/bin/ruff format --check .
```

`arq` is new this session (roadmap B5) — it pulls in `redis` (the asyncio client) as its own
dependency, so no separate pin is needed. `psycopg[binary]` and `uvicorn[standard]` are still
deliberately absent from that list and from CI, same reason as always. `reportlab` is needed only
to re-run `docs/make_roadmap.py`.

---

## 2. What session 10 delivered — item B5, the durable queue

Full context: `docs/DECISIONS.md` D35–D36.

**The problem.** `ThreadPoolClassificationQueue` (roadmap A4) answers the webhook fast and
classifies on a small in-process thread pool — but a Render redeploy kills that pool along with
anything still queued in it. The message row survives (persist-before-enqueue, §5); its
classification does not, silently, until someone notices an item never showed up.

**The fix — a fourth transport behind the existing seam.**
`orchestration/queue.py` already had three implementations of `ClassificationQueue`
(`BackgroundTasksQueue`, `InlineClassificationQueue`, `ThreadPoolClassificationQueue`), all sharing
one `MessageConsumer.consume`. B5 adds `RedisClassificationQueue`: `enqueue` stays synchronous like
the other three (ingestion did not change), and internally schedules the arq push as a
fire-and-forget task on the caller's already-running loop, logged rather than left to vanish if it
fails (D35). `build_redis_pool` builds the connection pool without connecting — same lazy-connect
trade `db/engine.py` already makes for `DATABASE_URL`.

**The consumer — `apps/api/worker.py`, a second composition root.** Run as
`arq apps.api.worker.WorkerSettings`, in its own OS process. It calls the exact same
`MessageConsumer.consume` the in-process queue calls directly. Importing the module does nothing
that can fail or connect — `Settings()` never raises and a pool is lazy — so a test can import it
and drive `consume_message` with a fake `ctx` with no environment configured at all.

**One wiring, not two — `orchestration/composition.py` (D36).** Before this session there was
exactly one process that ever consumed a message, so `main.py` wired the orchestrator, sender and
alerter inline. A second consumer means that wiring could drift between the two processes without
anyone noticing until production. `build_consumer(settings, database, classifier)` is now the one
place both `main.py` (the in-process fallback) and `worker.py` (the arq worker) build it from.

**`main.py`'s `assemble` now branches on `REDIS_URL`.** Unset — the default — the API stays exactly
what it was: a full pipeline on the in-process pool. Set, the API becomes a thin producer: no
sender, no alerter, no orchestrator, no per-message DB repos built in that process at all — those
move to the worker. `create_app`'s `on_shutdown` became `Callable[[], Awaitable[None]]` (was
`Callable[[], None]`) to let `main.py` `await` closing whichever queue it built; the only caller was
`main.py` itself, so this was a clean signature change, not a shim.

**Not deployed.** No Redis instance and no worker service exist in Render yet — that is a billing
decision the user deferred this session (see §4). The code path is fully tested against a fake arq
pool (no real Redis needed for any test) and is safe to turn on the moment both exist: set
`REDIS_URL` and nothing else changes.

**Roadmap regenerated.** `docs/make_roadmap.py` → **v2.10**: B5 moved to DONE, Track B 1.5d → 0.5d,
total 32.75d → **31.75d**. M1 is unaffected — B5 was never on the path to a first safe answer.

### Decisions made this session

| Decision | Choice | Where it lives |
|---|---|---|
| How the producer reaches Redis | **Stays sync; fire-and-forget task on the caller's loop, logged (D35)** | `orchestration/queue.py` |
| Where the orchestrator graph is built | **One shared function, called by both processes (D36)** | `orchestration/composition.py` |
| Which queue `main.py` builds | **`REDIS_URL` unset → in-process pool (unchanged default); set → thin producer** | `main.py: assemble` |

---

## 3. Traps and things not to re-litigate

Everything in session 9's list still holds (narrow `intents.yaml` triggers, `CONTROL_CHAT_PHONE_E164`
as a safety variable, `DATABASE_URL` must name `watcher_app` not `postgres`, the transaction-pooler
port 6543, `channel_configs` placeholder, `/webhook` path, replies not yet deliverable, RLS/adapter
rules, D24, `KNOWN_LEAKS` empty, no module-level `app`, no `temperature`, the eval gate not
measuring the prompt). This session adds:

- **`REDIS_URL` unset is a mode, not a degraded state.** Unlike a missing sender or alerter, there
  is no startup warning for it — the in-process pool is a fully supported, deliberate default for
  single-instance/dev, not a thing to "fix."
- **Turning B5 on needs two new Render resources, not one.** A worker service (Starter tier, no
  free tier exists for background workers on Render) and a Key Value/Redis instance (free/ephemeral
  is fine for this use — only job pointers live there, not message content, since the worker
  reloads from Postgres). Neither is provisioned yet. Confirmed via the Render MCP this session:
  `watcher-api` itself is still on the `free` plan too — that's the B4 blocker, unchanged.
- **Render's "Starter" is not a bundle.** It's a per-service tier name. Upgrading `watcher-api` to
  Starter does not create or pay for a worker or a Redis instance — each is billed separately, and
  each needs the tier chosen for it specifically. The Render *Pro* plan ($25/mo) is a workspace-level
  plan (seats, bandwidth) and does not cover per-service compute either.
- **B5 does not give message ordering.** Two turns in one thread, classified concurrently, can still
  race and open two conversations — arq gives no per-key ordering across its workers any more than
  the in-process thread pool did. Still open; see the roadmap page 11 risk table.
- **Don't route `ClassificationQueue.enqueue` through an `await` in the request path.** It was a
  live option (see D35) and was rejected: it would have made the whole seam `async` to close a race
  window no wider than one `BackgroundTasksQueue` already accepts. If a future session is tempted to
  "fix" this properly, read D35 first.
- **`apps/api/worker.py` reads `Settings()` at module level, not `get_settings()`.** Deliberate —
  see the module's own docstring. `get_settings()` is cached process-wide; a test importing the
  worker module must not poison that cache for an unrelated test. `main.py` is still the only place
  `get_settings()` (the cached one) should be called from application code.

---

## 4. What to do first — still B4, now with two more line items

**The trigger phrases (30 minutes, operator).** Unchanged from session 9. Still the cheapest safety
work on the board; still not done.

**B4 — the webhook subscription (0.5d).** Unchanged from session 9's checklist, plus B5 is now a
separate, optional decision layered on top rather than a precondition:

1. The real phone-number id in `channel_configs.external_id`.
2. Send credentials — `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`.
3. `CONTROL_CHAT_PHONE_E164`.
4. Upgrade `watcher-api` off the free Render plan (confirmed still `free` this session).

**B5 — turning the durable queue on (optional, separate billing decision).** The code is done and
merged; deploying it needs:

5. A Redis/Key Value instance on Render (free/ephemeral tier is fine to start).
6. A worker service on Render running `arq apps.api.worker.WorkerSettings` (Starter tier — no free
   tier for background workers).
7. `REDIS_URL` set on **both** the API service and the worker service, pointing at the same Redis
   instance.

This session's user explicitly deferred provisioning all of the above (both B4's Render upgrade and
B5's new resources) to be done by the operator directly, rather than through the Render MCP tools
available in-session. Nothing here is blocked on more engineering — only on someone doing the
above in the Render dashboard.

**Then 2.4** (knowledge base). **2.7 remains unblocked**.

---

## 5. Two numbers 2.7 should settle

Unchanged from sessions 5–9. Not touched this session.

1. **The system prompt's real token count.**
2. **Cost per message.**

---

## 6. Where things live

| What | Where |
|---|---|
| Entrypoint / wiring (API process) | `apps/api/main.py` — `create_application`, `assemble` |
| **Entrypoint / wiring (worker process, new)** | `apps/api/worker.py` — run as `arq apps.api.worker.WorkerSettings` |
| **Shared orchestrator wiring (new)** | `apps/api/orchestration/composition.py` — `build_consumer` |
| App factory + lifespan | `apps/api/app.py` — `create_app(..., on_shutdown=...)` (now async) |
| The pipeline | `apps/api/orchestration/worker.py` — `Orchestrator.process` |
| **The four queue transports** | `apps/api/orchestration/queue.py` — incl. `RedisClassificationQueue` (new) |
| The emergency detector | `apps/api/core/emergency.py` |
| The alert seam / its implementation | `apps/api/core/alerts.py`, `apps/api/channels/alerting.py` |
| **Queue + worker tests** | `apps/api/tests/test_queue.py`, `apps/api/tests/test_worker.py` (new) |
| Typed config (incl. `redis_url`) | `apps/api/core/config.py` + `core/settings_base.py` |
| The image | `apps/api/Dockerfile` |
| CD — image build + push | `.github/workflows/cd.yml` (unchanged this session) |
| CI — dependency install list | `.github/workflows/ci.yml` (added `arq` this session) |
| Locked decisions | `docs/DECISIONS.md` (D35–D36 are this session's) |
| The roadmap | `docs/make_roadmap.py` → `docs/Watcher_v2_Roadmap.pdf` (**v2.10**) |

---

## 7. Roadmap status against v2.10

| Track | Remaining | Note |
|---|---|---|
| A — Make it run | 0d | complete since session 7 |
| **B — Host it** | **0.5d** | B1–B3 ✅, **B5 ✅ (this session)** — left: B4 only |
| 2 — Receptionist | 3.5d | 2.4 knowledge, 2.7 eval (unblocked), 2.8 many properties |
| D — Control page | 13.75d | D2's 3 backend days are the hidden half |
| G — Guardrails | 4.0d | G3 ✅ — G1, G2, G4 remain; none is on the critical path |
| E — Sellable | 4.5d | Blocked on P1 for sequencing |
| 3 — Integration | 5.5d | 3.1 blocked on P1 |
| **Total** | **~31.75d** | at the observed rate, ~7 weeks to sellable |

**Milestone M1 — answers a real message, safely: ~2.5 days (B4 + 2.4), unchanged.** B5 was never on
this path — it protects a deploy that hasn't happened yet from losing work, which matters more once
there's a real guest to lose.

---

## 8. First five minutes of the next session

1. Check whether this session's branch (`claude/session-handoff-demo-timeline-goqvbe`) has been
   merged. If yes, branch from `main` normally. If not, either continue it or ask before branching
   from `main` and leaving it stranded.
2. Rebuild the venv from §1 and confirm **499 passed, 2 skipped**.
3. Read §3, particularly: `REDIS_URL` unset is a mode not a bug, B5 needs two *separate* new Render
   resources (not bundled into any existing plan), and B5 does not solve message ordering.
4. Check whether the operator has done any of §4's checklist (Render plan, worker service, Redis
   instance, WhatsApp credentials, `CONTROL_CHAT_PHONE_E164`) — none of it was done as of this
   session's end, all of it was explicitly left to them.
5. Do the operator's `intents.yaml` edit if nobody has, then B4.
