# Session handoff — read this first

**Updated:** end of session 7, 15 August 2026
**Branch:** `claude/a5-a6-implementation-tb5s5e` — one commit, pushed. **No PR opened yet.**
**`main` is at:** `0542375` — session-7 work is not on `main`; see §8 before branching
**Start at §2 for status, §8 for what to do first.**

Purpose: let a new session pick up without re-deriving anything. Companion documents are
`docs/Watcher_v2_Roadmap.pdf` (**v2.4**, regenerated from `docs/make_roadmap.py` this session) and
the three specs in `docs/specs/` — why the code is shaped the way it is.

Overwrite this file at the end of each session.

---

## 1. Verified state right now

Measured by running it, not read off a document.

| | |
|---|---|
| Branch | **`claude/a5-a6-implementation-tb5s5e`** — pushed, unmerged |
| `main` | `0542375` — sessions 1–6 |
| Tests | **406 passing** (was 375 at the start of this session) |
| Python files | 125 total; 86 source across 20 modules |
| Lint / types | ruff clean; strict mypy clean on 123 files |
| Recorded baseline | **88%** intent accuracy, gate passing — **still v2's number**, see §5 |
| Python | 3.13 |

To get green in a fresh container:

```bash
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python ruff==0.6.9 mypy==1.11.2 pytest==8.3.3 \
  pydantic==2.9.2 pydantic-settings==2.5.2 fastapi==0.115.0 httpx==0.27.2 \
  rapidfuzz==3.10.1 sqlalchemy==2.0.50 alembic==1.13.3 pyyaml==6.0.2 \
  types-PyYAML==6.0.12.20240917
.venv/bin/python -m pytest          # expect 406 passed
.venv/bin/mypy && .venv/bin/ruff check . && .venv/bin/ruff format --check .
```

`psycopg[binary]` and `uvicorn[standard]` are still deliberately absent from that list and from CI
— nothing imports either. `pip install -e .` gets you both. `reportlab` is needed only to re-run
`docs/make_roadmap.py`.

**The product gap, restated.** The application now **starts, connects, files, and answers**: a
signed webhook comes in, the message is persisted, attributed to a tenant, classified, joined to
its conversation, continued as a task that survives between turns, answered by the receptionist,
and the reply goes out over the Cloud API. Track A is complete.

What it cannot do is be *reached* — there is no Supabase project, no Dockerfile, no public URL
(Track B) — and it cannot yet tell an emergency from a maintenance request (**G3**). Read §3 on
that second one before deploying anything a guest can message.

---

## 2. What session 7 delivered

**Roadmap items A5 (2.25d) and A6 (1.0d) — 3.25 engineering days, and Track A is finished.**

Full reasoning: `docs/specs/a5-continuity-and-a6-outbound-sender.md`.

### A5 — continuity, and the end of the v1 filer

| Seam | Before | After |
|---|---|---|
| `Orchestrator.process` | sync; `asyncio.run` around the receptionist | `async`; the transport owns the loop |
| Conversation | none | `ConversationStore.begin` / `record_reply` |
| Task | `None` on every message | loaded from `task_rows`, saved back after the reply |
| `classifications` | never written | one row per classified message, with telemetry |
| `inbox_items.classification_id` | always null | points at that row |
| Rules / destinations | evaluated per message | **removed from this path** (D24) |

Four things are worth knowing without reading the spec:

1. **A receptionist without a conversation store is refused at construction.** It forgets the
   previous turn on every message, which looks like it works. That was the A4-era trap; it is now
   a `ValueError`.
2. **Every classified message reaches the receptionist**, and its own autonomy check decides the
   shape of the reply. An intent reserved for a human returns a handoff action — the guest is told
   someone is coming — *and* the item is filed `needs_review`. Both halves are true at once, which
   they never were before.
3. **The clarifying-turn budget is live.** `defaults.max_clarifying_turns` (3) and
   `defaults.on_max_turns` have been in the vocabulary since item 0.3 and nothing read them.
   Continuity is what made them load-bearing: without the budget, a task that cannot be filled asks
   the same question forever.
4. **`classifications` telemetry is measured, not invented.** `ClassificationOutcome` now carries
   `latency_ms` (wall clock across retries *and* escalation — what the guest actually waited for)
   and `prompt_version` (injected at construction). `model_used`, which used to be handed to the
   inbox writer and dropped, lives on the classification row.

### A6 — the outbound sender

`WhatsAppSender` on the `ChannelSender` seam: posts to the versioned Graph endpoint, renders quick
replies within WhatsApp's three-button / 20-character limits, retries 408/429/5xx with bounded
backoff and raises immediately on a 4xx. A sync `httpx.Client` called through `asyncio.to_thread`,
because the loop it runs on is not always ours.

**The channel credential fields moved out of `core/config.py`.** `channels/config.py` declares them
as `ChannelCredentials` and `Settings` extends it; `core/settings_base.py` holds the placeholder
handling and `ConfigError` that both halves share. **`KNOWN_LEAKS` is now empty — roadmap item 1.1
is closed.**

### Decisions made this session

| Decision | Choice | Where it lives |
|---|---|---|
| Rules + destinations in the message path | **Removed (D24).** Engine and both tables retained for the control page | `orchestration/worker.py`, `rules/engine.py`, `docs/DECISIONS.md` |
| Continuity | **Mandatory with a receptionist**, refused otherwise | `orchestration/worker.py` |
| Clarifying-turn budget | **From the vocabulary**, applied to asking and never to acting | `conversations/receptionist.py` |
| A superseded task | **`TaskStatus.ABANDONED`** — not `failed`; nothing went wrong | `conversations/task.py` |
| Reply ordering | **Record, then send.** A sent-but-unrecorded reply makes us ask again | `orchestration/worker.py` |
| A failed send | **Logged and reported (`delivered=False`), never raised** | `orchestration/worker.py` |
| Where the event loop comes from | **The queue transport**, not the orchestrator | `orchestration/queue.py` |
| Channel credentials | **`channels/config.py`; `Settings` extends it** | `channels/config.py`, `core/settings_base.py` |
| Choosing the sender | **`channels/factory.py`** — `main.py` is scanned by the boundary test | `channels/factory.py` |

---

## 3. Traps and things not to re-litigate

- **G3 is now more urgent than it was, not less.** `emergency=False` is still hardcoded (one named
  line in `_converse`, with a comment). Until A6, a gas leak was filed in silence; it now gets a
  confident, polite reply about maintenance. **Answering raised the cost of not detecting an
  emergency.** Do not deploy to a number a real guest can reach without G3.
- **Do not "fix" the empty slot dict by inventing extraction.** The classifier emits no slots —
  `ClassificationResult` has no such field — so a task fills only by the clarifying-turn budget
  expiring and then hands off. That is bounded and it escalates cleanly. Adding slots is a prompt
  change *and* a golden-set change (item 2.x), and it invalidates the recorded baseline.
- **Do not re-add rules or destinations to the orchestrator.** The tables and the engine are
  retained on purpose, for the control page (track D), where a human routes something deliberately.
  A message that gets answered has nowhere to be filed to.
- **Do not add a module-level `app` to `main.py`**, and **do not name a channel in it** — the
  boundary test scans it, which is why `channels/factory.py` exists.
- **`KNOWN_LEAKS` is empty. Keep it that way.** The machinery stays deliberately: an empty
  allowlist is the strongest form of that test, and a phone line is the next channel — exactly when
  someone will want to add "just one" exception back.
- **Do not make the webhook wait for classification.** Unchanged. The thread pool exists for that
  reason; **B5 (arq/Redis) is the replacement**, on the same seam.
- **Two messages arriving at once can race** in `find_or_create_conversation` and open two
  conversations for one thread. Ordering is the queue's job and the queue is in-process until B5.
  Do not paper over it with a lock that will not survive a second process.
- **`DATABASE_POOL_MODE=transaction` is the safe default, not the fast one.** Measure during
  B1/B3 before flipping it.
- **The tenant resolver raises on purpose.** **B1 must insert one enabled `channel_configs` row per
  endpoint** or every inbound message 500s — still the first thing to check when the first deploy
  looks broken.
- **`apps/api/tests/conftest.py` is load-bearing.** `StaticPool` + `check_same_thread=False`.
  Note that it is also why `test_a_second_message_continues_the_same_conversation` delivers each
  turn through its own app: two worker threads on one SQLite connection is a test artefact, and
  delivering separately proves the stronger claim anyway — continuity lives in the rows.
- **Do not re-add `temperature`** to the Anthropic payload; **`_thinking_policy` is vendor
  contract**. Unchanged from session 5.
- **`baseline.json` correctly records `claude-haiku-4-5-20251001`.** That is history. 2.7 rewrites it.
- **`docs/make_roadmap.py` is current (v2.4).** Edit it and re-run it. `docs/ROADMAP.md`,
  `docs/NEXT-STEPS-v2.md` and `docs/HANDOFF.md` are historical.
- **The eval gate does not measure the prompt.** It replays fixtures recorded under prompt v2 and
  reports 88% whatever the prompt says. See §5.

---

## 4. What to do first — B1, then G3

Track A is done. The critical path leaves it for the first time.

**B1 — Supabase project and migrations (0.5d, NOW).** Provision, set `DATABASE_URL`, run the three
existing migrations, **and insert one enabled `channel_configs` row per endpoint**. Half an hour of
provisioning plus the row that stops every message 500ing.

**G3 — the emergency path (1.5d, NOW).** Ahead of 2.4 now, for the reason in §3. The vocabulary
already declares the triggers and the alert; `core/autonomy.py` already takes `emergency` and
short-circuits everything on it. What is missing is the detector and the alert path.

**Then B2–B4** (RLS, Dockerfile + Render, domain/TLS/webhook subscription) — the start command is
`uvicorn apps.api.main:create_application --factory` — **then 2.4** (knowledge base).

**2.7 is still unblocked** and needs only a key and a decision to spend it; see §5.

---

## 5. Two numbers 2.7 should settle

Unchanged from sessions 5 and 6.

1. **The system prompt's real token count.** ~5k is a characters÷4 estimate. Haiku 4.5 will not
   cache a prefix below 4,096 tokens. The estimate clears the floor by ~30%, so caching almost
   certainly activates — but if the vocabulary shrinks it stops silently: no error, a larger bill.
2. **Cost per message.** Measurable now: `provider.last_usage` reports `input_tokens`,
   `output_tokens`, `cached_input_tokens` and `cache_hit_ratio` per call. Measure before quoting.

Also worth doing in 2.7: add Franco-Arabic cases to the golden set.

---

## 6. Where things live

| What | Where |
|---|---|
| Entrypoint / wiring | `apps/api/main.py` — `create_application`, `assemble` |
| The pipeline | `apps/api/orchestration/worker.py` — `Orchestrator.process` (async) |
| Orchestrator seams | `apps/api/orchestration/ports.py` — `ConversationStore`, `ClassificationWriter`, `InboxWriter`, `CrmLookup` |
| DB port implementations | `apps/api/db/orchestration_repo.py`, `db/repository.py`, `db/tenant_resolver.py` |
| Continuity persistence | `apps/api/db/conversation_repo.py` |
| Queue transports | `apps/api/orchestration/queue.py` — inline, BackgroundTasks, thread pool |
| Engine, sessions, pooling policy | `apps/api/db/engine.py` |
| Typed config | `apps/api/core/config.py` + `core/settings_base.py` |
| Channel credentials, sender, adapter choice | `apps/api/channels/config.py`, `channels/whatsapp.py`, `channels/factory.py` |
| LLM providers | `apps/api/classifier/anthropic.py`, `openai.py`, `factory.py` |
| Why A5/A6 are shaped this way | `docs/specs/a5-continuity-and-a6-outbound-sender.md` |
| Why A2/A4 are shaped this way | `docs/specs/a2-database-and-a4-composition-root.md` |
| Why A1/A3 are shaped this way | `docs/specs/a1-configuration-and-a3-llm-providers.md` |
| Current plan | `docs/Watcher_v2_Roadmap.pdf` (v2.4) ← `docs/make_roadmap.py` |
| Locked decisions | `docs/DECISIONS.md` |
| Channel-boundary rules | `apps/api/tests/test_boundary.py` — `KNOWN_LEAKS` (empty), `CHANNEL_REGISTRY` |

---

## 7. Roadmap status against v2.4

| Track | Remaining | Note |
|---|---|---|
| **A — Make it run** | **0d** | A1 ✅ A2 ✅ A3 ✅ A4 ✅ A5 ✅ A6 ✅ — **complete** |
| B — Host it | 4.25d | B1 also needs the `channel_configs` row |
| 2 — Receptionist | 3.5d | 2.4 knowledge, 2.7 eval (unblocked), 2.8 many properties |
| D — Control page | 13.75d | D2's 3 backend days are the hidden half |
| G — Guardrails | 5.5d | **G3 is now ahead of 2.4** — see §3 |
| E — Sellable | 4.5d | Blocked on P1 for sequencing |
| 3 — Integration | 5.5d | 3.1 blocked on P1 |
| **Total** | **~37d** | 12–18 sessions at the observed rate; ~8 weeks to sellable |

**Milestone M1 — answers a real message, safely: ~6.75 days** (B1–B4 + G3 + 2.4).

---

## 8. First five minutes of the next session

1. **Session-7 work is not on `main`.** Either open and merge a PR for
   `claude/a5-a6-implementation-tb5s5e` first, or branch from it:
   `git fetch origin && git checkout -B <new-branch> origin/claude/a5-a6-implementation-tb5s5e`.
   Do **not** branch from `main` — you would be building on a tree that cannot reply.
2. Rebuild the venv from §1 and confirm **406 passed**.
3. Read §3 (traps), particularly the first item about G3.
4. Start B1. It is half an hour of provisioning, plus the one row without which every message 500s.
