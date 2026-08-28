# DermaClub × Nada — demo pack

**Demo:** Tuesday 1 September 2026, 15:00–17:00 Africa/Cairo · one WhatsApp number, passed round
the room · **Book the journey against Wednesday 2 September** (see §6.1).

This is the pack for the demo itself: what the assistant does, what it deliberately will not do,
what data sits behind it, and what we need back from the clinic. It is written to be read by
people who are not engineers. Step-by-step deployment instructions are in
`docs/DERMACLUB-DEPLOY-RUNBOOK-2026-09-01.md`; the engineering record is in the two session
handoffs.

---

## 1. What Nada does

Nada is a WhatsApp receptionist for the clinic. On the demo number she can do six things, and only
these six:

| | What a patient gets |
|---|---|
| **Greets** | By name where WhatsApp gives us one, in Arabic, as the clinic's assistant rather than a bot with no employer. |
| **Answers what the clinic has written down** | Opening hours, branch addresses, package terms — from the clinic's own text, never invented. If the answer is not there, she fetches a person rather than guessing. |
| **Quotes a price** | From the imported catalogue, always with the currency, the number of sessions and what the package covers. Never a number on its own. |
| **Offers real appointment times** | The times come out of the clinic's diary on that turn. She never asks "what time suits you?" and then discovers nothing is free. |
| **Books, and gives a reference** | The appointment is written, and the reference — `DC-0266` and upward — is the patient's proof it exists. |
| **Hands over to a person** | For anything clinical, anything she cannot do, and anything she is not confident about. |

The whole journey, in the four turns the demo runs:

```
patient   عايزة أحجز فاشيال في المعادي بكرة
Nada      متاح عندنا 19:00 لـ Facial في فرع Maadi يوم Wednesday 02 September.  ← from the diary, now
patient   الساعة ٧
Nada      تأكيد الحجز: فاشيال، المعادي، Wednesday 02 September، 19:00 — صح كده؟  ← the slot is held
patient   تمام
Nada      تم الحجز ✅ رقم الحجز: DC-0266. مستنينك في الفرع.        ← the appointment now exists
```

That is the real transcript, produced by running the conversation against the imported workbook.
**One open time, not three.** The diary holds exactly one slot per treatment, branch and day, so
availability offers the one that is free or says there is none — a facial at Maadi on Wednesday is
19:00, and at Nasr City it is 17:00.

The treatment name, the branch name and the date are still in English inside those Arabic
sentences, because they are what the workbook holds and how dates are formatted today. Arabic names
for the treatments make a patient's words *resolve*; they do not translate the reply. Both are
fixable after the demo — a display-name column and a date locale — and neither is a bug.

Two details in that sequence are worth pointing at on the day:

* **The slot is held from the moment it is read back**, so it cannot be given away to someone else
  in the room while the patient is typing "تمام". If two people race for the last slot, exactly one
  of them gets it and the other is told the truth immediately.
* **"تمام" finishes the booking rather than ending the conversation.** On its own that word closes a
  chat. Mid-booking it means yes. Nada reads it against the question actually outstanding.

---

## 2. What Nada will not do

This is the more important half, and it is deliberate rather than incomplete.

**She does not answer clinical questions.** "Is laser safe while I'm breastfeeding?" is a question
about a person's body, and it goes to a clinician. She does not reassure, does not explain, and
does not ask a follow-up — asking one would imply the answer might change the outcome.

**She does not book injectables or skin boosters unsupervised.** Botox, filler and skin boosters go
to a clinician for approval whatever the patient says. Routine laser hair removal she books; the
protection there is the disclosure list below rather than a blanket rule.

**Eleven disclosures stop a booking wherever it has got to** — pregnancy, breastfeeding,
isotretinoin, blood thinners, an active infection, cancer treatment, autoimmune conditions,
implanted devices, epilepsy, anaesthetic allergy, keloid scarring. This is checked on every turn,
not once at the start, because a patient mentions the thing when they think of it, usually while
answering a question about something else. When it fires she stops and fetches a person — and says
nothing clinical, not even to name what she noticed. What the patient has already told us is kept,
so whoever picks the conversation up does not start from zero.

**In an emergency she names the clinic's doctor and nobody else.** This was the clinic's own
decision: telling a patient to call an ambulance is itself a triage judgement, and triage is the
one thing a receptionist may never make. She gives the doctor's number, says a person is being
alerted now, and alerts them.

**She never claims to have done something she has not done.** Anything she cannot do says so and
hands over. This sounds obvious; it was not free. Five things a client might probe — practitioner
rosters, promotions, retail and vouchers, stock, order status — have no data behind them and hand
over politely. They are safe to try and will be unexciting.

> **On the clinical lists.** The screening categories and the eleven disclosures are a draft written
> by an engineer, and so is the emergency wording. They are fit for a demo. **Your medical lead must
> approve both before a real patient reaches this number.** A term that is not on the list is a term
> nobody has written down — not a term somebody has cleared.

---

## 3. The data behind the demo

Everything Nada quotes comes from the clinic's own availability workbook. Nothing is invented and
there is no second copy of the catalogue anywhere.

| | |
|---|---|
| Branches | **14** — 5 real, 4 supplied, 5 placeholders, all bookable in the demo |
| Treatments | **35**, `DT001`–`DT035`, priced in EGP. Retail removed |
| Diary | **672 slots**, Mon 31 Aug – Sun 6 Sep, Friday closed, the 15:00 hour held out |
| Already booked | **265** of them, with the clinic's own references up to `DC-0265` |
| Open | **407** |

Three things the file settled, all confirmed with the clinic:

* Opening hours are **11:00–20:00**, as the workbook says, not the 12:00–19:00 quoted earlier.
* The Primelase 6-session package is **15,000 EGP**. The file now says so itself.
* The diary's 62 back-to-back appointments stand exactly as exported. The 15-minute gap between
  appointments applies to **new** bookings Nada makes, never retrospectively to the clinic's own
  diary — applied backwards it would declare most of the clinic's open slots invalid.

---

## 4. What we need from the clinic

| | What | Why it matters |
|---|---|---|
| 1 | **The Arabic names into the workbook.** Reviewed on 28 August and one question is left: what you call a Biostimulator | Until they are in the workbook, "عايزة أحجز فاشيال في المعادي" reaches no treatment and no branch, and Nada asks a question that cannot be answered |
| 2 | **Your medical lead's approval** of the screening categories, the eleven disclosures and the emergency wording | These are engineering drafts. Nothing goes live to a real patient without a clinician's name on them |
| 3 | **The real details for five branches** | Five of the fourteen are placeholders |
| 4 | **The doctor's number** to replace the demo number in the emergency reply | The emergency path is only as good as the number it rings |
| 5 | **Package terms as text** | The 15 laser-package clauses answer patients' questions with no new work. Note clause 1 and clause 11 contradict each other on refunds |

---

## 5. What the demo is not

Named here so nothing on the day is a surprise.

* **Voice notes do nothing.** Speech-to-text is not built. Please do not try one on the day.
* **Same-day booking is off** for the demo, by agreement. Book against Wednesday 2 September.
* **One question at a time.** A message asking price *and* nearest branch gets one of the two
  answered.
* **No Salesforce yet.** Bookings live in this system's own diary; the CRM connection is the work
  that follows a signature.
* **No photos, no diagnosis, ever.** Photo intake is post-demo work, and it will never include an
  automated opinion about a patient's skin.
* **Treatment names, branch names and dates read in English**, inside otherwise Arabic sentences.
  See §1.
* **The unlimited laser package quotes as one session.** Its count is only in its name, so DT026
  reads as a single session at 18,700 EGP. Conservative and wrong; a catalogue fix, not a code one.
  Best not probed on the day.

---

## 6. On the day

### 6.1 The script

Run the journey in §1 against **Wednesday 2 September**, which avoids the same-day rule. What is
open that day:

| Branch | Open |
|---|---|
| Maadi | 11:00 Primelase single · 13:00 Primelase 12 · 16:00 Body Shaping · 18:00 Basic Facial · **19:00 Facial** |
| New Cairo | 12:00 Peeling · 16:00 Botox · 17:00 Botox + Lip Booster · 18:00 Filler · 19:00 Filler 1 Syringe |
| Nasr City | 12:00 Cool Shaping · 13:00 CoolShape · 16:00 Basic Facial · **17:00 Facial** · 18:00 Medical Facial + Dermapen · 19:00 Peeling |

So `فاشيال` at Maadi is 19:00 and the reply is `الساعة ٧`. Asking for a different hour is not a
failure — Nada re-offers what is free — but it costs a turn in front of the client.

Worth demonstrating after it, in this order:

1. **A price question** — "جلسة الليزر بكام؟" — to show a quote that carries the session count.
2. **A clinical question** — "أنا حامل، ينفع أعمل ليزر؟" — to show the hand-off, and that Nada says
   nothing clinical back.
3. **A second phone racing for the same slot**, if there are two devices in the room. One books; the
   other is told the slot has gone, on the turn it happens.

### 6.2 Two things to know before someone in the room tries them

* **A bare hour means the afternoon.** "الساعة ٦" is 18:00, because the clinic opens at 11:00. A
  two-digit time is taken as written.
* **A weekday name means the next one**, never today.

---

## 7. After the demo

Unchanged from what was agreed, and none of it started: clinical sign-off, the Salesforce
connection with its daily per-branch digest, voice notes, secure photo intake, the staff hand-over
states that stop the bot talking while a person owns a conversation, real data for the placeholder
branches, and an SMS fallback. The second vertical — dental — is additive rather than a rewrite,
which was the point of building the taxonomy the way it is built.
