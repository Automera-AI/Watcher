# Spec — A2 DB engine and session · A4 Composition root

**Status:** Implemented. Roadmap v2.2 Track A, items A2 (0.5d) and A4 (1.0d).
**Why these two, in this order:** the roadmap's own instruction — "if you do only one thing next
session: A2, then A4". A1 gave the process its configuration and A3 gave it a model; neither is
reachable from a process that does not exist. A2 is the last dependency the entrypoint was waiting
on, and A4 is the entrypoint. After this, `create_app()` has a production caller and the pipeline
runs end to end: a signed webhook in, a classified, audited, filed message out.

---

## A2 — DB engine and session

### The problem

Every layer above the connection already spoke SQLAlchemy — the ORM models, the message repository,
the conversation repository — and nothing had ever connected. Sessions existed only where tests
constructed them, one per test, against in-memory SQLite. `DATABASE_URL` was read by
`alembic/env.py` for migrations and by nothing else.

### Module boundary

`apps/api/db/engine.py` — `create_db_engine`, `Database`, `build_database`, and the `SessionScope`
type everything else takes instead of a `Session`.

| Name | Contract |
|---|---|
| `normalize_database_url` | `postgres://` / `postgresql://` → `postgresql+psycopg://`. An explicitly named driver is preserved. |
| `engine_arguments` | `(url, create_engine kwargs)` — the pooling policy, as a pure function. |
| `create_db_engine` | The engine those two describe. |
| `Database` | An engine plus its sessionmaker, and the only blessed way to get a session out of them. |
| `Database.session()` | One unit of work: commit on success, **roll back on anything else**, close either way. |
| `Database.get_session()` | The same scope in FastAPI-dependency form. |
| `build_database(settings)` | The process's database, or `ConfigError` naming `DATABASE_URL`. |

### The decision that matters: the connection path, not the database

Supabase's application URI (port 6543) is pgbouncer in **transaction mode**. A client connection is
bound to a server connection only for the duration of a transaction, and the next transaction may
land on a different one. Two things do not survive that, and they fail differently:

* **Server-side prepared statements.** psycopg 3 prepares a statement after its fifth execution and
  then refers to it by name. The sixth execution may be on a backend that has never heard of it —
  `prepared statement "_pg3_0" does not exist`, or `already exists` in the other direction. Under
  load, intermittently, and never in a test. `prepare_threshold=None` disables preparation.
* **A client-side connection pool.** SQLAlchemy holding connections between checkouts is holding
  server connections pgbouncer wants to reassign. `NullPool` opens one per checkout and hands it
  straight back.

`DATABASE_POOL_MODE` selects the policy and defaults to `transaction`, which is the one that is
*always correct* and merely slower when it is unnecessary. `session` is the opt-out for a direct
connection (port 5432), where ordinary pooling with `pool_pre_ping` is safe and faster. The mode is
a variable rather than a guess from the URL because a pooler can be put in front of any host on any
port — the port is a hint, not a fact, and the failure from guessing wrong is the intermittent one
above.

The policy is a pure function of the URL and the mode. That is what lets CI — which installs no
Postgres driver at all — assert the pgbouncer rules directly, rather than trusting a comment.

**Still open, deliberately:** the load test the roadmap asks for. `NullPool` trades a TCP+TLS
handshake per checkout for pooler safety, and whether that is material at real volume is a
measurement, not an argument. Do it in B1/B3 against the provisioned project, and flip
`DATABASE_POOL_MODE` rather than editing code if the answer is that session mode wins.

### Tests — `apps/api/tests/test_engine.py` (21)

URL normalization in both directions, the three pooling policies, both halves of the transaction-mode
rule asserted separately, the session scope's commit/rollback/close behaviour including the failure
path, and `build_database` against a configured and an unconfigured environment.

---

## A4 — Composition root

### Module boundary

`apps/api/main.py`. Two functions:

* `create_application(settings=None)` — reads the environment, builds the database and the
  classifier, assembles. Four lines.
* `assemble(settings, database, classifier)` — the wiring itself, over collaborators it is given.
  This is the function the tests exercise; the one above is the part that decides what to pass it.

```
uvicorn apps.api.main:create_application --factory --host 0.0.0.0 --port 8000
```

`--factory` rather than a module-level `app`. A module-level application is built at *import* time,
so importing the module would read the environment, open an engine, and fail on any machine that has
neither — including a linter, a type checker, or a test importing it for something else.

### What A4 needed that did not exist

`create_app` takes its collaborators, and A4 is where they come from. Three of the four had no
implementation outside the test doubles:

| Port | Implementation | Notes |
|---|---|---|
| `MessageRepository` | `db/repository.py` · `SessionScopedMessageRepository` | Delegates to the existing session-bound repository, opening a session per call. A repository pinned to one session for the process's lifetime holds a pooled connection forever. |
| `MessageLoader` | `db/orchestration_repo.py` · `SqlAlchemyMessageLoader` | Reloads the persisted row (§5) plus the last 10 turns in the thread, **by timestamp, oldest→newest** (§7). |
| `AuditLog` / `InboxWriter` | `db/orchestration_repo.py` | The decision stops being a return value and becomes a row the control page can show (§4, §12). |
| `RulesProvider` / `CrmLookup` | `db/orchestration_repo.py` | Enabled rules in priority order; cached records for identity dedup (D9-a, cache-only). |
| `TenantResolver` | `db/tenant_resolver.py` · `ChannelConfigTenantResolver` | Endpoint identifier → tenant, via `channel_configs`. |
| `ClassificationQueue` | `orchestration/queue.py` · `ThreadPoolClassificationQueue` | See below. |

Every one of them takes a `SessionScope` rather than a `Session`: they are built once at startup and
called from a worker thread per message, and a session shared across threads is not a thing
SQLAlchemy supports.

### Decisions

**The queue is a thread pool, and that is a real constraint, not a preference.** `create_app` is
handed one queue for the life of the application. `BackgroundTasksQueue` cannot be that queue — it is
bound to a single request's `BackgroundTasks`, which exists only while a request is in flight — and
`InlineClassificationQueue` would make the webhook wait for up to two model calls before answering,
which is precisely what §5 forbids, because a platform that does not get a fast 200 retries and turns
one guest's message into several. So the composition root gets a pool and the request thread's
involvement ends at `submit`. In-flight work is lost on restart; persistence-before-enqueue means what
is lost is a classification, not a message. **B5 (arq/Redis) replaces this against the same seam.**

**Tenant resolution refuses to guess.** An endpoint with no enabled `channel_configs` row raises
`UnknownEndpoint`, which surfaces as a 500 and a platform retry that succeeds once the row exists.
The alternatives are both worse: a default tenant writes one customer's message into another's
account, and returning quietly loses a real guest's message to a configuration mistake nobody would
ever see. **B1 must insert the row when it provisions the project** — this is the first thing to
check if a deployed webhook 500s.

**The lookup does not filter by channel kind.** The question is "who owns this endpoint", and it is
the same question for a phone line as for a chat number. A resolver told which kind to expect would
need editing on the day a second channel is connected, which is the coupling `channel_configs` exists
to remove. `main.py` names no channel at all, and `test_boundary.py` scans it to keep it that way.

**One threshold, not two.** `Settings.tenant_policy()` applies the configured escalation threshold to
the routing bands as well. `core/policy.py` was written to keep those converged — a message is
escalated because it is not confident enough to act on, and the band decides whether to act on it
using the same number — and configuring one without the other reintroduces exactly the split that
module removed. Per-*tenant* overrides are a different thing and land with the control page.

**A malformed rule is skipped, not fatal.** `rules.conditions` is jsonb written by the control page,
so a row can be wrong in a way the column type cannot catch. Raising would take a tenant's whole
routing path down for one bad rule; the rule is dropped with a warning naming it and the message is
still routed by confidence band.

### What is deliberately not wired

**No receptionist.** The orchestrator is built without one, so the pipeline files and does not reply.
Wiring it now would produce a receptionist that forgets the previous turn — `worker.py` still passes
`task=None` and `extracted_slots={}`, which is **A5** — and it would have nothing to send a reply
with, which is **A6**. Filing works end to end; answering is the next two items, in that order.

**No media pipeline.** `MediaPipeline` needs an ASR/vision implementation behind `media/ports.py`,
and there is none. A voice note is persisted and classified on an empty transcript until there is.

**No property system.** `create_app` takes `property_system` and `resolve_api_key` together or not at
all, and it is not configured — that is item 3.1, blocked on P1.

**`classifications` rows are still not written.** The audit row carries the classification snapshot
(§4 designed it to), but the `classifications` table wants `latency_ms` and `prompt_version`, and the
orchestrator does not surface either; nor is `model_used` stored anywhere, since `inbox_items` has no
column for it. Populating that table properly is a small piece of work that belongs with A5, which is
already touching the same path — do not invent the missing telemetry to fill the columns.

### Error cases

| Case | Result |
|---|---|
| Missing environment variable | `ConfigError` at startup, naming every one of them, before a message can arrive |
| Unknown or disabled endpoint | `UnknownEndpoint` → 500 → platform retry |
| Message row gone at consume time | Logged by `MessageConsumer`, no crash |
| Anything raised inside a worker thread | Logged by the queue — a future nobody retrieves is otherwise a silent failure |
| Model returns unusable output twice | Existing §8 path: unclear → `needs_review` in the inbox |

### Tests — `apps/api/tests/test_main.py` (9) and `test_db_adapters.py` (16)

`test_main.py` assembles the real graph over SQLite with one stub model and pushes a signed webhook
through it: rows in `messages`, `audit_log` and `inbox_items`, tenant resolved through
`channel_configs`, idempotency on redelivery, low confidence filed for review, and — with a provider
that blocks until released — the assertion that the 200 arrives before classification has finished.
`test_db_adapters.py` covers the places where a row and the object it becomes are not the same shape:
the history window and its ordering, the source kind that lives on another table, the nullable band
written into a non-nullable column, and the malformed rule.

---

## What this does not do

Not in A2/A4: no outbound sender (A6), no conversation continuity (A5), no Supabase project (B1), no
RLS policy (B2), no Dockerfile (B3), no durable queue (B5). The process starts, listens, and files.
It does not yet answer.
