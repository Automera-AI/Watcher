# Watcher v2 — where we stand, what we need, and how long

**Date:** 3 August 2026
**Continues:** `Automera_Watcher_Repo_Reconciliation_20260803.md`, which ends at §10.
**Status:** Planning. No code written for this document.

Everything below was checked against the repo by running it, not read off a design document.
Where the older documents are wrong, this one says so.

---

## 11. Where we stand, in plain words

### 11.1 What works today

We have a machine that **listens and files**. A message arrives from WhatsApp, we check it is
really from Meta, we read it, we work out who sent it, we decide how confident we are, and we put
it somewhere — a CRM row, a list for a human, or a ping to the operator's phone.

That part is genuinely built and genuinely tested. Concretely, verified today:

| Thing | State |
|---|---|
| Python files | 66 |
| Tests | **86, all passing** — no database, no network, no API key needed |
| Backend modules | 10, each with the outside world behind a swappable plug |
| Database tables | 11, migration written |
| Eval runner + job queue | **Written, tested, and sitting unmerged on a branch** |

### 11.2 What does not work today

**It cannot talk back.** This is the whole gap, and it is worth being blunt about it.

The pipeline ends in one of three places, all of which mean "put this somewhere":
`AUTO_ROUTE`, `CONTROL_PING`, `INBOX_REVIEW`. There is no fourth option that means
**"answer the customer."** I checked — `orchestration/worker.py` lines 30-32, that is the
complete list.

Four things are missing before it can:

1. **A reply path.** A fourth outcome, a thing that writes the reply, and a way to send it back
   out. The sending machinery (retries, dead letters) already exists for CRM writes; it needs a
   read/reply direction added.
2. **Memory across turns.** Right now each message is judged alone. A receptionist needs to hold
   "this person is booking, they've given me a check-in date, I still need a check-out date"
   across five messages. That needs new tables (conversations, tasks) and the slot-filling logic.
3. **Knowledge.** It has nowhere to look up "what time is check-in" or "what's the WiFi
   password." There is **no knowledge base of any kind in the repo** — I searched; zero rows,
   zero tables, zero embeddings. This is entirely new work and §8 of the previous document
   forgot to budget for it.
4. **The core still speaks WhatsApp.** Field names like `wa_message_id` and `sender_wa_name` run
   through the whole system. A phone call has neither. This is a rename, not a rewrite, but it
   has to happen before more code is built on the current shape.

### 11.3 One correction to the previous document

§8 concluded that Meta's business verification "is now comfortably the thing setting your dates…
the only one." **That is no longer true**, because Meta now lets you start without verification.

The consequence is not small. It means **engineering is the critical path again.** The ~8-9 day
estimate stops being a number hiding behind a 1-4 week wait and becomes the actual date. It also
means there is no longer an excuse for the calendar slipping while we wait on someone else.

---

## 12. The knowledge base — what it is, and what to build it in

This is the biggest genuinely-new decision, so it gets its own section.

### 12.1 First, a correction: Graphify is not what I evaluated

An earlier draft of this section assessed **Graphiti** (Zep's agent-memory graph). The tool
actually under discussion is **Graphify**, which is a different product entirely. The
substitution was mine and the conclusion it produced was answering the wrong question.

**Graphify** (`github.com/Graphify-Labs/graphify`, ~102k stars, Apache-2.0/MIT, YC S26) turns a
**codebase** — plus its docs, SQL schemas, configs and PDFs — into a queryable knowledge graph.
It parses locally with tree-sitter, uses **no embeddings and no vector store**, tags every edge
as either extracted or inferred, and outputs three files: an HTML visualisation, a markdown
report, and a `graph.json`. You query it from the CLI or over MCP.

The decisive fact: it is a **developer-time tool**, for making AI coding assistants understand a
repository. Its own documentation states it is not designed for runtime application querying.

So it is not a candidate for the receptionist's knowledge base — not because it is bad, but
because it does a different job. A guest asking "what time is check-in?" is not a question about
our source code.

### 12.2 But should we use Graphify anyway? Probably yes — for the other job

Dismissing it as the product's knowledge base is not the same as dismissing it. There is a real
fit here, just not the one the question assumed.

This repo is 66 Python files, 10 modules, and about to undergo a rename that touches nearly every
file. Most of that work is agent-assisted. The recurring failure in that setup is an agent that
greps, half-understands the module boundaries, and puts new code in the wrong place. That is
precisely what Graphify is built to prevent, and two properties make it a low-risk thing to try:

- **Nothing leaves the machine** for code parsing — local AST, no API calls. Given a public repo
  that has already leaked a client name once, that matters.
- **No vector store and no embeddings**, so there is no index to maintain, and every edge can be
  traced to its source rather than being a similarity score.

The honest caveats: the graph is a snapshot that goes stale as the code changes, so it wants
regenerating rather than trusting; and it is worth confirming what the non-code path (docs, PDFs,
media — which uses LLM extraction, not local parsing) sends anywhere before pointing it at
anything tenant-related.

**Verdict: worth an afternoon during the §13 rename, as a development aid. It has no bearing on
the product's knowledge base, and the two decisions should not be bundled.**

One caution: at least four different domains present themselves as Graphify
(`graphify.com`, `graphify.net`, `getgraphify.com`, plus the GitHub org). All three sites refused
inspection, so I could only verify the GitHub repository. There is also a large volume of
near-identical SEO content about the project quoting wildly different star counts
(58k / 63k / 85k / 101k). **Install from the GitHub repository, not from a search result.**

### 12.3 The mistake to avoid first

The instinct is "upload the documents and let the AI read them." **Do not start there.** A
receptionist's job is mostly *exact facts*, and semantic search is *approximate by design*. A
door code that is approximately right is worse than no answer at all.

So split the knowledge into three kinds, because they want three different homes:

| Kind | Example | Where it belongs |
|---|---|---|
| **1. Exact facts** | WiFi password, check-in time, door code, address, parking bay | A plain database table. Exact lookup. No AI involved in *finding* it |
| **2. Prose and policy** | Cancellation policy, house rules, "can I bring a dog" | Semantic search over short chunks |
| **3. Live answers** | "Is 4-9 September free?", "how much for 3 nights?" | **Not a knowledge base at all.** A live API call to the property system |

Kind 1 is the bulk of the work and the bulk of the questions. Kind 3 is the one people wrongly
try to solve with a knowledge base — availability changes hourly, so it must be a live lookup.
Only kind 2 actually needs the clever retrieval everyone talks about.

### 12.4 How big is this actually?

Worth doing the arithmetic before choosing a tool, because it changes the answer completely.

A client with **50 units × ~30 facts each = ~1,500 rows.** Prose: maybe **200 short chunks.**

That is *nothing*. It fits in a spreadsheet. Postgres handles it without noticing. Any argument
that starts "but at scale…" needs to explain how we get from 1,500 rows to the millions where
specialised tools start to matter.

### 12.5 Every option, and why each wins or loses

**Option A — Put everything in the prompt. No retrieval at all.**
For a single property, the entire fact set is a few thousand tokens. Modern context windows
swallow that whole. Zero infrastructure, zero retrieval bugs, nothing to get wrong.
*Wins for: the demo.* Breaks at roughly 10+ properties, when the prompt gets long, slow, and
expensive. **This is the right answer for the first demo and should not be skipped out of pride.**

**Option B — Postgres table + pgvector. (Recommended)**
Exact facts in an ordinary table; prose chunks in the same database with `pgvector` for semantic
search. We already run Postgres on Render, so this adds **no new service, no new bill, no new
thing to operate**. Independent 2026 guidance lands in the same place: Postgres with pgvector is
the right call if you already run Postgres and stay under ~10M vectors. We are four orders of
magnitude under that.
*Wins for: everything up to a few hundred properties.*

**Option C — A dedicated vector database (Pinecone, Weaviate, Qdrant).**
These are good products solving a real problem: retrieval over millions of vectors, where
Postgres genuinely starts to strain. We have roughly 200 chunks per client. The scale argument
does not reach us and will not for years.

The costs are not theoretical. It is a second database, a second bill, and a second thing that
can be down at 2am. It is a second place tenant data lives, which matters concretely here: the
repo scopes every table per tenant, and a self-hosted deployment for regulated clients is on the
roadmap — a hosted vector service makes that promise harder to keep. It also splits the data,
so "give me this property's facts and its policy text" becomes two round trips and a join done
in application code instead of one query.

*Verdict: no. Revisit past a few million chunks, which is a problem we would be lucky to have.*

**Option D — A knowledge graph for the product's knowledge (Neo4j, or Graphiti-style memory).**
Distinct from Graphify in §12.1 — this is about storing *guest and property knowledge* as a
graph, not our source code.

The genuine appeal is temporal memory: storing facts with validity windows so the system knows
what was true in January versus now, and invalidates old facts rather than holding both. For a
returning-guest story — *"they complained about the AC last time"* — that is real value.

But it answers *"what did this guest tell us months ago?"*, not *"what is the WiFi password?"*,
and the latter is the overwhelming majority of a receptionist's traffic. Costs: another database
to run; ingestion needs LLM calls to extract entities and relationships, which is money, latency
and a new way to be silently wrong; and retrieval latency lands around 300ms — fine on WhatsApp,
meaningful against a sub-800ms phone budget.

Meanwhile the repo already has identity resolution and a messages table, which covers the
returning-guest case well enough to test whether anyone actually wants it.

*Verdict: not now.* Record it in `DECISIONS.md` as considered-and-deferred **with this reasoning**,
so it is not re-argued from scratch in three months.

**Option E — A managed knowledge-base service.**
Genuinely the fastest route to a working demo. But it puts client data in a third party, makes
the self-hosted path harder, and hands a core part of the product to someone else's roadmap and
pricing. The knowledge base *is* much of what clients pay for; outsourcing it is a strategic
choice, not a technical shortcut.
*Loses on: control of the thing being sold.*

**Recommendation: A for the demo, B as the real thing.** They are not in conflict — start with
facts in the prompt, and the move to pgvector later is additive, not a rewrite. The single most
important point in this section is §12.3: **most of a receptionist's knowledge is exact facts and
belongs in an ordinary table.** Every option above is only arguing about where the small prose
remainder lives, which is why the answer should be "wherever is cheapest to operate."

### 12.6 What it should look like to the client

This is a product decision as much as a technical one.

**Do not ask clients to "upload documents."** Ask them to **fill in a form**, one per property.
Structured questions with structured answers: check-in time, WiFi name, WiFi password, parking,
bins, heating. Plus one free-text box at the end for anything that did not fit.

Three reasons: the answers land already-structured so lookups are exact; a half-filled form
visibly shows what is missing; and it takes a client twenty minutes instead of "send us your
handbook" and a week of chasing.

Three things the design must include from day one:

1. **A sensitivity flag on every fact.** Door codes, lockbox codes and owner contacts are not
   ordinary facts. This connects directly to `test_autonomy.py`, which already says an access
   code requires proof of identity first. The knowledge table needs that column, and the reply
   path must respect it.
2. **A real "I don't know."** The system must be able to say it does not know and fetch a human.
   An invented check-in time is far worse than a two-minute wait. This is the autonomy gate doing
   its job.
3. **Every answer traceable to a fact.** When it says check-in is 3pm, we should be able to point
   at the row. That is what makes it debuggable, and it is what a client will ask about the first
   time it gets something wrong.

---

## 13. What we need, and how long it takes

### 13.1 The two finish lines, which are different

Be clear about which one is being promised, because they are three weeks apart.

**Finish line 1 — "a demo that convinces someone."** One property, WhatsApp, a real phone number.
It answers questions, holds a booking conversation across several messages, and hands off cleanly
when unsure. Facts can live in the prompt (Option A).

**Finish line 2 — "a real client's guests are using it."** Many properties, real knowledge base,
live availability, safety rules that hold under pressure, and someone watching it.

### 13.2 The work, in order

Sequencing note: **the intent vocabulary decision must move earlier than §10 put it.** It is a
one-hour founder call, and it blocks two of the four files we agreed to keep, plus the prompt and
the golden set. It is the cheapest thing on this list and the most-blocking.

| # | Work | Days | Notes |
|---|---|---|---|
| 0 | Decide the receptionist intent vocabulary. Write it into `DECISIONS.md` | — | Founder, one hour. **Blocks 1, 5, 6, 7** |
| 1 | Merge the eval branch | 0.25 | Verified: it is a clean fast-forward, two commits, no conflicts |
| 2 | Fix the client name in the golden set; decide on history rewrite | 0.25 | It is in a public repo today |
| 3 | Rename the message shape so the core stops speaking WhatsApp | 1.5 | 86 tests point at every site |
| 4 | Port the four kept files properly | 1 | **Not free** — see 13.3 |
| 5 | Conversations, tasks, slot filling | 2 | The genuinely new domain work |
| 6 | The reply path — fourth outcome, composer, send back out | 1.5 | Sending machinery already exists |
| 7 | Knowledge base: facts table, sensitivity flags, intake form, pgvector | 2.5 | **New. Was missing from the old estimate** |
| 8 | Autonomy gate into `core/policy.py`; raise the acting floor | 1 | Small code, important thinking |
| 9 | Prompt v2 + rewrite the golden set | 1 | The runner exists to score it |
| 10 | Python 3.12 → 3.13 | 0.5 | See 13.4 |
| 11 | End to end on a real number, then measure | 1 | |

**Total: ~12.5 days.**

### 13.3 Why the four kept files are not free

The previous document said keep these four, which is right. But none of them run today — they all
import `app.core.*` and the repo is `apps.api.*`. More importantly:

- **`test_boundary.py` does not catch our actual bug.** It bans the words `whatsapp`, `twilio`,
  and so on. Our leak is `wa_message_id` and `wa_chat_id`. `whatsapp` never matches `wa_`. The
  test that is supposed to stop the problem returning currently would not notice it. Add the
  `wa_` prefix.
- **`test_envelope.py` contradicts `test_boundary.py`.** It caps replies at three quick-reply
  buttons — which is a *WhatsApp* limit, living in the channel-agnostic core. That is the exact
  mistake the boundary test exists to prevent. Decide: cap in the WhatsApp adapter, or accept the
  core is permanently limited to the most restrictive channel.
- **`test_autonomy.py` needs something we do not have.** It relies on `identity_verified`. The
  repo does identity *matching* ("is this the same person as this record"), not *verification*
  ("has this person proved who they are"). Different thing, needs the verification-codes table.
- **`test_task.py` presumes the taxonomy decision.** It uses `booking_enquiry` and
  `availability_check`, which are not among the six locked intents. Hence item 0 above.

The genuinely valuable parts, which have no equivalent in the repo and should survive porting:
the rule that **changing a date cancels its confirmation**, and the rule that
**money and owner matters always reach a person regardless of confidence** — checked *before*
the confidence band, because confidence is not the same thing as authority.

### 13.4 Python 3.12 → 3.13

Correcting the baseline: the repo is **on 3.12 everywhere**, not 3.10 — `requires-python
>=3.12`, ruff `py312`, mypy `3.12`, CI `3.12`. So the move is 3.12 → 3.13.

I checked the upgrade surface and it is clean: **no removed stdlib modules** (nothing from PEP
594), no `datetime.utcnow`, no `locale.getdefaultlocale`. The suite also currently passes on
3.11, so the `>=3.12` pin is not yet load-bearing.

Steps: bump the four pins → run the suite under 3.13 → confirm pydantic, fastapi, sqlalchemy and
alembic all publish 3.13 wheels → add 3.13 to CI alongside 3.12 for one cycle → drop 3.12.
**Half a day, and it is not urgent.** Do it after the rename, not before — doing both at once
means two suspects when something breaks.

### 13.5 Realistic dates

At **six working days a week**, from a standing start:

| Milestone | When |
|---|---|
| Eval merged, name leak fixed, taxonomy decided | **End of day 1** |
| Core stops speaking WhatsApp; kept files ported | **End of week 1** |
| Holds a conversation and replies — *rough demo* | **Middle of week 2** |
| Knowledge base in, safety rules in — **demo-ready** | **End of week 2** |
| Measured, tuned, running on a real number — **pilot-ready** | **Middle of week 3** |
| Phone answering | **Week 4** |

**Demo in about two weeks. A real client's guests on it in about three.**

Phone is week 4 rather than later because the speech-to-text plug already exists in
`media/pipeline.py` — that was built for voice notes and turns out to be most of what a phone
connector needs.

### 13.6 What could realistically move these dates

Honest list, not a disclaimer:

- **The taxonomy decision drifting.** It is one hour and it blocks four things. If it slips a
  week, everything slips a week.
- **Knowledge base scope creep.** The 2.5 days assumes a structured form and Option A for the
  demo. "Can it read our PDF handbook?" is a different and larger project.
- **Quality, not features.** Getting to *working* is two weeks. Getting to *trustworthy* — where
  it does not confidently invent a check-in time — is measured by the eval runner, and that
  number is not fully in our control. Budget for a tuning tail.
- **Meta, still, a bit.** Starting unverified is real, but unverified accounts have limits on
  business-initiated messages. A receptionist mostly *replies* within the 24-hour window, so this
  is genuinely fine for demo and early pilot — verification should still be filed early so it is
  done before it starts to bind.
