"""Generate the Watcher v2 build roadmap, v2.0 (15 August 2026).

Same palette, chrome and scoring chips as v1.15; the layout follows the v1.14+ document (status
column per work item rather than "why / what it unblocks").

What changed in v2.0 — the reason for the major version bump:

A code audit on 15 August 2026 established that the v1.15 headline ("5.75 engineering days
remaining") counted only the numbered Track 0-3 work items. It excluded everything needed to make
any of them execute: there is no LLM client, no database connection, no application entrypoint, no
container, and no frontend. This revision adds the four tracks that were never priced -- A (make it
run), B (host it), D (control page), E (product surface) -- and restates the total as ~35.25 days.

It also corrects two items previously marked DONE. Conversation continuity is built but not wired:
ConversationRepository is never called outside tests, and the orchestrator passes empty slots and a
null task, so every message is treated as turn one.

The narrative source of truth is docs/LAUNCH-PLAN.md; this script renders it. Keep the day counts
here in sync with that document -- there should never be a third figure.
"""

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

OUT = "/home/user/Watcher/docs/Watcher_v2_Roadmap.pdf"
VERSION = "v2.0"
DATE = "2026-08-15"

INK = colors.HexColor("#12171f")
MUTED = colors.HexColor("#5b6675")
RULE = colors.HexColor("#d8dee7")
BAND = colors.HexColor("#f2f5f9")
ACCENT = colors.HexColor("#1f4e79")
DONE_C = colors.HexColor("#1e8449")
NEW_C = colors.HexColor("#7d3c98")

URG = {
    "NOW": colors.HexColor("#c0392b"),
    "HIGH": colors.HexColor("#d67200"),
    "MED": colors.HexColor("#1f6fb2"),
    "LOW": colors.HexColor("#7b8794"),
    "--": MUTED,
}
EASE = {
    "Trivial": colors.HexColor("#1e8449"),
    "Easy": colors.HexColor("#4a9c48"),
    "Moderate": colors.HexColor("#c08a00"),
    "Hard": colors.HexColor("#b8442c"),
    "--": MUTED,
}

ss = getSampleStyleSheet()


def st(name, **kw):
    base = dict(fontName="Helvetica", fontSize=9, leading=12.5, textColor=INK, alignment=TA_LEFT)
    base.update(kw)
    return ParagraphStyle(name, **base)


BODY = st("body")
CELL = st("cell", fontSize=8.2, leading=10.2)
CELLB = st("cellb", fontSize=8.2, leading=10.2, fontName="Helvetica-Bold")
NOTE = st("note", fontSize=7.6, leading=9.6, textColor=MUTED)
H1 = st("h1", fontSize=17, leading=21, fontName="Helvetica-Bold", spaceAfter=3)
H2 = st("h2", fontSize=12, leading=15, fontName="Helvetica-Bold", textColor=ACCENT,
        spaceBefore=13, spaceAfter=5, keepWithNext=1)
SUBT = st("subt", fontSize=8, leading=10.5, keepWithNext=1)
LEAD = st("lead", fontSize=9.6, leading=13.6, textColor=MUTED)


def chip(text, palette):
    return Paragraph(f'<font color="{palette[text].hexval()}"><b>{text}</b></font>', CELL)


STATUS_C = {
    "DONE": DONE_C,
    "NEXT": URG["NOW"],
    "NEW": NEW_C,
    "PENDING": URG["MED"],
    "NOT BUILT": URG["NOW"],
    "NOT STARTED": MUTED,
    "PART-BUILT": colors.HexColor("#c08a00"),
}


def status(text):
    """Status cell: the leading tag is coloured by state, the explanation is muted."""
    tag, _, rest = text.partition(" — ")
    colour = STATUS_C.get(tag, INK)
    head = f'<font color="{colour.hexval()}"><b>{tag}</b></font>'
    return Paragraph(head if not rest else f"{head} — {rest}", NOTE)


def band(text, colour=ACCENT, width=456):
    t = Table([[Paragraph(text, BODY)]], colWidths=[width])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), BAND),
        ("LINEBEFORE", (0, 0), (0, -1), 2.5, colour),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return t


def track_table(rows):
    """rows: (num, work, urgency, ease, days, status)"""
    data = [[
        Paragraph("<b>#</b>", CELL), Paragraph("<b>Work item</b>", CELL),
        Paragraph("<b>Urgency</b>", CELL), Paragraph("<b>Ease</b>", CELL),
        Paragraph("<b>Days</b>", CELL), Paragraph("<b>Status</b>", CELL),
    ]]
    for n, w, u, e, d, stat in rows:
        data.append([
            Paragraph(n, CELL), Paragraph(w, CELLB),
            chip(u, URG), chip(e, EASE),
            Paragraph(d, CELL), status(stat),
        ])
    t = Table(data, colWidths=[22, 110, 48, 48, 34, 194], repeatRows=1, hAlign="LEFT")
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BAND),
        ("LINEBELOW", (0, 0), (-1, 0), 0.7, RULE),
        ("LINEBELOW", (0, 1), (-1, -2), 0.3, RULE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("ALIGN", (4, 0), (4, -1), "CENTER"),
    ]))
    return t


def section(title, subtitle, rows):
    bits = [Paragraph(title, H2)]
    if subtitle:
        bits.append(Paragraph(subtitle, SUBT))
        bits.append(Spacer(1, 5))
    bits.append(track_table(rows))
    return bits


def two_col(left_title, left_rows, right_title, right_rows):
    t = Table([
        [Paragraph(f"<b>{left_title}</b>", CELL), Paragraph(f"<b>{right_title}</b>", CELL)],
        [Paragraph("<br/>".join(left_rows), CELL), Paragraph("<br/>".join(right_rows), CELL)],
    ], colWidths=[240, 216], hAlign="LEFT")
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BAND),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOX", (0, 0), (-1, -1), 0.4, RULE),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, RULE),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


story = []

# ---------------------------------------------------------------- page 1
story.append(Paragraph("Watcher v2 &mdash; Build Roadmap", H1))
story.append(Paragraph(
    "From a message&nbsp;filer to a receptionist. Every item scored for urgency and ease. "
    f"&nbsp;&bull;&nbsp; 15 August 2026 &nbsp;&bull;&nbsp; {VERSION}", LEAD))
story.append(Spacer(1, 10))

story.append(band(
    "<b>The correction this revision makes:</b> v1.15 said 5.75 days remained. That counted only "
    "the numbered Track&nbsp;0&ndash;3 items &mdash; it did not count the code that makes any of "
    "them execute. There is <b>no LLM client, no database connection, no application entrypoint, "
    "no container and no frontend</b>. The receptionist can be reasoned about; it cannot yet "
    "process a single real message. Honest remaining: <b>~35.25 engineering days</b>.",
    URG["NOW"]))

story.append(Paragraph("Where we stand today", H2))
story.append(two_col(
    "Built and tested &mdash; the business logic is genuinely good",
    [
        "<b>277 passing tests</b>, no DB or network needed",
        "74 source files across 15 modules (106 with tests)",
        "19 DB tables + 3 migrations",
        "Every external system behind a swappable seam",
        "Eval runner + CI accuracy gate, 50 golden cases",
        "Receptionist vocabulary: 19 intents, data not code",
        "Unified intent taxonomy &mdash; classifier = vocabulary",
        "<b>Prompt v3</b> &mdash; catalogue rendered from the vocabulary, "
        "tie-breaks, language rules, injection rule",
        "Channel-neutral envelope + chat/voice adapters",
        "Task state machine and the autonomy gate",
        "Persistence layer &mdash; conversations, turns, tasks",
        "Channel-neutral core &mdash; KNOWN_LEAKS = {}, enforced by test",
    ],
    "Missing &mdash; and nothing runs without these",
    [
        "<b>No LLM client</b> &mdash; provider.py is a Protocol; zero HTTP "
        "calls exist anywhere in apps/",
        "<b>No DB connection</b> &mdash; no engine or sessionmaker outside tests",
        "<b>No entrypoint</b> &mdash; create_app() has no production caller",
        "<b>No outbound</b> &mdash; ChannelSender has no implementation",
        "<b>No Dockerfile</b> &mdash; so cd.yml has never deployed anything",
        "<b>No RLS</b> &mdash; no policy in any migration",
        "<b>No control page</b> &mdash; one CSS file, no package.json",
        "<b>No control-page API</b> &mdash; ~25 endpoints, none written",
        "Knowledge &mdash; zero tables, zero rows",
        "Live availability &mdash; no read path to any PMS",
        "<b>A measured prompt</b> &mdash; the gate replays v2 fixtures, so "
        "88% is not v3&rsquo;s number",
    ],
))

story.append(Paragraph("What changed in this revision", H2))
story.append(two_col(
    "v1.15 &rarr; v2.0 (audit against the code)",
    [
        "Four unpriced tracks added: <b>A</b> make it run, <b>B</b> host it, "
        "<b>D</b> control page, <b>E</b> product surface",
        "Total restated: 5.75 &rarr; <b>~35.25 days</b>",
        "<b>2.1 / 2.2 downgraded</b> &mdash; conversation continuity is built "
        "but never wired; every message is treated as turn one",
        "New item 2.8 &mdash; many properties per client, not one",
        "Stack locked: Supabase + Render + Clerk",
    ],
    "What did not change",
    [
        "The business logic is sound and stays as built",
        "2.4 knowledge is still the last item before a demo",
        "3.1 still blocked on P1 (pick the first client)",
        "The scope guard still holds &mdash; no PDF handbook ingestion",
    ],
))

story.append(Spacer(1, 10))
story.append(Paragraph(
    "<b>Totals:</b> ~35.25 engineering days remaining. At the six-day week these estimates assume, "
    "that is ~6 weeks of pure build; with review, integration and the external approvals (P1&ndash;"
    "P3), plan <b>7&ndash;8 weeks to sellable</b> and <b>~2 weeks to a real phone number that "
    "answers</b>. The critical path is no longer 2.4 &mdash; it is <b>Track A</b>, because until "
    "the app can start and call a model, no other item can be demonstrated at all.", BODY))

story.append(PageBreak())

# ---------------------------------------------------------------- page 2
story.extend(section(
    "Track A &mdash; Make it run. &nbsp;[THE NEW CRITICAL PATH]",
    "None of this is on the old roadmap, and nothing else can be shown until it exists.",
    [
        ("A1", "Configuration layer", "NOW", "Easy", "0.5",
         "NOT BUILT — pydantic-settings over all ~20 vars in .env.example; only 2 are read today"),
        ("A2", "DB engine and session", "NOW", "Easy", "0.5",
         "NOT BUILT — engine, sessionmaker, get_session. Supabase transaction pooling needs "
         "NullPool + prepare_threshold=None"),
        ("A3", "Concrete LLM providers", "NOW", "Moderate", "1.5",
         "NOT BUILT — Anthropic + OpenAI against the existing LLMProvider seam. Mark the ~5k-token "
         "system block cacheable or it is the largest cost line"),
        ("A4", "Composition root", "NOW", "Moderate", "1.0",
         "NOT BUILT — apps/api/main.py, the first production caller of create_app()"),
        ("A5", "Wire conversation continuity", "NOW", "Moderate", "1.5",
         "PART-BUILT — ConversationRepository is never called outside tests; the orchestrator "
         "passes task=None and empty slots. Also converts process() to async, removing the "
         "asyncio.run() crash on the inline queue"),
        ("A6", "WhatsApp outbound sender", "NOW", "Moderate", "1.0",
         "NOT BUILT — ChannelSender has no implementation; replies are composed and never sent"),
    ]))

story.append(Spacer(1, 8))
story.append(band(
    "<b>Why A5 is not a small item.</b> Items 2.1 and 2.2 were marked DONE and both are genuinely "
    "written &mdash; the tables, the repository, the task state machine, the reply path. What was "
    "never written is the line joining them. <tt>worker.py</tt> calls the receptionist with "
    "<tt>extracted_slots={}</tt> and <tt>task=None</tt> hardcoded, so slot filling and task "
    "continuity are inert. The milestone &ldquo;holds a conversation&rdquo; is not yet true.",
    URG["HIGH"]))

story.extend(section(
    "Track B &mdash; Host it. &nbsp;[Supabase + Render]",
    "Stack locked 15 August 2026. B2 must land before a second client exists.",
    [
        ("B1", "Supabase project and migrations", "NOW", "Easy", "0.5",
         "NOT STARTED — provision, set DATABASE_URL, run the 3 existing migrations"),
        ("B2", "Row-Level Security", "NOW", "Moderate", "1.5",
         "NOT BUILT — no policy in any migration, though AGENTS.md calls tenant isolation "
         "non-negotiable. Per-session GUC + a cross-tenant read test"),
        ("B3", "Dockerfile and Render service", "HIGH", "Easy", "0.75",
         "NOT BUILT — the Dockerfile alone activates the dormant cd.yml image job"),
        ("B4", "Domain, TLS, Meta webhook", "HIGH", "Easy", "0.5",
         "NOT STARTED — Meta needs a public HTTPS URL before anything arrives"),
        ("B5", "Durable queue (Redis + arq)", "MED", "Moderate", "1.0",
         "NOT BUILT — BackgroundTasks loses in-flight messages on every deploy. The seam already "
         "exists, so nothing else changes"),
    ]))

story.append(PageBreak())

# ---------------------------------------------------------------- page 3
story.extend(section(
    "Track 2 &mdash; Make it a receptionist. &nbsp;[2.4, 2.7, 2.8 REMAIN]",
    "The reply path is written and the prompt now contains instructions a model can follow. What "
    "remains is knowledge, a measured prompt, and more than one property.",
    [
        ("2.1", "Conversations, tasks and slot filling", "HIGH", "Hard", "2.0",
         "PART-BUILT — 7 tables, ConversationRepository, task converters all written and tested; "
         "not wired into the pipeline. Wiring is priced as A5"),
        ("2.2", "The reply path", "HIGH", "Moderate", "1.5",
         "PART-BUILT — tool registry, receptionist, ChannelSender protocol. No sender "
         "implementation exists, so nothing is delivered. Priced as A6"),
        ("2.3", "Autonomy gate", "HIGH", "Easy", "1.0",
         "DONE — RECEPTIONIST_REPLY as the fourth RoutingAction; decide_autonomy() runs "
         "after classification and identity, before rule matching"),
        ("2.4", "Knowledge base", "HIGH", "Moderate", "2.0",
         "NEXT — facts table with sensitivity flags, a real &ldquo;I don&rsquo;t know&rdquo; that "
         "fetches a human, verification codes"),
        ("2.5", "Prompt v2 and rewrite the golden set", "MED", "Moderate", "1.0",
         "DONE — golden set 8 &rarr; 50 across all 19 intents. The prompt half was replaced by v3"),
        ("2.6", "Intent taxonomy unification", "NOW", "Easy", "0.5",
         "DONE — 19 vocabulary-aligned intents"),
        ("2.7", "Re-record the eval under prompt v3", "MED", "Easy", "0.5",
         "PENDING — unblocked the moment A3 lands and a key exists. Add Franco-Arabic cases"),
        ("2.8", "Many properties per client", "HIGH", "Moderate", "1.0",
         "NEW — the roadmap assumed one property whose facts fit in the prompt. Needs a properties "
         "table, per-property fact scoping, and message&rarr;property resolution"),
    ]))

story.append(Spacer(1, 8))
story.append(band(
    "<b>The eval is still not measuring the prompt.</b> AGENTS.md says no prompt change merges "
    "without an eval run. The gate does run and does pass &mdash; but it replays fixtures keyed by "
    "message text and recorded under prompt v2, so it reports 88% whatever the prompt says. Until "
    "2.7 runs, <b>treat 0.88 as v2&rsquo;s number</b>. baseline.json now says so in the file.",
    URG["MED"]))

story.append(PageBreak())

# ---------------------------------------------------------------- page 4
story.extend(section(
    "Track D &mdash; The control page. &nbsp;[ALL FIVE VIEWS]",
    "Built against DESIGN-SPEC.md. Never hardcode a colour &mdash; reference a token.",
    [
        ("D1", "Next.js scaffold, tokens, fonts, Clerk", "HIGH", "Easy", "1.0",
         "NOT STARTED — apps/control-page holds one CSS file. Committing package-lock.json "
         "activates CI&rsquo;s dormant web job"),
        ("D2", "REST API behind the views", "NOW", "Hard", "3.0",
         "NOT BUILT — the hidden half of this track. ~25 tenant-scoped, paginated endpoints for "
         "inbox, sources, destinations, rules and eval. None exist today"),
        ("D3", "Typed client from the OpenAPI schema", "MED", "Trivial", "0.25",
         "NOT STARTED — generated, not hand-written"),
        ("D4", "Inbox view", "NOW", "Moderate", "2.0",
         "NOT STARTED — the critical path (DESIGN-SPEC §7): confidence chip, three interaction "
         "patterns by band, field-edit popover, identity-match card"),
        ("D5", "Sources view + first-run wizard", "HIGH", "Easy", "1.0", "NOT STARTED"),
        ("D6", "Destinations + recipes + mapping", "HIGH", "Moderate", "1.25", "NOT STARTED"),
        ("D7", "Rules builder", "MED", "Moderate", "1.25", "NOT STARTED — condition → action, no DSL"),
        ("D8", "Admin / Eval viewer", "MED", "Easy", "0.75",
         "NOT STARTED — accuracy drift per client"),
        ("D9", "Arabic / RTL and accessibility", "HIGH", "Moderate", "1.0",
         "NOT STARTED — DESIGN-SPEC §9 and §10, across all views"),
    ]))

story.extend(section(
    "Track E &mdash; What makes it sellable rather than working",
    "Needed because the goal is a product with several paying clients, not a pilot.",
    [
        ("E1", "Tenant onboarding", "HIGH", "Moderate", "1.0",
         "NOT BUILT — create a tenant, connect a number, seed properties, without touching SQL"),
        ("E2", "Usage metering and limits", "MED", "Easy", "0.75",
         "NOT BUILT — the usage_events table exists; nothing writes to it"),
        ("E3", "Billing (Stripe)", "MED", "Moderate", "1.5",
         "NOT STARTED — droppable to zero if the first clients are invoiced manually"),
        ("E4", "Observability", "HIGH", "Easy", "0.75",
         "NOT STARTED — structured logs, Sentry, uptime checks"),
        ("E5", "Backups and residency statement", "MED", "Easy", "0.5",
         "NOT STARTED — a restore drill, and the residency story the regulated tier is sold on"),
    ]))

story.append(PageBreak())

# ---------------------------------------------------------------- page 5
story.extend(section(
    "Track 3 &mdash; Integration and measurement",
    "Base360.ai is ruled out as a partner. Hostaway, Guesty and Cloudbeds all publish APIs.",
    [
        ("3.1", "PropertySystemPort plus the first adapter", "MED", "Moderate", "2.5",
         "PENDING — vendor-neutral read port and router exist (PR #14); needs the first concrete "
         "adapter. Blocks pricing, availability and door codes. Cache facts, never availability"),
        ("3.2", "End to end on a real number, then measure", "HIGH", "Moderate", "1.0",
         "PENDING — the point at which the eval number becomes real rather than recorded"),
        ("3.3", "Tuning tail", "MED", "Hard", "2.0",
         "PENDING — getting to working is weeks away; getting to trustworthy is measured, and that "
         "number is not fully under your control"),
    ]))

story.extend(section(
    "Runs in parallel &mdash; start today",
    "None of these are engineering. All of them can quietly become the reason a date slips.",
    [
        ("P1", "Pick the first client", "NOW", "Easy", "--",
         "NOT STARTED — decides which PMS adapter gets built first. Without it, 3.1 is a guess"),
        ("P2", "PMS sandbox keys", "HIGH", "Easy", "0.5",
         "NOT STARTED — public docs, but approval and rate limits are theirs to grant. Shape the "
         "port from what two or three of them offer in common"),
        ("P3", "File Meta business verification", "HIGH", "Easy", "1 hr",
         "NOT STARTED — v1.15 called this no longer blocking. At paid volume across several "
         "clients it binds again. File it now"),
        ("P4", "Graphify as a build aid (optional)", "LOW", "Easy", "0.5",
         "NOT STARTED — maps the repo for the coding agents. Local parsing, nothing leaves "
         "the machine"),
    ]))

story.append(PageBreak())

# ---------------------------------------------------------------- page 6
story.append(Paragraph("Completed tracks (kept for the record)", H2))
story.extend(section(
    "Track 0 &mdash; Today. &nbsp;[COMPLETE]", "",
    [
        ("0.1", "Set default branch to main", "NOW", "Trivial", "1 min", "DONE"),
        ("0.2", "Remove client name from golden set and fixtures", "NOW", "Easy", "0.25", "DONE"),
        ("0.3", "Decide the receptionist intent vocabulary", "NOW", "Easy", "1 hr",
         "DONE — 19 intents, 84 examples, 5 languages, 38 tests"),
        ("0.4", "Merge the eval branch", "NOW", "Trivial", "0.25", "DONE"),
        ("0.5", "Delete the stale nifty-johnson branch", "LOW", "Trivial", "1 min", "DONE"),
    ]))

story.extend(section(
    "Track 1 &mdash; Foundations. &nbsp;[COMPLETE]", "",
    [
        ("1.1", "Stop the core speaking one channel", "NOW", "Moderate", "1.5",
         "DONE — external_id / thread_id / sender_display_name, channel field, "
         "migration 002, KNOWN_LEAKS = {} with a boundary test holding it there"),
        ("1.2", "Port the four kept scaffold files", "HIGH", "Moderate", "1.0", "DONE"),
        ("1.3", "Python 3.12 to 3.13", "LOW", "Easy", "0.5",
         "DONE — 7 version pins across pyproject, packages and CI"),
    ]))

story.append(Paragraph("The dates (revised 15 August 2026)", H2))
dates = [[Paragraph(f"<b>{h}</b>", CELL) for h in ("Milestone", "v1.15 said", "Revised", "Status")]]
for milestone, original, revised, stat in (
    ("Eval merged, name leak fixed, vocabulary decided", "Day 1", "--", "DONE"),
    ("Core stops speaking one channel; scaffold ported", "Week 1", "--", "DONE"),
    ("Taxonomy unified, prompt v2, eval at 50", "14 Aug", "--", "DONE"),
    ("Prompt v3 &mdash; instructions a model can follow", "14 Aug", "--", "DONE"),
    ("<b>M1 &mdash; answers a real message on a real number</b>", "~2 days from 14 Aug",
     "~10 days", "NEXT — Track A + B + 2.4"),
    ("<b>M2 &mdash; you can watch and correct it</b>", "not scheduled", "~+11.5 days",
     "PENDING — Track D, all five views"),
    ("<b>M3 &mdash; sellable</b>", "~5 days from 14 Aug", "~+13.75 days",
     "PENDING — Track E + 3.1, blocked on P1"),
    ("Phone answering", "Week 4", "after M3", "PENDING — the speech-to-text seam already exists"),
):
    dates.append([
        Paragraph(milestone, CELL), Paragraph(original, NOTE),
        Paragraph(revised, NOTE), status(stat),
    ])
t = Table(dates, colWidths=[168, 74, 74, 140], repeatRows=1, hAlign="LEFT")
t.setStyle(TableStyle([
    ("BACKGROUND", (0, 0), (-1, 0), BAND),
    ("LINEBELOW", (0, 0), (-1, 0), 0.7, RULE),
    ("LINEBELOW", (0, 1), (-1, -2), 0.3, RULE),
    ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ("TOPPADDING", (0, 0), (-1, -1), 5),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ("LEFTPADDING", (0, 0), (-1, -1), 5),
]))
story.append(t)

story.append(Paragraph("What would actually move these dates", H2))
for head, text in (
    ("The gap was never technical difficulty &mdash; it was scope accounting.",
     "Every item in Tracks A, B, D and E is ordinary, well-understood work. None of it is hard in "
     "the way 2.1 was hard. It was simply never on the list, so the list said 5.75 days while the "
     "system could not start."),
    ("Supabase connection pooling.",
     "Transaction-mode pgbouncer and SQLAlchemy prepared statements do not get along. Settle it in "
     "A2 with a load test, not in production at 2am."),
    ("RLS retrofitted late.",
     "Adding row-level security after application queries exist surfaces as silent empty result "
     "sets rather than errors. B2 sits before the second tenant for that reason."),
    ("D2 is invisible in its own track title.",
     "&ldquo;Build the control page&rdquo; reads as frontend work; three of its days are backend "
     "endpoints that do not exist. It is the item most likely to be cut by accident."),
    ("Cost per message is still unmeasured.",
     "Nothing has called a model, so there is no unit economics figure. A3 makes it measurable and "
     "prompt caching is what keeps it small. Measure before quoting a price."),
    ("PMS API access.",
     "Docs are public, but sandbox approval and rate limits are theirs to grant. Start P2 today; "
     "it is the new version of the Meta-verification lesson."),
):
    story.append(Paragraph(f"<b>{head}</b> {text}", BODY))
    story.append(Spacer(1, 5))

story.append(Spacer(1, 4))
story.append(band(
    "<b>If you do only one thing next session:</b> start Track A. Not 2.4. The knowledge base was "
    "the right answer while the question was &ldquo;what does it lack?&rdquo; &mdash; but the "
    "receptionist cannot start, cannot reach a model and cannot send a reply, and no amount of "
    "knowledge changes that. A1 through A4 is the shortest path to a system that exists.",
    URG["NOW"]))

story.append(Paragraph("Session log", H2))
for label, text in (
    ("Session 1", "PR #13. Tests 228 &rarr; 248 (+20). Items 1.1, 1.3, 2.1, 2.2, 2.3 "
                  "= 6.5 engineering days."),
    ("Session 2", "PR #14 and #15. Tests 248 &rarr; 259 (+11). Items 2.5, 2.6 = 1.5 days."),
    ("Session 3", "PR #16. Tests 259 &rarr; 277 (+18). Prompt v3, ~0.5 days, unplanned."),
    ("Session 4", "Code audit against the roadmap. No application code changed. Four unpriced "
                  "tracks added; 2.1 and 2.2 downgraded to part-built; stack locked to Supabase + "
                  "Render + Clerk. See docs/LAUNCH-PLAN.md."),
    ("Cumulative", "<b>8.5 engineering days delivered. ~35.25 remaining.</b>"),
):
    story.append(Paragraph(f"<b>{label}:</b> {text}", NOTE))
    story.append(Spacer(1, 4))


# ---------------------------------------------------------------- chrome
def decorate(canvas, doc):
    canvas.saveState()
    w, h = A4
    canvas.setFillColor(ACCENT)
    canvas.rect(0, h - 12 * mm, w, 12 * mm, stroke=0, fill=1)
    canvas.setFont("Helvetica-Bold", 8)
    canvas.setFillColor(colors.white)
    canvas.drawString(20 * mm, h - 8 * mm, "WATCHER v2")
    canvas.setFont("Helvetica", 8)
    canvas.drawRightString(w - 20 * mm, h - 8 * mm, f"Build Roadmap  •  {VERSION}  •  {DATE}")
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.4)
    canvas.line(20 * mm, 15 * mm, w - 20 * mm, 15 * mm)
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(20 * mm, 11 * mm, "Planning document — estimates assume a six-day week")
    canvas.drawRightString(w - 20 * mm, 11 * mm, f"Page {doc.page}")
    canvas.restoreState()


doc = BaseDocTemplate(
    OUT, pagesize=A4,
    leftMargin=20 * mm, rightMargin=20 * mm,
    topMargin=18 * mm, bottomMargin=20 * mm,
    title=f"Watcher v2 - Build Roadmap {VERSION}", author="Watcher",
    subject="Roadmap with urgency, ease and status per work item",
)
frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="f")
doc.addPageTemplates([PageTemplate(id="main", frames=[frame], onPage=decorate)])
doc.build(story)
print("wrote", OUT)
