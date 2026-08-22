# Session handoff — read this first

**Updated:** end of session 10, 22 August 2026
**Branch:** `claude/roadmap-2-1-2-2-zodk41` — branched from `main` after PR #23 merged
**`main` is at:** `6affc37` — PR #23 (the CD image-name fix) is merged, so sessions 1–9 are all on
`main`. This session's commit (`cdf4b1c`) is on top of that, pushed as **PR #24, not yet merged**.
**Deployed:** https://watcher-api-lup7.onrender.com — live, still serving `6affc37` (PR #24 has not
deployed; see §4).
**A separate branch exists** (`claude/session-handoff-demo-timeline-goqvbe`, also not merged) that
built roadmap item B5 (the durable Redis/arq queue) from the same base. Its work is not reflected
here, and this session did not touch it. Whoever merges both reconciles the two, rather than
either one guessing at the other's content.
**Start at §2 for status, §4 for what to do first.**

Purpose: let a new session pick up without re-deriving anything. Companion documents are
`docs/Watcher_v2_Roadmap.pdf` (**v2.11**, regenerated this session from `docs/make_roadmap.py`)
and the specs in `docs/specs/` — why the code is shaped the way it is.

Overwrite this file at the end of each session.

---

## 1. Verified state right now

Measured by running it or reading the live systems back, not read off a document.

| | |
|---|---|
| Branch | `claude/roadmap-2-1-2-2-zodk41`, based on `main`@`6affc37`, pushed as **PR #24** |
| Tests | **506 passing**, 2 skipped without a Postgres (was 486 at session start) |
| Lint / types | ruff clean; strict mypy clean on 133 source files |
| Recorded baseline | **88%** intent accuracy, gate passing — still v2's number, unchanged (see §5) |
| Database | live — Supabase `watcher-prod`, `qjpjxspycuafqqgudsiv`, eu-central-1, PG 17 |
| **Schema** | **still `alembic_version` = `004_row_level_security`.** PR #24 adds migration `005_facts` — it is written and tested against SQLite, but it has **not** been applied to production, because the PR has not merged/deployed. `alembic upgrade head` needs to run against prod after it does — see §4 |
| **`channel_configs.external_id`** | **fixed, this session.** No longer the placeholder — verified by reading the row back from Supabase directly. This was independent of the PR: a live `UPDATE`, not a migration |
| **Send credentials** | **`WHATSAPP_ACCESS_TOKEN` and `WHATSAPP_PHONE_NUMBER_ID` set on Render this session.** Verified by a clean live redeploy (still on commit `6affc37`, so this was purely an env-var change) — not verified by an actual test send |
| Service | live — Render `watcher-api`, `srv-da0a81jl550s73d0b1i0`, **plan: `free`** (confirmed via the Render MCP this session — still the cold-start blocker) |
| **Webhook subscription** | **not independently verified this session.** Last directly confirmed state (session 9): handshake proven, not subscribed. A separate branch's own handoff, written at the start of this session, reports the operator subscribed it since via a custom domain — that claim is carried forward here, not re-checked against Meta |
| Redis / worker | not provisioned — unrelated to this session; see the other branch if picking up B5 |
| Python | 3.13 |

To get green in a fresh container:

```bash
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python ruff==0.6.9 mypy==1.11.2 pytest==8.3.3 \
  pydantic==2.9.2 pydantic-settings==2.5.2 fastapi==0.115.0 httpx==0.27.2 \
  rapidfuzz==3.10.1 sqlalchemy==2.0.50 alembic==1.13.3 pyyaml==6.0.2 \
  types-PyYAML==6.0.12.20240917
.venv/bin/python -m pytest          # expect 506 passed, 2 skipped
.venv/bin/mypy && .venv/bin/ruff check . && .venv/bin/ruff format --check .
```

`psycopg[binary]` and `uvicorn[standard]` are still deliberately absent from that list and from
CI. `reportlab` and `openpyxl` are needed only to re-run `docs/make_roadmap.py` and
`scripts/import_property_facts.py` respectively — neither is a project dependency.

---

## 2. What session 10 delivered

### Part one — roadmap item 2.4, the knowledge base (PR #24, not yet merged)

Full context: `docs/DECISIONS.md` D35–D37.

**The problem.** Five intents declare `answer_from_knowledge` as their `terminal_tool`
(`property_question`, `check_in_support`, `directions`, `checkout_question`, `general_info`), and
nothing implemented it. `conversations/receptionist.py`'s `handle()` reached `execute` for every
one of them and said the same thing — *"All set! I've noted everything down."* — whether or not
that was true. "Is there parking?" was never looked up anywhere.

**The fix.** A `facts` table (`FactRow`, migration `005`) behind row-level security, matched
against a guest's raw message (slot extraction, item 2.x, still doesn't exist) via
`core/knowledge.py`'s `best_match`. That module is worth reading before touching the matcher: the
first version scored whole sentences with `rapidfuzz.fuzz.WRatio` and, tested against a curated
export of one real property (see below), confused *"is there parking?"* with *"is there a
garden"* — both scored identically because the shared template ("is there a ___") outweighed the
one word that actually distinguishes them. The fix strips a small stopword list from both sides
before scoring (`MATCH_THRESHOLD = 60.0`, tuned against that same real data, not a guess).

**The sensitivity flag, and where it stops.** A `FactRow` can be `sensitive=True`. An unverified
match to one is treated by `AnswerFromKnowledge` exactly like no match at all — the same "I don't
know" `defaults.on_no_knowledge: handoff_to_human` already asks for. This is deliberately **not**
G1 (roadmap track G, not built): G1 is a reply-path-wide gate; this is one tool refusing to guess.
**A door code, a key box code or a unit number does not go in this table at all**, sensitive or
not — `intents.yaml` forbids `check_in_support` from ever disclosing one through
`answer_from_knowledge`, verified or not. That's a hard exclusion in
`scripts/import_property_facts.py`'s column map, not a judgement call left to whoever populates
the table next.

**The dispatch fix, scoped narrowly.** `receptionist.handle()` now actually calls the tool named
by `terminal_tool` for `answer_from_knowledge` specifically. Every *other* unimplemented
`terminal_tool` (`check_availability`, `lookup_reservation`, `quote_price`, `hold_slot`,
`confirm_booking`, `create_ticket` — roadmap 3.1) keeps the old placeholder reply. That was a
deliberate scope decision: fixing those too would be silently widening this item into a promise
about booking/availability it does not keep.

**Tested against real data.** `scripts/import_property_facts.py` turns one row of the operator's
own property-management export into facts; `apps/api/tests/fixtures/demo_property_facts.json` is
a committed, curated output of that script for one real property, and
`test_knowledge_integration.py` runs the whole path against it — JSON → `FactRow` rows behind a
real RLS-scoped session → `SqlAlchemyFactRepository` → `AnswerFromKnowledge` →
`receptionist.handle()`.

**`REGISTRY` is process-global, and that needed guarding.** `configure_knowledge` (D37) swaps the
real, DB-backed tool into `conversations/tools.REGISTRY` — the same seam A5 already used for
`take_message`/`handoff_to_human`. The first time this ran, it broke seven unrelated tests in
`test_orchestration.py` and `test_receptionist.py`: any test that calls `main.assemble` (i.e.
`test_main.py`) left a `SqlAlchemyFactRepository` bound to *that test's* database sitting in the
global registry for the next test to trip over. Fixed with an autouse fixture in
`apps/api/tests/conftest.py` that snapshots and restores `REGISTRY` around every test — read that
fixture's docstring before adding a fourth registry-backed tool.

**Verification of 2.1/2.2, requested before starting 2.4.** Checked, not assumed: both are
genuinely wired into the live path (`main.py`'s `assemble()`, unconditional), with real DB
persistence and a real WhatsApp client behind them, matching what the roadmap already claimed.
Nothing needed fixing there.

Also verified: 486 → 506 tests, mypy/ruff clean throughout.

### Part two — the placeholder row and send credentials, fixed live, same session

Independent of PR #24 merging — these were direct actions against the live systems, not code:

1. **`channel_configs.external_id`.** Was `PLACEHOLDER_WHATSAPP_PHONE_NUMBER_ID` since B1
   (15 August). This was the single highest-priority item on the whole board: with the webhook
   reachable, every real inbound message was resolving against no enabled `channel_configs` row,
   `ChannelConfigTenantResolver` was raising `UnknownEndpoint`, and `ingestion/router.py` doesn't
   catch it — a 500 on every real message, before persistence, before anything else ran. Fixed by
   a direct `UPDATE` in the Supabase SQL editor (the user ran it, given the value themselves —
   this session gave navigation instructions and verified the result by reading the row back, not
   by asking for the value in chat). Confirmed: `external_id` is now a real 16-digit numeric id,
   `still_placeholder = false`.
2. **`WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`.** Set on Render. Confirmed via
   `list_deploys`: the redeploy the env-var change triggered is `status: live`, still on commit
   `6affc37` (no code changed, just the environment). Not confirmed by an actual outbound send —
   that's still untested end to end.

### Decisions made this session

| Decision | Choice | Where it lives |
|---|---|---|
| How a guest's question is matched to a fact | **`rapidfuzz.fuzz.WRatio`, stopwords stripped first, threshold 60** (D35) | `core/knowledge.py` |
| Scope of the `sensitive` flag | **`answer_from_knowledge` only, narrower than G1; door/key/unit codes excluded from the table entirely** (D36) | `core/knowledge.py`, `conversations/tools.py`, `scripts/import_property_facts.py` |
| Where the knowledge lookup is wired in | **`conversations/tools.REGISTRY` via `configure_knowledge`, not threaded through `Orchestrator`** (D37) | `main.py`, `conversations/tools.py` |

---

## 3. Traps and things not to re-litigate

Everything in session 9's list still holds except the one item below it resolves. This session
adds:

- **Migration `005_facts` is not applied to production yet.** The code is merged nowhere and
  deployed nowhere. Once PR #24 merges, run `alembic upgrade head` against `DATABASE_URL` (see
  `docs/specs/b1-b3-hosting-and-isolation.md` §4) — it is not wired into Render's build/start
  command, so it will not happen automatically. Deploying the code without this leaves
  `SqlAlchemyFactRepository.search()` hitting "relation facts does not exist" on the first real
  `property_question`.
- **A door code, a key box code or a unit number must never become a `FactRow`.** Not a style
  preference — `intents.yaml` forbids `check_in_support` from disclosing one through
  `answer_from_knowledge` regardless of verification. If 2.8 or D5 (the knowledge view) ever add a
  bulk-import or an editing UI, this exclusion has to travel with them, not just live in one
  script's column map.
- **The `sensitive` flag is not G1.** Don't read `AnswerFromKnowledge`'s narrow gate as the
  reply-path-wide disclosure enforcement G1 (track G) still has to build — money/owner ceilings
  and the `identity_verified` flag being read *everywhere* the vocabulary implies are both still
  open.
- **`REGISTRY` (`conversations/tools.py`) is process-global mutable state.** A fourth
  registry-backed tool needs the same test isolation the `conftest.py` autouse fixture already
  gives the third. Forgetting this reads as an unrelated test failing somewhere else in the suite,
  not as an error in the file you're touching.
- **`MATCH_THRESHOLD = 60.0` was tuned against one real property's facts, not derived.** If 2.7's
  eventual golden set surfaces false matches or false misses, that number — and whether WRatio is
  even the right scorer — is the first thing to revisit, not the dispatch logic around it.
- **The channel_configs placeholder fix and the send credentials are already live** and did not
  go through this branch's code or PR #24 — don't re-do them, and don't expect merging PR #24 to
  have caused them.
- **Webhook subscription status is inherited, not verified.** See §1. If a message still doesn't
  reach the service, check this before assuming the placeholder fix didn't work.
- Everything from session 9 not superseded above: narrow `intents.yaml` triggers still not
  widened (confirm with the operator), `CONTROL_CHAT_PHONE_E164` still unset, `DATABASE_URL` must
  name `watcher_app` not `postgres`, the transaction-pooler port 6543, the webhook path is
  `/webhook`, RLS/adapter rules (a new adapter takes a `TenantScope`), D24 (no rules/destinations
  in the orchestrator), `KNOWN_LEAKS` empty, no module-level `app`, no `temperature`, the eval gate
  not measuring the prompt (§5), B5 (durable queue) not on this branch at all.

---

## 4. What to do first

**0. If merging PR #24: run the migration.** `alembic upgrade head` against production
`DATABASE_URL` immediately after that deploy goes live — see the trap above. Skipping this is a
silent outage for every `answer_from_knowledge` intent, not a loud one: the receptionist will hand
off instead of erroring, so nothing in the logs screams about it.

**Re-verify the webhook subscription.** This session inherited but did not check Meta's
subscription status directly. Confirm it's actually live before assuming B4's first checklist item
is closed.

**`CONTROL_CHAT_PHONE_E164`** — still unset. Without it, an emergency is detected, answered and
filed, and the only alert is a log line. This is now the only unclosed item on B4 that isn't
"upgrade the Render plan."

**Upgrade `watcher-api` off the free Render plan** — confirmed still `free` this session. The
cold-start-as-timeout risk is live-traffic risk now that the placeholder is fixed and (per the
inherited report) the webhook is reachable.

**Then 2.7** (re-record the eval under prompt v3 — unblocked, needs only a key) **or 2.8** (many
properties per client). Both are the only work left on Track 2.

Reconcile with the B5 branch (`claude/session-handoff-demo-timeline-goqvbe`) at some point — it
has real, tested work (the durable queue) sitting unmerged alongside this session's PR #24.
Neither branch depends on the other, so either order of merging is fine, but somebody has to
actually do it.

---

## 5. Two numbers 2.7 should settle

Unchanged from sessions 5–9. Not touched this session.

1. **The system prompt's real token count.**
2. **Cost per message.**

---

## 6. Where things live

| What | Where |
|---|---|
| Entrypoint / wiring | `apps/api/main.py` — `create_application`, `assemble` |
| The pipeline | `apps/api/orchestration/worker.py` — `Orchestrator.process` |
| The receptionist / task dispatch | `apps/api/conversations/receptionist.py` — `handle`, `_answer_from_knowledge` |
| **The knowledge base (new)** | `apps/api/core/knowledge.py` (`Fact`, `best_match`), `apps/api/db/knowledge_repo.py` (`SqlAlchemyFactRepository`) |
| **The tool registry (new entry)** | `apps/api/conversations/tools.py` — `AnswerFromKnowledge`, `configure_knowledge`, `REGISTRY` |
| **Facts migration (new)** | `alembic/versions/005_facts.py` — not yet applied to production, see §3/§4 |
| **Import tooling + fixture (new)** | `scripts/import_property_facts.py`, `apps/api/tests/fixtures/demo_property_facts.json` |
| **Knowledge tests (new)** | `apps/api/tests/test_knowledge.py`, `test_knowledge_integration.py`; updated: `test_receptionist.py`, `test_orchestration.py` |
| **Global-registry test isolation (new)** | `apps/api/tests/conftest.py` — `_restore_tool_registry` |
| The emergency detector | `apps/api/core/emergency.py` |
| The alert seam / its implementation | `apps/api/core/alerts.py`, `apps/api/channels/alerting.py` |
| Typed config | `apps/api/core/config.py` + `core/settings_base.py` |
| The image | `apps/api/Dockerfile` |
| CI — dependency install list | `.github/workflows/ci.yml` |
| Locked decisions | `docs/DECISIONS.md` (D35–D37 are this session's) |
| The roadmap | `docs/make_roadmap.py` → `docs/Watcher_v2_Roadmap.pdf` (**v2.11**) |
| Tenant resolution (relevant to the placeholder fix) | `apps/api/db/tenant_resolver.py` — `ChannelConfigTenantResolver` |

---

## 7. Roadmap status against v2.11

| Track | Remaining | Note |
|---|---|---|
| A — Make it run | 0d | complete since session 7 |
| B — Host it | 1.5d | B1–B3 ✅ — B4 two of four items closed live this session; B5 not on this branch |
| **2 — Receptionist** | **1.5d** | **2.4 ✅ (this session)** — left: 2.7 (unblocked), 2.8 |
| D — Control page | 13.75d | D2's 3 backend days are the hidden half |
| G — Guardrails | 4.0d | G3 ✅ — G1, G2, G4 remain; none on the critical path |
| E — Sellable | 4.5d | Blocked on P1 for sequencing |
| 3 — Integration | 5.5d | 3.1 blocked on P1 |
| **Total** | **~30.75d** | at the observed rate, ~7 weeks to sellable |

**Milestone M1 — answers a real message, safely: ~0.5 days.** B4 alone, and two of its four items
are already closed. What's left: `CONTROL_CHAT_PHONE_E164` and the Render plan upgrade — both
operator/billing decisions, not engineering.

---

## 8. First five minutes of the next session

1. Check whether PR #24 has been merged. If yes and the migration hasn't run yet, **run
   `alembic upgrade head` against production before anything else** — see §4.
2. Check whether `claude/session-handoff-demo-timeline-goqvbe` (the B5 branch) has also merged, or
   still needs reconciling with this one.
3. Re-verify the webhook subscription against Meta directly — this session only inherited that
   claim.
4. Rebuild the venv from §1 and confirm **506 passed, 2 skipped** (or the post-merge count if B5
   has landed too).
5. Read §3, particularly: migration 005 isn't live yet, the door/key/unit-code exclusion in the
   facts table, and `REGISTRY`'s process-global state.
6. Check whether the operator has set `CONTROL_CHAT_PHONE_E164` or moved off the free Render plan
   — neither was done as of this session's end.
