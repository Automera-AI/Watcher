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

---

## Engineering follow‑ups created by these decisions (Sprint 1)

- [ ] Replace open‑string `intent` / `suggested_record_type` with the locked **enums** (`schemas/enums.py`, `classification.py`).
- [ ] Change `MEDIUM_CONFIDENCE_THRESHOLD` **0.60 → 0.5** (`schemas/common.py`); converge `band_for()` with the classifier's `escalation_threshold`.
- [x] Implement **AnthropicProvider** + **OpenAIProvider** behind the `LLMProvider` seam, reading the pinned model IDs from config. *(roadmap A3 — `classifier/anthropic.py`, `classifier/openai.py`, wired by `classifier/factory.py`; the OpenAI provider doubles as the vLLM/Qwen path)*
- [x] Add a typed **Settings** object in `core/` (extend `MetaSettings`) reading the pinned model/ASR config. *(roadmap A1 — `core/config.py`; `Settings.meta()` returns the existing `MetaSettings`)*
- [ ] Alembic target + Render Postgres URL wired in deploy.

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
