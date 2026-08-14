# Session Handoff — 2026-08-14

**Branch:** `claude/file-review-planning-dcyb2g`
**PR:** [#13](https://github.com/amahmoudosman96-lgtm/Watcher/pull/13)
**Tests:** 248 passing | mypy clean | ruff clean
**Roadmap:** `docs/ROADMAP_v2.md` (v1.13) — the v2 build roadmap with urgency/ease scoring

---

## Where we stand (against v2 roadmap)

| Track | Status | Remaining |
|---|---|---|
| Track 0 — Today (0.1–0.5) | **COMPLETE** | — |
| Track 1 — Foundations (1.1–1.3) | **COMPLETE** | — |
| Track 2 — Receptionist (2.1–2.6) | **CORE DONE** (2.1, 2.2, 2.3) | 2.4 Knowledge base (2.0d), 2.5 Prompt v2 (1.0d), 2.6 Taxonomy unification (0.5d) |
| Track 3 — Integration (3.1–3.2) | Not started | 3.1 PMS read port (2.5d), 3.2 End-to-end (1.0d) |
| Parallel (P1–P4) | Not started | Founder decisions, API docs, Meta verification |

**~6.5 engineering days remain.** Critical path: 2.6 (taxonomy) → 2.4 (knowledge) → 3.1 (PMS read port).

---

## What was done this session

Five items from the v2 plan, committed individually (6.5 engineering days delivered):

### Item 1.1 — De-WhatsApp the core [Urgency: NOW, Ease: Moderate, 1.5d]
Renamed channel-specific identifiers across 8 core files:
- `wa_message_id` → `external_id`, `wa_chat_id` → `thread_id`, `sender_wa_name` → `sender_display_name`
- Added `channel: str` field (default `"whatsapp"`) to `MessageEnvelope`
- Moved `MetaSettings`/`ConfigError` from `core/config.py` → `channels/whatsapp.py` (deleted `core/config.py`)
- Added `ChannelConfig` ORM model for per-tenant channel credentials
- `KNOWN_LEAKS = {}` in `test_boundary.py` — the boundary scanner now passes clean
- `CHANNEL_REGISTRY` allows legitimate data values naming channels (defaults, enum members)
- Alembic migration `002_channel_neutral_renames.py` handles column/constraint renames + migrates `waba_id`/`phone_number_id` into `channel_configs`
- Added `to_inbound_turn()` bridge in `schemas/envelope.py` to convert `MessageEnvelope` → `InboundTurn`

### Item 1.3 — Python 3.12 → 3.13 [Urgency: LOW, Ease: Easy, 0.5d]
Seven version pins updated: `pyproject.toml` (requires-python, ruff target-version, mypy python_version), `packages/eval/pyproject.toml`, `packages/intents/pyproject.toml`, `.github/workflows/ci.yml` (2 locations).

### Item 2.1 — Conversations, tasks, slot filling + persistence [Urgency: HIGH, Ease: Hard, 2.0d]
- Alembic migration `003_conversations_and_tasks.py` — 7 new tables
- ORM models added to `db/models.py`: `Contact`, `Conversation`, `Turn`, `TaskRow`, `UnderstandingRow`, `CorrectionRow`, `UsageEvent`
- `TaskRow` (ORM) vs `Task` (in-memory dataclass in `conversations/task.py`) — deliberate naming to avoid collision
- `db/conversation_repo.py` — `ConversationRepository` with `find_or_create_conversation`, `record_turn`, `get_active_task`, `save_task`, `create_task`, plus `task_from_row`/`task_to_row` converters

### Item 2.2 — The reply path [Urgency: HIGH, Ease: Moderate, 1.5d]
- `conversations/tools.py` — `ToolResult`, `Tool` ABC, `REGISTRY` dict, `TakeMessage`, `HandoffToHuman`, `validate_registry()` (checks against vocabulary `terminal_tool`)
- `conversations/receptionist.py` — `async handle()`: check autonomy → manage task state → return `OutboundAction` (ask/confirm/say/handoff)
- `channels/sender.py` — `ChannelSender` protocol stub (real Meta send-via-API is a separate item)
- 8 receptionist tests

### Item 2.3 — Autonomy gate wiring [Urgency: HIGH, Ease: Easy, 1.0d]
- `RECEPTIONIST_REPLY` added as fourth `RoutingAction` in `orchestration/worker.py`
- `Receptionist` protocol defined; `Orchestrator.__init__` accepts optional `receptionist`
- After classification + identity resolution, `decide_autonomy()` runs before rule matching when receptionist is provided
- Acting intents (`act` / `act_and_notify`) invoke the receptionist → `RECEPTIONIST_REPLY`
- Hand-off intents fall through to existing rule → band routing
- `ProcessOutcome` extended with `autonomy: Autonomy | None` and `outbound_action: OutboundAction | None`
- 4 new orchestration tests

---

## Key design decisions

1. **Two intent taxonomies coexist (gap — item 2.6).** `IntentType` (classification enum: `new_lead`, `support_issue`, etc.) is the routing taxonomy. Vocabulary intents (`property_question`, `booking_enquiry`, etc.) are the conversational taxonomy the receptionist uses. The autonomy gate returns `hand_off` for any intent not in the vocabulary — so the receptionist path only fires when the classifier produces a vocabulary-recognized intent. **Unifying them (2.6) is the single highest-leverage next item.**

2. **Boundary enforcement.** `test_boundary.py` scans core files for channel-specific vocabulary. `CHANNEL_REGISTRY` exempts legitimate data values (e.g., `default="whatsapp"` in model columns). Adapter packages (`ingestion`, `channels`) are exempt from scanning.

3. **`asyncio.run()` in sync orchestrator.** The receptionist is async (tools may do I/O), but `Orchestrator.process()` is sync. The bridge uses `asyncio.run()` — acceptable for now since the orchestrator runs in a background task, not in an existing event loop.

---

## What to do next (v2 roadmap order)

> **If you do only one thing today:** unify the intent taxonomy (2.6) and start the knowledge base (2.4). One is half a day of enum refactoring, the other is the last hard engineering item before a demo. Together they make the receptionist real.

### Immediate (NOW / HIGH urgency)

1. **2.6 Intent taxonomy unification** [NOW, Easy, 0.5d] — Merge `IntentType` enum with vocabulary intent names so `decide_autonomy()` recognises classified intents and the receptionist fires on real messages. The vocabulary already declares the canonical set; the classification enum needs to adopt it.

2. **2.4 Knowledge base** [HIGH, Moderate, 2.0d] — Facts table with sensitivity flags, prose in pgvector, and a real "I don't know" that fetches a human. Door codes are not ordinary facts. For the demo, one property's facts fit in the prompt — no retrieval needed.

3. **2.5 Prompt v2** [MED, Moderate, 1.0d] — Rewrite golden set 8 → ~50. The vocabulary and eval runner are ready. Watch mixed-language accuracy — it is 0% on the current 8.

### After demo

4. **3.1 PropertySystemPort** [MED, Moderate, 2.5d] — Build the port, not a Hostaway integration. Blocks pricing, availability and door codes.
5. **3.2 End to end** [HIGH, Moderate, 1.0d] — Real number, real eval.

### Parallel (founder, not engineering)

- **P1** Pick the first client — decides which PMS adapter.
- **P2** Read Hostaway/Guesty/Cloudbeds API docs, get sandbox keys.
- **P3** File Meta business verification.

---

## How to verify

```bash
pip install pytest pydantic fastapi sqlalchemy rapidfuzz alembic httpx pyyaml
pip install ruff==0.6.9
python3 -m pip install mypy==1.11.2 types-PyYAML==6.0.12.20240917

python3 -m ruff check .
python3 -m ruff format --check .
python3 -m mypy
python3 -m pytest
```

All 248 tests should pass.
