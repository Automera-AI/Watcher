# Spec — A5 Wire continuity + excise the v1 filer · A6 Outbound sender

**Status:** Implemented. Roadmap v2.3 Track A, items A5 (2.25d) and A6 (1.0d) — the last two items
in Track A.
**Why these two, in this order:** the roadmap's own instruction — "if you do only one thing next
session: A5, then A6". After A4 the application started, connected and filed: a signed webhook came
in, the message was persisted, attributed to a tenant, classified, and the decision written to
`audit_log` and `inbox_items`. What it could not do was **reply**. A5 is what joins the conversation
to the task; A6 is what puts a message on the wire. A6 without A5 is a receptionist that forgets the
previous turn — it looks like it works, which is worse than not shipping it.

After this, the loop is closed. A guest who messages the number gets an answer.

---

## A5 — Continuity, and the end of the v1 filer

### The problem, stated exactly

Items 2.1 and 2.2 were genuinely written. `conversations`, `turns` and `task_rows` existed;
`ConversationRepository` existed; the `Task` state machine and the receptionist existed. What was
never written was the line joining them:

* `ConversationRepository` was called by nothing outside its own tests, for four sessions.
* The orchestrator passed `task=None` and `extracted_slots={}`, so slot filling and task continuity
  were inert. Every message opened a brand-new job.
* `classifications` — a table with eleven columns — had never had a row written to it.

### What changed

| Seam | Before | After |
|---|---|---|
| `Orchestrator.process` | sync; `asyncio.run` around the receptionist | `async`; the caller owns the loop |
| Conversation | none | `ConversationStore.begin` / `record_reply` (`db/orchestration_repo.py`) |
| Task | `None`, every message | loaded from `task_rows`, saved back after the reply |
| `classifications` | never written | one row per classified message, with its telemetry |
| `inbox_items.classification_id` | always null | points at that row |
| Rules / destinations | evaluated per message | **removed from this path** (D24) |
| Reply | composed, discarded | recorded, then sent (A6) |

### The v1 filer, and why removing it is the right call

The orchestrator used to answer an inbound message twice. It would evaluate the tenant's rules and,
on a match, auto-route the message to a destination; failing that it filed by confidence band, where
HIGH meant "auto-route". Both of those are v1's answer — *file this somewhere* — and the receptionist
is v2's — *answer it*. Keeping both meant one message could be auto-routed to a Sheet and replied to
in the same pass, and the two answers had nothing to say to each other.

So the orchestrator no longer takes a `RulesProvider` and `ProcessOutcome` no longer carries
`matched_rule_id` or `destination_id`. **The tables are retained, not dropped**, and so are
`rules/engine.py`, `rules/models.py` and `SqlAlchemyRulesProvider`: deliberate routing by a human is
a control-page feature and this is the evaluator it will use. `RulesProvider` moved from the
orchestrator's ports to `rules/engine.py`, beside the engine it feeds, which is the honest statement
of who depends on it now — nobody in the message path.

With destinations gone, `AUTO_ROUTE` went with them; there is nothing to route *to*. Without a
receptionist the pipeline degrades to filing by band — MEDIUM or better pings the control chat, LOW
and unreadable messages wait in the inbox — and with one, every classified message reaches it.

### Where a hand-off goes now

Previously a message the autonomy gate reserved for a person fell through to rules-and-bands, and
the guest heard nothing. Now the receptionist is called for *every* classified message and its own
autonomy check decides the shape of the reply. An intent the vocabulary reserves for a human returns
a handoff action — the guest is told a person is coming — and the item is filed `needs_review` for
that person. Both halves are true at once, which they never were before.

### The loop that continuity introduces, and the guard for it

Continuity has a failure mode that no-continuity does not: a task that cannot make progress no
longer fails, it *loops*. A guest who never supplies the missing detail gets the same question on
every message, forever.

The vocabulary has declared `defaults.max_clarifying_turns: 3` and `defaults.on_max_turns:
handoff_to_human` since item 0.3, and nothing had ever read them — harmless while every message
opened a fresh task. They are read now, in `conversations/receptionist.py`, which is the file that
decides what to say next. `ConversationState.replies_sent` counts the outbound turns recorded
*since the active task was created*, so a guest who books a stay and then asks about parking starts
the budget again: they are asking about something else.

The guard applies to `ask` and `confirm` and not to `execute`. A task with everything it needs
completes even on the last turn of the budget; handing off a request we could simply have finished
would be worse than the loop it prevents.

### `classifications`, and the two columns that kept it empty

The table wants `latency_ms` and `prompt_version`, and the orchestrator surfaced neither. The
previous handoff was explicit: *do not invent the missing telemetry to fill the columns*. So the
classifier reports it instead — `ClassificationOutcome` carries `latency_ms` and `prompt_version`,
measured and injected respectively:

* **Latency spans the whole tiered policy**, not the last call. A message retried once and then
  escalated cost three model calls and the guest waited for all of them; a per-call number would
  report the fastest thing that happened and hide the slow path entirely. The clock is injected, so
  the measurement is asserted by a test that does not sleep.
* **`prompt_version` is the version the service was built with**, passed in at construction rather
  than imported by the writer. A row keyed to a prompt version the writer guessed at proves nothing
  to an eval.

`model_used` had been passed to the inbox writer and dropped — `inbox_items` has no such column. It
now lives on the classification row, and the inbox item points at it via `classification_id`.

### Async, and where the event loop comes from

`process()` is `async` because the receptionist and the sender are. The previous arrangement — a
sync `process` calling `asyncio.run` around the receptionist — opened and tore down a loop per
message and could not be called from anywhere that already had one.

The loop now comes from the transport, which is the only layer that knows what it is running inside:

| Transport | Loop |
|---|---|
| `ThreadPoolClassificationQueue` | `asyncio.run` per message, on the worker thread doing the work |
| `BackgroundTasksQueue` | the server's own loop; `add_task` awaits a coroutine function |
| `InlineClassificationQueue` | `asyncio.run`, which correctly refuses to run inside a live loop |

The database work inside `process` is still synchronous. Each message has its own thread and its own
loop, so a blocking query blocks nothing but the message it belongs to.

### Idempotency in the transcript

`turns.idempotency_key` is unique and the key is the channel's own id for the message. A redelivery
that reaches the store — a queue retry after a partial failure — finds the existing row rather than
raising, because the alternative is a message that can never be processed at all. The reply's key is
derived from the inbound one (`<key>:reply`) rather than generated, so re-processing cannot leave
two replies in the transcript or double-count the turn budget.

**A new task supersedes rather than overwrites.** When the guest changes the subject, the old task
row is marked `abandoned` — a new `TaskStatus` member, not a schema change, since the column is a
varchar — so it leaves the active set. Marking it `failed` would be a lie: nothing went wrong, the
guest simply moved on.

### Known limitation: concurrent arrival

Two messages in the same thread arriving close enough together to be classified concurrently can
race in `find_or_create_conversation`, producing two open conversations for one thread. Per-message
ordering is a property of the queue, and the queue is an in-process thread pool until **B5**, where
ordered per-conversation delivery belongs. It is recorded here rather than papered over with a
lock that would not survive a second process.

---

## A6 — The outbound sender

### The problem

`ChannelSender` was a protocol with no implementation. `Settings.whatsapp_send_credentials()` had
been written for it in A1 and never called.

### Module boundary

`apps/api/channels/whatsapp.py` — `SendCredentials`, `to_payload`, `WhatsAppSender`,
`ChannelSendError`. `apps/api/channels/factory.py` — `build_sender`.

| Name | Contract |
|---|---|
| `to_payload(action, recipient)` | The Cloud API body. Quick replies → an interactive button message; everything else → text. |
| `WhatsAppSender.send(action, turn)` | Post it, or raise `ChannelSendError`. |
| `build_sender(credentials)` | The sender, or `None` when this process has nothing to send with. |

The channel's limits are applied here and nowhere else: `QUICK_REPLY_LIMIT` (three buttons) and
`BUTTON_TITLE_LIMIT` (twenty characters). Neither raises — the core is free to have composed six
long options, and a receptionist that composed one too many must not become a crashed reply.

### The client is synchronous and the send is not

`httpx.Client` is thread-safe, lives for the life of the process and reuses connections; the
blocking call is handed to a worker thread with `asyncio.to_thread`. That matters because the loop
this runs on is not always ours — under `BackgroundTasksQueue` it is the server's, and a blocking
POST there would stall every request in flight rather than just this reply. An `AsyncClient` would
have to be rebuilt per message, since each thread-pool message gets a fresh loop.

Retries mirror the LLM transport's policy without sharing its code: 408/429/5xx are retried with
bounded exponential backoff, and a 400 or 401 raises immediately. A revoked token fails the same way
three times, so retrying it buys a slower failure and three times the log noise. The two policies
are deliberately separate because Meta's error semantics are not a model provider's, and
`post_json` raises `ProviderError`, which a channel has no business raising.

### A failed send does not lose the message

By the time the send runs, the message has been classified, the classification recorded, the reply
composed and written to the transcript. A raise here would lose all of that to a transient 502. So
the failure is logged and reported as `ProcessOutcome.delivered = False`, and the decision is still
filed. `delivered` is `None` when there was no sender at all.

**The reply is recorded before it is sent**, deliberately. A reply we sent but did not record makes
us ask the same question on the next turn; a reply we recorded but failed to send leaves a visible,
correctable gap in one conversation.

### Moving the credentials behind `channels/` — the last `KNOWN_LEAKS` entry

`core/config.py` held `WHATSAPP_ACCESS_TOKEN` and friends. Not per-channel *behaviour*, but fields
that belong to a channel, and the last entry in the boundary test's allowlist.

`channels/config.py` now declares them as `ChannelCredentials`, and `Settings` **extends** it. One
object still reads one `.env` and one environment; what changed is which module knows that a send
needs a token and a number. The accessors moved with the fields: `meta()` answers "can this process
verify an inbound webhook", `send_credentials()` answers "can it reply", `can_send()` is what the
composition root asks before deciding whether this process can answer at all.

Two supporting moves this required:

* `core/settings_base.py` — the placeholder handling and the "name every missing variable at once"
  error, shared by both halves, in a module that names no channel. Neither half can import the
  other without a cycle.
* `ConfigError` moved there too. It had been living inside a channel adapter since before there was
  a configuration layer, which meant the core imported its own missing-configuration error from a
  channel. `apps.api.channels` re-exports it, so no import site changed.

`KNOWN_LEAKS` is now empty, which is roadmap item **1.1 finished**. The machinery stays: an empty
allowlist is the strongest form of that test, and the next channel is a phone line — exactly when
someone will want to add "just one" exception back.

### `main.py` may not name a channel

The composition root decides what the process is made of, but the boundary test scans `main.py`, so
the concrete adapter is chosen in `channels/factory.py`. `main.py` calls `build_sender(settings)`
and holds the result as a `ChannelSender`. Connecting a phone line is an edit to that factory and to
nothing above it.

### The degraded state that is still allowed

A process with no send credentials starts, ingests, classifies, continues conversations and records
the replies it composed — it simply cannot deliver them. That is every deploy between B1 and B4, and
it is one loud warning at startup rather than a refusal to boot, because everything except the last
step still works.

---

## What this does not do

* **Slot extraction.** The receptionist still receives `extracted_slots={}` because the classifier
  does not emit slots — `ClassificationResult` has no such field, and adding one is a prompt change
  and a golden-set change, which is item 2.x and would invalidate the recorded baseline. The
  consequence is real and bounded: a task fills only by the clarifying-turn budget expiring, and
  then hands off to a person. That is a receptionist that acknowledges, holds context across turns
  and escalates cleanly — not one that completes a booking unaided.
* **Emergency detection.** `emergency=False` is still hardcoded, now at one named line with a
  comment rather than buried in a call. Detection is **G3**, which owns the vocabulary's triggers
  and the alert path. A gas leak still files as an ordinary maintenance request, and that is why G3
  is not optional before a real guest reaches this number.
* **Ordered delivery.** See "concurrent arrival" above — B5.
* **`understandings` / `corrections`.** Still unwritten. They belong to the correction loop on the
  control page (track D), not here.
