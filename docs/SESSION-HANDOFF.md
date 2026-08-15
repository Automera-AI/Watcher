# Session handoff — read this first

**Updated:** end of session 6, 15 August 2026
**Branch:** `claude/a2-a4-roadmap-ju60pc` — one commit, `bdd45bf`, pushed. **No PR opened yet.**
**`main` is at:** `b7cbefe` — **session-6 work is not on `main`**; see §8 before branching
**Start at §2 for status, §8 for what to do first.**

Purpose: let a new session pick up without re-deriving anything. Companion documents are
`docs/Watcher_v2_Roadmap.pdf` (**v2.3**, regenerated from `docs/make_roadmap.py` this session) and
the two specs in `docs/specs/` — why the code is shaped the way it is.

**This file is now in the repo.** Sessions 3–5 delivered their handoffs as loose files; the result
was that `README.md` pointed at a session-2 document for three sessions. It is versioned here
instead. Overwrite it at the end of each session.

---

## 1. Verified state right now

Measured by running it, not read off a document.

| | |
|---|---|
| Branch | **`claude/a2-a4-roadmap-ju60pc`** — commit `bdd45bf`, pushed, unmerged |
| `main` | `b7cbefe` — sessions 1–5 only |
| Tests | **375 passing** (was 325 at the start of this session) |
| Python files | 121 total; 83 source across 20 modules |
| Lint / types | ruff clean; strict mypy clean on 119 files |
| Recorded baseline | **88%** intent accuracy, gate passing — **still v2's number**, see §5 |
| Python | 3.13 |

To get green in a fresh container:

```bash
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python ruff==0.6.9 mypy==1.11.2 pytest==8.3.3 \
  pydantic==2.9.2 pydantic-settings==2.5.2 fastapi==0.115.0 httpx==0.27.2 \
  rapidfuzz==3.10.1 sqlalchemy==2.0.50 alembic==1.13.3 pyyaml==6.0.2 \
  types-PyYAML==6.0.12.20240917
.venv/bin/python -m pytest          # expect 375 passed
.venv/bin/mypy && .venv/bin/ruff check . && .venv/bin/ruff format --check .
```

**Two new runtime dependencies, both deliberately absent from that list and from CI:**
`psycopg[binary]` (the Postgres driver) and `uvicorn[standard]` (the ASGI server). Nothing imports
either — the tests run on SQLite and the pooling policy is a pure function precisely so it can be
asserted with no driver installed. `pip install -e .` gets you both. `reportlab` is needed only to
re-run `docs/make_roadmap.py`.

**The product gap, restated.** The application now **starts, connects, and files**: a signed
webhook comes in, the message is persisted, attributed to a tenant, classified, and the decision is
written to `audit_log` and `inbox_items`. What it still cannot do is **reply**. Nothing joins the
conversation to the task (A5) and nothing puts a message on the wire (A6). A guest who messages the
number today gets a perfectly filed silence.

---

## 2. What session 6 delivered

**Roadmap items A2 (0.5d) and A4 (1.0d) — 1.5 engineering days. Commit `bdd45bf`, unmerged.**

### A2 — `apps/api/db/engine.py`

Engine, sessionmaker, and the session scope. The tree had spoken SQLAlchemy since the data model
landed and had never connected; sessions existed only where a test constructed one.

The interesting part is the connection path, not the database:

1. **Transaction-mode pooling is assumed by default.** Supabase's application URI (port 6543) is
   pgbouncer in transaction mode, where a client connection is bound to a server connection for one
   transaction only. Neither psycopg's server-side prepared statements nor SQLAlchemy's own
   connection pool survives that, and both fail *intermittently, under load, in production* rather
   than in a test. `DATABASE_POOL_MODE` defaults to `transaction` → `NullPool` +
   `prepare_threshold=None`. `session` is the opt-out for a direct connection.
2. **The policy is a pure function of `(url, mode)`.** `engine_arguments()` returns the
   `create_engine` kwargs, which is what lets CI assert the pgbouncer rules with no Postgres driver
   installed at all.
3. **A bare `postgresql://` URI is rewritten onto psycopg 3.** SQLAlchemy resolves it to psycopg 2,
   which this project does not ship, so pasting the URI from the dashboard would have failed at the
   first connection. An explicitly named driver is preserved.
4. **`alembic/env.py` connects through the same two functions.** Migrations resolving a different
   driver from the application is something a deploy discovers on the day it matters. It also reads
   `DATABASE_URL` through `Settings` now (so a placeholder counts as unset there too) and escapes
   `%` before handing the URL to Alembic's ConfigParser, which would otherwise interpolate it.

`SessionScope` — a callable returning a context-managed session — is what every DB-touching object
takes now, rather than a `Session`. Objects built once at startup and called from a worker thread
per message cannot hold a session: not across threads, and not across messages.

### A4 — `apps/api/main.py`

The first production caller of `create_app()`:

```
uvicorn apps.api.main:create_application --factory --host 0.0.0.0 --port 8000
```

`create_application(settings=None)` reads the environment and builds the database and classifier;
`assemble(settings, database, classifier)` is the wiring, and is what the tests exercise. Nothing in
`create_app()` changed.

It was less "assembly" than the roadmap expected. Three of `create_app`'s four collaborators had no
implementation outside the test doubles, so A4 also brought the database ones:

| Port | Implementation |
|---|---|
| `MessageRepository` | `db/repository.py` · `SessionScopedMessageRepository` — delegates to the existing session-bound repository, one session per call |
| `MessageLoader` | `db/orchestration_repo.py` · `SqlAlchemyMessageLoader` — the persisted row plus the last 10 turns **by timestamp** (§7) |
| `AuditLog` / `InboxWriter` | `db/orchestration_repo.py` — the decision becomes a row the control page can show |
| `RulesProvider` / `CrmLookup` | `db/orchestration_repo.py` |
| `TenantResolver` | `db/tenant_resolver.py` · `ChannelConfigTenantResolver` |
| `ClassificationQueue` | `orchestration/queue.py` · `ThreadPoolClassificationQueue` |

### Decisions made this session

| Decision | Choice | Where it lives |
|---|---|---|
| Pooling policy | **Assume a transaction pooler**; `DATABASE_POOL_MODE=session` opts out. Wrong this way costs latency; wrong the other way is an intermittent outage | `db/engine.py`, `.env.example`, `docs/DECISIONS.md` |
| Driver | **psycopg 3**, with a bare `postgresql://` rewritten onto it | `db/engine.py`, `pyproject.toml` |
| Entrypoint shape | **`--factory`, no module-level `app`** — a module-level application is built at *import* time, so importing the module would read the environment and open an engine | `main.py` |
| Queue transport | **A process-level thread pool.** `BackgroundTasksQueue` needs a live request; inline consumption would make the webhook wait for two model calls, which §5 forbids | `orchestration/queue.py` |
| Tenant attribution | **`channel_configs`, and raise when there is no row.** A default tenant writes one customer's message into another's account | `db/tenant_resolver.py` |
| Channel neutrality of the lookup | **Do not filter by channel kind.** "Who owns this endpoint" is the same question for a phone line; `main.py` names no channel and `test_boundary.py` scans it | `db/tenant_resolver.py` |
| One threshold, not two | `Settings.tenant_policy()` applies the configured escalation threshold to the routing bands as well — `core/policy.py` exists to keep those converged | `core/config.py` |
| Roadmap artifacts | **Regenerated `make_roadmap.py` to v2.3** rather than letting a fourth version appear | `docs/make_roadmap.py` |

---

## 3. Traps and things not to re-litigate

- **Do not add a module-level `app` to `main.py`.** It looks tidier and it breaks importing the
  module on any machine without a full environment — which includes a test importing `assemble`.
  `--factory` is the whole point.
- **Do not make the webhook wait for classification.** The thread pool exists because `create_app`
  takes one queue for the life of the process and `BackgroundTasks` needs a live request. Inline
  consumption is a §5 violation: a platform that does not get a fast 200 retries, turning one
  guest's message into several. **B5 (arq/Redis) is the replacement**, on the same seam.
- **`DATABASE_POOL_MODE=transaction` is the safe default, not the fast one.** If you want session
  pooling, measure it during B1/B3 and flip the variable. Do not delete `NullPool` from the code
  because a local direct connection felt slow.
- **The tenant resolver raises on purpose.** `UnknownEndpoint` → 500 → platform retry, which
  succeeds once the row exists. Do not add a default tenant. **B1 must insert one enabled
  `channel_configs` row per endpoint** or every inbound message 500s — this is the first thing to
  check when the first deploy looks broken.
- **Do not wire the receptionist yet.** `assemble()` deliberately builds the orchestrator without
  one. Doing it before A5 produces a receptionist that forgets the previous turn (`worker.py` still
  hardcodes `task=None` and `extracted_slots={}`), and it has nothing to send with until A6.
- **`classifications` rows are still not written**, and the orchestrator does not surface
  `latency_ms` or `prompt_version`. Do not invent them to fill the columns — populate that table in
  A5, which is already in that code path.
- **`core/config.py` is still in `KNOWN_LEAKS`.** A6 is the moment to clear it: move the channel
  credential fields behind `channels/` and delete the entry.
- **`apps/api/tests/conftest.py` is load-bearing.** `StaticPool` + `check_same_thread=False` is what
  lets the worker thread see the same in-memory SQLite database the request wrote to. Without it the
  composition-root tests pass while proving nothing.
- **Do not re-add `temperature`** to the Anthropic payload, and **`_thinking_policy` is vendor
  contract, not preference** (see the A1/A3 spec). Unchanged from session 5.
- **`baseline.json` correctly records `claude-haiku-4-5-20251001`.** That is history. 2.7 rewrites it.
- **`docs/make_roadmap.py` is now current (v2.3).** Edit it and re-run it; do not hand-write a fifth
  version. `docs/ROADMAP.md`, `docs/NEXT-STEPS-v2.md` and `docs/HANDOFF.md` are historical.
- **The eval gate does not measure the prompt.** It replays fixtures recorded under prompt v2 and
  reports 88% whatever the prompt says. See §5.

---

## 4. What to do first — A5, then A6

**A5 — Wire continuity + excise the v1 filer (2.25d, NOW).** The largest remaining Track A item and
the one that makes "holds a conversation" true. `ConversationRepository` is still never called
outside tests. The orchestrator passes `task=None` and `extracted_slots={}`, so slot filling and task
continuity are inert. Also converts `process()` to async and removes the rules/destinations threading
(D24). Tables retained, not dropped. Two small things belong in this pass because it is already in
that code path: populating `classifications`, and deciding where `model_used` goes.

**A6 — Outbound sender (1.0d, NOW).** `ChannelSender` has no implementation; replies are composed
and never sent. `Settings.whatsapp_send_credentials()` already exists for it. Also the moment to
move the channel credential fields out of `core/config.py` and clear the last `KNOWN_LEAKS` entry.

**Then Track B.** B1 is half an hour of provisioning plus the `channel_configs` row; B3's Dockerfile
now has a known start command. **G3 (1.5d) before any real guest can message the number** —
`worker.py` hardcodes `emergency=False`, so a gas leak files a maintenance ticket.

---

## 5. Two numbers 2.7 should settle

Unchanged from session 5, and still unblocked — 2.7 needs only a key and a decision to spend it.

1. **The system prompt's real token count.** ~5k is a characters÷4 estimate. Haiku 4.5 will not
   cache a prefix below 4,096 tokens, and the cheap tier is where caching pays for itself. The
   estimate clears the floor by ~30% and the true count is probably higher, so caching almost
   certainly activates — but if the vocabulary shrinks it stops silently: no error, just a larger
   bill. Sonnet 5's floor is 1,024, so escalation is not exposed.
2. **Cost per message.** Measurable now: `provider.last_usage` reports `input_tokens`,
   `output_tokens`, `cached_input_tokens` and `cache_hit_ratio` per call. Measure before quoting.

Also worth doing in 2.7: add Franco-Arabic cases to the golden set.

---

## 6. Where things live

| What | Where |
|---|---|
| Entrypoint / wiring | `apps/api/main.py` — `create_application`, `assemble` |
| Engine, sessions, pooling policy | `apps/api/db/engine.py` — `Database`, `SessionScope`, `engine_arguments` |
| DB port implementations | `apps/api/db/orchestration_repo.py`, `db/repository.py`, `db/tenant_resolver.py` |
| Queue transports | `apps/api/orchestration/queue.py` — inline, BackgroundTasks, thread pool |
| Typed config | `apps/api/core/config.py` — `Settings`, `get_settings()`, `tenant_policy()` |
| LLM providers | `apps/api/classifier/anthropic.py`, `openai.py`, `factory.py` |
| Why A2/A4 are shaped this way | `docs/specs/a2-database-and-a4-composition-root.md` |
| Why A1/A3 are shaped this way | `docs/specs/a1-configuration-and-a3-llm-providers.md` |
| Current plan | `docs/Watcher_v2_Roadmap.pdf` (v2.3) ← `docs/make_roadmap.py` |
| Locked decisions | `docs/DECISIONS.md` |
| Channel-boundary rules | `apps/api/tests/test_boundary.py` — `KNOWN_LEAKS`, `CHANNEL_REGISTRY` |

---

## 7. Roadmap status against v2.3

| Track | Remaining | Note |
|---|---|---|
| **A — Make it run** | **3.25d** | A1 ✅ A2 ✅ A3 ✅ A4 ✅ · A5 next, then A6 |
| B — Host it | 4.25d | B1 also needs the `channel_configs` row |
| 2 — Receptionist | 3.5d | 2.4 knowledge, 2.7 eval (unblocked), 2.8 many properties |
| D — Control page | 13.75d | D2's 3 backend days are the hidden half |
| G — Guardrails | 5.5d | G3 emergency path is not optional before a real guest |
| E — Sellable | 4.5d | Blocked on P1 for sequencing |
| 3 — Integration | 5.5d | 3.1 blocked on P1 |
| **Total** | **~40.25d** | 13–20 sessions at the observed rate; ~9 weeks to sellable |

**Milestone M1 — answers a real message, safely: ~10 days** (Track A + B1–B4 + G3 + 2.4).

---

## 8. First five minutes of the next session

1. **Session-6 work is not on `main`.** Either open and merge a PR for
   `claude/a2-a4-roadmap-ju60pc` first, or branch from it:
   `git fetch origin && git checkout -B <new-branch> origin/claude/a2-a4-roadmap-ju60pc`.
   Do **not** branch from `main` and start A5 — you will be building on a tree with no entrypoint.
2. Rebuild the venv from §1 and confirm **375 passed**.
3. Read §3 (traps) and `docs/specs/a2-database-and-a4-composition-root.md`.
4. Start A5. It is the last thing between a system that files and a receptionist.
