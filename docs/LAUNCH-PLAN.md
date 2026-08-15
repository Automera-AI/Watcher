# Watcher — Launch Plan

**Written:** 15 August 2026 · **Supersedes the day-count in** `Watcher_v2_Roadmap.pdf` (v1.15)
**Target:** a multi-tenant product, many properties per client, nothing hardcoded, sellable.
**Stack:** Supabase Postgres + Render API + Clerk auth, EU (Frankfurt).
**Decisions D14–D26** in `DECISIONS.md` are the locked outcome of the design review in §13.

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
| A | **Make it run** — provider, config, engine, entrypoint, sender, conversation wiring, v1 excision | 6.75 |
| B | **Host it** — Supabase, RLS, Docker, Render, domain, durable queue | 4.25 |
| C | **Knowledge + multi-property** (roadmap 2.4, extended) | 3.00 |
| D | **Control page** — six receptionist views + the REST API behind them + spec rewrite | 13.75 |
| E | **Product surface** — onboarding, metering, billing, observability, backups | 4.50 |
| F | **Roadmap remainder** — PMS bridges, eval re-record, end-to-end, tuning tail | 6.00 |
| G | **Receptionist guardrails** — quote provenance, verification, emergency, config split | 5.50 |
| | **Total** | **43.75** |

Three figures have now been published for this work. For the record: **5.75** (roadmap v1.15,
counted only the numbered work items), **35.25** (after the infrastructure audit in §2), and
**43.75** (after the design review in §13 priced the guardrails the vocabulary already requires).
Each increase came from reading something that was already in the repository.

At ~2–3 engineering days per working session (the observed rate across sessions 1–3), that is
**15–20 sessions**. At the six-day week the roadmap assumes, ~7.3 weeks of pure build; with review
and the external approvals below, plan **9–10 weeks to sellable**.

---

## 4. Milestones

### M1 — "It answers a real message, safely" · ~13.5 days
Phase A + B1–B4 + G3 (emergency) + C1/C2. A real WhatsApp number, real model calls, live Supabase,
deployed on Render, watched through logs. **This is the first moment the system exists as a system.**

The emergency path is inside M1 and not negotiable: `worker.py` hardcodes `emergency=False` today,
so a gas leak currently classifies as `maintenance_issue`. You cannot take real guest traffic
before that is wired.

### M2 — "You can watch and correct it" · +13.75 days
Phase D. Six receptionist views and the REST endpoints behind them. Bad classifications become
correctable and the guardrails become visible, which is what makes a pilot survivable.

### M3 — "Sellable" · +16.5 days
B5, C3, and Phases E, F and the rest of G. RLS enforced and tested, multi-property, the first PMS
bridge, quote provenance, usage metering, billing, and an eval number that is measured rather than
replayed.

---

## 5. Phase A — make it run · 6.0d

| ID | Item | Days | Notes |
|---|---|---:|---|
| A1 | Configuration | 0.5 | `apps/api/config.py` on `pydantic-settings`, covering every var in `.env.example`. Fold in the existing `MetaSettings.from_env()` rather than duplicating it; keep `ConfigError`. |
| A2 | Database session | 0.5 | `apps/api/db/session.py`: engine from `DATABASE_URL`, `sessionmaker`, a `get_session` dependency. **Supabase transaction-mode pooling needs `NullPool` + `prepare_threshold=None`** — get this right here or it fails intermittently under load later. |
| A3 | LLM providers | 1.5 | `anthropic_provider.py` + `openai_provider.py` satisfying the existing `LLMProvider` protocol. Tool-call/constrained decoding against `CLASSIFICATION_TOOL_SCHEMA`. **Mark the system block `cache_control`** — ~5k tokens per inbound message otherwise (handoff gotcha #5). Model IDs from A1, never from code. Raise `ProviderError` on transport failure so the retry policy in `classifier/service.py` applies unchanged. |
| A4 | Composition root | 1.0 | `apps/api/main.py` assembling the real app: repository, queue, classifier, orchestrator, receptionist, audit, inbox, rules, CRM lookup. First production caller of `create_app()`. Prove it with an integration test that boots against a temporary Postgres. |
| A5 | Conversation wiring **+ v1 excision** | 2.25 | Connect `ConversationRepository` into the orchestrator: `find_or_create_conversation` → `get_active_task` → pass the real task and slots to the receptionist → `save_task` + `record_turn`. Convert `Orchestrator.process` to `async def`, drop `asyncio.run()`, update `MessageConsumer` and both queue transports. **This is the change that makes it hold a conversation.** In the same pass (D24), remove the rules/destinations threading — `RuleAction.destination_id` is the rules engine's only action, and the vocabulary's build-validated `force_hand_off` replaces it. **Stop threading the tables; do not drop them.** |
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

## 8. Phase D — control page, six receptionist views · 13.75d

**Why the view set changed (D15).** `DESIGN-SPEC.md` §8 specifies Inbox · Sources · Destinations ·
Rules · Admin — the UI of the v1 message-filer, where the job is triaging a *record* and routing it
to a Sheet or a CRM. The v2 receptionist has nine tools (`lookup_reservation`, `check_availability`,
`quote_price`, `hold_slot`, `confirm_booking`, `answer_from_knowledge`, `create_ticket`,
`take_message`, `handoff_to_human`) and talks to guests. It does not route records anywhere. Two of
the five views serve a product we are no longer building.

| ID | Item | Days | Notes |
|---|---|---:|---|
| D0 | **DESIGN-SPEC §8 rewrite** | 1.0 | New view set, against the nine tools and the guardrails. Tokens, type scale, confidence chip and the RTL rules in §9/§10 all carry over unchanged — only §8 and the components in §6 that serve Destinations/Rules are replaced. |
| D1 | Scaffold | 1.0 | Next.js 15 + TS + Tailwind, `design-tokens.css` mapped into the Tailwind theme, Inter/Cairo self-hosted via `next/font` (no runtime CDN — `AGENTS.md`), Clerk. Commit `package-lock.json`; activates CI's dormant `web` job. |
| D2 | **REST API** | 3.0 | **The hidden half of this phase.** Tenant-scoped, paginated, tested endpoints for handoffs, conversations, emergencies, properties/facts, quotes and eval. None exist today. |
| D3 | Typed client | 0.25 | Generated from the FastAPI OpenAPI schema. |
| D4 | **Handoff queue** | 2.0 | The operator's actual work queue and the replacement for Inbox: everything the bot escalated, with *why* — max clarifying turns, tool failure, no knowledge, `force_hand_off`, low confidence. One reason per item, which is why D24 removes the second escalation mechanism. |
| D5 | **Conversations** | 1.5 | Live threads, what the bot said and on what basis, take-over by a human. |
| D6 | **Emergencies** | 0.75 | Alarm-like, acknowledged, never buried in a queue. Backed by G3. |
| D7 | **Properties & Facts** | 1.5 | The knowledge editor that makes Phase C usable, with sensitivity flags visible on the row. |
| D8 | **Quotes & Audit** | 1.0 | Every price the bot said, with provenance, re-checkable months later. Also the surface where the D20 door-code risk is recorded and visible. |
| D9 | Admin / Eval viewer | 0.75 | Accuracy, per-language breakdown, spend. Founder-only, role-gated. |
| D10 | Arabic/RTL + a11y | 1.0 | DESIGN-SPEC §9/§10 carry over unchanged, applied across the new views. |

Never hardcode a colour — reference a token.

## 8a. Phase G — receptionist guardrails · 5.5d

The vocabulary already requires all of this; none of it is built. These are not new features, they
are the enforcement of rules `packages/intents/intents.yaml` states and `packages/intents/schema.py`
validates.

| ID | Item | Days | Notes |
|---|---|---:|---|
| G1 | Quote path and provenance | 2.0 | Extend `AvailabilityResult` with `rate_or_quote_id` and `valid_until` — **as shipped it cannot satisfy `quoting.provenance_required`, which `schema.py:176` enforces**. Add the 300s freshness bound, `on_stale_or_failed: handoff_to_human`, and the audit write that must land *before* the number reaches the guest. |
| G2 | Reservation lookup + verification | 1.5 | Booking reference + a second fact (D19). Unlocks `lookup_reservation` and, per D20, door codes on the same bar. Fixes the `identity_verified` wiring in the same pass (D21) so a fuzzy name match stops reading as proof. |
| G3 | Emergency path | 1.5 | Wire trigger matching **before classification**, per the vocabulary. Six trigger families across three scripts, including `locked_out_at_night` (22:00–07:00, needs tenant timezone). `reply_immediately`, then Twilio outbound call to the operator. Removes the `emergency=False` hardcode at `worker.py:162`. |
| G4 | Client config split | 0.5 | D23. `property_system`, `quote_prices`, `force_hand_off`, `disabled_intents` stay in YAML behind the build validator; currency, timezone, hours, wording move to the DB. |

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
| F1 | PMS adapter (3.1) | 2.5 | **Direction settled by D16.** `HttpPropertySystemAdapter` calling a client-supplied URL that speaks Watcher's own contract (~1.0d), plus the first bridge — Hostaway — which **Watcher writes and hosts** (~1.5d). The published `/v1/property-system` router is an *inbound* projection for operations and integration testing; it does not fetch anything. Hostaway will not implement your contract, so the vendor-neutral port relocates the adapter rather than removing it. Subsequent vendors ~1.5d each. **Blocked on P1.** |
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
- **Door-code disclosure (D20) — decided with reasoning, risk accepted.** Door codes unlock on
  booking reference + a second fact. The case for it: roughly half of bookings are OTA, the
  platform has already verified the guest by account and payment, and that guest receives the code
  through the OTA app anyway — a second challenge is friction without much added assurance.

  The residual risk: both facts appear on the confirmation email, so a forwarded or screenshotted
  email can obtain a code. `test_autonomy.py:17` argues against it in the repository's own words.

  Two mitigations were considered and rejected. Requiring the **sender's number to match the
  reservation** looks obvious but fails precisely for OTA guests — Airbnb and Booking.com issue
  masked relay numbers, which `SESSION-HANDOFF.md` §8 puts at "about half of all guests" — and
  `Reservation` (`property_system/schemas.py:62`) carries no phone field to match against.
  Gating **by lock type** — per-booking PINs released automatically, static key-box codes to a
  human — was the stronger option and remains the thing to revisit (D20a): a PIN expires at
  checkout, whereas a leaked static code compromises every future guest until someone physically
  attends the property. Surfaced in the Quotes & Audit view (D8).
- **Twilio is a new vendor and a new egress path.** `AGENTS.md` forbids runtime external calls for
  the self-hosted regulated tier. An emergency call to the operator is egress. Fine for the SaaS
  tier; needs an answer before the first regulated sale.
- **The GCC residency claim is currently unsupportable.** D25 puts both Supabase and Render in
  Frankfurt. The marketing story mentions data residency for regulated GCC clients; that migration
  is a named future cost, not a configuration flag.

---

## 13. Design review — what the grilling changed

The audit in §2 found that nothing runs. A second pass, reading `intents.yaml`,
`property_system/schemas.py` and `DESIGN-SPEC.md` against each other, found that parts of the plan
pointed at the wrong product and that the vocabulary requires enforcement nobody had priced:

1. **Two of the five control-page views serve the v1 message-filer.** → D15, six receptionist views.
2. **Emergency detection is never invoked** — `emergency=False` hardcoded. A gas leak files a
   maintenance ticket. → D22, into M1.
3. **`AvailabilityResult` cannot satisfy `quoting.provenance_required`** — no `rate_or_quote_id`,
   no `valid_until`, while `schema.py:176` enforces both. Two shipped modules contradict each
   other. → D17.
4. **The port is read-only by design but the vocabulary promises `hold_slot` and
   `confirm_booking`**, and `get_reservation` is gated on verification that does not exist. → D18,
   enquiry + lookup only.
5. **The published property-system API is inbound**, so "integrate with anything" was not built in
   either direction. → D16.
6. **The rules engine's only action is `destination_id`** — it is the destinations mechanism, not a
   separate one, and it duplicates the vocabulary's `force_hand_off`. → D24.

Each of these was already in the repository. None required a decision that could not have been made
in week one; they had simply never been read against each other.
