# Arabic aliases — draft for client review

**Status: DRAFT. Not imported, not shipped.** These are proposed values for two new columns in the
clinic's own workbook. Nothing here is in the database, in the code, or in the committed workbook,
and nothing should be until the clinic has read it.

**Date:** 28 August 2026 · **For:** the DermaClub demo, Tuesday 1 September
**Data:** `docs/dermaclub-aliases-draft.csv` (the same table, machine-readable)
**Check:** `scripts/check_alias_resolution.py`

---

## 1. Why this exists

The workbook is in English. Branches are `Maadi`, `New Cairo`, `Nasr City`; services are
`Basic Facial`, `Primelase Laser Package - 6 Sessions`. The patients in the demo write Egyptian
Arabic. Nothing in the code transliterates or translates, deliberately — a guess about a treatment
name in shared source is a guess nobody clinical has read, quoted at a patient — so the mapping is
data the clinic owns, in the file decision 2 already makes the source of truth.

Measured against the committed workbook, today:

```
18 phrases: 2 resolved, 0 ask a clarifying question, 16 reach nothing
```

The two that resolve are the two written in English. With this draft laid over the same file:

```
18 phrases: 17 resolved, 1 ask a clarifying question, 0 reach nothing
```

Both numbers come from `scripts/check_alias_resolution.py` against
`docs/DermaClub_Availability_DEMO_2026-08-26_1.xlsx`; re-run it on whatever the clinic sends back.

---

## 2. What to do with this

1. Read §5 and §6 and correct anything that is not what the clinic's own patients say.
2. Answer the three questions in §7 — two of them block four services outright.
3. Add one column headed **`Aliases`** to the **Branches** sheet and one to the **Services** sheet,
   and paste the corrected values in. Separate alternatives with `|`.
4. Send the workbook back. It is re-imported, and `check_alias_resolution.py` is run against it
   before the demo.

`docs/DermaClub_Aliases_DRAFT_2026-08-28.xlsx` holds exactly those two columns, keyed by Branch ID
and Service ID, ready to paste. The client's availability workbook is not modified by anything in
this repository.

---

## 3. The rules the list follows

**One name reaches one row.** An alias that reaches two services is an error the importer refuses
on, and for a good reason: it is how a patient gets booked for the treatment they did not ask for.
Where two catalogue rows are genuinely indistinguishable in Arabic, this draft gives **neither** an
alias and asks the clinic instead (§7).

**Generic words are left generic on purpose.** `ليزر` is not an alias for any single package. A
patient who says it reaches seven, and the receptionist asks which — which is the correct outcome,
because picking one would quote a real price for a package nobody asked about, and three of the
laser packages cost within a few hundred pounds of each other.

**Both article forms, where a patient writes both.** `المعادي` and `معادي` are different strings to
the matcher. Spellings that differ only by hamza (`أ`/`ا`), `ة`/`ه` or `ى`/`ي` are **already folded**
and must not be listed twice — the importer counts that as a name reaching one row twice and
refuses the file. (This draft did exactly that once, on `أبريلين`, and the check caught it.)

**Session counts are part of the name.** `برايم ليز` alone would reach the single session, the
6-session package and the 12-session package. Each alias carries its own count, because the
difference between them is 3,100 EGP and 16,350 EGP.

---

## 4. One thing to decide about digits

The matcher folds Arabic letters but **not** Arabic-Indic digits: `٦` and `6` are different
characters to it, so `ليزر فل بودي ٦ جلسات` does not match an alias written with `6`.

This draft therefore lists both digit forms wherever a number distinguishes a package. That is
verbose but needs no code change and no further decision.

The alternative is a one-line change in `normalise_service_name` to fold `٠١٢٣٤٥٦٧٨٩` to
`0123456789`, which would halve the column. It touches the same folding used by emergency
matching, so it is a change to make deliberately and test, not on demo week. **Recommendation:
ship the verbose column now, make the change after the demo.**

---

## 5. Branches

Confirmed by the client: `المعادي` → `Maadi`.

| ID | Branch | Proposed `Aliases` | Status |
|---|---|---|---|
| DC01 | Maadi | `المعادي\|معادي` | **confirmed** |
| DC02 | New Cairo | `القاهرة الجديدة\|التجمع\|التجمع الخامس\|تجمع خامس` | drafted |
| DC03 | Nasr City | `مدينة نصر\|نصر سيتي` | drafted |
| DC04 | Heliopolis | `مصر الجديدة\|هليوبوليس\|هليوبولس` | drafted |
| DC05 | 6th of October | `السادس من أكتوبر\|أكتوبر\|6 أكتوبر\|٦ أكتوبر` | drafted |
| DC06 | Sheikh Zayed | `الشيخ زايد\|شيخ زايد\|زايد` | drafted |
| DC07 | Tanta | `طنطا` | drafted |
| DC08 | Mansoura | `المنصورة\|منصورة` | drafted |
| DC09 | Zagazig | `الزقازيق\|زقازيق` | drafted |
| DC10 | Zamalek | `الزمالك\|زمالك` | drafted (placeholder branch) |
| DC11 | Mohandessin | `المهندسين\|مهندسين` | drafted (placeholder branch) |
| DC12 | Alexandria Smouha | `سموحة\|الإسكندرية سموحة\|اسكندرية سموحة` | drafted (placeholder branch) |
| DC13 | Ismailia | `الإسماعيلية\|إسماعيلية` | drafted (placeholder branch) |
| DC14 | Damanhour | `دمنهور` | drafted (placeholder branch) |

Two deliberate omissions:

* **`نصر` alone** is not an alias for Nasr City. It is an ordinary Arabic word, and a patient
  naming a branch writes `مدينة نصر`.
* **`الإسكندرية` alone** is not an alias for Smouha. There is one Alexandria branch today; there
  may be two next year, and an alias written now is the one that silently books the wrong one then.

The five placeholder branches keep their aliases. Decision 5 puts all fourteen in the demo, and a
branch that is offered has to be nameable.

---

## 6. Services

Confirmed by the client: `فاشيال` → `Facial` (DT002).

That one mapping also settles the DT001/DT002 tie the source-data review raised: `Basic Facial` and
`Facial` are both 750 EGP for 45 minutes, and today a patient saying either is asked which they
meant. `فاشيال` on DT002 and only the qualified forms on DT001 resolves it outright — a canonical
ID with an alias map behind it, which is what deviation 3 asked for.

| ID | Service | Proposed `Aliases` | Status |
|---|---|---|---|
| DT001 | Basic Facial | `فاشيال بيسك\|الفاشيال البيسك\|فاشيال أساسي` | follows the confirmed pattern |
| DT002 | Facial | `فاشيال\|الفاشيال` | **confirmed** |
| DT003 | Medical Facial + Dermapen | `فاشيال طبي\|الفاشيال الطبي\|ديرمابن\|ديرما بن\|فاشيال ديرمابن` | drafted |
| DT004 | Peeling | `بيلينج\|بيلنج\|تقشير\|التقشير` | drafted |
| DT005 | Hair Treatments | `علاج الشعر\|علاجات الشعر\|جلسة شعر` | drafted |
| DT006 | Skin Boosters | `سكين بوستر\|سكين بوسترز\|بوسترات البشرة` | drafted — see Q3 |
| DT007 | Botox | `بوتوكس\|البوتوكس` | drafted |
| DT008 | Botox + Lip Booster | `بوتوكس وليب بوستر\|بوتوكس + ليب بوستر\|ليب بوستر` | drafted |
| DT009 | Filler | `فيلر\|الفيلر` | drafted |
| DT010 | Filler 1 Syringe | `فيلر سرنجة\|فيلر سرنجة واحدة\|سرنجة فيلر` | drafted |
| DT011 | Filler 2 Syringes + 1 Free | `فيلر سرنجتين\|فيلر 2 سرنجة\|فيلر ٢ سرنجة` | drafted |
| DT012 | Apriline Dermal Filler | `أبريلين\|فيلر أبريلين` | drafted |
| DT013 | Biostimulator | `بيوستيميوليتور\|بيو ستيميوليتور` | drafted — see Q3 |
| DT014 | Sculpting Injection 4 Sessions | `حقن تكميم\|إبر التكميم\|حقن التكميم` | drafted |
| DT015 | Sculpting Alternative Injection | `حقن بديل التكميم\|بديل التكميم\|بديل تكميم` | drafted |
| DT016 | Bikini & Underarm Laser — 6 Sessions | `ليزر بيكيني\|ليزر بيكيني وتحت الإبط\|بيكيني وتحت الإبط` | drafted |
| DT017 | Full Body Laser — 3 Sessions | `ليزر فل بودي 3 جلسات\|ليزر فل بودي ٣ جلسات\|ليزر جسم كامل 3 جلسات` | drafted |
| DT018 | Full Body Laser — 6 Sessions | `ليزر فل بودي 6 جلسات\|ليزر فل بودي ٦ جلسات\|ليزر جسم كامل 6 جلسات` | drafted |
| DT019 | Full Body Laser — 12 Sessions | `ليزر فل بودي 12 جلسة\|ليزر فل بودي ١٢ جلسة\|ليزر جسم كامل 12 جلسة` | drafted |
| DT020 | Annual Half Body (12 Sessions) | *(none)* | **blocked — Q1** |
| DT021 | Laser Annual 12 Sessions | `الباقة السنوية\|ليزر سنوي 12 جلسة\|ليزر سنوي ١٢ جلسة` | drafted |
| DT022 | Laser GenZ 3 Sessions | `جين زد\|ليزر جين زد\|باقة جين زد` | drafted |
| DT023 | Laser Half Body Annual 12 Sessions | *(none)* | **blocked — Q1** |
| DT024 | Laser Maintenance 4 Sessions | *(none)* | **blocked — Q2** |
| DT025 | Laser Semi-Annual 6 Sessions | `ليزر نصف سنوي\|نص سنوي 6 جلسات\|نص سنوي ٦ جلسات` | drafted |
| DT026 | Laser Super Annual Unlimited | `سوبر انيوال\|جلسات غير محدودة\|باقة غير محدودة` | drafted |
| DT027 | Maintenance Package (4 Sessions Full Body) | *(none)* | **blocked — Q2** |
| DT028 | Primelase — Single Session | `برايم ليز جلسة واحدة\|برايمليز جلسة واحدة\|جلسة برايم ليز` | drafted |
| DT029 | Primelase — 6 Sessions | `برايم ليز 6 جلسات\|برايم ليز ٦ جلسات\|برايمليز 6 جلسات` | drafted |
| DT030 | Primelase — 12 Sessions | `برايم ليز 12 جلسة\|برايم ليز ١٢ جلسة\|برايمليز 12 جلسة` | drafted |
| DT031 | Anti-Cellulite 4 Sessions | `سيلوليت\|السيلوليت\|علاج السيلوليت` | drafted |
| DT032 | Body Shaping | `تنسيق قوام` | drafted — see Q2 |
| DT033 | Cool Shaping 4 Sessions | `كول شيبنج\|كول شيبينج` | drafted — see Q2 |
| DT034 | CoolShape Fat Freezing — 4 Sessions | `تجميد الدهون\|تجميد دهون\|كول شيب تجميد الدهون` | drafted |
| DT035 | PowerShape Body Contouring — 4 Sessions | `باور شيب\|باورشيب\|باور شيب 4 جلسات` | drafted |

Two deliberate omissions here too:

* **`شعر` alone** is not an alias for `Hair Treatments`. For this clinic's patients, `شعر` almost
  always means laser hair removal, and DT005 is a scalp treatment.
* **`تكميم` alone** is not an alias for anything. DT014 is `حقن تكميم` and DT015 is
  `حقن بديل التكميم`; a patient who says only `تكميم` currently reaches DT014 through the
  catalogue's own name, which is worth the clinic knowing about.

---

## 7. Questions that need an answer

**Q1 — DT020 vs DT023.** `Annual Half Body (12 Sessions)` at 17,800 and
`Laser Hair Removal Half Body Annual 12 Sessions` at 14,300 are the same words in Arabic. What does
a patient ask for that distinguishes them? Until there is an answer neither has an alias, so
neither can be reached in Arabic.

**Q2 — the four maintenance and body-shaping look-alikes.** The same problem in three places:

* DT024 `Laser Maintenance 4 Sessions` (6,900) vs DT027 `Maintenance Package (4 Sessions Full
  Body)` (8,600) — both `صيانة 4 جلسات`. Neither has an alias.
* DT032 `Body Shaping` (400) vs DT035 `PowerShape Body Contouring - 4 Sessions` (4,000) — the same
  modality in the suitability PDF. Each keeps only its own distinguishing word.
* DT033 `Cool Shaping 4 Sessions` (4,500) vs DT034 `CoolShape Fat Freezing - 4 Sessions` (5,600) —
  currently separated by an English spelling. Thin, and it holds only because no Arabic alias
  crosses them.

**Q3 — two terms an engineer should not choose.** `Skin Boosters` are widely called `حقن نضارة`,
but so is mesotherapy, which is not in this catalogue; and `Biostimulator` is often
`محفزات الكولاجين`. Both are drafted with transliterations only. What does the clinic call them to
patients?

---

## 8. What this does not cover

* **Nothing clinical.** These are names, not indications. The screening category list and the
  disclosure triggers remain an unsigned clinical draft, unchanged by this.
* **No new intents, no code change.** The importer already reads an `Aliases` column on both
  sheets and the resolver already matches against every alias. This is the data those two have
  been waiting for.
* **`ليزر` still asks.** Seven laser packages match it. If the clinic wants a bare `ليزر` to mean
  one specific package, that is a catalogue decision and a question for the same review.
