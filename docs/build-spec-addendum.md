# Watcher — Build Spec Addendum (to MVP v1.2)

**Status:** Build-ready addendum · resolves the engineering gaps left open by `WhatsApp_Bot_MVP_1.2_final.pdf`.
**Relationship to v1.2:** The PDF remains the product/strategy source of truth. This document resolves the
decisions v1.2 left unspecified, so an AI-agent build loop can start Phase 1 without re-litigating architecture
mid-build. UI decisions live in `DESIGN-SPEC.md` (this repo).
**Repo:** `amahmoudosman96-lgtm/Watcher` — the product. (The Automera marketing website is a separate
repo; nothing is shared between them.)

---

## 0. Decisions locked

| # | Decision | Choice |
|---|----------|--------|
| D1 | Product codebase location | **This repo (`Watcher`)** — dedicated, separate from the marketing website |
| D2 | Control-page authentication | **Managed auth provider** (Clerk for SaaS; self-hostable for regulated tier) |
| D3 | Non-text WhatsApp messages | **Transcribe (voice) + OCR/vision (images, PDFs)**, then classify extracted text |
| D5 | Design system | **`DESIGN-SPEC.md` drafted fresh from v1.2** — original system, nothing from the website |

Items still tagged **🔲 NEEDS INPUT** depend on a fact only the founder knows or a commercial choice; the
consolidated list is in §17.

---

## 1. Repository & project structure

```
Watcher/
├─ apps/
│  ├─ api/                      # FastAPI: webhook receiver, classifier, router, REST for control page
│  │  ├─ ingestion/             # Meta webhook handler, HMAC verify, payload → message envelope
│  │  ├─ classifier/            # LLM call, prompt, Pydantic schemas, model tiering
│  │  ├─ media/                 # transcription + OCR pipeline (D3)
│  │  ├─ identity/              # per-message resolution + weekly sweep
│  │  ├─ destinations/          # google_sheets.py, webhook.py, recipes/
│  │  ├─ rules/                 # rule engine
│  │  ├─ control_chat/          # interactive-button flow + pending-action state machine
│  │  ├─ audit/                 # audit log writes/queries
│  │  ├─ db/                    # SQLAlchemy 2.0 models, Alembic migrations
│  │  └─ core/                  # config, auth glue, tenancy, queue, LLM provider abstraction
│  └─ control-page/             # Next.js 15 + TS; Inbox, Sources, Destinations, Rules, Admin/Eval
├─ packages/
│  └─ eval/                     # CLI eval tool, golden set (JSONL), HTML+JSON reporters
├─ deploy/
│  ├─ saas/                     # shared multi-tenant infra (Render/AWS)
│  └─ self-hosted/              # single-tenant Terraform + vLLM/Qwen swap-in (Phase 5, gated)
├─ DESIGN-SPEC.md               # ✅ drafted — governs every control-page screen
├─ docs/build-spec-addendum.md  # this file
└─ AGENTS.md                    # agent build-loop instructions (spec-per-deliverable)
```

> **D1-a RESOLVED:** `DESIGN-SPEC.md` now exists in this repo, drafted from v1.2's stated aesthetic
> (Stripe-meets-Linear, Inter/Cairo, Tailwind, Lucide, the four views). It is an original system — nothing was
> copied from the Automera marketing website, per the founder's instruction.

---

## 2. Authentication & authorization (D2)

v1.2 never mentions auth. Concrete model:

- **Provider:** **Clerk** for the SaaS tier (fastest polished React integration, built-in org/multi-tenant
  primitives). **Regulated-tier caveat:** Clerk is US-hosted SaaS, which conflicts with "data never leaves the
  client environment" (v1.2 §10). For self-hosted/Phase 5 the auth layer must swap to a self-hostable provider
  (**Supabase Auth**, **Ory Kratos**, or **Authentik**). → Abstract auth behind a thin internal interface so
  SaaS=Clerk, self-hosted=Supabase/Ory is a config swap, not a rewrite. **🔲 NEEDS INPUT (D2-a):** confirm
  Clerk for SaaS, or name a preferred provider.
- **Tenancy binding:** every user belongs to exactly one `tenant`. One auth-provider org == one tenant.
  Enforced server-side on every request (§3).
- **Roles (v1):** `owner` and `operator`. `owner` manages destinations, rules, billing, invites; `operator`
  triages the inbox and resolves identity matches. Richer RBAC is v2. (Admin/Eval view is owner-only.)
- **Control-chat ↔ account binding (the missing link in v1.2):** the WhatsApp number that receives
  control-chat pings must map to a control-page user. Onboarding stores `tenant.control_chat_phone_e164`;
  inbound control-chat button replies are authenticated by matching the sender's E.164 against that field plus
  a short-lived signed token embedded in the interactive message (§10). No control action is honored from an
  unbound number.

---

## 3. Multi-tenancy & data isolation

- **SaaS tier:** shared Postgres, **row-level isolation by `tenant_id`** via Postgres **Row-Level Security**
  plus an application-layer filter (defense in depth). Each request sets a session GUC
  (`app.current_tenant`) from the authenticated principal. Chosen over schema-per-tenant because per-client
  volume is low (low thousands of msgs/month) — one small Postgres holds all SaaS clients.
- **Self-hosted/regulated tier:** same schema, deployed single-tenant — one DB, one tenant, RLS still on
  (harmless, keeps code identical). This is v1.2 §10's "same code, different topology".
- **Per-tenant secrets** (WABA tokens, Sheets creds, webhook URLs) encrypted at rest (envelope encryption;
  KMS in SaaS, client-supplied key in self-hosted). Never plaintext.

---

## 4. Data model (concrete schema)

Postgres; all business tables carry `tenant_id` + RLS. Pydantic v2 models defined once back the LLM output,
the DB row, and the REST contract (v1.2 §9).

- **`tenants`** — id, name, tier (`saas`|`self_hosted`), control_chat_phone_e164, waba_id, phone_number_id,
  soft_cap_records, created_at.
- **`sources`** — id, tenant_id, wa_chat_id, kind (`direct`|`group`), display_name, **`excluded` bool**
  (opt-out model, v1.2 §4), created_at. *(We store the exclusion list, not an inclusion list.)*
- **`messages`** — id, tenant_id, wa_message_id (unique per tenant — idempotency §5), source_id,
  sender_phone_e164, sender_wa_name, direction, type (`text`|`audio`|`image`|`document`|`other`), body_text,
  media_id, media_mime, transcript_text (D3), received_at, raw_payload (jsonb).
- **`classifications`** — id, tenant_id, message_id, intent, person_name, person_appears_to_be, company_name,
  company_domain_hint, phone_e164, language, summary_one_line, suggested_record_type, confidence_overall,
  confidence_intent, confidence_person, confidence_company, model_used, prompt_version, latency_ms,
  created_at. *(Mirrors the v1.2 §3 schema 1:1.)*
- **`inbox_items`** — id, tenant_id, message_id, classification_id, status
  (`pending`|`auto_routed`|`confirmed`|`skipped`|`needs_review`), band (`high`|`medium`|`low`),
  assigned_action (jsonb), resolved_by, resolved_at.
- **`crm_cache`** — id, tenant_id, external_record_id, record_type, name, company, phones (E.164 array),
  emails, source_destination_id, last_synced_at. *(Resolves the lookup tension — §9.)*
- **`identity_resolutions`** — id, tenant_id, message_id, candidate_ids (jsonb), decision
  (`merge`|`link_related`|`new`), decided_by, decided_at, considered (jsonb, "never ask twice").
- **`destinations`** — id, tenant_id, kind (`google_sheets`|`webhook`), label, config (jsonb), field_mapping
  (jsonb), created_at.
- **`rules`** — id, tenant_id, name, conditions (jsonb), action (jsonb), enabled, priority, created_at.
- **`audit_log`** — id, tenant_id, message_id, action, classification_snapshot (jsonb), destination_id,
  destination_record_id, destination_record_url, actor (`bot`|user_id), created_at.
- **`eval_runs`** — id, golden_set_version, model, prompt_version, overall_accuracy, per_field (jsonb),
  calibration (jsonb), per_language (jsonb), confusion (jsonb), created_at.

---

## 5. Message ingestion — reliability (not in v1.2)

- **HMAC:** validate `X-Hub-Signature-256` against the app secret on every POST; reject on mismatch.
- **Idempotency:** Meta re-delivers and can duplicate. Dedup on `wa_message_id` (unique per tenant). Return
  `200` quickly *after* enqueue, before classification, so Meta doesn't retry on slow LLM calls.
- **Ordering:** assemble conversation history by message timestamp, not arrival order.
- **Status webhooks:** filter out `statuses.*` (delivered/read); only `messages.*` enter the pipeline.
- **Durability:** persist the raw envelope to `messages` *before* enqueuing, so a worker crash never loses a
  message (at-least-once + idempotency ≈ exactly once). Queue: FastAPI background tasks (MVP) → arq+Redis
  (scale), per v1.2 §11.

---

## 6. Media handling pipeline (D3)

GCC traffic is voice-note-heavy; v1.2's schema is text-only.

1. Webhook stores `messages` row with `media_id`; download via the Meta media endpoint into tenant-scoped
   object storage (S3-compatible; client bucket in self-hosted).
2. **Audio (voice notes):** transcribe. **🔲 NEEDS INPUT (D3-a):** provider — recommend **OpenAI Whisper
   API** for SaaS (strong Arabic + dialect), with self-hostable **faster-whisper/whisper.cpp** for the regulated
   tier (no audio egress). Confirm or name a preferred ASR.
3. **Images / PDFs:** extract text via the **primary LLM's vision** (Claude) for images/business cards
   (handles layout + Arabic better than classic OCR); **Tesseract** fallback for self-hosted. PDFs → text
   extraction → same classifier path.
4. Extracted `transcript_text` flows into the **same** classifier as `body_text`. The prompt is told the source
   modality so it calibrates confidence slightly lower for noisier ASR output.
5. **Cost:** transcription + vision raise per-message cost above v1.2's "single-digit dollars" estimate (still
   small at MVP volume). Add a per-tenant monthly media-processing ceiling as a guardrail; surface spend in
   the Admin/Eval view (§16).

---

## 7. Conversation-history retrieval (under-specified in v1.2)

- Per incoming message, fetch the **last N messages in the same `wa_chat_id`** (recommend N=10 or a
  ~2,000-token budget, whichever is smaller), ordered oldest→newest, included as prior turns with role +
  timestamp. The confidence rubric (v1.2 §3) explicitly depends on this context.
- History comes from our own `messages` table — no extra Meta calls. Truncate oldest-first to cap escalation
  cost.

---

## 8. Classifier service (model pinning + tiering)

- **Tiering (v1.2 §7):** first pass on the cheap tier; if `confidence.overall < 0.85`, re-run the same
  message+history through the larger model and take its result.
- **🔲 NEEDS INPUT (D8-a): pin exact model IDs.** v1.2 says "Haiku first, Sonnet/Opus on escalation,
  OpenAI as peer fallback," but availability has moved since the doc was drafted. Confirm the specific model
  IDs (and whether Opus is budgeted for hardest escalations). These live in config, not code.
- **Structured output:** constrained decoding / tool-call mode enforcing the v1.2 §3 schema. On
  schema-invalid output: retry once, then mark `unclear` → inbox.
- **Prompt versioning:** every classification stores `prompt_version`; the eval tool keys regressions to it.
- **Provider abstraction:** one internal `classify(message, history) -> Classification` interface with Anthropic
  / OpenAI / vLLM-Qwen implementations behind it (enables the self-hosted swap and cross-provider evals).

---

## 9. Identity resolution — resolving the v1.2 contradiction

**Most important correction here.** v1.2 §7 says every message does a synchronous **lookup against the
destination CRM**, but v1.2 §5 commits to **webhook-only destinations** — and a write-only Zapier/Make
webhook **cannot be queried**. The two sections contradict each other.

Resolution for v1:
- Per-message identity check runs against **our own `crm_cache`** (§4), not the live CRM, populated by:
  1. **Write-through** — every record the bot creates is also written to `crm_cache` (the common duplicate
     source: contacts the bot itself created).
  2. **Google Sheets** read-back — the Sheets connector *can* read, so its cache stays fresh.
- For pure-webhook CRMs (HubSpot etc.) we cannot read back in v1. Be **honest in-product**: per-message
  dedup covers bot-created + Sheets records; cross-system dedup against externally-created records is **v2**
  (matches v1.2's own out-of-scope row "auto-dedup across multiple destination CRMs").
- **🔲 NEEDS INPUT (D9-a):** accept this framing, or fund a HubSpot *read* exception to make per-message
  dedup real for the most common CRM?
- **Fuzzy matching:** E.164 exact + alternate-phone (primary signal); name+company via `rapidfuzz`
  (token-set ratio). Thresholds: ≥0.92 auto-merge candidate (still surfaced in medium band), 0.75–0.92
  surface for review, <0.75 new. Tunable per tenant.

---

## 10. Control-chat state machine (missing in v1.2)

- Medium-confidence → send a WhatsApp **interactive button message** to `control_chat_phone` with
  `Confirm` / `Change` / `Skip`. Each button `id` encodes `inbox_item_id` + a short-lived **signed token**
  (HMAC, ~15-min TTL) so replies are authenticated and bound to a specific item.
- Inbound reply → verify token + sender == `control_chat_phone` → apply action → write audit → one-line
  confirmation back in control chat.
- **Timeout:** no reply within ~24h (inside the Meta service window) → the item stays in the control-page
  inbox as fallback; the button message just expires.
- **`Change`** deep-links to the inbox item. High-confidence: one-line audit summary, no buttons.
  Low-confidence: straight to inbox, no ping. (Maps to DESIGN-SPEC §7.)

---

## 11. Destinations & recipes

- **Google Sheets:** service-account model (v1.2 §6). User pastes sheet URL + shares with the service-account
  email; field mapping per destination.
- **Webhook recipes:** a **versioned JSON template per destination** (HubSpot, Pipedrive, Notion, Airtable,
  Custom) describing payload shape + a mapping UI binding internal fields → recipe placeholders. Ship the 3
  Phase-2 recipes (HubSpot, Pipedrive, Notion). Store recipe id + resolved mapping on `destinations`.
- **Reliability:** webhook POSTs get retry-with-backoff + a **dead-letter** surface in the Admin view so failed
  routes are visible, not silently lost. Every attempt + final record id/url → `audit_log`.

---

## 12. Rules engine

Matches v1.2 §9 (simple, no DSL). Conditions as jsonb list (`sender_in_list`, `sender_is_new`,
`message_contains`), ANDed, with `priority`; first match wins, action auto-routes + writes audit `actor=bot`.
Auto-routed items still appear in the inbox marked `auto` (audit trail, v1.2 §4).

---

## 13. Eval tool — confirmations

v1.2 §12 is detailed. Two clarifications:
- **CI gate** ("blocks merge if accuracy drops >2pp") needs the golden set + a provider key in CI.
  **🔲 NEEDS INPUT (D13-a):** allow an LLM key in CI secrets, or run the gate against recorded-response
  fixtures (cheaper, deterministic, no live key)?
- **Golden-set PII:** production messages promoted to the set are customer data — redact/pseudonymize before
  committing JSONL to git, or keep the set in tenant-scoped storage outside the repo (ties to §14).

---

## 14. Data residency, retention, PII (partial in v1.2 §10)

- **Retention:** **🔲 NEEDS INPUT (D14-a):** how long to keep raw WhatsApp content + media? Recommend a
  configurable per-tenant default (12 months) with hard delete of raw bodies/media after, keeping only the
  structured classification + audit record. Regulated tenants may want shorter.
- **Subject deletion (PDPL/GDPR):** need a "delete everything for this phone/contact" operation — not in
  v1.2; recommend a minimal version in v1 since GCC regulated buyers will ask.
- **Golden-set PII:** see §13.
- **Self-hosted LLM path:** v1.2 §10's open-weights/Bedrock question is well-framed; tracked as an external
  watch item, not a Phase 1–4 blocker.

---

## 15. Control-page localization

UI chrome **English** for v1; message *data* renders correctly incl. Arabic/RTL runs (DESIGN-SPEC §9). Full
RTL UI mirroring is v2. **🔲 NEEDS INPUT (D15-a):** confirm English chrome is fine for your pilot clients.

---

## 16. Updated cost model note

v1.2's cost section predates D3. Add transcription (~$0.006/min Whisper) + vision/OCR per media message.
Still under the $20/client target at low volume, but media-heavy clients should be tracked separately and
per-tenant LLM + media spend surfaced in the Admin/Eval view.

---

## 17. Consolidated open questions (NEEDS INPUT)

> **Placeholders scaffolded:** the model IDs (D8-a), WhatsApp number / Meta API identifiers, and ASR provider
> below have `<...>` placeholders in **`.env.example`** at the repo root. Filling that file in answers the
> config half of these items; the framing/commercial choices still need a decision.

### Blocks starting the build
1. **(D8-a) Model IDs** — confirm first-pass / escalation / fallback model IDs; is Opus budgeted for hardest
   escalations? *(placeholders: `CLASSIFIER_MODEL_*` in `.env.example`)*
2. **(D2-a) Auth provider** — confirm Clerk for SaaS (+ Supabase/Ory for self-hosted), or name another.
3. **Accounts/access** — do you already have: a verified Meta Business + WABA (or test number)? Anthropic +
   OpenAI API access? A cloud account for hosting (AWS/Render)? *(placeholders: `META_*` / `WHATSAPP_*` in
   `.env.example`)*

### Shapes Phase 1–2
4. **(D3-a)** Transcription provider (Whisper API vs self-hostable) — confirm.
5. **(D9-a)** Identity dedup framing — accept "cache + Sheets in v1, webhook-CRM read-back is v2", or fund a
   HubSpot read exception?
6. **(D13-a)** LLM key in CI for the eval gate, or recorded fixtures?
7. **(D14-a)** Retention default + include subject-deletion in v1?
8. **(D15-a)** English UI chrome for v1 acceptable?

### Commercial / Phase 0
9. **Named pilot?** Do you already have ≥1 of the "2–3 LOIs", or is sourcing them part of the work?
10. **Soft-cap number** — routed-records soft cap + overage behavior (warn / throttle / fee)?
11. **Self-hosted tier pricing** — v1.2 §15 leaves open pending GCC conversations; flagged so it isn't lost
    before Phase 5.
12. **Group messages** — confirm the bot watches WhatsApp *group* chats (v1.2 §2); group sender identity +
    per-participant extraction warrants a dedicated mini-spec.

---

## 18. Recommended first build slices (once §17 block-1 is answered)

1. Repo scaffold + Pydantic schemas (the §4 schema is the single source of truth — build first).
2. Meta webhook receiver: HMAC + idempotency + persist-before-enqueue (§5).
3. Classifier: provider abstraction + tiering + structured output (§8).
4. Eval tool + 50-example golden set → baseline number (v1.2 §12).
5. Then the control page (Phase 2), built against `DESIGN-SPEC.md`.

Media pipeline (§6) slots in right after the text classifier works end-to-end, before the first pilot, so
voice-note traffic isn't a Phase-3 surprise.
