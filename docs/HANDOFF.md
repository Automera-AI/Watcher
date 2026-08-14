# Session Handoff — 2026-08-14

**Branch:** `claude/file-review-planning-dcyb2g`
**PR:** [#13](https://github.com/amahmoudosman96-lgtm/Watcher/pull/13)
**Tests:** 248 passing | mypy clean | ruff clean

---

## What was done

Five items from the implementation plan, committed individually:

### Item 1.1 — De-WhatsApp the core
Renamed channel-specific identifiers across 8 core files:
- `wa_message_id` → `external_id`
- `wa_chat_id` → `thread_id`
- `sender_wa_name` → `sender_display_name`
- Added `channel: str` field (default `"whatsapp"`) to `MessageEnvelope`
- Moved `MetaSettings`/`ConfigError` from `core/config.py` → `channels/whatsapp.py` (deleted `core/config.py`)
- Added `ChannelConfig` ORM model for per-tenant channel credentials
- `KNOWN_LEAKS = {}` in `test_boundary.py` — the boundary scanner now passes clean
- `CHANNEL_REGISTRY` allows legitimate data values naming channels (defaults, enum members)
- Alembic migration `002_channel_neutral_renames.py` handles column/constraint renames + migrates `waba_id`/`phone_number_id` into `channel_configs`
- Added `to_inbound_turn()` bridge in `schemas/envelope.py` to convert `MessageEnvelope` → `InboundTurn`

### Item 1.3 — Python 3.12 → 3.13
Seven version pins updated: `pyproject.toml` (requires-python, ruff target-version, mypy python_version), `packages/eval/pyproject.toml`, `packages/intents/pyproject.toml`, `.github/workflows/ci.yml` (2 locations).

### Item 2.1 — Conversations, tasks, slot filling + persistence
- Alembic migration `003_conversations_and_tasks.py` — 7 new tables
- ORM models added to `db/models.py`: `Contact`, `Conversation`, `Turn`, `TaskRow`, `UnderstandingRow`, `CorrectionRow`, `UsageEvent`
- `TaskRow` (ORM) vs `Task` (in-memory dataclass in `conversations/task.py`) — deliberate naming to avoid collision
- `db/conversation_repo.py` — `ConversationRepository` with `find_or_create_conversation`, `record_turn`, `get_active_task`, `save_task`, `create_task`, plus `task_from_row`/`task_to_row` converters

### Item 2.2 — The reply path
- `conversations/tools.py` — `ToolResult`, `Tool` ABC, `REGISTRY` dict, `TakeMessage`, `HandoffToHuman`, `validate_registry()` (checks against vocabulary `terminal_tool`)
- `conversations/receptionist.py` — `async handle()`: check autonomy → manage task state → return `OutboundAction` (ask/confirm/say/handoff)
- `channels/sender.py` — `ChannelSender` protocol stub (real Meta send-via-API is a separate item)
- 8 receptionist tests

### Item 2.3 — Autonomy gate wiring
- `RECEPTIONIST_REPLY` added as fourth `RoutingAction` in `orchestration/worker.py`
- `Receptionist` protocol defined; `Orchestrator.__init__` accepts optional `receptionist`
- After classification + identity resolution, `decide_autonomy()` runs before rule matching when receptionist is provided
- Acting intents (`act` / `act_and_notify`) invoke the receptionist → `RECEPTIONIST_REPLY`
- Hand-off intents fall through to existing rule → band routing
- `ProcessOutcome` extended with `autonomy: Autonomy | None` and `outbound_action: OutboundAction | None`
- 4 new orchestration tests

---

## Key design decisions made

1. **Two intent taxonomies coexist.** `IntentType` (classification enum: `new_lead`, `support_issue`, etc.) is the routing taxonomy. Vocabulary intents (`property_question`, `booking_enquiry`, etc.) are the conversational taxonomy the receptionist uses. The autonomy gate returns `hand_off` for any intent not in the vocabulary — so the receptionist path only fires when the classifier produces a vocabulary-recognized intent. Unifying them is future work.

2. **Boundary enforcement.** `test_boundary.py` scans core files for channel-specific vocabulary. `CHANNEL_REGISTRY` exempts legitimate data values (e.g., `default="whatsapp"` in model columns). Adapter packages (`ingestion`, `channels`) are exempt from scanning.

3. **`asyncio.run()` in sync orchestrator.** The receptionist is async (tools may do I/O), but `Orchestrator.process()` is sync. The bridge uses `asyncio.run()` — acceptable for now since the orchestrator runs in a background task, not in an existing event loop.

---

## What's next

With items 1.1, 1.3, 2.1, 2.2, 2.3 complete, the remaining plan items are:

1. **Concrete LLM providers** (Anthropic/OpenAI) against the `LLMProvider` seam — needs API keys
2. **Grow golden set 8 → 50** and re-record fixtures for real baseline accuracy
3. **DB-backed `MessageLoader`** for `orchestration/queue.py` + wire `BackgroundTasksQueue` into webhook route
4. **Real `ChannelSender` implementation** for WhatsApp (Meta Cloud API send)
5. **Intent taxonomy unification** — merge `IntentType` enum with vocabulary intents so the receptionist fires on real classified messages
6. **REST API** for control page (inbox/sources/destinations/rules)
7. **Inbox view** (frontend)

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
