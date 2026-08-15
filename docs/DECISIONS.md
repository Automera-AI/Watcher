# Watcher — Locked Decisions

**Status:** Decision record. Closes the Track‑0 / §17 "Decision Gate" from the roadmap. These are the
single source of truth for engineering; supersedes the open `🔲 NEEDS INPUT` items in
`docs/build-spec-addendum.md §17` that they resolve. Locked 2026‑06‑14.

---

## Founder decisions (Lane A)

| # | Decision | **Locked choice** | Downstream impact |
|---|----------|-------------------|-------------------|
| D8‑a | Classifier model tiering | **Haiku 4.5 → Sonnet 4.6, GPT‑4o‑mini fallback** | LLM provider impls; `.env` model IDs pinned |
| — | SaaS hosting | **Render** | CD deploy target; Alembic DB; staging env |
| D2‑a | Control‑page auth | **Clerk** (swap to self‑hostable for regulated tier) | Auth slice; tenancy binding |
| — | Intent taxonomy + schema | **Pin 6 intents + 3 record types as enums; keep flat schema** | `schemas/enums.py` + classification; eval confusion matrix |

**Pinned model IDs** (in `.env.example`):
- First pass: `claude-haiku-4-5-20251001`
- Escalation: `claude-sonnet-4-6`
- Fallback: `gpt-4o-mini`

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
- [ ] Implement **AnthropicProvider** + **OpenAIProvider** behind the `LLMProvider` seam, reading the pinned model IDs from config.
- [ ] Add a typed **Settings** object in `core/` (extend `MetaSettings`) reading the pinned model/ASR config.
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

---

## Session 4 decisions — product shape and guardrails (locked 2026‑08‑15)

Taken after a code audit that found the roadmap's remaining-work figure excluded all
infrastructure. Context and pricing in `docs/LAUNCH-PLAN.md`.

| # | Decision | **Locked choice** | Downstream impact |
|---|----------|-------------------|-------------------|
| D14 | What we are launching | **Multi‑tenant product, many properties per client, nothing hardcoded** | Everything below; RLS is mandatory, not deferred |
| D15 | Control‑page view set | **Six receptionist views** — Handoff queue · Conversations · Emergencies · Properties & Facts · Quotes & Audit · Admin/Eval | `DESIGN-SPEC.md` §8 rewritten. Destinations and Rules views dropped — they serve the v1 message‑filer |
| D16 | Property‑system direction | **Outbound adapter to Watcher's own contract; Watcher writes and hosts the per‑PMS bridges** | `HttpPropertySystemAdapter` calls a client‑supplied URL; Hostaway bridge first |
| D17 | Quote path | **Built properly** — extend `AvailabilityResult` with `rate_or_quote_id` + `valid_until`; 300s freshness; provenance to the audit log **before** the reply is sent | Resolves the contradiction between `property_system/schemas.py` and `quoting.provenance_required` |
| D18 | Booking scope at launch | **Enquiry + reservation lookup only** | `hold_slot` and `confirm_booking` disabled. No write port, no authority spec needed yet |
| D19 | Guest verification | **Booking reference + a second fact** | Unlocks reservation details *and* door codes — see D20 |
| D20 | Door‑code disclosure | **Same bar as D19 — reference + fact** | **Reaffirmed twice, with reasoning.** Rationale: roughly half of bookings come through an OTA, which has already verified the guest by account and payment, and that guest receives the code through the OTA app regardless — so a second challenge adds friction without adding much assurance. Residual risk accepted: both facts appear on the confirmation email, so a forwarded or screenshotted email can obtain a code. Two mitigations considered and rejected — (a) require the sender's number to match the reservation, which **fails for OTA guests specifically** because Airbnb and Booking.com issue masked relay numbers (`SESSION-HANDOFF.md` §8: "about half of all guests"), and `Reservation` carries no phone field today; (b) gate by lock type, releasing per‑booking PINs but sending static key‑box codes to a human. Surface the decision in the Quotes & Audit view (D8) |
| D20a | Door‑code risk to revisit | **Static key‑box codes are the open exposure** | Not a blocker, but the one case where D20's reasoning does not reach: a per‑booking PIN expires at checkout, whereas a leaked static code compromises every future guest until someone physically attends the property. Revisit once real traffic shows how often door codes are requested and what lock types the first clients actually use |
| D21 | `identity_verified` wiring | **Fixed when the verification mechanism ships**, not before | Until then it remains fuzzy‑match output; `decide_autonomy` keeps treating it conservatively |
| D22 | Emergency path | **Trigger matching wired before classification + Twilio outbound call to the operator** | New vendor. `worker.py` currently hardcodes `emergency=False` |
| D23 | Per‑client config | **Split by blast radius** — `property_system`, `quote_prices`, `force_hand_off`, `disabled_intents` stay in YAML behind the build validator; currency, timezone, hours, wording, properties, facts move to the DB | Keeps the validator that refuses `quote_prices: true` against a non‑quotable system. Onboarding is part deploy, part self‑serve |
| D24 | v1 message‑filer backend | **Excised from the pipeline inside A5; tables retained** | Rules and destinations are one mechanism (`RuleAction.destination_id`). The vocabulary's `force_hand_off` / `disabled_intents` replaces it, validated at build time. Avoids two independent escalation paths |
| D25 | Hosting and region | **Supabase + Render, EU (Frankfurt)** | GCC data‑residency claim is not currently supportable; the migration is a named, unpriced future cost |
| D26 | Capacity assumption | **Solo founder + coding agents, most days; ~2–3 engineering days per session** | ~43.75 days ≈ 15–20 sessions. External approvals (Meta, PMS) run on calendar, not effort |

**Revised remaining work: ~43.75 engineering days** (was 5.75 in roadmap v1.15, 35.25 after the
infrastructure audit, +8.5 from the decisions above).
