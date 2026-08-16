# G3 — the emergency path

**Session 9, 16 August 2026.** Why the emergency detector and the alert path are shaped the way
they are, and what they deliberately do not do.

Companion to the three A-track specs and `b1-b3-hosting-and-isolation.md`. Read §1 if you only
read one section: it is the safety argument the rest of the file implements.

---

## 1. The problem A5 created

Before continuity, an emergency was handled badly in a way that was easy to see. A guest typed
*there is a smell of gas* and the message was classified, filed as `maintenance_issue`, and left
in an inbox. Nobody was told, but nobody was misled either.

A5 closed the loop and made it worse. The system answers now, so the same message got a fluent,
confident, polite reply about a maintenance request while the flat filled with gas. **A confident
wrong answer is worse than silence**, because silence at least looks like nothing happened.

The vocabulary had the answer to this from item 0.3 and nothing read it:

```yaml
emergency:
  action: handoff_to_human
  alert: phone_call_to_operator
  reply_immediately: true
  triggers: [gas, fire, flood, medical, security, locked_out_at_night]
```

`core/autonomy.py` has taken an `emergency` flag and short-circuited on it since 1.2.
`orchestration/worker.py` passed `emergency=False` as a literal with a comment explaining that
nothing checked. G3 is the two missing halves: **the detector, and the alert**.

---

## 2. Where the check runs, and why that is the item

`intents.yaml` says emergencies are "checked before intent, before confidence, before anything".
That is not a stylistic preference and it is not satisfied by checking inside the receptionist.

The check runs in `Orchestrator.process`, **after the media pipeline and before the classifier**:

| Step | Why it is on that side of the check |
|---|---|
| media enrichment | *before* — a voice note saying there is a fire has no text until it is transcribed |
| **emergency detection** | — |
| classification | *after* — a model is a network round trip that can be slow, wrong, or down |
| identity resolution | *after* — who they are does not change what to do |
| the receptionist | *after* — an emergency is not a job to make progress on |

An emergency therefore **never reaches a model**. That is asserted twice: once in
`test_orchestration.py` with a classifier that raises if called, and once end to end in
`test_main.py`, through the real assembled graph, checking that `classifications` is empty and the
stub provider recorded no calls.

The consequence is that an emergency has no intent, no confidence and no classification row. This
is correct rather than a gap: the vocabulary's instruction is to stop being useful beyond the
safety line, and a row claiming a model judged this message would be a fiction the control page
would then display.

---

## 3. The detector (`apps/api/core/emergency.py`)

Deterministic phrase matching over the declared triggers. No scoring, no threshold, no model. The
property that matters is that **an operator can read `intents.yaml` and know exactly what will
fire**, because they are the person who carries the consequences of it not firing.

### The bias is one-directional and it is on purpose

A false positive costs a phone call about a guest who mentioned the fireplace. A false negative
costs the thing the item exists to prevent. Every judgement call below takes that trade in the
same direction:

| Decision | Why |
|---|---|
| **Arabic matches as a substring** | Arabic attaches its article and conjunctions to the front of the word: حريق is الحريق with the article. A word-boundary test invents false negatives on the most ordinary way to write the sentence |
| **Latin matches on word boundaries** | *fire* as a substring fires on "fireplace", "fireworks", "campfire" — a false positive with no compensating catch. Latin does not agglutinate, so the boundary costs nothing |
| **Digits are word characters** | Franco-Arabic spells sounds as numerals (`re7et ghaz`, `7ari2`), so `7ari2` is a word and does not match inside a booking reference |
| **Diacritics and letter variants folded** | A guest typing ريحه rather than ريحة is a guest with a gas leak |
| **First match wins, declaration order** | There is nothing to rank. A phrase is present or it is not |

### The one trigger with a clock

`locked_out_at_night` carries `only_between: ["22:00", "07:00"]` and the vocabulary's own note
explains it: *locked out at 2pm is a support request; at 2am it is a person on a street.*

"Night" is the **guest's** local clock, not the container's. `TenantPolicy.timezone` (new, set from
`TENANT_TIMEZONE`, default `Asia/Dubai`) is what the window is read in. The test that proves it
matters uses one instant that is 23:30 in Dubai and 21:30 in Cairo — inside the window in one
market and outside it in the other, on the same message.

A misspelled zone fails at **startup**, not at 2am: `Settings` validates it, which is the one place
this repo requires a value that has a working default, and the reason is that a present-but-wrong
value here is never a half-configured machine — it is a typo.

### What it does not do

It does not paraphrase. `test_a_phrasing_the_vocabulary_does_not_declare_does_not_fire` asserts
that *"I smell gas"* — an obvious way to say it — matches neither declared phrase and does **not**
fire. That is a real gap and it is left visible in the suite rather than papered over in code:
widening the trigger list is a one-line edit to `intents.yaml`, it is the operator's edit rather
than this module's, and (checked) it touches neither the classifier prompt nor the recorded eval
baseline. **Widening the gas, fire and medical triggers is the first thing to do before a real
guest can reach this number.**

---

## 4. What happens when one fires

`Orchestrator._emergency`, in this order, and the order is the safety property:

1. **Record both halves of the exchange** before either goes anywhere — the inbound turn and the
   reply — so a crash mid-emergency leaves a transcript rather than nothing.
2. **Dispatch the reply and the alert concurrently** (`asyncio.gather`). Sequentially, one waits on
   the other's retries; neither "the guest hears nothing for ten seconds" nor "the operator waits
   while we retry the guest" is an acceptable way to spend that time.
3. **File it** as `NEEDS_REVIEW` at the `HIGH` band with a snapshot naming the trigger. The band is
   not a model's confidence — nothing sorts above this on the control page.
4. **Stop.** No classification, no identity, no receptionist, no task.

**The job already in flight is left alone**, not abandoned. A guest with a gas leak may well come
back to their booking question, the conversation belongs to a person either way, and throwing away
what the receptionist had collected only makes the resumed conversation worse. This is why
`ConversationStore.record_reply` now takes `task: Task | None` — the emergency reply belongs to the
transcript and to no job, and inventing a task would put a fake intent on `task_rows`.

The reply itself (`EMERGENCY_REPLY`) is **bilingual in one message**. We know which *phrase*
matched, not which language the guest reads — a Franco-Arabic trigger is typed by someone who may
prefer either — and a safety line is the last place to spend a guess. It names no emergency number:
999 in the UAE and 122 in Egypt are per-market facts belonging to a client's configuration, and a
wrong number printed with confidence is worse than "your local emergency number".

---

## 5. The alert, and the gap it does not hide

The vocabulary asks for `phone_call_to_operator`. **Nothing in this process can place a phone
call.** Three options existed; two of them were dishonest.

| Option | Verdict |
|---|---|
| Treat a text notification as satisfying `phone_call_to_operator` | No. The whole file becomes decorative the first time someone checks |
| Refuse to alert because the declared channel is unavailable | No. A message that reaches somebody beats a principle that reaches nobody |
| **Deliver on the best channel there is and report exactly what was done** | Yes |

So `AlertOutcome` carries the channel it *used*, `EmergencyAlert` carries the channel that was
*asked for*, and `outcome.satisfies(requested)` is a one-line answer to "was the operator actually
called?" — which is `False` today, per emergency, in the log, and once at startup.

The seam is `OperatorAlerter` in `core/alerts.py`; the implementation is
`channels/alerting.py::WhatsAppOperatorAlerter`, chosen by `channels/factory.py::build_alerter`.
The core file that wires it (`main.py`) never names a channel, so `KNOWN_LEAKS` stays empty. A
voice alert is a new implementation of the same protocol and nothing above it changes.

**The alerter never raises.** A failed alert must not cost the guest the immediate reply the
vocabulary requires. It logs `CRITICAL` and returns `delivered=False`. And the orchestrator writes
its own `CRITICAL` line *before* calling the alerter at all — the one record that does not depend
on a credential, a network or a configuration being right.

---

## 6. What is now required before a real guest

Two variables, and the second is new:

* `WHATSAPP_ACCESS_TOKEN` + `WHATSAPP_PHONE_NUMBER_ID` — without them nothing is delivered, the
  emergency reply included.
* **`CONTROL_CHAT_PHONE_E164`** — without it there is no alerter and the only alert is a log line.
  `build_alerter` warns at startup and the warning says exactly that.

Neither absence stops the process from starting, for the same reason A6's did not: a service that
ingests, detects, answers and files is a real and working state, and it is every deploy between B1
and B4. But **a deploy missing either is not one to point a guest's number at**, and that sentence
belongs in the B4 runbook next to the phone-number id.

---

## 7. What G3 did not do

* **No voice alert.** `phone_call_to_operator` remains unsatisfied and is reported as such. It
  needs a voice provider, which is the same dependency as the phone channel.
* **No per-client trigger overrides.** `clients/*.yaml` can already narrow other things; emergency
  triggers are deliberately global for now — a client cannot switch one off by accident.
* **No local emergency numbers in the reply.** Per-market data, and wrong is worse than generic.
* **No repeat suppression.** Five messages about the same fire raise five alerts. That is the right
  default for a safety path and the wrong one for an operator's phone; it is a control-page setting
  when there is a control page.
* **No slot extraction, no vocabulary edit.** Both were out of scope and both stay where they were.
