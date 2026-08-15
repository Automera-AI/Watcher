"""Generate the Watcher v2 roadmap PDF: urgency, ease, effort, per work item.

**v2.5 — 15 August 2026.** This generator is the single source of the roadmap PDF. Edit this file
and re-run it rather than producing another version by hand; v2.2 flagged that three roadmap
artifacts disagreed, and regenerating from here is what keeps that closed.

    python docs/make_roadmap.py     # needs reportlab
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
VERSION = "v2.5"
DATED = "15 August 2026"

INK = colors.HexColor("#12171f")
MUTED = colors.HexColor("#5b6675")
RULE = colors.HexColor("#d8dee7")
BAND = colors.HexColor("#f2f5f9")
ACCENT = colors.HexColor("#1f4e79")
DONE_GREEN = colors.HexColor("#1e8449")

URG = {
    "NOW": colors.HexColor("#c0392b"),
    "HIGH": colors.HexColor("#d67200"),
    "MED": colors.HexColor("#1f6fb2"),
    "LOW": colors.HexColor("#7b8794"),
    "DONE": DONE_GREEN,
}
EASE = {
    "Trivial": colors.HexColor("#1e8449"),
    "Easy": colors.HexColor("#4a9c48"),
    "Moderate": colors.HexColor("#c08a00"),
    "Hard": colors.HexColor("#b8442c"),
    "—": MUTED,
}

ss = getSampleStyleSheet()


def st(name, **kw):
    base = dict(fontName="Helvetica", fontSize=9, leading=12.5, textColor=INK, alignment=TA_LEFT)
    base.update(kw)
    return ParagraphStyle(name, **base)


BODY = st("body")
SMALL = st("small", fontSize=8, leading=10.5)
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


def banner(text, edge=ACCENT):
    t = Table([[Paragraph(text, BODY)]], colWidths=[456])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), BAND),
        ("LINEBEFORE", (0, 0), (0, -1), 2.5, edge),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return t


def track_table(rows):
    """rows: (num, work, urgency, ease, days, note)"""
    data = [[
        Paragraph("<b>#</b>", CELL), Paragraph("<b>Work item</b>", CELL),
        Paragraph("<b>Urgency</b>", CELL), Paragraph("<b>Ease</b>", CELL),
        Paragraph("<b>Days</b>", CELL), Paragraph("<b>Status</b>", CELL),
    ]]
    for n, w, u, e, d, note in rows:
        data.append([
            Paragraph(n, CELL), Paragraph(w, CELLB),
            chip(u, URG), chip(e, EASE),
            Paragraph(d, CELL), Paragraph(note, NOTE),
        ])
    t = Table(data, colWidths=[24, 118, 42, 48, 32, 192], repeatRows=1, hAlign="LEFT")
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


def two_col(left_title, right_title, left, right):
    t = Table([
        [Paragraph(f"<b>{left_title}</b>", CELL), Paragraph(f"<b>{right_title}</b>", CELL)],
        [Paragraph(left, CELL), Paragraph(right, CELL)],
    ], colWidths=[228, 228], hAlign="LEFT")
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


def notes_table(rows, first_col=112):
    t = Table([[Paragraph(f"<b>{a}</b>", CELL), Paragraph(b, NOTE)] for a, b in rows],
              colWidths=[first_col, 456 - first_col], hAlign="LEFT")
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -2), 0.3, RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def section(title, subtitle, rows):
    bits = [Paragraph(title, H2)]
    if subtitle:
        bits.append(Paragraph(subtitle, SUBT))
        bits.append(Spacer(1, 5))
    bits.append(track_table(rows))
    return bits


story = []

# ---------------------------------------------------------------- page 1
story.append(Paragraph("Watcher v2 &mdash; Build Roadmap", H1))
story.append(Paragraph(
    "From a message&nbsp;filer to a receptionist. Every item scored for urgency and ease. "
    f"&nbsp;&bull;&nbsp; <b>{VERSION}</b> &nbsp;&bull;&nbsp; {DATED} &nbsp;&bull;&nbsp; "
    "supersedes the v2.4 PDF of the same date", LEAD))
story.append(Spacer(1, 10))

story.append(banner(
    "<b>What this revision does.</b> v2.4 put the critical path outside Track&nbsp;A for the first "
    "time: host it, then make it safe. Session&nbsp;8 built the hosting half. <b>The schema now "
    "exists on a real database</b> (Supabase, eu-central-1, migrated and stamped), <b>tenant "
    "isolation is enforced by Postgres rather than by convention</b> (RLS forced on all 20 tables, "
    "behind a role that cannot bypass it), and <b>the API is a container image</b> at the path "
    "cd.yml has been waiting on since it was written. What is left of Track&nbsp;B is a card on "
    "the Render account, a domain, and the durable queue. "
    "<b>Remaining: ~34.5 engineering days.</b>"))

story.append(Paragraph("Where we stand today", H2))
story.append(two_col(
    "Built and tested", "Missing &mdash; nothing ships without these",
    "<b>417 passing tests</b>, no DB or network needed<br/>"
    "86 source files across 20 modules (125 with tests)<br/>"
    "19 DB tables + 4 migrations, <b>applied to a live database</b><br/>"
    "Every external system behind a swappable seam<br/>"
    "Eval runner + CI accuracy gate, 50 golden cases<br/>"
    "Receptionist vocabulary: 19 intents, data not code<br/>"
    "Prompt v3 &mdash; catalogue rendered from the vocabulary<br/>"
    "Channel-neutral envelope + chat/voice adapters<br/>"
    "Task state machine and the autonomy gate<br/>"
    "Typed config over every .env variable (A1)<br/>"
    "Anthropic + OpenAI on the LLM seam, caching on (A3)<br/>"
    f'<font color="{DONE_GREEN.hexval()}"><b>NEW</b></font> &bull; Engine + session scope, '
    "pooler-safe by default (A2)<br/>"
    f'<font color="{DONE_GREEN.hexval()}"><b>NEW</b></font> &bull; Composition root: the process '
    "starts and files a message end to end (A4)<br/>"
    f'<font color="{DONE_GREEN.hexval()}"><b>NEW</b></font> &bull; Conversation continuity: one '
    "task carried across turns, classifications recorded (A5)<br/>"
    "Outbound sender &mdash; the reply reaches the guest (A6)<br/>"
    f'<font color="{DONE_GREEN.hexval()}"><b>NEW</b></font> &bull; Supabase project, migrated, '
    "with the channel_configs row (B1)<br/>"
    f'<font color="{DONE_GREEN.hexval()}"><b>NEW</b></font> &bull; RLS forced on every table, '
    "per-transaction tenant GUC, cross-tenant test (B2)<br/>"
    f'<font color="{DONE_GREEN.hexval()}"><b>NEW</b></font> &bull; Container image; the project '
    "installs from pyproject (B3)",

    f'<font color="{DONE_GREEN.hexval()}"><b>No DB connection &mdash; done (A2)</b></font><br/>'
    f'<font color="{DONE_GREEN.hexval()}"><b>No entrypoint &mdash; done (A4)</b></font><br/>'
    f'<font color="{DONE_GREEN.hexval()}"><b>No memory across turns &mdash; done (A5)</b></font>'
    "<br/>"
    f'<font color="{DONE_GREEN.hexval()}"><b>No outbound &mdash; done (A6)</b></font><br/>'
    "<b>No emergency detection</b> &mdash; emergency=False is still hardcoded (G3)<br/>"
    "<b>No slot extraction</b> &mdash; the model emits no slots, so a task fills only by "
    "handing off (2.x)<br/>"
    f'<font color="{DONE_GREEN.hexval()}"><b>No database &mdash; done (B1)</b></font><br/>'
    f'<font color="{DONE_GREEN.hexval()}"><b>No RLS &mdash; done (B2)</b></font><br/>'
    f'<font color="{DONE_GREEN.hexval()}"><b>No Dockerfile &mdash; done (B3)</b></font><br/>'
    "<b>No running service</b> &mdash; Render wants billing details before it will create one, "
    "free plan included (B3)<br/>"
    "No public URL &mdash; so nothing can reach the webhook yet (B4)<br/>"
    "No control page &mdash; one CSS file, no package.json<br/>"
    "No control-page API &mdash; ~25 endpoints, none written<br/>"
    "Knowledge &mdash; zero tables, zero rows<br/>"
    "Live availability &mdash; no read path to any PMS<br/>"
    "A measured prompt &mdash; the gate replays v2 fixtures, so 88% is not v3's number"))

story.append(Paragraph("What changed in this revision (v2.4 &rarr; v2.5)", H2))
story.append(two_col(
    "Delivered", "Consequences",
    "<b>B1, B2 and B3 delivered</b> &mdash; 2.5 engineering days. "
    "Total 37&nbsp;&rarr;&nbsp;<b>34.5</b><br/>"
    "<b>Track B: 4.25&nbsp;&rarr;&nbsp;1.75 days</b><br/>"
    "Tests 406 &rarr; <b>417</b> (+11). Migration 004 is the first one nothing autogenerated<br/>"
    "<b>watcher_app</b> &mdash; the application connects as a role that cannot bypass RLS; "
    "Supabase's postgres role can, which would have made the policies decorative<br/>"
    "Every tenant-facing adapter now takes a TenantScope rather than a SessionScope",

    "<b>Tenant isolation is a property of the database</b> now, not of remembering the WHERE "
    "clause &mdash; the claim AGENTS.md calls non-negotiable is enforced and tested<br/>"
    "<b>Building the image found two defects the pinned test environment hides</b>: the intent "
    "vocabulary was missing from the wheel, and Starlette 1.0 removed add_event_handler<br/>"
    "<b>G3 is now the only thing between here and a number a guest can message</b> &mdash; B4 is "
    "half a day of DNS once a service exists<br/>"
    "<b>The Render service is blocked on billing, not engineering</b> &mdash; everything it needs "
    "is written down in the deploy runbook<br/>"
    "Unchanged: 2.4 is still the last item before a demo; 3.1 still blocked on P1; the "
    "scope guard still holds &mdash; no PDF handbook ingestion"))

story.append(Spacer(1, 10))
story.append(Paragraph(
    "<b>Totals:</b> ~34.5 engineering days remaining. At the observed 2&ndash;3 engineering days "
    "per working session that is <b>11&ndash;17 sessions</b>; at the six-day week these estimates "
    "assume, ~5.8 weeks of pure build &mdash; so plan <b>~7.5 weeks to sellable</b> and "
    "<b>~4.25 days to a real number that answers safely</b>. The critical path is now "
    "<b>G3 to make it safe, B4 to expose it, and 2.4</b> &mdash; G3 first, because the system "
    "already answers.",
    BODY))

story.append(PageBreak())

# ---------------------------------------------------------------- page 2
story.extend(section(
    "Track A &mdash; Make it run. &nbsp;[COMPLETE &mdash; ALL SIX DONE]",
    "Configuration, a database, a model, an entrypoint, continuity and a sender. The track that "
    "turned a library into a running receptionist is finished; what follows is hosting it safely.",
    [
        ("A1", "Configuration layer", "DONE", "Easy", "0.5",
         "<b>DONE</b> &mdash; core/config.py; every variable in .env.example typed, required per "
         "subsystem, placeholders read as absence, secrets as SecretStr"),
        ("A2", "DB engine and session", "DONE", "Easy", "0.5",
         "<b>DONE</b> &mdash; db/engine.py; engine, sessionmaker, session scope. Transaction-mode "
         "pooling assumed by default (NullPool + prepare_threshold=None), DATABASE_POOL_MODE opts "
         "out; a bare postgresql:// URI is rewritten onto psycopg 3; alembic connects the same way"),
        ("A3", "Concrete LLM providers", "DONE", "Moderate", "1.5",
         "<b>DONE</b> &mdash; Anthropic + OpenAI on the existing seam; system block marked "
         "cacheable; per-call usage reported, so cost is measurable"),
        ("A4", "Composition root", "DONE", "Moderate", "1.0",
         "<b>DONE</b> &mdash; apps/api/main.py, run as "
         "<font name='Courier'>uvicorn apps.api.main:create_application --factory</font>. Also the "
         "five database port implementations create_app needed and a process-level worker pool, so "
         "the webhook still answers before classification runs"),
        ("A5", "Wire continuity + excise v1 filer", "DONE", "Moderate", "2.25",
         "<b>DONE</b> &mdash; a ConversationStore in the message path: the inbound turn is "
         "recorded, the active task is loaded and saved back, the reply joins the transcript. "
         "process() is async; classifications is populated with measured latency and the prompt "
         "version; the rules/destinations threading is gone (D24), tables retained"),
        ("A6", "Outbound sender", "DONE", "Moderate", "1.0",
         "<b>DONE</b> &mdash; WhatsAppSender on the ChannelSender seam, quick replies rendered "
         "within the channel's limits, bounded retries. The credential fields moved behind "
         "channels/config.py and the last KNOWN_LEAKS entry is gone with them"),
    ]))

story.append(Spacer(1, 8))
story.append(banner(
    "<b>What Track A leaves behind.</b> Items 2.1 and 2.2 were genuinely written &mdash; the "
    "tables, the repository, the task state machine, the reply path &mdash; and the line joining "
    "them never was. A5 wrote that line and A6 wrote the wire. The milestone &ldquo;holds a "
    "conversation&rdquo; is now true, and the next thing between this and a real guest is not "
    "another feature: it is <b>B1&ndash;B4 to host it and G3 to make it safe</b>.",
    DONE_GREEN))

story.append(PageBreak())

story.extend(section(
    "Track B &mdash; Host it. &nbsp;[Supabase + Render]",
    "Stack locked 15 August 2026. B2 must land before a second client exists. B1 grew one "
    "precondition this session &mdash; see its note.",
    [
        ("B1", "Supabase project and migrations", "DONE", "Easy", "0",
         "<b>DONE</b> &mdash; watcher-prod in eu-central-1, migrations applied and stamped, one "
         "enabled channel_configs row. Its external_id is a placeholder until the Meta "
         "phone-number id replaces it &mdash; one UPDATE, in the deploy runbook"),
        ("B2", "Row-Level Security", "DONE", "Moderate", "0",
         "<b>DONE</b> &mdash; migration 004: enabled and forced on all 20 tables, policies on "
         "app.current_tenant, and a watcher_app role that cannot bypass them (Supabase's postgres "
         "role can, which is the trap). Verified cross-tenant on the live database"),
        ("B3", "Dockerfile and Render service", "NOW", "Easy", "0.25",
         "<b>IMAGE DONE</b> &mdash; apps/api/Dockerfile, which activates the cd.yml image job; "
         "the project installs from pyproject. <b>The Render service is blocked on billing "
         "details</b> (a card is required even on the free plan), not on engineering"),
        ("B4", "Domain, TLS, webhook subscription", "HIGH", "Easy", "0.5",
         "<b>NOT STARTED</b> &mdash; the platform needs a public HTTPS URL before anything arrives"),
        ("B5", "Durable queue (Redis + arq)", "MED", "Moderate", "1.0",
         "<b>NOT BUILT</b> &mdash; the in-process worker pool A4 ships loses in-flight "
         "classifications on every deploy. Persist-before-enqueue means a message is never lost, "
         "only its classification. Same seam, same consumer"),
    ]))

story.append(Spacer(1, 10))
story.append(banner(
    "<b>Runs in parallel &mdash; start today.</b> P1 pick the first client (NOW, blocks 3.1) "
    "&bull; P2 PMS sandbox keys (HIGH, 0.5d) &bull; P3 file the platform business verification "
    "(HIGH, 1 hr) &bull; P4 Graphify as a build aid (LOW, optional). Founder time, excluded from "
    "the total &mdash; and P2 and P3 are the two that make Track B's week land on time.",
    URG["HIGH"]))

story.append(PageBreak())

# ---------------------------------------------------------------- page 4
story.extend(section(
    "Track 2 &mdash; Make it a receptionist. &nbsp;[2.4, 2.7, 2.8 REMAIN]",
    "What remains is knowledge, a measured prompt, and more than one property.",
    [
        ("2.1", "Conversations, tasks and slot filling", "HIGH", "Hard", "spent",
         "<b>WIRED (A5)</b> &mdash; 2.0d spent here, the wiring priced in A5. Slot <i>extraction</i> "
         "is the one part still missing: the model emits no slots, so a task fills only by the "
         "clarifying-turn budget expiring. That is a prompt change and a golden-set change"),
        ("2.2", "The reply path", "HIGH", "Moderate", "spent",
         "<b>WIRED (A6)</b> &mdash; 1.5d spent here, the sender priced in A6"),
        ("2.3", "Autonomy gate", "DONE", "Easy", "1.0",
         "<b>DONE</b> &mdash; RECEPTIONIST_REPLY as the fourth RoutingAction"),
        ("2.4", "Knowledge base", "HIGH", "Moderate", "2.0",
         "<b>NEXT after Track A</b> &mdash; facts table with sensitivity flags, a real "
         "&ldquo;I don't know&rdquo; that fetches a human, verification codes"),
        ("2.5", "Prompt v2 and rewrite the golden set", "DONE", "Moderate", "1.0",
         "<b>DONE</b> &mdash; golden set 8 &rarr; 50 across all 19 intents"),
        ("2.6", "Intent taxonomy unification", "DONE", "Easy", "0.5",
         "<b>DONE</b> &mdash; 19 vocabulary-aligned intents"),
        ("2.7", "Re-record the eval under prompt v3", "MED", "Easy", "0.5",
         "<b>UNBLOCKED</b> &mdash; A3 landed; needs only a key. Add Franco-Arabic cases, and settle "
         "the two open numbers on the next page"),
        ("2.8", "Many properties per client", "HIGH", "Moderate", "1.0",
         "properties table, per-property fact scoping, message &rarr; property resolution"),
    ]))

story.append(Spacer(1, 8))
story.append(banner(
    "<b>The eval is still not measuring the prompt.</b> The gate runs and passes, but it replays "
    "fixtures keyed by message text and recorded under prompt v2, so it reports 88% whatever the "
    "prompt says. Until 2.7 runs, treat 0.88 as v2's number. baseline.json says so in the file, and "
    "correctly records claude-haiku-4-5-20251001 as the model that produced those fixtures.",
    URG["MED"]))

story.append(Paragraph("Tracks D, G, E and 3 &mdash; unchanged from v2.1", H2))
story.append(Paragraph("Summarised here; the v2.1 PDF carries the full per-item detail.", SUBT))
story.append(Spacer(1, 5))
story.append(notes_table([
    ("D &mdash; The control page (D0&ndash;D10) &nbsp; 13.75d",
     "Six receptionist views. <b>D2 (REST API, 3.0d) is the hidden half</b> &mdash; "
     "&ldquo;build the control page&rdquo; reads as frontend work; three of its days are backend "
     "endpoints that do not exist. Still the item most likely to be cut by accident"),
    ("G &mdash; Receptionist guardrails (G1&ndash;G4) &nbsp; 5.5d",
     "Not new features &mdash; these enforce rules intents.yaml states and schema.py validates and "
     "nothing in apps/ honours. <b>G3 (emergency path, 1.5d) is not optional before a real "
     "guest</b>: worker.py hardcodes emergency=False, so a gas leak files a maintenance ticket"),
    ("E &mdash; What makes it sellable (E1&ndash;E5) &nbsp; 4.5d",
     "Tenant onboarding, usage metering, billing, observability, backups and residency"),
    ("3 &mdash; Integration and measurement (3.1&ndash;3.3) &nbsp; 5.5d",
     "3.1 blocked on P1. Base360.ai ruled out; Hostaway, Guesty and Cloudbeds all publish APIs"),
], first_col=150))

story.append(PageBreak())

# ---------------------------------------------------------------- page 5
story.append(Paragraph("New this revision: what A5 and A6 settled, and what they left open", H2))
story.append(notes_table([
    ("Continuity",
     "Every classified message now runs against a <b>ConversationStore</b>: the inbound turn is "
     "recorded, the job already in flight is loaded, and the updated job and the reply are written "
     "back. A receptionist constructed without a store is <b>refused</b> &mdash; one that forgets "
     "the previous turn looks like it works, which is the exact failure this item existed to "
     "remove. ConversationRepository, written in item 2.1, is finally called by something other "
     "than its own tests"),
    ("The clarifying-turn budget",
     "Continuity introduces a failure mode that no-continuity does not: a task that cannot make "
     "progress no longer fails, it <b>loops</b>. The vocabulary has declared "
     "max_clarifying_turns&nbsp;=&nbsp;3 and on_max_turns&nbsp;=&nbsp;handoff_to_human since item "
     "0.3 and nothing read them. They are read now. The guard applies to asking, never to acting: "
     "a task with everything it needs still completes on the last turn of the budget"),
    ("The v1 filer",
     "The orchestrator used to answer an inbound message twice &mdash; auto-route it by rule or "
     "band, <i>and</i> reply to it. Routing to a spreadsheet was v1's answer; the receptionist is "
     "v2's. <b>Rules and destinations are gone from the message path (D24) and both tables are "
     "retained</b>, because deliberate routing by a human is a control-page feature and this is "
     "the evaluator it will use. AUTO_ROUTE went with them: there is nothing to route to"),
    ("classifications, at last",
     "The table sat empty for four sessions because two of its columns &mdash; latency_ms and "
     "prompt_version &mdash; had no honest source. The classifier reports them now rather than the "
     "writer inventing them: <b>latency spans the whole tiered policy</b>, retries and escalation "
     "included, because that is what the guest waited for. model_used, which used to be handed to "
     "the inbox writer and dropped, lives on that row, and inbox_items points at it"),
    ("The wire",
     "WhatsAppSender posts to the versioned Graph endpoint for the number we send as. The "
     "three-button cap and the 20-character title limit are applied there and nowhere else, and "
     "neither raises. <b>A failed send is logged and reported, never raised</b> &mdash; by then the "
     "message is classified, the reply recorded and the decision filed, and a transient 502 must "
     "not undo all of it. The reply is recorded <i>before</i> it is sent, deliberately"),
    ("Item 1.1, closed",
     "The channel credential fields moved from core/config.py to <b>channels/config.py</b>, which "
     "Settings extends: one object still reads one environment, but the knowledge of what a send "
     "needs sits with the channel. <b>KNOWN_LEAKS is now empty</b> &mdash; no core file names a "
     "channel &mdash; and the machinery stays, because an empty allowlist is the strongest form of "
     "that test and a phone line is next"),
    ("Still not written",
     "<b>Slot extraction.</b> The receptionist receives an empty slot dict because "
     "ClassificationResult has no slots field; adding one is a prompt change and a golden-set "
     "change, which is item 2.x. The consequence is bounded and honest: a task fills only by the "
     "budget expiring and then fetches a person. <b>Emergency detection is still hardcoded to "
     "False</b> &mdash; that is G3, and it is the last safety gap"),
], first_col=118))

story.append(Paragraph("Two numbers nobody has measured, both cheap to settle during 2.7", H2))
story.append(notes_table([
    ("1. The prompt's real token count",
     "~5k is a characters&divide;4 estimate. Haiku 4.5 will not cache a prefix below <b>4,096 "
     "tokens</b>, and the cheap tier is where caching pays for itself. The estimate clears the "
     "floor by ~30%, and the true count is probably higher (Arabic and Franco-Arabic tokenize "
     "worse than the approximation), so caching almost certainly activates &mdash; but if the "
     "vocabulary shrinks it stops silently: no error, just a larger bill. Sonnet 5's floor is "
     "1,024, so escalation is not exposed either way"),
    ("2. Cost per message",
     "Still unmeasured, but <b>measurable</b>: TokenUsage reports cached versus fresh input tokens "
     "per call, normalised across both providers. Measure before quoting a price"),
], first_col=118))

story.append(Paragraph("How the total reconciles", H2))
story.append(Paragraph(
    "A 0 + B 1.75 + D 13.75 + E 4.5 + G 5.5 + (2.4, 2.7, 2.8) 3.5 + (3.1, 3.2, 3.3) 5.5 = "
    "<b>34.5</b>", BODY))
story.append(Spacer(1, 4))
story.append(Paragraph(
    "Rows 2.1 and 2.2 show &ldquo;spent&rdquo; rather than a number because their days are already "
    "delivered and their wiring was priced in A5 and A6, now built &mdash; adding them would "
    "double-count 3.5 days. P1&ndash;P4 is excluded: founder time, not engineering. Counting scope "
    "loosely is what produced the 5.75 figure that v2.1 replaced, so the arithmetic is stated "
    "rather than implied.", NOTE))

story.append(Paragraph("The dates", H2))
dates = Table([
    [Paragraph("<b>Milestone</b>", CELL), Paragraph("<b>v2.4 said</b>", CELL),
     Paragraph("<b>Now</b>", CELL), Paragraph("<b>Status</b>", CELL)],
    [Paragraph("M1 &mdash; answers a real message, safely", CELLB),
     Paragraph("~6.75 days", CELL), Paragraph("<b>~4.25 days</b>", CELL),
     Paragraph("<b>NEXT</b> &mdash; G3 + B4 + 2.4. The database and the image are done; the "
               "emergency path is what &ldquo;safely&rdquo; still depends on", NOTE)],
    [Paragraph("M2 &mdash; you can watch and correct it", CELLB),
     Paragraph("+13.75 days", CELL), Paragraph("+13.75 days", CELL),
     Paragraph("PENDING &mdash; Track D, six receptionist views", NOTE)],
    [Paragraph("M3 &mdash; sellable", CELLB),
     Paragraph("+16.5 days", CELL), Paragraph("+16.5 days", CELL),
     Paragraph("PENDING &mdash; Tracks E, F and the rest of G. Blocked on P1", NOTE)],
    [Paragraph("Phone answering", CELLB),
     Paragraph("after M3", CELL), Paragraph("after M3", CELL),
     Paragraph("PENDING &mdash; the speech-to-text seam already exists", NOTE)],
], colWidths=[124, 62, 62, 208], hAlign="LEFT")
dates.setStyle(TableStyle([
    ("BACKGROUND", (0, 0), (-1, 0), BAND),
    ("LINEBELOW", (0, 0), (-1, 0), 0.7, RULE),
    ("LINEBELOW", (0, 1), (-1, -2), 0.3, RULE),
    ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ("TOPPADDING", (0, 0), (-1, -1), 5),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ("LEFTPADDING", (0, 0), (-1, -1), 5),
]))
story.append(dates)

story.append(PageBreak())

# ---------------------------------------------------------------- page 5
story.append(Paragraph("What would actually move these dates", H2))
story.append(Paragraph("The risks v2.3 named, re-scored against what session 7 learned.", SUBT))
story.append(Spacer(1, 5))
story.append(notes_table([
    ("Supabase connection pooling",
     "<b>Downgraded from imminent to unmeasured.</b> A2 assumes the transaction pooler and is safe "
     "there by default. What is not known is the cost of that safety &mdash; NullPool trades a "
     "handshake per checkout for correctness. Measure it during B1/B3 and flip "
     "DATABASE_POOL_MODE if session mode wins; do not settle it in production at 2am"),
    ("A missing channel_configs row",
     "<b>NEW.</b> The first deployed webhook will 500 on every message until B1 inserts one row per "
     "endpoint. It is a one-line fix and a genuinely confusing hour if nobody wrote it down, which "
     "is why it is in B1's status, the README and the spec"),
    ("RLS retrofitted late",
     "Unchanged. Adding row-level security after application queries exist surfaces as silent empty "
     "result sets rather than errors. B2 sits before the second tenant for that reason"),
    ("D2 is invisible in its own track title",
     "Unchanged, and still the most likely accidental cut. Three of Track D's days are backend"),
    ("Cost per message",
     "Unmeasured, not unknown. A3 made it measurable and turned prompt caching on. Measure before "
     "quoting a price"),
    ("PMS API access",
     "Unchanged. Docs are public, but sandbox approval and rate limits are theirs to grant. Start "
     "P2 today"),
    ("Three roadmap artifacts disagree",
     "<b>CLOSED.</b> docs/make_roadmap.py generates this document into "
     "docs/Watcher_v2_Roadmap.pdf. Edit the generator and re-run it; do not hand-write another "
     "version"),
    ("A receptionist that answers but cannot recognise an emergency",
     "<b>NEW, and the most serious item on this page.</b> Until A6 a gas leak was filed in silence; "
     "now it gets a polite, confident reply about maintenance. Answering raises the cost of "
     "emergency=False rather than lowering it, which is why <b>G3 moves ahead of 2.4</b> for any "
     "deployment a real guest can reach"),
    ("Two messages arriving at once",
     "<b>NEW, bounded.</b> Two turns in one thread classified concurrently can race and open two "
     "conversations. Per-message ordering is a property of the queue, and the queue is an "
     "in-process pool until <b>B5</b>, where ordered per-conversation delivery belongs. Recorded "
     "rather than papered over with a lock that would not survive a second process"),
], first_col=132))

story.append(Spacer(1, 10))
story.append(banner(
    "<b>If you do only one thing next session: B1, then G3.</b> Not 2.4, and not D. The "
    "receptionist now answers &mdash; on a laptop, to a number nobody can message. B1&ndash;B4 is "
    "3.25 days and ends with a public HTTPS URL the platform can deliver to (and B1 must insert "
    "the channel_configs row, or every message 500s). <b>G3 is 1.5 days and it is what makes "
    "answering safe</b>: emergency=False is hardcoded, so a gas leak now gets a confident reply "
    "about maintenance rather than a person. Answering made that worse, not better.",
    URG["NOW"]))

story.append(Paragraph("Session log", H2))
log = Table([
    [Paragraph("<b>Session</b>", CELL), Paragraph("<b>Delivered</b>", CELL),
     Paragraph("<b>Tests</b>", CELL), Paragraph("<b>Days</b>", CELL)],
    [Paragraph("1", CELL), Paragraph("PR #13 &mdash; items 1.1, 1.3, 2.1, 2.2, 2.3", NOTE),
     Paragraph("228 &rarr; 248", NOTE), Paragraph("6.5", CELL)],
    [Paragraph("2", CELL), Paragraph("PR #14 and #15 &mdash; items 2.5, 2.6", NOTE),
     Paragraph("248 &rarr; 259", NOTE), Paragraph("1.5", CELL)],
    [Paragraph("3", CELL), Paragraph("PR #16 &mdash; prompt v3, unplanned", NOTE),
     Paragraph("259 &rarr; 277", NOTE), Paragraph("~0.5", CELL)],
    [Paragraph("4", CELL),
     Paragraph("Code audit plus a design review. No application code changed. Five unpriced tracks "
               "added; 2.1/2.2 downgraded to part-built; emergency detection found unwired; the "
               "control page rebuilt around six receptionist views. Decisions D14&ndash;D26", NOTE),
     Paragraph("277", NOTE), Paragraph("&mdash;", CELL)],
    [Paragraph("5", CELL),
     Paragraph("PR #17 &mdash; items A1, A3. Config layer over all ~20 env vars; Anthropic + "
               "OpenAI providers with prompt caching and per-call usage; D8-a re-pinned to the "
               "Claude 5 family", NOTE),
     Paragraph("277 &rarr; 325", NOTE), Paragraph("2.0", CELL)],
    [Paragraph("6", CELL),
     Paragraph("PR #18 &mdash; items A2, A4. Engine and session scope, pooler-safe by default; the "
               "composition root and the five database port implementations it needed; a "
               "process-level worker pool so the webhook still answers before classification; "
               "alembic aligned to the same connection path", NOTE),
     Paragraph("325 &rarr; 375", NOTE), Paragraph("1.5", CELL)],
    [Paragraph("7", CELL),
     Paragraph("Items A5, A6 &mdash; Track A complete. Conversation and task continuity in "
               "the message path; the clarifying-turn budget honoured; classifications populated "
               "with measured telemetry; process() async; the v1 rules/destinations filer excised "
               "(D24); the outbound sender, and the channel credentials moved behind channels/, "
               "which emptied KNOWN_LEAKS and closed item 1.1", NOTE),
     Paragraph("375 &rarr; 406", NOTE), Paragraph("3.25", CELL)],
    [Paragraph("8", CELLB),
     Paragraph("<b>Items B1, B2, B3.</b> Supabase project provisioned, migrated and stamped, with "
               "the channel_configs row tenant resolution raises without; migration 004 &mdash; "
               "RLS enabled and forced on all 20 tables behind a watcher_app role that cannot "
               "bypass it, a per-transaction tenant GUC carried by every adapter, and one narrow "
               "SELECT-only exception for the endpoint lookup that runs before a tenant is known; "
               "the container image, whose first build exposed a missing package data file and a "
               "removed Starlette API", NOTE),
     Paragraph("<b>406 &rarr; 417</b>", NOTE), Paragraph("2.5", CELLB)],
], colWidths=[44, 296, 68, 48], hAlign="LEFT")
log.setStyle(TableStyle([
    ("BACKGROUND", (0, 0), (-1, 0), BAND),
    ("LINEBELOW", (0, 0), (-1, 0), 0.7, RULE),
    ("LINEBELOW", (0, 1), (-1, -2), 0.3, RULE),
    ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ("TOPPADDING", (0, 0), (-1, -1), 5),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ("LEFTPADDING", (0, 0), (-1, -1), 5),
    ("ALIGN", (3, 0), (3, -1), "CENTER"),
]))
story.append(log)
story.append(Spacer(1, 6))
story.append(Paragraph(
    "<b>Cumulative: 17.75 engineering days delivered. ~34.5 remaining.</b>", SMALL))


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
    canvas.drawRightString(w - 20 * mm, h - 8 * mm, f"Build Roadmap    {VERSION}    2026-08-15")
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.4)
    canvas.line(20 * mm, 15 * mm, w - 20 * mm, 15 * mm)
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(20 * mm, 11 * mm,
                      "Planning document — supersedes v2.2; estimates assume a six-day week")
    canvas.drawRightString(w - 20 * mm, 11 * mm, f"Page {doc.page}")
    canvas.restoreState()


doc = BaseDocTemplate(
    OUT, pagesize=A4,
    leftMargin=20 * mm, rightMargin=20 * mm,
    topMargin=18 * mm, bottomMargin=20 * mm,
    title=f"Watcher v2 - Build Roadmap {VERSION}", author="Watcher",
    subject="Roadmap with urgency and implementation ease per work item",
)
frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="f")
doc.addPageTemplates([PageTemplate(id="main", frames=[frame], onPage=decorate)])
doc.build(story)
print("wrote", OUT)
