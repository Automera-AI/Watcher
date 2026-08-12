# Receptionist intent vocabulary

Roadmap item **0.3**. Unblocks 1.2, 2.4, 2.5 and the golden set.

Holiday homes, Dubai and Egypt, guests only. Quotes live availability and live prices from the
property system. **19 intents, 84 example messages, 5 languages, 6 emergency triggers.**

Lives at `packages/intents/`, run from the repo root like `packages/eval`.

```
intents.yaml                       the vocabulary. This is the thing to review.
schema.py                          loads and validates it.
compile.py                         validates, then writes build/*.json. Run this in CI.
tests/test_intents.py              38 tests. Most of them break the file on purpose.
clients/dubai-holiday-homes.yaml   a client that quotes prices, both channels
clients/egypt-holiday-homes.yaml   a client that does not, WhatsApp only
build/                             generated. Do not edit. Gitignored.
```

```bash
python -m packages.intents packages/intents/intents.yaml packages/intents/clients/*.yaml
python -m packages.intents.compile   # the same checks, then writes the JSON. Non-zero on any problem.
python -m pytest packages/intents
```

PyYAML is a **build** dependency, not a runtime one. `schema.py` imports `yaml` lazily inside the
two functions that parse it, so an application loading `build/intents.json` through
`load_compiled` never needs a YAML parser in the image. That is the pipeline below made literal
rather than just described.

---

## On YAML: right about editing, backwards about speed

Editability is the real argument and it is a good one. A client's wording, opening hours and
required details should not need a deploy, and a YAML file is something an operations person can
be handed. That is why this stayed YAML.

Speed is the other way round. YAML is the **slowest** of the obvious formats, not the fastest.
Measured on this file, 200 runs, median:

| | Time | Relative |
|---|---|---|
| YAML, `yaml.safe_load`, the default pure-Python loader | 49.3 ms | **417x slower** |
| YAML, `CSafeLoader`, the C library | 3.9 ms | 33x slower |
| JSON, `json.loads` | 0.1 ms | 1x |

YAML's grammar is large: anchors, references, multiple string styles, implicit typing. That is
what makes it pleasant to write and expensive to parse. JSON has almost no grammar, and a C
parser in the standard library.

**And it does not matter**, which is the more useful point. The vocabulary loads once when the
process starts, so even the slow path costs 49 milliseconds per deploy. It would only matter if
something parsed it per message, and nothing should. Load once, hold it in memory.

So `compile.py` keeps both properties rather than trading one away:

```
intents.yaml  →  compile.py (validates)  →  build/intents.json  →  the application
    humans edit this          CI runs this            ships this
```

The application never parses YAML. It loads JSON in a tenth of a millisecond, and it can trust
that JSON completely, because an invalid file never becomes JSON in the first place. Same reason
you compile anything: catch it at build time, ship the fast artefact.

If you would rather skip the compile step for now, at least pass `Loader=CSafeLoader`. That is a
one-line change and it recovers 92% of the gap.

---

## Why there is a validator at all

The cost of YAML is that a typo becomes a runtime failure rather than a build failure. A runtime
failure here is a guest at 2am.

`schema.py` buys that back. It already caught two real bugs in this file while I was writing it:
`modify_reservation` read back dates it never collected, and `extend_stay` quoted a price with no
rule against discounting. Neither would have been obvious in review.

CI already runs it: `test_compiled_json_matches_the_yaml` shells out to
`python -m packages.intents.compile` and compares the JSON back against the YAML, so an invalid
vocabulary fails the `api` job. Give it its own `ci.yml` step when the compiled JSON starts being
shipped as a deploy artifact rather than a test one.

### Two more it did not catch, found on the way in

Both are fixed, and both now have a validator behind them, because a check that exists but cannot
fire is worse than no check — it reads like coverage.

**The fire trigger could never fire.** It shipped as `7arі2`, where the `і` is U+0456, Cyrillic.
On screen it is the Latin `i`. In a string comparison it is not, so the one trigger standing
between a guest and a fire would have matched nothing, forever, silently. Reading the file does
not find this; only a machine does. `EmergencyTrigger` now rejects any phrase that mixes
alphabets — Franco-Arabic is Latin plus digits, Arabic is Arabic, and nothing legitimate is both.

**The Franco-Arabic check was dead code.** The condition was
`if latin and terminal_tool == "handoff_to_human" and not intent.examples`, and a non-empty
`latin` implies a non-empty `examples`, so the third clause is always false and the branch could
never run. The docstring advertised a guarantee the file did not have.

It is now a rule that means something. `spoken: true|false` is declared per language, `false` only
for `ar-EG-latin`, and an intent that acts alone must have at least one example in a language
people actually say out loud — otherwise its phone test set in item 3.2 comes out empty and nobody
notices. `Vocabulary.spoken_languages` is what that test set filters on.

---

## The five decisions worth arguing with

Everything else is mechanical. These five are judgement, and they are the ones to push back on.

**1. Price questions before booking are answerable. Money questions after are not.**
`price_enquiry` reads a live rate and says it. `billing_question` always fetches a person. The
line is whether money has already changed hands. Before, it is a quote and quotes are what a
property system is for. After, it is a dispute, and a dispute needs someone who can decide.

Your rule, that it must never say a price it cannot check, needed one more thing than the file
had. "Not from memory" was covered. "Cannot be checked by an API" was not, and there was a real
hole: nothing stopped a client whose rates live in a spreadsheet from switching quoting on. That
is now the one combination the build refuses.

There are two halves to checkable, and the second is easy to miss:

- **Askable now.** The client's system must be in `property_systems` with `quotable: true`.
  Hostaway, Guesty and Cloudbeds are. A spreadsheet is not, and neither is a client with nothing
  connected yet, which is every client until roadmap item 3.1 lands.
- **Re-checkable later.** Every quote writes its origin to the audit log *before* the number
  reaches the guest: which system, which rate identifier, when it was fetched, how long it is
  valid, the currency, and the exact dates and guest count it covers. Miss any of those and the
  quote is not sent. A number nobody can reproduce is a number you cannot defend when a guest
  disputes it three weeks later.

Two rules were added to the never list off the back of this. It may not repeat a price from
earlier in the same conversation without asking again, because a price from ten minutes ago is a
price from memory. And it may not say a number when the system did not answer, which is the
failure that produces a confident invented rate.

**1b. `owner_enquiry` exists even though the audience is guests.**
Added in 1.2, when the v2 scaffold turned out to carry it. Owners message the guest line whether
or not they are supposed to, and the receptionist has to recognise one and stop — the same reason
`spam` is in the list. It is `hand_off`, and it is the second half of *money and owner matters
always reach a person*, which NEXT-STEPS §13.3 names as a rule that must survive porting. The
scaffold's `viewing_request` was **not** taken: viewings are lettings traffic, not holiday-homes
reception, and adding it would quietly turn `audience: guests_only` into a fiction.

**2. Cancellations always reach a person, even a verified guest with a clear policy.**
A cancellation is a refund and a refund is money going backwards. The receptionist records the
request and fetches someone. This is the most likely rule you will want to relax once volume
picks up. The tests will stop you, on purpose.

**3. Franco-Arabic is its own language, not a variant.**
`ar-EG-latin` is Egyptian Arabic typed in Latin letters with digits for the sounds Latin has no
letter for: `3` for ع, `7` for ح, `2` for ء. "3ayez ahgez sha22a" is a real booking request. A
large share of Egyptian WhatsApp is written this way.

It has to be a separate language tag for two reasons. The accuracy report breaks down by
language, and if this hides inside `ar-EG` you will never see it failing. And it is **text only,
never spoken**, so it must never be used to build a phone test set.

**4. Emergencies are checked before intent, before confidence, before anything.**
Gas, fire, flood, medical, security, and locked out between 10pm and 7am. These skip the whole
pipeline: reply now, ring a human. A receptionist that files a gas leak as a maintenance ticket
is worse than no receptionist.

The night-time lockout rule is the one to notice. Locked out at 2pm is a support request. Locked
out at 2am is a person standing on a street.

**5. `unclear` hands off after one question, and that is a feature.**
The failure mode that kills trust is not saying "I don't know." It is picking the nearest intent
in order to seem useful. `never: pick the closest intent and act on it` is doing real work.

---

## How it splits

| | Count | Meaning |
|---|---|---|
| Acts on its own | 9 | Availability, prices, property questions, directions, check-in, door codes, checkout, general info, spam |
| Acts and tells someone | 4 | Booking holds, changes, extensions, maintenance |
| Always a person | 6 | Cancellations, billing, payment, complaints, owner enquiries, unclear |

`max_autonomy` is a **ceiling, not a decision**. The gate takes the lower of this and what the
confidence band allows, so a low-confidence booking enquiry still fetches a human. Half confident
is fine for filing a message. It is not enough to hold a unit.

---

## What clients can and cannot change

Narrow, never widen. A client can switch an intent off, force more things to a human, or change
which details are collected. A client **cannot** re-enable something the base file hands off,
and cannot disable a safety-critical intent, because disabling it does not stop guests asking,
it just leaves it unhandled. The tests cover both.

The two examples are deliberately different. The Dubai one quotes prices and runs both channels.
The Egyptian one is WhatsApp only and forces price questions to a person, because rates there are
seasonal and negotiated, which is normal in that market and not a limitation of the product.

A client also cannot put a typed-only language on a voice channel. The Egyptian file explains why
in its own comment — *"no phone line, so nothing spoken"* — and that is exactly the licence it
needs to list Franco-Arabic at all. Give that client a voice channel without dropping
`ar-EG-latin` and a speech model gets handed `3ayez ahgez`, which it can only read as noise. The
base file already records which languages are spoken; a client may not contradict it.

---

## Not done yet

**84 examples, and roadmap item 2.5 wants about 60 *good* ones.** These are seeds, mine, written
from the outside. Replace them with real guest messages as soon as you have any. The corrections
table and its `promoted_to_golden` flag exist for exactly this: every time a human fixes the
receptionist, that is a labelled example that cost you nothing.

**`quote_price` is a tenth tool.** The architecture document listed nine. Quoting live prices
needs its own tool with its own freshness rule, separate from `check_availability`, because a
date being free and a rate being current fail differently. Update the registry when you build it.

**Prices depend on item 3.1.** `price_enquiry` and `availability_check` both need the read path
to the property system. Until that exists they will fall through to `on_tool_failure`, which is
a hand-off. That is correct behaviour, and it means the file is safe to merge before 3.1 lands.
