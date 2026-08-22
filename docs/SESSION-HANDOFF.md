# Session handoff — read this first

**Updated:** end of session 11, 22 August 2026
**Branch:** `claude/webhook-setup-b4-b5-757alb` — branched from `main`@`250cfd6` (PR #24 / item 2.4
already merged). Not yet merged; push and open a PR before starting a new session on top of it.
**`main` is at:** `250cfd6` — PR #24 (2.4, the knowledge base) is merged. This session's commit
(B5, reconciled with 2.4) is on top of that, on this branch, not yet a PR.
**Deployed:** https://watcher-api-lup7.onrender.com / https://webhook.automera.co — live, serving
`250cfd6` (this session's B5 commit is not deployed; see §4).
**Start at §2 for status, §4 for what to do first.**

Purpose: let a new session pick up without re-deriving anything. Companion documents are
`docs/Watcher_v2_Roadmap.pdf` (**v2.12**, regenerated this session from `docs/make_roadmap.py`)
and the specs in `docs/specs/`.

**Two branches existed with real, unmerged work at session start**: this one (`claude/roadmap-2-1-2-2-zodk41`,
item 2.4, already merged as PR #24 by the time this session started) and
`claude/session-handoff-demo-timeline-goqvbe` (item B5, the durable queue, forked from the commit
*before* 2.4 and never merged). This session reconciled the two onto the current branch — see §2.

Overwrite this file at the end of each session.

---

## 1. Verified state right now

Measured by running it or reading the live systems back, not read off a document.

| | |
|---|---|
| Branch | `claude/webhook-setup-b4-b5-757alb`, based on `main`@`250cfd6` (2.4 merged) |
| Tests | **521 passing**, 2 skipped without a Postgres (506 after 2.4 alone; +15 net from reconciling B5) |
| Lint / types | ruff clean; strict mypy clean on 136 source files |
| Database | live — Supabase `watcher-prod`, `qjpjxspycuafqqgudsiv`, eu-central-1, PG 17 |
| Schema | `alembic_version` includes `005_facts` per session 10's handoff — **not independently re-verified this session; check before trusting it** |
| `channel_configs.external_id` | confirmed still fixed — real 16-digit id, `enabled = true` (read directly from Supabase this session) |
| `messages` table | **0 rows.** Confirmed by direct query this session — see §2 for what this means for B4 |
| Send credentials | `WHATSAPP_ACCESS_TOKEN` / `WHATSAPP_PHONE_NUMBER_ID` — not independently re-verified this session (can't read secret values via the Render MCP); inherited as set from session 10 |
| Service | live — Render `watcher-api`, `srv-da0a81jl550s73d0b1i0`, **plan: `free`** (confirmed via the Render MCP this session — still the cold-start blocker) |
| Custom domain | **confirmed** — `webhook.automera.co` is the service's primary URL (Render's own deploy log names it explicitly) |
| Webhook subscription | **confirmed NOT verified — likely not actually subscribed.** See §2 |
| Redis | **`watcher-redis`**, a free-plan Render Key Value instance in Frankfurt, created this session. Not yet wired to anything — see §2 |
| Worker service | **does not exist.** This session could not create it — see §2 |
| Python | 3.13 |

To get green in a fresh container:

```bash
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python ruff==0.6.9 mypy==1.11.2 pytest==8.3.3 \
  pydantic==2.9.2 pydantic-settings==2.5.2 fastapi==0.115.0 httpx==0.27.2 \
  rapidfuzz==3.10.1 sqlalchemy==2.0.50 alembic==1.13.3 pyyaml==6.0.2 \
  types-PyYAML==6.0.12.20240917 arq
.venv/bin/python -m pytest          # expect 521 passed, 2 skipped
.venv/bin/mypy && .venv/bin/ruff check . && .venv/bin/ruff format --check .
```

`psycopg[binary]`, `uvicorn[standard]` and `reportlab` are still deliberately absent from that list
and from CI — `reportlab` is needed only to re-run `docs/make_roadmap.py`.

---

## 2. What session 11 delivered

### Part one — reconciling B5 (the durable queue) onto main after 2.4

Full context: `docs/DECISIONS.md` D38–D40.

**The problem.** `claude/session-handoff-demo-timeline-goqvbe` had built B5 — a real,
tested `RedisClassificationQueue`, `apps/api/worker.py` as an arq consumer, and
`orchestration/composition.py` so the API and the worker build one identical object graph — but
it forked before 2.4 merged, and was never pushed as a PR against a repo the session had access
to look up (it existed only as a remote branch). Meanwhile 2.4 had merged (PR #24) and put its own
wiring — `configure_knowledge` — directly inline in `main.py`'s `assemble()`, the same place B5
had restructured into a `REDIS_URL` branch.

**The fix.** Cherry-picked B5's commit onto `main`@`250cfd6`. The only genuine code overlap
between the two branches was `main.py` — every other touched file was disjoint (`comm -12` on the
two diffs' file lists confirms this). Resolved by keeping B5's structure (the `REDIS_URL` branch,
`build_consumer` from `composition.py`) and moving 2.4's `configure_knowledge(SqlAlchemyFactRepository(...))`
call into `composition.build_consumer` itself, so both the in-process path (`main.py`, no
`REDIS_URL`) and the arq worker (`apps/api/worker.py`) get the knowledge base wired identically.
`docs/DECISIONS.md`'s D35–D37 (2.4) and B5's own D35–D36 collided on numbering since both
branched from the same commit; B5's became D38–D39, with a new D40 recording where the knowledge
wiring ended up.

**Verified, not assumed.** 521 tests pass (506 after 2.4 alone, +15 net — B5 added tests for the
new queue transport and worker composition root), ruff clean, mypy clean on 136 source files.

### Part two — B4: the webhook subscription claim did not survive a direct check

The session handoff carried into this session (and the one before it, and the one before that)
said the operator's own account reported the webhook subscribed in Meta via a custom domain, with
each session noting it had not independently re-verified this. This session did, directly:

1. **The custom domain is genuinely correctly configured.** Render's own deploy log states
   explicitly: `Available at your primary URL https://webhook.automera.co + 1 more domain`. Not
   an assumption — Render's own output.
2. **Both Meta credentials are set.** The app starts clean (`Application startup complete` in the
   logs) with no `ConfigError`, which `settings.meta()` would raise if `META_APP_SECRET` or
   `META_WEBHOOK_VERIFY_TOKEN` were missing.
3. **But `list_logs` on the Render service, filtered to `path: /webhook`, over the service's
   entire lifetime (since Aug 15), returns zero results.** No GET (handshake) and no POST
   (message) has ever hit that path. The only recorded inbound HTTP traffic at all is a couple of
   health-check-shaped `GET /` 404s.
4. **`select count(*) from messages` on the live Supabase database returns 0.** Confirms (3):
   nothing has ever actually been ingested.
5. **This sandbox could not fire a test request itself** — the environment's egress proxy blocks
   outbound connections to arbitrary hosts, including both `webhook.automera.co` and the
   `onrender.com` URL directly (confirmed via `WebFetch` and via the proxy's own status endpoint,
   which logged both as `connect_rejected`).

**Conclusion:** the infrastructure is right; the Meta-side subscription step was not actually
completed, or was reset at some point and never redone. The fix is entirely operator-side — see
§4 — and needs a *positive* check next time (a real handshake or message showing up), not a
repeated claim.

### Part three — provisioning Redis, and where the tooling stops

At the user's request, created **`watcher-redis`**, a free-plan Render Key Value instance in
Frankfurt (`red-da4v8ngu01pc73dfkr30`). This is genuinely live.

**What could not be finished, and why it's a tool gap, not a decision not to:**

- **No connection string is readable.** The Render MCP's `get_key_value`/`list_key_value` don't
  expose it — by the same design that makes `update_environment_variables` write-only. It's only
  visible on the instance's own dashboard page: https://dashboard.render.com/r/red-da4v8ngu01pc73dfkr30
- **No worker service exists, and this session's tooling cannot create the right thing.** Render
  has two service types: Web Service (needs an open HTTP port) and Background Worker (doesn't).
  The only creation tool available (`mcp__Render__create_web_service`) makes the former. An arq
  worker binds no port at all — pointed at that tool, the result would be a service Render kills
  repeatedly for "no open port detected," not a working consumer.
- **No secret values are copyable.** `DATABASE_URL`, `ANTHROPIC_API_KEY`/`OPENAI_API_KEY`,
  `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `TENANT_TIMEZONE` are already set on
  `watcher-api`, needed again on the worker, and unreadable through the same seam that makes (1)
  true.

**`REDIS_URL` was deliberately left unset on `watcher-api`.** Setting it flips `main.py`'s
`assemble()` to the thin-producer branch — no orchestrator, no sender, no alerter in that process
at all. With no worker consuming the queue, that would turn the *currently working* in-process
pipeline into one that accepts a message and never classifies or replies to it — a regression, not
progress, and the opposite of what B4 is trying to achieve. Do not set it until the worker
service below is created and confirmed consuming jobs.

### Decisions made this session

| Decision | Choice | Where it lives |
|---|---|---|
| Where the knowledge wiring lives after B5 | **`orchestration/composition.build_consumer`, not `main.py`** (D40) | `apps/api/orchestration/composition.py` |
| How `RedisClassificationQueue.enqueue` reaches Redis | **fire-and-forget, synchronous seam, logged not awaited** (D38, from B5's original branch) | `apps/api/orchestration/queue.py` |
| Where the orchestrator graph is built | **one function, called by both processes** (D39, from B5's original branch) | `apps/api/orchestration/composition.py` |

---

## 3. Traps and things not to re-litigate

Everything in session 10's list still holds except the webhook item below, which it resolves (by
re-opening it, not by closing it further). This session adds:

- **The webhook is not subscribed, whatever the last three sessions' handoffs said.** Don't carry
  the "subscribed via a custom domain" claim forward again. The next positive check is either a
  `GET /webhook` handshake showing up in Render's logs, or a real WhatsApp message producing a row
  in `messages` — nothing short of one of those two should be read as confirmation.
- **`REDIS_URL` must stay unset on `watcher-api` until the worker service exists and is verified
  running.** Setting it early silently breaks message delivery — see §2, part three. There is no
  test in this repo that would catch this, because it's a deploy-time/infra ordering issue, not a
  code one.
- **The worker service needs to be created by hand, as a Background Worker, not through
  `mcp__Render__create_web_service`.** See §4 for the exact settings.
- **B5's Redis instance has `persistenceMode: off`** — the free plan doesn't support persistence.
  Data (queued jobs) does not survive a Redis restart. This is a real, if narrow, gap in B5's
  "durable" claim on the free tier; upgrading the Key Value plan closes it, same as the API's own
  free-plan cold-start problem.
- **Migration `005_facts`'s live status was not re-verified this session.** Session 10's handoff
  said it had been applied; this session took that on trust rather than re-querying
  `alembic_version` directly. Confirm before assuming it's true.
- **`REGISTRY` (`conversations/tools.py`) is still process-global mutable state**, and now there
  are two processes (the API and the arq worker) that can each populate it independently at
  startup — `composition.build_consumer` is the one place that happens, on purpose, so a fourth
  registry-backed tool still only needs to be wired there once.
- Everything from session 10 not superseded above: the door/key/unit-code exclusion in the facts
  table, `MATCH_THRESHOLD = 60.0` untuned beyond one property, narrow `intents.yaml` triggers
  still not widened, `CONTROL_CHAT_PHONE_E164` still unset, `DATABASE_URL` must name `watcher_app`
  not `postgres`, the transaction-pooler port 6543, the webhook path is `/webhook`, RLS/adapter
  rules, D24 (no rules/destinations in the orchestrator), `KNOWN_LEAKS` empty, no module-level
  `app`, no `temperature`, the eval gate not measuring the prompt.

---

## 4. What to do first

**1. Redo the Meta webhook subscription — don't just check whether it's marked done.** Meta App
Dashboard → your WhatsApp app → Configuration → Webhook. Callback URL:
`https://webhook.automera.co/webhook`. Click **Verify and Save**, and confirm the `messages`
field is subscribed under Webhook fields (not just that a URL is filled in). Then send one real
WhatsApp message to the business number and confirm it shows up — either a new row in
`messages`, or a `POST /webhook` 200 in Render's request logs. Only that counts as confirmation.

**2. Finish provisioning B5** (10 minutes, manual — the tooling gap in §2 part three):
   - Grab the internal Redis URL from https://dashboard.render.com/r/red-da4v8ngu01pc73dfkr30
   - Render dashboard → New → **Background Worker** (not Web Service) → same repo, branch `main`
     (or wherever this session's B5 commit lands after merging)
   - Build command: `pip install .` — start command: `arq apps.api.worker.WorkerSettings`
   - Env vars: copy `DATABASE_URL`, `ANTHROPIC_API_KEY`/`OPENAI_API_KEY`, `WHATSAPP_ACCESS_TOKEN`,
     `WHATSAPP_PHONE_NUMBER_ID`, `TENANT_TIMEZONE`, `CONTROL_CHAT_PHONE_E164` (once set) from
     `watcher-api`'s own settings, plus the new `REDIS_URL`
   - Deploy it, confirm the worker logs show `Application startup complete` (arq's own) with no
     errors, **then and only then** set `REDIS_URL` on `watcher-api` itself and redeploy
   - Send a real message and confirm it round-trips through the worker (a reply arrives)

**3. `CONTROL_CHAT_PHONE_E164`** — still unset. Without it, an emergency is detected, answered and
filed, and the only alert is a log line.

**4. Upgrade `watcher-api` off the free Render plan** — confirmed still `free` this session.

**5. Then 2.7** (re-record the eval under prompt v3) **or 2.8** (many properties per client).

---

## 5. Two numbers 2.7 should settle

Unchanged. Not touched this session.

1. **The system prompt's real token count.**
2. **Cost per message.**

---

## 6. Where things live

| What | Where |
|---|---|
| Entrypoint / wiring (API process) | `apps/api/main.py` — `create_application`, `assemble` |
| **The shared consumer graph (new, B5)** | `apps/api/orchestration/composition.py` — `build_consumer`, `ConsumerGraph` |
| **The arq worker's own composition root (new, B5)** | `apps/api/worker.py` — `WorkerSettings`, `startup`, `shutdown`, `consume_message` |
| **The durable queue transport (new, B5)** | `apps/api/orchestration/queue.py` — `RedisClassificationQueue`, `build_redis_pool` |
| The pipeline | `apps/api/orchestration/worker.py` — `Orchestrator.process` |
| The receptionist / task dispatch | `apps/api/conversations/receptionist.py` |
| The knowledge base | `apps/api/core/knowledge.py`, `apps/api/db/knowledge_repo.py` |
| The tool registry | `apps/api/conversations/tools.py` — `AnswerFromKnowledge`, `configure_knowledge`, `REGISTRY` |
| The webhook routes | `apps/api/ingestion/router.py` — `GET`/`POST /webhook` |
| The emergency detector | `apps/api/core/emergency.py` |
| The alert seam / its implementation | `apps/api/core/alerts.py`, `apps/api/channels/alerting.py` |
| Typed config | `apps/api/core/config.py` + `core/settings_base.py` — `redis_url`/`redis_dsn()` new this session (from B5) |
| CI — dependency install list | `.github/workflows/ci.yml` — now installs `arq` |
| Locked decisions | `docs/DECISIONS.md` (D38–D40 are this session's) |
| The roadmap | `docs/make_roadmap.py` → `docs/Watcher_v2_Roadmap.pdf` (**v2.12**) |
| Render services | `watcher-api` (`srv-da0a81jl550s73d0b1i0`), `watcher-redis` (`red-da4v8ngu01pc73dfkr30`, new this session) |

---

## 7. Roadmap status against v2.12

| Track | Remaining | Note |
|---|---|---|
| A — Make it run | 0d | complete since session 7 |
| B — Host it | 0.5d | B1–B3, B5 ✅ — B4's webhook item re-opened this session |
| 2 — Receptionist | 1.5d | 2.4 ✅ — left: 2.7 (unblocked), 2.8 |
| D — Control page | 13.75d | unchanged |
| G — Guardrails | 4.0d | G3 ✅ — G1, G2, G4 remain |
| E — Sellable | 4.5d | Blocked on P1 |
| 3 — Integration | 5.5d | 3.1 blocked on P1 |
| **Total** | **~29.75d** | at the observed rate, ~7 weeks to sellable |

**Milestone M1 — answers a real message, safely:** still B4 alone, but its first checklist item —
the Meta subscription — needs to be *redone*, not re-confirmed. See §4.

---

## 8. First five minutes of the next session

1. Read §2 and §4 above before doing anything else.
2. Redo the Meta webhook subscription and confirm with a real message — don't trust the last
   handoff's word for it, including this one; re-check `messages` row count or Render's request
   logs directly.
3. If picking up B5: finish provisioning the worker service (§4 item 2) — the code side is done
   and merged onto this branch; only the Render-side wiring remains, and it's a 10-minute manual
   task, not engineering.
4. Rebuild the venv from §1 and confirm **521 passed, 2 skipped**.
5. Check whether the operator has set `CONTROL_CHAT_PHONE_E164` or moved off the free Render plan
   — neither was done as of this session's end.
6. This branch (`claude/webhook-setup-b4-b5-757alb`) has not been opened as a PR yet — do that
   before building further on top of it.
