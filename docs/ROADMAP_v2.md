# Watcher v2 — Build Roadmap

From a message filer to a receptionist. Every item scored for urgency and ease. - 14 August 2026 - v1.13

> **The whole gap in one line:** the pipeline can listen and file, but it cannot reply. Three outcomes exist and all three mean "put this somewhere". Adding a fourth — **answer the customer** — is the project.

## Where we stand today

| Built and tested | Missing |
|---|---|
| 248 passing tests, no DB or network needed | Knowledge — zero tables, zero rows |
| 98 Python files, 13 modules | Live availability — no read path to any PMS |
| 18 DB tables + 3 migrations | Proof of identity — matching is not verifying |
| Every external system behind a swappable seam | Intent taxonomy unification — classification and vocabulary use different enums |
| Eval runner + job queue, with a CI accuracy gate | Prompt v2 — golden set still at 8 examples |
| Receptionist vocabulary: 19 intents, data not code | |
| Channel-neutral envelope + WhatsApp/voice adapters | |
| Task state machine and the autonomy gate | |
| **Reply path wired — RECEPTIONIST_REPLY is the fourth outcome** | |
| **Persistence — conversations, turns, tasks in Postgres** | |
| **Channel-neutral core — KNOWN_LEAKS = {}, boundary test enforces it** | |
| **Python 3.13** | |

## How to read the scores

- **NOW** — Today or tomorrow. Something is actively bleeding, or it blocks other work.
- **HIGH** — This sprint. The demo does not exist without it.
- **MED** — Needed before a real client, not before a demo.
- **LOW** — Do it when convenient. Nothing waits on it.
- **Trivial** — Minutes. A setting, a click, a delete.
- **Easy** — Understood problem, no design decisions left open.
- **Moderate** — Real work, but the pattern already exists in the repo to copy.
- **Hard** — Genuinely new. Nothing in the repo to copy from.

**Totals:** ~6.5 engineering days remaining by the numbers below. Track 0 is done, Track 1 is done, and the core of Track 2 (2.1, 2.2, 2.3) is done. At six days a week that is a demo in about one week and a real client's guests on it in about two. The critical path is now **2.4 (knowledge base)** and **3.1 (PMS read port)**: no facts means no answers, and no read path means no pricing, no availability and no door codes.

---

## Track 0 — Today. Hours, not days. [COMPLETE]

All five items done.

| # | Work item | Urgency | Ease | Days | Status |
|---|---|---|---|---|---|
| 0.1 | Set default branch to main | NOW | Trivial | 1 min | **DONE** |
| 0.2 | Remove the client name from the golden set and fixtures | NOW | Easy | 0.25 | **DONE** |
| 0.3 | Decide the receptionist intent vocabulary | NOW | Easy | 1 hr | **DONE** — 19 intents, 84 examples, 5 languages, 38 tests |
| 0.4 | Merge the eval branch | NOW | Trivial | 0.25 | **DONE** |
| 0.5 | Delete the stale nifty-johnson branch | LOW | Trivial | 1 min | **DONE** |

---

## Track 1 — Foundations. Week 1. [COMPLETE]

All three items done.

| # | Work item | Urgency | Ease | Days | Status |
|---|---|---|---|---|---|
| 1.1 | Stop the core speaking WhatsApp | NOW | Moderate | 1.5 | **DONE** — `wa_message_id` to `external_id`, `wa_chat_id` to `thread_id`, `sender_wa_name` to `sender_display_name`. Added `channel` field. `KNOWN_LEAKS = {}`. Alembic migration 002. `MetaSettings` moved to `channels/whatsapp.py`. `to_inbound_turn()` bridge added. `CHANNEL_REGISTRY` exempts legitimate data values. |
| 1.2 | Port the four kept scaffold files | HIGH | Moderate | 1.0 | **DONE** (prior session) |
| 1.3 | Python 3.12 to 3.13 | LOW | Easy | 0.5 | **DONE** — 7 version pins updated across pyproject.toml, packages, and CI. |

---

## Track 2 — Make it a receptionist. Week 2. [CORE DONE, 2.4 and 2.5 REMAIN]

The product's reply path is wired end-to-end. What remains is the knowledge base and prompt tuning.

| # | Work item | Urgency | Ease | Days | Status |
|---|---|---|---|---|---|
| 2.1 | Conversations, tasks and slot filling | HIGH | Hard | 2.0 | **DONE** — 7 new tables via Alembic migration 003: `contacts`, `conversations`, `turns`, `task_rows`, `understandings`, `corrections`, `usage_events`. ORM models in `db/models.py`. `ConversationRepository` with `find_or_create_conversation`, `record_turn`, `get_active_task`, `save_task`, `create_task`. `task_from_row`/`task_to_row` converters. Verification-codes table deferred to 2.4 (knowledge base). |
| 2.2 | The reply path | HIGH | Moderate | 1.5 | **DONE** — Tool registry (`TakeMessage`, `HandoffToHuman`) validated against vocabulary `terminal_tool`. Receptionist function in `conversations/receptionist.py`: check autonomy, manage task state, return `OutboundAction` (ask/confirm/say/handoff). `ChannelSender` protocol in `channels/sender.py`. 8 tests. |
| 2.3 | Autonomy gate | HIGH | Easy | 1.0 | **DONE** — `RECEPTIONIST_REPLY` as fourth `RoutingAction`. `Receptionist` protocol in worker. After classification + identity resolution, `decide_autonomy()` runs before rule matching. Acting intents invoke receptionist; hand-off falls through to existing routing. `ProcessOutcome` carries `autonomy` and `outbound_action`. 4 new orchestration tests. |
| 2.4 | Knowledge base | HIGH | Moderate | 2.0 | **NEXT** — Facts table with sensitivity flags, prose in pgvector, and a real "I don't know" that fetches a human. Door codes are not ordinary facts. For the demo, one property's facts fit in the prompt — no retrieval needed. |
| 2.5 | Prompt v2 and rewrite the golden set | MED | Moderate | 1.0 | **PENDING** — Unblocked: 0.3 is done. Receptionist vocabulary, 8 cases rewritten and ~50 added. The runner from 0.4 already exists to score them. Watch the mixed-language number — it is 0% on the current 8. |

### Known design gap: intent taxonomy unification

`IntentType` (the classification enum: `new_lead`, `support_issue`, etc.) and the vocabulary intents (`property_question`, `booking_enquiry`, etc.) are separate taxonomies. The autonomy gate returns `hand_off` for any intent not in the vocabulary, so the receptionist path only fires when the classifier produces a vocabulary-recognized intent. **Unifying the two is prerequisite to the receptionist handling real traffic.** Estimate: 0.5 days, Easy — the vocabulary already declares the canonical set; the classification enum needs to adopt it.

| # | Work item | Urgency | Ease | Days | Status |
|---|---|---|---|---|---|
| 2.6 | Intent taxonomy unification | NOW | Easy | 0.5 | **NEW** — Merge `IntentType` enum with vocabulary intent names so `decide_autonomy()` recognises classified intents and the receptionist fires on real messages. |

---

## Track 3 — Integration and launch. Week 3.

Base360.ai is ruled out as a partner: their product is substantially ours and their client base is closed to us. Hostaway, Guesty and Cloudbeds all publish APIs, so the capability is available without the strategic cost.

| # | Work item | Urgency | Ease | Days | Status |
|---|---|---|---|---|---|
| 3.1 | `PropertySystemPort` plus the first adapter — the bottleneck | MED | Moderate | 2.5 | **PENDING** — Build the port, not a Hostaway integration. Blocks pricing, availability and door codes: with no way to look up a booking, the identity check cannot run. `crm_cache` is the right shape; delivery is write-only, so this needs a new read port. Cache facts; never cache availability. |
| 3.2 | End to end on a real number, then measure | HIGH | Moderate | 1.0 | **PENDING** — The point at which the eval number becomes real rather than recorded. |

---

## Runs in parallel — start on day one

None of these are engineering. All of them can quietly become the reason a date slips.

| # | Work item | Urgency | Ease | Days | Status |
|---|---|---|---|---|---|
| P1 | Pick the first client | NOW | Easy | -- | Decides which PMS adapter gets built first. Without it, 3.1 is a guess. |
| P2 | Read the Hostaway / Guesty / Cloudbeds API docs, get sandbox keys | HIGH | Easy | 0.5 | Public docs, usually self-service sandboxes. Shape the port from what two or three of them offer in common. |
| P3 | File Meta business verification | MED | Easy | 1 hr | No longer blocking — unverified start is allowed, and a receptionist mostly replies inside the 24-hour window. File it anyway before volume makes it bind. |
| P4 | Graphify as a build aid (optional) | LOW | Easy | 0.5 | Maps the repo for the coding agents during the 1.1 rename. Local parsing, no vector store, nothing leaves the machine. |

---

## The dates (revised 14 August 2026)

| Milestone | Original | Revised | Status |
|---|---|---|---|
| Eval merged, name leak fixed, vocabulary decided | End of day 1 | -- | **DONE** |
| Core stops speaking WhatsApp; scaffold files ported | End of week 1 | -- | **DONE** |
| Holds a conversation and replies — rough demo | Middle of week 2 | End of this week | **DONE** (wiring complete, needs taxonomy unification + knowledge for a real demo) |
| Knowledge and safety rules in — demo-ready | End of week 2 | ~2 days from now | Blocked on 2.4 + 2.6 |
| Live availability, measured, on a real number — pilot-ready | Middle of week 3 | ~5-6 days from now | Blocked on 3.1 + P1 |
| Phone answering | Week 4 | Week 4 | Unchanged — speech-to-text seam already exists |

---

## What would actually move these dates

**Intent taxonomy unification drifting.** Half a day of work that the receptionist literally cannot function without on real traffic. It is the new 0.3 — small, high leverage, do it first.

**Knowledge base scope creep.** The 2.0 days assumes a structured intake form plus PMS sync. "Can it read our PDF handbook?" is a different and much larger project. Say no for now.

**Quality, not features.** Getting to *working* is days away. Getting to *trustworthy* — where it never invents a check-in time — is measured by the eval runner, and that number is not fully under your control. Budget a tuning tail.

**PMS API access.** Docs are public, but sandbox approval and rate limits are theirs to grant. Start P2 on day one; it is the new version of the Meta-verification lesson.

> **If you do only one thing today:** unify the intent taxonomy (2.6) and start the knowledge base (2.4). One is half a day of enum refactoring, the other is the last hard engineering item before a demo. Together they make the receptionist real.

---

## Session log — 14 August 2026

**PR:** [#13](https://github.com/amahmoudosman96-lgtm/Watcher/pull/13) on `claude/file-review-planning-dcyb2g`
**Tests:** 228 to 248 (+20 new tests)
**Commits:** 5 (one per item: 1.1, 1.3, 2.1, 2.2, 2.3) + docs

Items completed this session: 1.1 (1.5d), 1.3 (0.5d), 2.1 (2.0d), 2.2 (1.5d), 2.3 (1.0d) = **6.5 engineering days delivered**.
