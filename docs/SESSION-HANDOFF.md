# Session handoff — read this first

**Updated:** end of session 12, 23 August 2026
**Branch:** `claude/roadmap-update-tasks-0rnm5z` — branched from `main`@`ce32618` (PR #25 merged, so
sessions 1–11 are all on `main`). This session's work is on this branch; push and open a PR.
**`main` is at:** `ce32618` — the B4/B5 merge (PR #25). This session added no commits to `main`.
**Deployed:** https://watcher-api-lup7.onrender.com / https://webhook.automera.co — live, serving
`ce32618`. `watcher-worker` (the arq worker) is also live on `ce32618`. This session's branch is
not deployed.
**Start at §2 for status, §4 for what to do first.**

Companion documents: `docs/Watcher_v2_Roadmap.pdf` (**v2.13**, regenerated this session from
`docs/make_roadmap.py`) and the specs in `docs/specs/`.

Overwrite this file at the end of each session.

---

## 1. Verified state right now

Measured by running it or reading the live systems back, not read off a document.

| | |
|---|---|
| Branch | `claude/roadmap-update-tasks-0rnm5z`, based on `main`@`ce32618` |
| Tests | **535 passing**, 2 skipped without a Postgres (was 521; +14 from 2.8) |
| Lint / types | ruff clean; strict mypy clean on 139 source files; `ruff format` clean |
| Database | live — Supabase `watcher-prod`, `qjpjxspycuafqqgudsiv`, eu-central-1, PG 17 |
| **Schema** | **`alembic_version` = `005_facts`** — migration 005 (2.4's facts table) was found still unapplied (at 004) and **applied to production this session**. Verified: `facts` exists, RLS enabled+forced, one tenant-isolation policy, `watcher_app` can SELECT/INSERT. **Migration 006 (properties, this session) is NOT applied** — it lands with the 2.8 deploy; see §3/§4 |
| `messages` table | **0 rows**, `conversations` **0 rows** — nothing has ever been ingested. Confirmed by direct query this session |
| Webhook subscription | **still no positive proof.** `/webhook` request logs on `watcher-api`: zero hits since Aug 16. The operator reports the Meta subscription completed; the first real inbound message landing a `messages` row is the check that closes this |
| Send credentials | `WHATSAPP_ACCESS_TOKEN` / `WHATSAPP_PHONE_NUMBER_ID` — inherited as set; not re-verified (can't read secret values via the Render MCP) |
| `CONTROL_CHAT_PHONE_E164` | operator reports it set, but the deployed **worker still logged** "no emergency alert path configured" on its last boot (Aug 22). A redeploy showing that warning gone is the positive proof |
| Service (API) | live — Render `watcher-api`, `srv-da0a81jl550s73d0b1i0`, **plan: `free`** (confirmed via the Render MCP this session — the one open B4 item) |
| **Worker** | **live — `watcher-worker`, `srv-da4vob3ncjis73fafi10`, a paid `starter` Background Worker** running `arq apps.api.worker.WorkerSettings`, deployed on `ce32618`. Logs confirm it started for `consume_message` and connected to Redis. This closed B5's last open piece |
| Redis | `watcher-redis`, `red-da4v8ngu01pc73dfkr30`, Frankfurt Key Value instance, live |
| `REDIS_URL` on watcher-api | **still deliberately unset** — see §3. The worker is live and consuming, but the API stays on its in-process path until `REDIS_URL` is set to flip it to the producer branch |
| Python | 3.13 |

To get green in a fresh container:

```bash
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python ruff==0.6.9 mypy==1.11.2 pytest==8.3.3 \
  pydantic==2.9.2 pydantic-settings==2.5.2 fastapi==0.115.0 httpx==0.27.2 \
  rapidfuzz==3.10.1 sqlalchemy==2.0.50 alembic==1.13.3 pyyaml==6.0.2 \
  types-PyYAML==6.0.12.20240917 arq
.venv/bin/python -m pytest          # expect 535 passed, 2 skipped
.venv/bin/mypy && .venv/bin/ruff check . && .venv/bin/ruff format --check .
```

`reportlab` is an extra needed only to re-run `docs/make_roadmap.py`; `anthropic` only to run
`scripts/measure_prompt.py` with a key. Neither is a project dependency.

---

## 2. What session 12 delivered

### Part one — verified the live systems, and closed B4/B5 on the roadmap

The uploaded handoff was from session 10 and predated PR #24/#25; the in-repo handoff was session
11's. This session checked the live systems directly rather than trusting either:

- **B5 is fully done.** `watcher-worker` — the arq Background Worker that was session 11's one
  unprovisioned piece — now exists as a paid Render service, deployed and live on `ce32618`, its
  logs confirming it started for `consume_message` and connected to Redis. Redis (`watcher-redis`)
  backs it.
- **B4 is done bar the plan upgrade.** On the user's instruction, the Meta webhook subscription and
  the operator number are taken as completed operator-side. The one remaining item is moving
  `watcher-api` off the Render free plan. The custom domain and both Meta credentials stay
  confirmed. *Caveat carried forward honestly:* at check time `messages` was still empty and
  `/webhook` had zero recorded hits, so the first real inbound message is still the positive proof.

Roadmap **v2.13** records this (B5 → DONE, B4 → done bar the upgrade), regenerated from
`docs/make_roadmap.py`.

### Part two — applied migration 005 to production

Production schema was still at `004_row_level_security` — migration `005_facts` (2.4's knowledge
table) had **never been applied**, so a real `property_question` would have hit "relation facts does
not exist". Applied it this session via the Supabase MCP, replicating the migration exactly (table,
two indexes, RLS enable+force, revoke anon/authenticated, tenant-isolation policy) and stamping
`alembic_version` to `005_facts`. Verified by reading the schema back.

### Part three — item 2.8, many properties per client (DONE)

Full context: `docs/DECISIONS.md` D41–D42.

**The problem.** A client is an agency with many units, but a `FactRow` carried only `tenant_id`, so
every fact was true of the whole tenant. "The wifi password is…" belongs to one flat, not all of
them.

**The build.**
- A **`properties` table** (`Property` model, migration `006`) behind the same forced RLS every
  tenant table carries — `name`, nullable `external_id` (the PMS's id, the 3.1 join key), nullable
  `timezone`, `active`.
- A nullable **`facts.property_id`** (FK → `properties.id`). `NULL` = tenant-wide (the common case);
  a set value scopes the fact to one unit. `SqlAlchemyFactRepository.search(tenant_id, property_id)`
  returns tenant-wide facts **plus** the resolved property's — never another property's.
- **Message → property resolution** (`core/property.py`, `db/property_repo.py`): an explicit
  endpoint hint wins and is authoritative; else a single-property tenant resolves to its one unit;
  else `None` (tenant-wide only). A guest on a shared number is never answered from the wrong unit's
  sheet. Wired into `AnswerFromKnowledge` via `configure_knowledge(knowledge, properties)` in
  `orchestration/composition.build_consumer`.

**Honest scope.** The single-property fallback is the path that runs in production today; the hint
path (a number wired to one unit) is built and tested but not yet plumbed through the receptionist,
because no channel carries a property-scoped endpoint yet. The booking-based signal is 3.1. This
mirrors 2.4's honesty: do the real bounded thing, document the rest. 521 → 535 tests.

### Part four — item 2.7, started (one step still needs a key)

`scripts/measure_prompt.py` (operator-run, like `make_roadmap.py`) settles the two numbers roadmap
2.7 leaves open. It reports the system prompt is **21,777 characters** (≈5.4k tokens by the crude
chars÷4 estimate — clear of Haiku 4.5's 4,096-token cache floor, so the cacheable prefix does
cache), and prints the **exact** `count_tokens` figure and the per-message cost the moment an
Anthropic key is present.

**What is still key-gated:** the eval re-record itself — running the classifier over the 50-case
golden set against a live model to regenerate `packages/eval/fixtures/recorded_haiku.jsonl` and
replace the 0.88 baseline (still v2's number) with the real v3 one — and adding Franco-Arabic golden
cases, which need the fixtures that live run produces. The Anthropic keys are set on the Render
services but **not in a sandbox/dev environment where the re-record runs**, and the Render MCP can't
read secret values back. See §4.

### Decisions made this session

| Decision | Choice | Where it lives |
|---|---|---|
| How a fact is scoped to a property | **nullable `facts.property_id`; NULL = tenant-wide; lookup unions NULL + resolved id** (D41) | `db/models.py`, `db/knowledge_repo.py` |
| How a message resolves to a property | **explicit hint wins; else single-property tenant; else None** (D42) | `core/property.py`, `db/property_repo.py` |

---

## 3. Traps and things not to re-litigate

- **Migration 006 is not applied to production.** The `properties` table and `facts.property_id`
  exist only in this branch's code and in the test schema (`create_all`). When 2.8 deploys, run the
  migration against production `DATABASE_URL` — the same manual step 005 needed, because the app's
  Render build/start command does not run `alembic upgrade`. Deploying the 2.8 code without it means
  `SqlAlchemyPropertyRepository` hits "relation properties does not exist".
- **The alembic chain is Postgres-only** — migration `002_channel_neutral_renames` already uses an
  `ALTER` SQLite can't do, so `alembic upgrade` never runs end-to-end on SQLite. Tests build the
  schema from ORM metadata (`Base.metadata.create_all`), not alembic. Migration 006's FK and RLS are
  guarded Postgres-only for the same reason; the ORM model still declares the FK, so tests get it.
- **A door code, a key box code or a unit number must never become a `FactRow`** — unchanged from
  2.4. If D5 (the knowledge-editing UI) adds bulk import or editing, this exclusion travels with it.
  Note that 2.8's per-property scoping does not weaken it: a unit number is still excluded from the
  table entirely, not stored as a property-scoped fact.
- **`REDIS_URL` must stay unset on `watcher-api` until you have confirmed the worker consumes.**
  Setting it flips `main.py`'s `assemble()` to the thin-producer branch (no orchestrator/sender/
  alerter in the API process); with the worker now live this is *closer* to safe, but confirm a real
  message round-trips through the worker before flipping it, or you turn a working in-process
  pipeline into one that enqueues and never replies. No test catches this — it's deploy-time
  ordering.
- **`REGISTRY` (`conversations/tools.py`) is still process-global mutable state**, and now
  `AnswerFromKnowledge` carries a second collaborator (the property resolver). `configure_knowledge`
  is still the one seam; the `conftest.py` autouse fixture still snapshots/restores it per test.
- **2.7's 0.88 is still v2's number.** The eval gate replays v2 fixtures keyed by message text, so it
  reports 0.88 whatever the prompt says. The measurement tool settled the prompt's *size*, not its
  *accuracy* — that still needs the re-record with a key.
- Everything from session 11 not superseded: `MATCH_THRESHOLD = 60.0` untuned beyond one property,
  narrow `intents.yaml` triggers, `DATABASE_URL` must name `watcher_app` not `postgres`, the
  transaction-pooler port 6543, the webhook path is `/webhook`, RLS/adapter rules, D24, `KNOWN_LEAKS`
  empty, no module-level `app`, no `temperature`.

---

## 4. What to do first

**1. Get an Anthropic key into a run environment and finish 2.7.** The re-record is the only Track 2
work left. With a key set (`ANTHROPIC_API_KEY`), first run `python scripts/measure_prompt.py` to
capture the exact token count and cost, then re-record the classifier over the golden set against a
live model, regenerate `packages/eval/fixtures/recorded_haiku.jsonl`, add Franco-Arabic golden
cases, and bump `packages/eval/baseline.json` to the real v3 accuracy. The keys are on Render but
not readable back; the re-record runs wherever you have a key locally.

**2. When 2.8 deploys, apply migration 006** to production `DATABASE_URL` (`alembic upgrade head`, or
replicate it via the Supabase MCP as 005 was). See §3.

**3. Confirm B4 with a real message.** Send one real WhatsApp message and confirm it produces a
`messages` row (or a `POST /webhook` 200 in Render's logs). Until then B4's webhook item is reported
done, not proven.

**4. Upgrade `watcher-api` off the free plan** — the one remaining B4 item, operational not
engineering. A cold start on the free plan reads to Meta as a timeout.

**5. Redeploy and confirm the emergency-alert warning is gone** once `CONTROL_CHAT_PHONE_E164` is
actually set on the running service.

---

## 5. Where things live

| What | Where |
|---|---|
| Entrypoint / wiring (API process) | `apps/api/main.py` — `create_application`, `assemble` |
| The shared consumer graph | `apps/api/orchestration/composition.py` — `build_consumer` (wires 2.8's resolver) |
| The arq worker | `apps/api/worker.py` — `WorkerSettings`, `consume_message` |
| The durable queue transport | `apps/api/orchestration/queue.py` |
| The pipeline | `apps/api/orchestration/worker.py` — `Orchestrator.process` |
| The receptionist / task dispatch | `apps/api/conversations/receptionist.py` |
| The knowledge base | `apps/api/core/knowledge.py`, `apps/api/db/knowledge_repo.py` (now property-scoped) |
| **Properties (new, 2.8)** | `apps/api/core/property.py` (`Property`, `resolve_property`), `apps/api/db/property_repo.py` (`SqlAlchemyPropertyRepository`) |
| **Properties migration (new, 2.8)** | `alembic/versions/006_properties.py` — not yet applied to production |
| The tool registry | `apps/api/conversations/tools.py` — `AnswerFromKnowledge`, `configure_knowledge` |
| **Prompt-measurement tool (new, 2.7)** | `scripts/measure_prompt.py` |
| **2.8 tests (new)** | `apps/api/tests/test_property.py`; updated: `test_receptionist.py`, `test_orchestration.py` |
| Facts migration | `alembic/versions/005_facts.py` — applied to production this session |
| Typed config | `apps/api/core/config.py` + `core/settings_base.py` |
| Locked decisions | `docs/DECISIONS.md` (D41–D42 are this session's) |
| The roadmap | `docs/make_roadmap.py` → `docs/Watcher_v2_Roadmap.pdf` (**v2.13**) |
| Render services | `watcher-api` (`srv-da0a81jl550s73d0b1i0`, free), `watcher-worker` (`srv-da4vob3ncjis73fafi10`, starter), `watcher-redis` (`red-da4v8ngu01pc73dfkr30`) |

---

## 6. Roadmap status against v2.13

| Track | Remaining | Note |
|---|---|---|
| A — Make it run | 0d | complete since session 7 |
| B — Host it | 0.25d | B1–B3, B5 ✅ — B4 down to the paid-plan upgrade |
| **2 — Receptionist** | **0.5d** | **2.4, 2.8 ✅** — left: 2.7 (started; re-record needs a key) |
| D — Control page | 13.75d | unchanged |
| G — Guardrails | 4.0d | G3 ✅ — G1, G2, G4 remain |
| E — Sellable | 4.5d | Blocked on P1 |
| 3 — Integration | 5.5d | 3.1 blocked on P1 |
| **Total** | **~28.5d** | at the observed rate, ~7 weeks to sellable |

**Milestone M1 — answers a real message, safely:** one operator action away — the paid Render
upgrade — with the subscription, operator number and worker reported or verified done. The positive
proof still to watch for is the first real inbound message landing a `messages` row.

---

## 7. First five minutes of the next session

1. Read §2 and §4 above.
2. Rebuild the venv from §1 and confirm **535 passed, 2 skipped**.
3. If a key is available: run `scripts/measure_prompt.py`, then do the 2.7 re-record (the only
   Track 2 work left).
4. If 2.8 has deployed: confirm migration 006 was applied to production (`alembic_version` =
   `006_properties`) — if not, apply it before the first real `property_question` reaches a
   multi-property tenant.
5. Check whether `watcher-api` has moved off the free plan, and whether a real message has finally
   produced a `messages` row.
