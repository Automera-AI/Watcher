# Watcher — Launch Plan

**Written:** 15 August 2026 · **Supersedes the day-count in** `Watcher_v2_Roadmap.pdf` (v1.15)
**Target:** a multi-tenant product, many properties per client, nothing hardcoded, sellable.
**Stack decision (locked this session):** Supabase Postgres + Render API + Clerk auth.

---

## 1. Why this document exists

The roadmap PDF says **5.75 engineering days remain**. That figure counts only the numbered
work items in Tracks 0–3. It does not count the code that makes any of them execute.

An audit of the repository on 15 August 2026 found that **no part of the system can process a
real message today**. There is no LLM client, no database connection, no application
entrypoint, no container, and no frontend. The honest remaining figure is **~36 engineering
days**.

`ROADMAP.md` §7 already lists most of these gaps under "Next up". They were simply never
priced into the headline. This document prices them.

---

## 2. Audit — what is true on `main`

### Genuinely built, and good

277 passing tests · 19 ORM tables · 3 Alembic migrations · Pydantic v2 schemas as the single
source of truth · ports/protocols at every external boundary · a ~5k-token classifier prompt
rendered from `packages/intents/intents.yaml` · an eval harness with 50 golden cases and a CI
accuracy gate.

The business logic and the seams are in good shape. This plan does not re-do any of it.

### Missing — verified against the code, not the docs

| # | Gap | Evidence |
|---|-----|----------|
| 1 | **No LLM client** | `apps/api/classifier/provider.py` defines a `Protocol` only. No `anthropic`/`openai`/`httpx` call exists anywhere in `apps/`. The classifier has never called a model. |
| 2 | **No database connection** | No `create_engine` or `sessionmaker` in application code — only in `tests/test_db.py` and `tests/test_conversation_repo.py`. Only `alembic/env.py` reads `DATABASE_URL`. |
| 3 | **No application entrypoint** | `apps/api/app.py::create_app()` has no production caller. No `main.py`, no module-level `app`. The API cannot be started. |
| 4 | **No outbound messaging** | `apps/api/channels/sender.py` is a `Protocol`; nothing implements it. Replies are composed and never sent. |
| 5 | **No container, so no deploys** | No `Dockerfile`. `.github/workflows/cd.yml` detects its absence and self-skips — CD has never deployed anything. |
| 6 | **No tenant isolation in the DB** | No `ROW LEVEL SECURITY` or `POLICY` statement in any migration, though `AGENTS.md` calls multi-tenancy "non-negotiable". |
| 7 | **No configuration layer** | Only `MetaSettings.from_env()` (2 variables). `.env.example` declares ~20; nothing reads the rest. |
| 8 | **No control page** | `apps/control-page/` holds `design-tokens.css` and a README. No `package.json`; CI's `web` job self-skips. |
| 9 | **No control-page API** | The API exposes the webhook and property-system routes only. Inbox, Sources, Destinations, Rules and Admin have **no endpoints at all**. |

### Two items marked DONE that are not true end-to-end

**A. Conversation continuity is not wired (Items 2.1 / 2.2).**
`ConversationRepository` (`apps/api/db/conversation_repo.py`) is never called outside tests.
`apps/api/orchestration/worker.py` invokes the receptionist with `extracted_slots={}` and
`task=None` hardcoded — so **every message is treated as turn one**. The persistence layer and
the reply path both exist; the wire between them does not.

**B. `InlineClassificationQueue` raises at runtime.**
`worker.py::process()` calls `asyncio.run()`. The inline queue calls `consume` synchronously
from inside the `async def receive` handler (`apps/api/ingestion/router.py:46`), which raises
`RuntimeError: asyncio.run() cannot be called from a running event loop`. The `BackgroundTasks`
path survives only because FastAPI runs sync callables in a worker thread. Both are fixed by
making the orchestrator async (A5).

### One scope change from the goal

The roadmap assumes **one property** — "one property's facts fit in the prompt; no retrieval
needed". Many properties per client requires a `properties` table, per-property fact scoping,
and resolution of an inbound message to a property. Priced as C3.

---

## 3. The estimate

| Phase | Work | Days |
|---|---|---:|
| A | **Make it run** — provider, config, engine, entrypoint, sender, conversation wiring | 6.00 |
| B | **Host it** — Supabase, RLS, Docker, Render, domain, durable queue | 4.25 |
| C | **Knowledge + multi-property** (roadmap 2.4, extended) | 3.00 |
| D | **Control page** — all five views + the REST API behind them | 11.50 |
| E | **Product surface** — onboarding, metering, billing, observability, backups | 4.50 |
| F | **Roadmap remainder** — PMS adapter, eval re-record, end-to-end, tuning tail | 6.00 |
| | **Total** | **35.25** |

At the six-day week the roadmap assumes, that is **~6 weeks of pure build**. With review,
integration, and the external approvals below, plan **7–8 weeks to sellable**.

---

## 4. Milestones

### M1 — "It answers a real message" · ~10 days
Phase A + B1–B4 + C1/C2. A real WhatsApp number, real model calls, live Supabase, deployed on
Render, watched through logs. **This is the first moment the system exists as a system.**

### M2 — "You can watch and correct it" · +11.5 days
Phase D. All five views and the ~25 REST endpoints behind them. Bad classifications become
correctable, which is what makes a pilot survivable rather than embarrassing.

### M3 — "Sellable" · +13.75 days
Phases E and F. RLS enforced and tested, multi-property, the first PMS adapter, usage metering,
billing, and an eval number that is measured rather than replayed.

---

## 5. Phase A — make it run · 6.0d

| ID | Item | Days | Notes |
|---|---|---:|---|
| A1 | Configuration | 0.5 | `apps/api/config.py` on `pydantic-settings`, covering every var in `.env.example`. Fold in the existing `MetaSettings.from_env()` rather than duplicating it; keep `ConfigError`. |
| A2 | Database session | 0.5 | `apps/api/db/session.py`: engine from `DATABASE_URL`, `sessionmaker`, a `get_session` dependency. **Supabase transaction-mode pooling needs `NullPool` + `prepare_threshold=None`** — get this right here or it fails intermittently under load later. |
| A3 | LLM providers | 1.5 | `anthropic_provider.py` + `openai_provider.py` satisfying the existing `LLMProvider` protocol. Tool-call/constrained decoding against `CLASSIFICATION_TOOL_SCHEMA`. **Mark the system block `cache_control`** — ~5k tokens per inbound message otherwise (handoff gotcha #5). Model IDs from A1, never from code. Raise `ProviderError` on transport failure so the retry policy in `classifier/service.py` applies unchanged. |
| A4 | Composition root | 1.0 | `apps/api/main.py` assembling the real app: repository, queue, classifier, orchestrator, receptionist, audit, inbox, rules, CRM lookup. First production caller of `create_app()`. Prove it with an integration test that boots against a temporary Postgres. |
| A5 | Conversation wiring | 1.5 | Connect `ConversationRepository` into the orchestrator: `find_or_create_conversation` → `get_active_task` → pass the real task and slots to the receptionist → `save_task` + `record_turn`. Convert `Orchestrator.process` to `async def`, drop `asyncio.run()`, update `MessageConsumer` and both queue transports. **This is the change that makes it hold a conversation.** |
| A6 | Outbound sender | 1.0 | `channels/whatsapp_sender.py` implementing `ChannelSender` against the Meta Graph API, reusing the existing `RenderedMessage` / `QUICK_REPLY_LIMIT` rendering. Wire `ProcessOutcome.outbound_action` to actually send. |

## 6. Phase B — host it · 4.25d

| ID | Item | Days | Notes |
|---|---|---:|---|
| B1 | Supabase project | 0.5 | Provision, set `DATABASE_URL`, run the 3 existing migrations, verify pooling. |
| B2 | Row-Level Security | 1.5 | Migration adding `ENABLE ROW LEVEL SECURITY` + a `tenant_id = current_setting('app.tenant_id')::uuid` policy to all 19 tenant tables; set the GUC per session in `get_session`. Test that a cross-tenant read returns zero rows. **Required before client #2 exists.** |
| B3 | Container + Render | 0.75 | `apps/api/Dockerfile` — this alone activates the dormant `cd.yml` image job. Render web service, secrets, `/health` wired to the existing endpoint. |
| B4 | Domain + Meta webhook | 0.5 | TLS, webhook subscription against the live HTTPS URL, verify the `X-Hub-Signature-256` path end to end. |
| B5 | Durable queue | 1.0 | Redis + arq worker calling the same `MessageConsumer.consume`. `BackgroundTasks` loses in-flight messages on every Render deploy or restart — not acceptable for a paid product. The seam already exists, so nothing else changes. |

## 7. Phase C — knowledge + multi-property · 3.0d

| ID | Item | Days | Notes |
|---|---|---:|---|
| C1 | Facts table | 1.5 | Sensitivity flags — door codes are not ordinary facts. Includes the verification-codes table deferred from 2.1. |
| C2 | A real "I don't know" | 0.5 | Fetches a human instead of inventing a check-in time. |
| C3 | Multi-property | 1.0 | `properties` table, facts scoped per property, and resolution of an inbound message to a property (booking/PMS reference, or the number it arrived on). **Not in the roadmap; required by the "many properties" goal.** |

> **Scope guard — unchanged and still right.** "Can it read our PDF handbook?" is a different
> and much larger project. Structured intake + PMS sync only.

## 8. Phase D — control page, all five views · 11.5d

| ID | Item | Days | Notes |
|---|---|---:|---|
| D1 | Scaffold | 1.0 | Next.js 15 + TS + Tailwind, `design-tokens.css` mapped into the Tailwind theme, Inter/Cairo self-hosted via `next/font` (no runtime CDN — `AGENTS.md`), Clerk. Commit `package-lock.json`; activates CI's dormant `web` job. |
| D2 | **REST API** | 3.0 | **The hidden half of this phase.** ~25 endpoints: inbox list/detail/confirm/edit/route, sources, destinations, rules CRUD, eval reports. Tenant-scoped, paginated, tested. None exist today. |
| D3 | Typed client | 0.25 | Generated from the FastAPI OpenAPI schema. |
| D4 | Inbox view | 2.0 | The critical path (DESIGN-SPEC §7): confidence chip, three interaction patterns by band, field-edit popover, identity-match card. |
| D5 | Sources + first-run wizard | 1.0 | |
| D6 | Destinations | 1.25 | Recipe picker + webhook URL + field mapping. |
| D7 | Rules builder | 1.25 | Condition → action. No DSL. |
| D8 | Admin / Eval viewer | 0.75 | Accuracy drift per client. |
| D9 | Arabic/RTL + a11y | 1.0 | DESIGN-SPEC §9/§10, across all views. |

Build against `DESIGN-SPEC.md`. Never hardcode a colour — reference a token.

## 9. Phase E — product surface · 4.5d

| ID | Item | Days | Notes |
|---|---|---:|---|
| E1 | Tenant onboarding | 1.0 | Create tenant, connect a number, seed properties — without touching SQL. |
| E2 | Usage metering | 0.75 | The `usage_events` table already exists; wire it and enforce limits. |
| E3 | Billing (Stripe) | 1.5 | **Droppable to 0 if you invoice manually for the first clients.** |
| E4 | Observability | 0.75 | Structured logs, Sentry, uptime checks. |
| E5 | Backups + residency | 0.5 | Restore drill, and the data-residency statement the regulated GCC tier is sold on. |

## 10. Phase F — roadmap remainder · 6.0d

| ID | Item | Days | Notes |
|---|---|---:|---|
| F1 | PMS adapter (3.1) | 2.5 | Hostaway / Guesty / Cloudbeds. **Blocked on P1.** |
| F2 | Eval re-record (2.7) | 0.5 | Re-record `recorded_haiku.jsonl` under prompt v3, add Franco-Arabic golden cases, remove `stale_note` from `baseline.json`. Unblocked the moment A3 lands. **Until then 0.88 is v2's number, not v3's.** |
| F3 | End-to-end + measure (3.2) | 1.0 | The point at which the eval number becomes real rather than recorded. |
| F4 | Tuning tail | 2.0 | The roadmap flags this itself: trustworthy is further away than working. |

---

## 11. Start today — not engineering, and the usual reason dates slip

| | Item | Why it bites |
|---|---|---|
| **P1** | **Pick the first client** | Decides which PMS adapter gets built. Blocks F1 right now. |
| **P2** | **Hostaway / Guesty / Cloudbeds sandbox keys** | Docs are public; approval and rate limits are theirs to grant, not yours. |
| **P3** | **Meta business verification** | The roadmap downgraded this to "no longer blocking". At paid volume across multiple clients it binds again — file it now. |

---

## 12. Risks specific to this plan

- **Supabase pooling.** Transaction-mode pgbouncer and SQLAlchemy's prepared statements do not
  get along. Settle it in A2 with a load test, not in production.
- **RLS retrofitted late.** Adding row-level security after application queries exist tends to
  surface as silent empty result sets. B2 is placed before any second tenant for that reason.
- **D2 is invisible in the plan's title.** "Build the control page" reads as frontend work;
  three of its days are backend endpoints that do not exist yet. Do not let it get cut.
- **Cost per message.** Not yet measured, because nothing has called a model. A3 makes it
  measurable; prompt caching is what keeps it small. Measure before quoting a price.
- **The eval measures nothing today.** The gate replays fixtures recorded under prompt v2 and
  reports 88% whatever the prompt says. Treat the number as unknown until F2.
