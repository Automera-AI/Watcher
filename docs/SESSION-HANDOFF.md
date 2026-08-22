# Session handoff — read this first

**Updated:** end of session 9, 22 August 2026 (continued after the G3 roadmap update)
**Branch:** `claude/g3-roadmap-review-z66nd9` — **fully merged into `main` as of PR #23**. It has no
unmerged commits left; a new session should restart it from `main` rather than continue on it.
**`main` is at:** `6affc37` — PR #23 merged, so PRs #20 (B1+B2+B3), #21 (G3), #22 (docs) and #23
(CD fix) are all on `main`
**Deployed:** https://watcher-api-lup7.onrender.com — live, serving from `6affc37`
**CD:** green — the image job builds and pushes for the first time since B3 (see §2)
**Start at §2 for status, §8 for what to do first.**

Purpose: let a new session pick up without re-deriving anything. Companion documents are
`docs/Watcher_v2_Roadmap.pdf` (**v2.9**) and the five specs in `docs/specs/` — why the code is
shaped the way it is. `g3-emergency-path.md` is the safety argument behind the whole emergency
path and should be read before anyone edits it.

**This file supersedes the version written earlier in session 9.** That version was accurate
through the G3 PR and the roadmap regeneration (§2's G3 subsection is unchanged from it). What
follows is what happened afterward, in the same session: a broken CD pipeline, found and fixed.

**The roadmap generator is honest again, and that is still worth one paragraph.** The v2.8 PDF
circulated while its generator stayed outside the repository — `docs/make_roadmap.py` was still at
v2.5, so nothing here could reproduce the document everyone was reading. Session 9 re-derived
v2.8's content from the PDF itself, folded G3 into it, and regenerated: generator and artifact
agree at **v2.9**, 12 pages. **Never hand-edit the PDF** — edit the generator and re-run
`python docs/make_roadmap.py` (needs `reportlab`). Nothing after G3 touched the roadmap, so v2.9 is
still current.

Overwrite this file at the end of each session.

---

## 1. Verified state right now

Measured by running it, not read off a document.

| | |
|---|---|
| Branch | `claude/g3-roadmap-review-z66nd9`, merged — content is identical to `main` |
| `main` | **`6affc37`** — PR #23 merged 2026-08-21T20:35Z |
| Tests | **486 passing**, 2 skipped without a Postgres |
| Lint / types | ruff clean; strict mypy clean on 129 source files |
| Recorded baseline | **88%** intent accuracy, gate passing — **still v2's number**, see §5 |
| Database | **live** — Supabase `watcher-prod`, `qjpjxspycuafqqgudsiv`, eu-central-1, PG 17 |
| Schema | `alembic_version` = **`004_row_level_security`** |
| Service | **live** — Render `watcher-api`, `srv-da0a81jl550s73d0b1i0`, deploy `dep-da4bd0jncjis739eeqpg` at `6affc37`, status `live` |
| **CD (GitHub Actions)** | **green** — run 21 (`32524314100`) succeeded; the image job now actually builds and pushes to GHCR, confirmed for the first time since B3 |
| Webhook | `GET /webhook` handshake **verified** against the live service |
| Python | 3.13 |

To get green in a fresh container:

```bash
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python ruff==0.6.9 mypy==1.11.2 pytest==8.3.3 \
  pydantic==2.9.2 pydantic-settings==2.5.2 fastapi==0.115.0 httpx==0.27.2 \
  rapidfuzz==3.10.1 sqlalchemy==2.0.50 alembic==1.13.3 pyyaml==6.0.2 \
  types-PyYAML==6.0.12.20240917
.venv/bin/python -m pytest          # expect 486 passed, 2 skipped
.venv/bin/mypy && .venv/bin/ruff check . && .venv/bin/ruff format --check .
```

`psycopg[binary]` and `uvicorn[standard]` are still deliberately absent from that list and from CI —
nothing imports either, and the two skipped tests are the RLS ones that need a real Postgres.
`pip install .` gets you everything. `reportlab` is needed only to re-run `docs/make_roadmap.py`.

**The product gap, restated.** The application starts, connects, files, answers, and — since the
first half of this session — **knows the difference between a gas leak and a maintenance
request**. It has somewhere to connect to and somewhere to run, and now its container image
actually builds in CI rather than silently failing after the fact.

One thing it still cannot do: it **cannot deliver a reply**. The process warns
`no send credentials configured` at startup, so replies are composed and recorded and never sent.
That warning has a second, louder companion — `no emergency alert path configured` — because the
alert rides on the same credentials plus the operator's number. Nothing routes a guest to it yet
either (B4). Read §3 before pointing a real number at it.

---

## 2. What session 9 delivered

### Part one: roadmap item G3 — the emergency path (1.5d)

Full reasoning: `docs/specs/g3-emergency-path.md`. A5 made this urgent rather than merely
outstanding: before continuity a gas leak was filed in silence, and after it the system gave the
same message a fluent, confident, polite reply about maintenance.

**The detector — `apps/api/core/emergency.py`.** Deterministic phrase matching over the six
triggers `intents.yaml` has declared since item 0.3. No model, no scoring, no threshold.

| | |
|---|---|
| Where it runs | `Orchestrator.process`, **after media enrichment, before the classifier** (D30) |
| Arabic | matched as a substring — الحريق is حريق with the article attached |
| Latin | matched on word boundaries — otherwise "fireplace" is a fire |
| Franco-Arabic | digits are word characters, so `7ari2` is a word and not a fragment |
| Normalisation | diacritics, alef forms, ta marbuta and whitespace folded on both sides |
| The night trigger | `only_between` read in **`TenantPolicy.timezone`** — the guest's clock (D34) |

**An emergency never reaches a model.** Asserted twice: once against a classifier that raises if
called, and once end to end through the assembled graph, checking `classifications` stays empty.

**What happens when one fires — `Orchestrator._emergency`.** Both halves of the exchange are
recorded first; then the reply and the operator alert are dispatched **concurrently**; then the
item is filed `NEEDS_REVIEW` at the `HIGH` band with the trigger in the snapshot; then nothing else
runs. No classification, no identity, no receptionist, no task.

**The alert — `core/alerts.py` + `channels/alerting.py`.** The vocabulary asks for
`phone_call_to_operator`. Nothing wired can place a call, so the alerter **delivers on the channel
there is and reports which one it used** (D32) rather than pretending or refusing.

**The roadmap, regenerated.** `docs/make_roadmap.py` was three revisions behind the PDF in
circulation. It is now **v2.9**: v2.8's twelve pages re-derived from the artifact, with G3 moved to
DONE, Track G at 4.0d, the total at **32.75d**, M1 at **~2.5 days**.

Decisions D30–D34 (see `docs/DECISIONS.md`), tests 417 → 486.

### Part two: CD was broken, and had been for three merges

Right after PR #22 (the roadmap docs) merged, the user reported the CD workflow failing on

```
ERROR: failed to build: invalid tag "ghcr.io/amahmoudosman96-lgtm/Watcher/api:latest":
repository name must be lowercase
```

**Diagnosis.** GHCR is an OCI registry; OCI repository names must be lowercase. `cd.yml` built its
tag from `${{ github.repository }}`, which preserves the repo's actual casing —
`amahmoudosman96-lgtm/Watcher`, capital W. The tag was invalid before `buildx` did any work.

**This was not new and not caused by G3.** Every step in the image job is gated on
`steps.detect.outputs.exists`, and no `apps/api/Dockerfile` existed until B3 (PR #20) — so the job
skipped itself and reported success on every run before that. Checked against the actual run
history: runs for PRs #20, #21 and #22 (the first three merges after the Dockerfile existed) all
failed with this exact error; nothing about G3's content was implicated.

**The fix — PR #23, `f0dad21`, merged.** One step added to `.github/workflows/cd.yml`:

```yaml
- name: Image name
  id: image
  run: echo "name=ghcr.io/${GITHUB_REPOSITORY,,}/api" >> "$GITHUB_OUTPUT"
```

with the tags in the build step switched to `${{ steps.image.outputs.name }}`. Lowercased in a
step rather than the repo renamed, so a fork or a future rename doesn't reopen the bug, and so
Render's `repo` field (a URL, not an ID) and this session's GitHub scope are untouched.

**Verified, not assumed:** CD run 21 (`32524314100`) on `6affc37` completed with
`conclusion: success` — the image job built and pushed to GHCR for the first time since B3. Render
auto-deployed the same commit independently (`dep-da4bd0jncjis739eeqpg`, status `live`); it was
never affected by the CD failure because it builds from source (`pip install .`) on push to `main`,
not from the GHCR image — the `deploy` job in `cd.yml` is still the gated §17 placeholder and
`needs: image`, so it has still never run for real.

### Decisions made this session

| Decision | Choice | Where it lives |
|---|---|---|
| Where the emergency check runs | **Before the classifier, after media** (D30) | `orchestration/worker.py` |
| How a trigger is matched | **Declared phrases; two rules for two scripts** (D31) | `core/emergency.py` |
| The alert channel gap | **Deliver on what exists, report what was used** (D32) | `core/alerts.py`, `channels/alerting.py` |
| An emergency reply belongs to no task | **`record_reply(task=None)`; job in flight untouched** (D33) | `orchestration/ports.py`, `db/orchestration_repo.py` |
| Where "night" is measured | **`TENANT_TIMEZONE`, validated at startup** (D34) | `core/policy.py`, `core/config.py` |
| The GHCR image tag | **Lowercased in a workflow step, not by renaming the repo** | `.github/workflows/cd.yml` |

---

## 3. Traps and things not to re-litigate

- **`intents.yaml`'s trigger list is narrower than it reads.** "I smell gas" matches neither
  declared gas phrase and does **not** fire — there is a test asserting exactly that. Widening it
  is a one-line edit to the vocabulary, the operator's edit rather than the code's, and it touches
  neither the prompt nor the eval baseline. **Do this before a real guest can reach the number.**
- **`CONTROL_CHAT_PHONE_E164` is now a safety variable, not a convenience.** Without it there is no
  alerter and the only alert is a log line. The startup warning says so; believe it.
- **Do not move the emergency check inside the receptionist.** It would then depend on the
  classifier succeeding, which is the exact failure G3 exists to remove.
- **Do not make the detector clever.** No fuzzy matching, no model, no paraphrase. The
  reviewability of `intents.yaml` by the person who carries the consequences is the design.
- **Do not interpolate `${{ github.repository }}` into a GHCR tag again.** It carries the repo's
  actual casing (`…/Watcher`), GHCR requires lowercase, and the fix already lives in `cd.yml` as
  `steps.image.outputs.name` — reuse it rather than hardcoding the name a second time.
- **A green CD run before now proves nothing about the image job.** Every prior "success" on this
  workflow, before B3 added the Dockerfile, was the job skipping itself under its own detect-and-
  skip gate. Run 21 is the first run where the image job actually executed and it passed.
- **Never let `DATABASE_URL` name `postgres`.** Supabase's `postgres` role has `rolbypassrls`;
  that one substitution silently disables every policy in migration 004. It must name
  **`watcher_app`**.
- **The app role's password is not in the repository and must not be.** It was set during session 8
  and appears in that session's transcript; rotate it if that is not acceptable.
- **Use the transaction pooler URI (port 6543), not the direct one.** `db.<ref>.supabase.co`
  resolves to IPv6 only and Render's outbound is IPv4. Copy the exact pooler host from Supabase →
  Connect; the `aws-N-eu-central-1` prefix is per-cluster and should not be guessed.
- **The `channel_configs` row is a placeholder** (`PLACEHOLDER_WHATSAPP_PHONE_NUMBER_ID`). Until
  step 1 of the B4 runbook, no real endpoint resolves — by design, it fails loudly.
- **The webhook path is `/webhook`** — singular, no prefix. A bare browser `GET /webhook` returns
  `403 verification failed` because the three `hub.*` parameters are absent; that is not a fault.
- **`<angle brackets>` around a configured value mean "unset".** The same brackets pasted into a
  *URL* fail quietly instead — that cost a debugging round.
- **The deployed `DATABASE_URL`'s pooler host is unverified.** SQLAlchemy connects lazily, so
  startup proves nothing and the first tenant query is the test.
- **Replies are composed and not delivered** until `WHATSAPP_ACCESS_TOKEN` and
  `WHATSAPP_PHONE_NUMBER_ID` are set. This now includes the emergency reply and the alert.
- **A new adapter must take a `TenantScope`.** `test_rls.py` asserts this per adapter.
- **The two RLS tests skip without `WATCHER_RLS_DATABASE_URL`.** Deliberate; CI ships no Postgres
  driver.
- **Do not "fix" the empty slot dict by inventing extraction.** Still item 2.x: it is a prompt
  change *and* a golden-set change, and it invalidates the recorded baseline.
- **Do not re-add rules or destinations to the orchestrator** (D24). Retained for the control page.
- **Do not add a module-level `app` to `main.py`**, and **do not name a channel in it**.
- **`KNOWN_LEAKS` is empty. Keep it that way.**
- **Do not make the webhook wait for classification.** B5 (arq/Redis) is the replacement.
- **Do not re-add `temperature`**; **`_thinking_policy` is vendor contract**.
- **The eval gate does not measure the prompt.** It replays fixtures recorded under prompt v2 and
  reports 88% whatever the prompt says. See §5.

---

## 4. What to do first — B4, and the operator's edit

**The trigger phrases (30 minutes, operator).** Widen gas, fire and medical in `intents.yaml` with
the phrasings real guests use. Nothing else in the codebase needs to change and no baseline moves.
This is the cheapest safety work left on the board.

**B4 — the webhook subscription (0.5d).** Mostly de-risked: the handshake is verified end to end
against the live service, and **no custom domain is needed**. Four things stand between here and a
guest:

1. **The real phone-number id** in `channel_configs.external_id`, replacing the placeholder.
2. **Send credentials** — `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`.
3. **`CONTROL_CHAT_PHONE_E164`** — since G3, without it an emergency is detected, answered and
   filed, and no person is told.
4. **A paid Render instance.** The free one sleeps after ~15 minutes; a 30–60s cold start reads to
   Meta as a timeout and earns a retry. This is the real B4 blocker, not DNS.

**Then 2.4** (knowledge base). **2.7 remains unblocked** and needs only a key and a decision to
spend it; see §5.

*Not urgent, but worth a look next session:* now that the CD image job actually runs, it's worth
opening the run and confirming the Docker build itself succeeds end to end (not just the tag), and
that the pushed image is one the eventual deploy job could use.

---

## 5. Two numbers 2.7 should settle

Unchanged from sessions 5–8.

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
| App factory + lifespan | `apps/api/app.py` — `create_app(..., on_shutdown=...)` |
| The pipeline | `apps/api/orchestration/worker.py` — `Orchestrator.process` (async) |
| **The emergency detector** | `apps/api/core/emergency.py` |
| **The alert seam / its implementation** | `apps/api/core/alerts.py`, `apps/api/channels/alerting.py` |
| **Emergency tests** | `apps/api/tests/test_emergency.py`, `test_alerting.py`, and the G3 block in `test_orchestration.py` |
| Orchestrator seams | `apps/api/orchestration/ports.py` |
| DB port implementations | `apps/api/db/orchestration_repo.py`, `db/repository.py`, `db/tenant_resolver.py` |
| Engine, session scopes, the tenant stamp | `apps/api/db/engine.py` |
| RLS policies | `alembic/versions/004_row_level_security.py` |
| Isolation tests | `apps/api/tests/test_rls.py` |
| The image | `apps/api/Dockerfile` (build context is the repo root) |
| **CD — image build + push (fixed this session)** | `.github/workflows/cd.yml` |
| Typed config | `apps/api/core/config.py` + `core/settings_base.py` |
| Channel credentials, sender, adapter choice | `apps/api/channels/` |
| LLM providers | `apps/api/classifier/anthropic.py`, `openai.py`, `factory.py` |
| **Why G3 is shaped this way** | `docs/specs/g3-emergency-path.md` |
| Why B1–B3 are shaped this way + the deploy runbook | `docs/specs/b1-b3-hosting-and-isolation.md` |
| Why A5/A6 are shaped this way | `docs/specs/a5-continuity-and-a6-outbound-sender.md` |
| Why A2/A4 are shaped this way | `docs/specs/a2-database-and-a4-composition-root.md` |
| Why A1/A3 are shaped this way | `docs/specs/a1-configuration-and-a3-llm-providers.md` |
| Locked decisions | `docs/DECISIONS.md` (D30–D34 are this session's) |
| The roadmap, and the only way to change it | `docs/make_roadmap.py` → `docs/Watcher_v2_Roadmap.pdf` (v2.9) |
| Channel-boundary rules | `apps/api/tests/test_boundary.py` |

---

## 7. Roadmap status against v2.9

Unchanged by the CD fix — it was an infrastructure bug, not a roadmap item.

| Track | Remaining | Note |
|---|---|---|
| **A — Make it run** | **0d** | complete since session 7 |
| B — Host it | **1.5d** | B1 ✅ B2 ✅ B3 ✅ — left: B4 (0.5d), B5 (1.0d) |
| 2 — Receptionist | 3.5d | 2.4 knowledge, 2.7 eval (unblocked), 2.8 many properties |
| D — Control page | 13.75d | D2's 3 backend days are the hidden half |
| G — Guardrails | **4.0d** | **G3 ✅** — G1, G2, G4 remain; none is on the critical path |
| E — Sellable | 4.5d | Blocked on P1 for sequencing |
| 3 — Integration | 5.5d | 3.1 blocked on P1 |
| **Total** | **~32.75d** | at the observed rate, ~7 weeks to sellable |

**Milestone M1 — answers a real message, safely: ~2.5 days** (B4 + 2.4). G3 is done and CD now
proves the image it will one day be deployed from, so what stands between here and a guest is
operational rather than engineering.

---

## 8. First five minutes of the next session

1. **`main` has everything through this session** (`6affc37`) — branch from it normally. The
   feature branch used this session is fully merged; do not resume it.
2. Rebuild the venv from §1 and confirm **486 passed, 2 skipped**.
3. Read §3, particularly the first three items: the narrow trigger list,
   `CONTROL_CHAT_PHONE_E164` now being a safety variable, and the GHCR tag fix.
4. Do the operator's `intents.yaml` edit, then B4. Everything else on Track B is deferred (B5).
