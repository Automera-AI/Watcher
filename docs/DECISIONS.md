# Watcher — Locked Decisions

**Status:** Decision record. Closes the Track‑0 / §17 "Decision Gate" from the roadmap. These are the
single source of truth for engineering; supersedes the open `🔲 NEEDS INPUT` items in
`docs/build-spec-addendum.md §17` that they resolve. Locked 2026‑06‑14.

---

## Founder decisions (Lane A)

| # | Decision | **Locked choice** | Downstream impact |
|---|----------|-------------------|-------------------|
| D8‑a | Classifier model tiering | **Haiku 4.5 → Sonnet 5, GPT‑4o‑mini fallback** | LLM provider impls; `.env` model IDs pinned |
| — | SaaS hosting | **Render** | CD deploy target; Alembic DB; staging env |
| D2‑a | Control‑page auth | **Clerk** (swap to self‑hostable for regulated tier) | Auth slice; tenancy binding |
| — | Intent taxonomy + schema | **Pin 6 intents + 3 record types as enums; keep flat schema** | `schemas/enums.py` + classification; eval confusion matrix |

**Pinned model IDs** (in `.env.example`), revised 2026‑08‑15 to the Claude 5 family:
- First pass: `claude-haiku-4-5` — unchanged in substance; the Claude 5 family has no Haiku, and
  Haiku 4.5 is still the current cheap tier. First‑pass traffic is every inbound message, so
  moving it up a tier is a cost decision, not a version bump.
- Escalation: `claude-sonnet-5` (was `claude-sonnet-4-6`)
- Fallback: `gpt-4o-mini`

Switching the escalation tier to a Claude 5 model changes two things in the request, both handled
in `classifier/factory.py` rather than per‑deploy config: the family rejects a non‑default
`temperature` with a 400, and it thinks unless told not to — which, with `max_tokens` bounding
thinking and the tool call together, would truncate the classification rather than error.

**Intent enum** (role‑guide vocabulary): `new_lead`, `existing_contact_reply`, `support_issue`,
`internal_team`, `spam_or_noise`, `unclear`.
**Record‑type enum:** `individual_only`, `contact_under_company`, `company_only`.

---

## Spec‑aligned defaults (locked unless revisited)

| # | Decision | **Locked default** | Rationale |
|---|----------|--------------------|-----------|
| — | Confidence bands | **HIGH ≥ 0.85 · MEDIUM ≥ 0.5 · LOW < 0.5** | Aligns repo's 0.60 → **0.5** to the v1.2 rubric / role‑guide |
| D9‑a | Identity dedup scope | **Cache‑only v1** (no live CRM roundtrip) | Addendum §9 resolves the flowchart contradiction; webhook CRMs can't be read back in v1 |
| D3‑a | ASR provider | **Whisper API** (SaaS) · faster‑whisper (self‑hosted) | Strong Arabic; self‑hostable path for no‑egress tier |
| D13‑a | Eval in CI | **Recorded fixtures in CI; live key nightly** | Deterministic, cheap, no live key on every PR |
| — | Schema shape | **Flat (addendum §4)** | One model backs LLM output + DB row + REST contract |
| — | DB connection path | **Assume a transaction‑mode pooler** (`NullPool` + `prepare_threshold=None`); `DATABASE_POOL_MODE=session` opts out | Supabase's app URI is pgbouncer in transaction mode, where prepared statements and a client‑side pool both break — intermittently, under load. Safe default; the cost is a handshake per checkout, to be measured in B1/B3 |
| D24 | Rules + destinations in the message path | **Removed** — the orchestrator neither evaluates rules nor assigns a destination. Engine, `rules` and `destinations` tables all retained for the control page (track D) | Auto‑routing a message to a Sheet was v1's answer to an inbound message; the receptionist is v2's. A message that gets *answered* has nowhere to be filed to, and keeping both meant one message could be routed and replied to at once (roadmap A5) |
| — | Conversation continuity | **Every classified message runs against a `ConversationStore`**; a receptionist without one is refused at construction | A receptionist with no store forgets the previous turn on every message — it looks like it works and it is the exact failure A5 exists to remove (roadmap A5) |
| — | Clarifying‑turn budget | **`defaults.max_clarifying_turns` from the vocabulary**, then `defaults.on_max_turns` (hand off) | Once a task survives between messages, a task that cannot be filled asks the same question forever. The vocabulary declared this from item 0.3 and nothing read it until continuity made it load‑bearing |
| — | A failed send | **Logged, reported on the outcome, not raised** | The message is classified, the reply is recorded and the decision is filed by the time the send runs. Raising would lose all of it to a transient 502 from the channel (roadmap A6) |
| — | Channel credential fields | **Declared by `channels/config.py`, inherited by `Settings`** | An access token and a phone‑number id are facts about a channel. One object still reads one environment; the *knowledge* of what a channel needs sits with the channel. Emptied `KNOWN_LEAKS` (roadmap A6) |
| D25 | The role the application connects as | **`watcher_app`** — no `BYPASSRLS`, owns no tables, password set by the deploy and never by a migration | Measured on the live project: Supabase's `postgres` role has `rolbypassrls = true`. An application using the URI from the dashboard is exempt from every policy, so RLS would be enforced in the schema and bypassed in production — the worst of both, because it reads as protection (roadmap B2) |
| D26 | How a session says which tenant it is | **`app.current_tenant`, set per transaction by `Database.tenant_session`** | The addendum (§3) named a session GUC; `set_config(…, is_local => true)` is the form that cannot outlive its transaction, which matters behind a pooler that hands the connection to the next tenant. Every adapter already received a `tenant_id` and passed it no further than the `WHERE` clause |
| D27 | Reading `channel_configs` before a tenant is known | **A second, `SELECT`‑only policy that applies only when no tenant is set** | Endpoint → tenant is the one question asked before the answer exists. The alternative (a blanket allow, or the app role bypassing RLS for one query) opens the whole table permanently. A session that has adopted a tenant sees only its own endpoints; one that has not sees endpoint rows and nothing else in the database (roadmap B2) |
| D28 | Shutdown wiring | **A `lifespan` context manager passed to `create_app`**, not `add_event_handler` | Starlette 1.0 removed `add_event_handler`. The pinned CI versions kept passing while an image built from this repo's own dependency ranges crashed at startup — found by building it (roadmap B3) |
| D29 | The image installs the project | **`pip install .`, with `pyproject.toml` as the only dependency list** | A Dockerfile with its own `pip install fastapi uvicorn …` is a second dependency list nobody updates. It also forced the packaging to be honest: `intents.yaml` is read off disk at import and had to be declared as package data (roadmap B3) |
| D30 | Where the emergency check runs | **In `Orchestrator.process`, after media enrichment and before the classifier** | `intents.yaml` has said "checked before intent, before confidence, before anything" since 0.3. A model is a network round trip that can be slow, wrong or down, and none of that may stand between a guest saying *smell of gas* and an operator. After media, because a voice note has no text until it is transcribed. An emergency therefore has no classification row, which is honest rather than missing (roadmap G3) |
| D31 | How a trigger is matched | **Declared phrases only — Arabic as a substring, Latin on word boundaries, digits treated as letters** | Arabic attaches its article to the front of the word (حريق → الحريق), so a boundary test there invents false negatives; *fire* as a substring fires on "fireplace", so a substring test in Latin invents false positives. No scoring and no model: an operator must be able to read the file and know what will fire. The cost is that an undeclared phrasing (*"I smell gas"*) does not fire, which is asserted in the suite rather than hidden (roadmap G3) |
| D32 | The alert channel gap | **Deliver on the best channel there is and report which one was used** | The vocabulary asks for `phone_call_to_operator` and nothing wired can place a call. Treating a text notification as satisfying it makes the declaration decorative; refusing to alert at all trades a person reached for a principle. `AlertOutcome.channel` beside `EmergencyAlert.requested_channel` keeps the gap visible per emergency and once at startup (roadmap G3) |
| D33 | An emergency reply belongs to no task | **`ConversationStore.record_reply` takes `task: Task \| None`; the job in flight is left untouched** | Both halves of the exchange belong on the transcript, and a task row with an invented intent would be a fiction the control page then explains. Leaving the active task alone rather than abandoning it is deliberate: the guest may return to their booking question, and the conversation is a person's either way (roadmap G3) |
| D34 | Where "night" is measured | **`TenantPolicy.timezone`, from `TENANT_TIMEZONE`, validated at startup** | One trigger fires only between 22:00 and 07:00 and that is the guest's clock, not the container's — Dubai and Cairo are an hour or two apart, which is an hour of the window either side of midnight. It is the one defaulted value this repo validates eagerly: present-but-wrong here is a typo, not a half-configured machine, and it would otherwise surface at 2am (roadmap G3) |
| D35 | How a guest's question is matched to a fact | **`rapidfuzz.fuzz.WRatio`, scored after stripping a small stopword list from both sides, threshold 60** | Scoring the whole sentence let shared scaffolding ("is there a ___?") outweigh the one word that actually distinguishes two facts — a real test against the operator's own property sheet scored "is there parking?" identically against "is there a garden" and "is there a dishwasher". Stripping stopwords first means the score is won or lost on the content words. No model: an unmatched question should look for a person, not guess (roadmap 2.4) |
| D36 | Scope of the `sensitive` flag on a fact | **`answer_from_knowledge` only, narrower than G1** | G1 (track G, not built) will eventually gate the whole reply path. Until it exists, a sensitive fact matched for an unverified guest is treated exactly like no match — the same "I don't know" as a genuine miss — rather than inventing a partial version of G1 this item was not scoped to build. A door code, a key box code or a unit number is excluded from the table entirely rather than marked sensitive: `intents.yaml` forbids `check_in_support` from disclosing those through this tool regardless of verification (roadmap 2.4) |
| D37 | Where the knowledge lookup is wired in | **`conversations/tools.REGISTRY`, via `configure_knowledge`, not threaded through `Orchestrator`/`Receptionist`** | `REGISTRY` was already a small process-global service locator (A5's `take_message`/`handoff_to_human`); a third, DB-backed entry follows the seam that exists rather than adding a second one. The trade is process-global mutable state, which the test suite pays for with an autouse fixture that snapshots and restores `REGISTRY` per test (roadmap 2.4) |
| D38 | How `RedisClassificationQueue.enqueue` reaches Redis | **Stays synchronous, like every other `ClassificationQueue` implementation; the push runs as a fire-and-forget task on the caller's already-running loop, logged rather than awaited inline** | Awaiting the push inline would have meant making the whole seam `async` — `ClassificationQueue`, `IngestionService.ingest`, three existing implementations, every caller — to close a race window (a crash between "task scheduled" and "Redis acknowledges it") that is no wider than the one `BackgroundTasksQueue` already accepts today. The persisted row survives either way (§5); only its classification would need retriggering (roadmap B5) |
| D39 | Where the orchestrator graph is built | **One function, `orchestration/composition.build_consumer`, called by both `main.py` (the in-process fallback) and `apps/api/worker.py` (the arq worker)** | Before B5 there was exactly one process that ever consumed a message, so `main.py` wired it inline. A second consumer means a sender, an alerter, or a repo added to one process's wiring and not the other is now possible — and would only surface in whichever process nobody tested. One construction removes the seam for that bug to live in (roadmap B5) |
| D40 | Where the knowledge base wiring lives after B5 | **Inside `orchestration/composition.build_consumer`, not `main.py`'s in-process branch** | 2.4 called `configure_knowledge` directly in `main.py::assemble`. B5 moved everything `assemble` used to build inline into `composition.build_consumer` so the arq worker gets an identical graph. `configure_knowledge` moved with it — an arq worker process that never populated `REGISTRY` would silently answer every knowledge question with "I don't know" (roadmap B5, reconciling with 2.4) |
| D41 | How a fact is scoped to a property | **A nullable `facts.property_id`; `NULL` means tenant-wide and the knowledge lookup returns tenant-wide facts *plus* the resolved property's, never another property's** | An agency's fact is usually true of one unit, not all — but making the column required would force a tenant-wide fact ("office hours are 9–5") to be duplicated per property. Nullable, with the lookup unioning `NULL` and the resolved id, keeps the common tenant-wide case free and scopes the rest. A message whose property cannot be resolved gets `NULL`-only, so a shared-number guest is never answered from the wrong unit's sheet (roadmap 2.8) |
| D42 | How a message resolves to a property | **An explicit endpoint hint wins and is authoritative; otherwise a single-property tenant resolves to its one unit; otherwise `None` (tenant-wide only)** | Slot extraction (2.x) and the PMS booking lookup (3.1) are both unbuilt, so the signals available today are few and the resolution is honest about it. The single-property fallback covers the overwhelmingly common early client; the hint path (an endpoint wired to one unit) is built and tested for a number-per-unit client but not yet plumbed through the receptionist, since no channel carries a property-scoped endpoint today. Guessing a unit among many with no signal is the one thing it refuses to do (roadmap 2.8) |
| D43 | What the eval baseline measures | **The first-pass model alone (`claude-haiku-4-5`, escalation pinned off), re-recorded live under prompt v3; borderline misses are left in rather than relabelled** | The classifier escalates a low-confidence first pass to a larger model, but the eval exists to measure whether the *prompt* improves the cheap tier every message pays for — folding the escalation model's quality into the number is how 0.88 stopped meaning anything. Recording first-pass-only isolates the prompt. The one miss at 0.98 (a general_info-vs-property_question "nearest supermarket" case, predicted at 0.78, which escalates in production) is kept as an honest confusable the confusion matrix surfaces, not edited away in the golden set. `scripts/record_fixtures.py` is the committed re-record tool; the fingerprint in `baseline.json` is what distinguishes two runs when the version string agrees (roadmap 2.7) |

---

## Engineering follow‑ups created by these decisions (Sprint 1)

- [ ] Replace open‑string `intent` / `suggested_record_type` with the locked **enums** (`schemas/enums.py`, `classification.py`).
- [ ] Change `MEDIUM_CONFIDENCE_THRESHOLD` **0.60 → 0.5** (`schemas/common.py`); converge `band_for()` with the classifier's `escalation_threshold`.
- [x] Implement **AnthropicProvider** + **OpenAIProvider** behind the `LLMProvider` seam, reading the pinned model IDs from config. *(roadmap A3 — `classifier/anthropic.py`, `classifier/openai.py`, wired by `classifier/factory.py`; the OpenAI provider doubles as the vLLM/Qwen path)*
- [x] Add a typed **Settings** object in `core/` (extend `MetaSettings`) reading the pinned model/ASR config. *(roadmap A1 — `core/config.py`; `Settings.meta()` returns the existing `MetaSettings`)*
- [x] Wire the **engine and session scope** over `DATABASE_URL`, pooler‑safe by default. *(roadmap A2 — `db/engine.py`)*
- [x] Build the **composition root**: the first production caller of `create_app()`. *(roadmap A4 — `main.py`, run as `uvicorn apps.api.main:create_application --factory`)*
- [x] Wire **conversation + task continuity** into the message path and populate `classifications`. *(roadmap A5 — `orchestration/ports.py`, `db/orchestration_repo.py`; `process()` is async and the v1 filer is gone)*
- [x] Implement the **outbound sender** behind `ChannelSender` and move the channel credentials behind `channels/`. *(roadmap A6 — `channels/whatsapp.py`, `channels/config.py`, `channels/factory.py`)*
- [x] Provision the database and apply the migrations, plus the `channel_configs` row tenant resolution needs. *(roadmap B1 — Supabase `watcher-prod`, eu‑central‑1, stamped at `003`)*
- [x] **Row‑Level Security**: policies on every table, a session GUC, and a cross‑tenant read test. *(roadmap B2 — `alembic/versions/004_row_level_security.py`, `db/engine.py`, `apps/api/tests/test_rls.py`)*
- [x] Containerize the API at the path `cd.yml` waits on. *(roadmap B3 — `apps/api/Dockerfile`)*
- [x] **Emergency detection and the alert path**: the vocabulary's triggers read, the guest answered immediately, an operator told. *(roadmap G3 — `core/emergency.py`, `core/alerts.py`, `channels/alerting.py`, the short-circuit in `orchestration/worker.py`)*
- [x] **Durable queue**: an arq/Redis producer and its own worker process, behind the existing `ClassificationQueue` seam. *(roadmap B5 — `orchestration/queue.py`'s `RedisClassificationQueue`, `orchestration/composition.py`, `apps/api/worker.py`)*
- [ ] Widen the emergency trigger phrases in `intents.yaml` — operator's edit; "I smell gas" matches nothing today. *(does not touch the prompt or the eval baseline; see `docs/specs/g3-emergency-path.md` §3)*
- [ ] A voice alerter, so `phone_call_to_operator` is satisfied rather than reported unmet. *(same dependency as the phone channel)*
- [ ] Alembic target + Render Postgres URL wired in deploy. *(the Render service itself is blocked on billing details; see `docs/specs/b1-b3-hosting-and-isolation.md`)*
- [ ] Provision the arq worker service and the Redis instance on Render, then set `REDIS_URL`. *(billing decision, left to the operator; B5's code is ready either way)*

---

## Still open (not blocking Sprint 1)

| Item | Owner | When |
|------|-------|------|
| Self‑hosted tier pricing (per‑seat vs per‑deployment) | Founder | 2–3 GCC conversations during pilot |
| AWS Bedrock MENA availability for Anthropic | Founder | Research call before first regulated sale |
| Soft‑cap number + overage behavior | Founder | Before paid pilots (Phase 4) |
| Group‑chat support (§17.12) | — | v2 / dedicated mini‑spec |

---

## External account values to capture (Lane A, then into `.env`)

From Meta App dashboard once verification/test number is ready: `META_APP_ID`, `META_APP_SECRET`,
`META_WEBHOOK_VERIFY_TOKEN` (you choose), `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_BUSINESS_ACCOUNT_ID`.
Plus `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`. These stay secret — never committed.
