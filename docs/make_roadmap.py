"""Generate the Watcher v2 roadmap PDF: urgency, ease, effort, per work item.

**v2.13 — 23 August 2026.** This generator is the single source of the roadmap PDF. Edit this
file and re-run it rather than producing another version by hand; v2.2 flagged that three roadmap
artifacts disagreed, and regenerating from here is what keeps that closed.

**Scope note.** v2.12 reconciled B5 (the durable queue) onto main after 2.4, and re-opened B4's
webhook subscription claim after a direct check found zero traffic. v2.13 records what session 12
verified against the live systems and then closed: the arq **worker service now exists** — a paid
Render Background Worker, deployed and consuming — so **B5 is fully done**, not half-provisioned;
and B4's webhook subscription and operator number are taken as completed operator-side (the one
sub-item still open is moving watcher-api off the free plan), so **B4 is done bar that upgrade**.
Migration 005 (the facts table, 2.4) was also found still unapplied to production this session and
**applied** — the live schema is now at 005_facts, so answer_from_knowledge no longer hits a
missing table. Track 2 work (2.7, 2.8) begins on this base.

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
VERSION = "v2.13"
DATED = "23 August 2026"
STAMP = "2026-08-23"

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

NEW = f'<font color="{DONE_GREEN.hexval()}"><b>NEW</b></font> &bull; '


def done(text):
    return f'<font color="{DONE_GREEN.hexval()}"><b>{text}</b></font>'


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
    "supersedes the v2.12 PDF", LEAD))
story.append(Spacer(1, 10))

story.append(banner(
    "<b>What this revision does.</b> v2.11 delivered 2.4; v2.12 reconciled B5's durable queue onto "
    "main and re-opened B4's webhook claim after a direct check found zero traffic. <b>This "
    "revision closes what those left open, verified against the live systems.</b> First, <b>B5 is "
    "now fully done</b>: the missing piece &mdash; the arq worker service &mdash; exists as "
    "<b>watcher-worker</b>, a paid Render Background Worker, deployed and live on current main, its "
    "own logs confirming it started for consume_message and connected to Redis; watcher-redis backs "
    "it. Second, <b>B4 is done bar one item</b>: the Meta webhook subscription and the operator "
    "number are completed operator-side, closing the two checks session 11 re-opened, and the "
    "custom domain and both Meta credentials remain confirmed &mdash; the single sub-item still "
    "open is moving watcher-api off the Render free plan, so a cold start no longer reads to Meta "
    "as a timeout. Third, migration <b>005 (the facts table) was found still unapplied to "
    "production and applied</b> this session &mdash; the live schema is now 005_facts, so "
    "answer_from_knowledge no longer risks a missing-table error on the first real question. "
    "Fourth, on Track 2, <b>2.8 (many properties per client) is delivered</b> and 2.7 is started "
    "&mdash; a properties table, per-property fact scoping, and message&nbsp;&rarr;&nbsp;property "
    "resolution, 521&nbsp;&rarr;&nbsp;535 tests. <b>Remaining: ~28.5 engineering days.</b>"))

story.append(Paragraph("Where we stand today", H2))
story.append(two_col(
    "Built and tested", "Missing &mdash; nothing ships without these",
    "<b>535 passing tests</b>, no DB or network needed<br/>"
    "91 source files across 20 modules (133 with tests)<br/>"
    "21 DB tables + 6 migrations &mdash; through 005 <b>applied to the live database</b>; 006 "
    "(properties) applies on deploy<br/>"
    "Every external system behind a swappable seam<br/>"
    "Eval runner + CI accuracy gate, 50 golden cases<br/>"
    "Receptionist vocabulary: 19 intents, data not code<br/>"
    "Prompt v3 &mdash; catalogue rendered from the vocabulary<br/>"
    "Channel-neutral envelope + chat/voice adapters<br/>"
    "Task state machine and the autonomy gate<br/>"
    "Typed config over every .env variable (A1)<br/>"
    "Anthropic + OpenAI on the LLM seam, caching on (A3)<br/>"
    "Engine + session scope, pooler-safe by default (A2)<br/>"
    "Composition root: the process starts and files a message end to end (A4)<br/>"
    "Conversation continuity: one task carried across turns, classifications recorded (A5)<br/>"
    "Outbound sender &mdash; the reply reaches the guest (A6)<br/>"
    "Supabase project, migrated, with the channel_configs row (B1)<br/>"
    "RLS forced on every table, per-transaction tenant GUC, cross-tenant test (B2)<br/>"
    "Container image; the project installs from pyproject (B3)<br/>"
    "Deployed and serving on Render, Frankfurt (B3)<br/>"
    "Emergency detection &mdash; the declared triggers matched before the classifier, in two "
    "scripts and Franco-Arabic (G3)<br/>"
    "The alert path &mdash; an operator seam, an implementation, and an honest report of which "
    "channel was used (G3)<br/>"
    "<b>Knowledge base</b> &mdash; a facts table behind RLS, fuzzy-matched against a "
    "guest's own words, a sensitivity flag a fact can carry, and a real &ldquo;I don't "
    "know&rdquo; (2.4)<br/>"
    "<b>Durable queue</b> &mdash; a fourth ClassificationQueue transport on arq/Redis, "
    "an arq worker as its own composition root, and one shared graph-builder so the worker "
    "and the API can't drift (B5)<br/>"
    f"{NEW}<b>Deployed and consuming</b> &mdash; watcher-worker (a paid Render Background Worker) "
    "and watcher-redis both live; the worker's logs confirm it started for consume_message and "
    "connected to Redis (B5)<br/>"
    f"{NEW}<b>facts table live in production</b> &mdash; migration 005 applied to the Supabase DB "
    "this session, RLS forced, schema now at 005_facts (2.4)",

    f'{done("No DB connection &mdash; done (A2)")}<br/>'
    f'{done("No entrypoint &mdash; done (A4)")}<br/>'
    f'{done("No memory across turns &mdash; done (A5)")}<br/>'
    f'{done("No outbound &mdash; done (A6)")}<br/>'
    f'{done("No emergency detection &mdash; done (G3)")}<br/>'
    "<b>No slot extraction</b> &mdash; the model emits no slots, so a task fills only by "
    "handing off (2.x)<br/>"
    f'{done("No database &mdash; done (B1)")}<br/>'
    f'{done("No RLS &mdash; done (B2)")}<br/>'
    f'{done("No Dockerfile &mdash; done (B3)")}<br/>'
    f'{done("No running service &mdash; done (B3)")}<br/>'
    f'{done("Knowledge: zero tables, zero rows &mdash; table live, migration 005 applied (2.4)")}<br/>'
    f'{done("No outbound credentials &mdash; set on Render, confirmed by a clean redeploy, not by a test send (B4)")}<br/>'
    f'{done("No worker service &mdash; watcher-worker live, a paid Background Worker consuming (B5)")}<br/>'
    f'{done("No operator number &mdash; CONTROL_CHAT_PHONE_E164 completed operator-side (B4)")}<br/>'
    f'{done("No webhook subscription &mdash; completed operator-side; first inbound message is the proof to watch for (B4)")}<br/>'
    "<b>Still on the free Render plan</b> &mdash; the one B4 item left: watcher-api sleeps after "
    "~15 minutes and a 30&ndash;60s cold start reads to Meta as a timeout, so the warm-instance "
    "half of B4 needs the paid upgrade<br/>"
    "<b>Narrow trigger phrases</b> &mdash; the detector matches what an operator wrote down and "
    "does not paraphrase; &ldquo;I smell gas&rdquo; matches neither declared gas phrase. One "
    "line of YAML, and it is the operator's line<br/>"
    "The database path is unproven &mdash; SQLAlchemy connects lazily, so the pooler host is "
    "tested by the first message, not by startup<br/>"
    "No control page &mdash; one CSS file, no package.json<br/>"
    "No control-page API &mdash; ~25 endpoints, none written<br/>"
    "Live availability &mdash; no read path to any PMS<br/>"
    "A measured prompt &mdash; the gate replays v2 fixtures, so 88% is not v3's number"))

story.append(PageBreak())

# ---------------------------------------------------------------- page 2
story.append(Paragraph("What changed in this revision (v2.12 &rarr; v2.13)", H2))
story.append(two_col(
    "Delivered", "Consequences",
    "<b>B5 fully done</b> (session 12) &mdash; the worker service, the one piece open at the end "
    "of session 11, now exists. watcher-worker is a paid Render Background Worker running "
    "arq apps.api.worker.WorkerSettings, deployed and live on current main; its logs confirm it "
    "started for consume_message and connected to Redis. Verified via the Render MCP<br/>"
    "<b>B4 done bar the plan upgrade</b> &mdash; the Meta webhook subscription and the operator "
    "number (CONTROL_CHAT_PHONE_E164) are completed operator-side, closing the two checks session "
    "11 re-opened. The custom domain and both Meta credentials stay confirmed<br/>"
    "<b>Migration 005 applied to production</b> &mdash; found still at 004 this session and "
    "brought to 005_facts: the facts table now exists behind forced RLS, with the tenant-isolation "
    "policy and the watcher_app grant, so answer_from_knowledge can no longer hit a missing table "
    "on the first real property_question<br/>"
    "<b>2.8 delivered</b> (session 12) &mdash; a properties table (migration 006), per-property "
    "fact scoping, and message&nbsp;&rarr;&nbsp;property resolution. Track 2 is down to 2.7<br/>"
    "<b>2.7 started</b> &mdash; scripts/measure_prompt.py settles the prompt's size (21,777 chars, "
    "clear of Haiku's cache floor) and prints the exact token count and cost when a key is present; "
    "the accuracy re-record and Franco-Arabic cases are what still need one<br/>"
    "<b>Track B: 0.5&nbsp;&rarr;&nbsp;0.25 days</b> &mdash; B5 done, B4 down to the paid-plan "
    "upgrade. With 2.8, total 29.75&nbsp;&rarr;&nbsp;<b>28.5</b><br/>"
    "Tests <b>521&nbsp;&rarr;&nbsp;535</b> (2.8's properties, resolver and scoped search)",

    "<b>M1 &mdash; answers a real message, safely &mdash; is within one operator action:</b> the "
    "paid Render upgrade. Everything else on B4 is reported done; the first real inbound message "
    "producing a messages row (or a POST /webhook 200 in the logs) is the positive proof still "
    "worth watching for, since at check time both were still empty<br/>"
    "<b>The queue is real end to end now, not just in tests.</b> A message the API enqueues to "
    "Redis will survive a deploy and be picked up by watcher-worker &mdash; provided REDIS_URL is "
    "set on watcher-api to switch it to the producer branch, which stays a deliberate "
    "confirm-the-worker-consumes-first step<br/>"
    "<b>A sensitive fact is narrowly gated, not fully gated.</b> An unverified match to a sensitive "
    "fact is refused &mdash; the same &ldquo;I don't know&rdquo; as a genuine miss &mdash; but that "
    "is one tool refusing to guess, not G1's reply-path-wide disclosure gate, still open<br/>"
    "<b>A client can be an agency now, not one flat.</b> A fact scopes to a unit or stays "
    "tenant-wide (2.8); a guest on a shared number gets the tenant-wide facts, never another "
    "unit's, until a per-message property signal exists &mdash; the endpoint hint is wired and "
    "tested, the booking-based one waits on 3.1. Migration 006 applies on deploy, like 005<br/>"
    "Unchanged: 3.1 still blocked on P1; slot extraction (item 2.x) is still unwritten; the eval "
    "still replays v2 fixtures, which 2.7's re-record is what changes &mdash; and it needs a key"))

story.append(Spacer(1, 10))
story.append(Paragraph(
    "<b>Totals:</b> ~28.5 engineering days remaining. At the observed 2&ndash;3 engineering days "
    "per working session that is <b>~10&ndash;14 sessions</b>; at the six-day week these estimates "
    "assume, ~4.75 weeks of pure build &mdash; so plan <b>~7 weeks to sellable</b>. The critical "
    "path is <b>B4's last item alone</b> &mdash; the paid Render upgrade &mdash; which is "
    "operational, not engineering; with the subscription, the operator number and the worker all "
    "reported or verified done, M1 is one billing action away.",
    BODY))

story.append(Spacer(1, 8))
story.append(banner(
    "<b>What is left in front of a pilot.</b> 2.4 closed the receptionist's silence &mdash; it now "
    "answers property questions on real data, and the facts table is live in production as of this "
    "session. B5's queue is deployed and consuming, and B4's checklist is down to one line: move "
    "watcher-api off the free plan so a cold start doesn't read to Meta as a timeout. The single "
    "positive proof still worth watching for is the first real inbound message landing a row in "
    "messages.",
    DONE_GREEN))

story.append(PageBreak())

# ---------------------------------------------------------------- page 3
story.extend(section(
    "Track A &mdash; Make it run. &nbsp;[COMPLETE &mdash; ALL SIX DONE]",
    "Configuration, a database, a model, an entrypoint, continuity and a sender. The track that "
    "turned a library into a running receptionist is finished; what follows is hosting it safely.",
    [
        ("A1", "Configuration layer", "DONE", "Easy", "0.5",
         "<b>DONE</b> &mdash; core/config.py; every variable in .env.example typed, required per "
         "subsystem, placeholders read as absence, secrets as SecretStr. G3 added one field: "
         "TENANT_TIMEZONE, and it is the one defaulted value this layer validates eagerly"),
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
    "them never was. A5 wrote that line and A6 wrote the wire. G3 has now made answering safe, so "
    "the milestone is no longer &ldquo;holds a conversation&rdquo; but <b>holds a conversation it "
    "is allowed to be having</b>. As of 2.4 it also has something true to say. What is between "
    "this and a real guest is B4.",
    DONE_GREEN))

story.append(PageBreak())

# ---------------------------------------------------------------- page 4
story.extend(section(
    "Track B &mdash; Host it. &nbsp;[Supabase + Render]",
    "Stack locked 15 August 2026. B2 landed before a second client exists. B5 is fully done as of "
    "this session &mdash; the worker service now exists; B4 is done bar the paid-plan upgrade "
    "&mdash; see their notes.",
    [
        ("B1", "Supabase project and migrations", "DONE", "Easy", "0",
         "<b>DONE</b> &mdash; watcher-prod in eu-central-1, migrations applied and stamped, one "
         "enabled channel_configs row. Its external_id was a placeholder until session 10, when "
         "it was replaced with the real 16-digit Meta phone-number id by a live UPDATE, verified "
         "by reading the row back"),
        ("B2", "Row-Level Security", "DONE", "Moderate", "0",
         "<b>DONE</b> &mdash; migration 004: enabled and forced on all 20 tables, policies on "
         "app.current_tenant, and a watcher_app role that cannot bypass them (Supabase's postgres "
         "role can, which is the trap). Verified cross-tenant on the live database"),
        ("B3", "Dockerfile and Render service", "DONE", "Easy", "0",
         "<b>DONE</b> &mdash; apps/api/Dockerfile, which activates the cd.yml image job, and the "
         "service is live in Frankfurt. WHATSAPP_ACCESS_TOKEN and WHATSAPP_PHONE_NUMBER_ID were "
         "set on Render in session 10, confirmed by a clean redeploy &mdash; not yet by an actual "
         "outbound send. Still on the free plan"),
        ("B4", "Webhook subscription and a warm instance", "NOW", "Easy", "0.25",
         "<b>DONE bar the plan upgrade</b> &mdash; the Meta webhook subscription and the operator "
         "number (CONTROL_CHAT_PHONE_E164) are completed operator-side, closing the two items "
         "session 11's direct check had re-opened. What is independently confirmed this session: "
         "webhook.automera.co is the service's primary domain (Render's deploy log names it), and "
         "the app starts clean with META_APP_SECRET and META_WEBHOOK_VERIFY_TOKEN both set. <b>One "
         "operational sub-item remains: watcher-api is still on the Render free plan</b> (confirmed "
         "via the Render MCP this session) &mdash; the free one sleeps after ~15 minutes and a "
         "30&ndash;60s cold start reads to Meta as a timeout, so the upgrade is what makes the "
         "warm-instance half of this item real. Note for whoever picks this up: at check time the "
         "messages table still had zero rows and /webhook zero recorded hits, so the first real "
         "inbound message is still the positive proof to watch for"),
        ("B5", "Durable queue (Redis + arq)", "DONE", "Moderate", "0",
         "<b>DONE</b> &mdash; RedisClassificationQueue as a fourth ClassificationQueue transport, "
         "apps/api/worker.py as its arq consumer, and orchestration/composition.build_consumer as "
         "the one graph-builder both the API and the worker call, so the knowledge base (2.4) and "
         "everything else can't drift between them. Reconciled onto main after 2.4 landed. The "
         "provisioning half, open at the end of session 11, is now closed: watcher-redis (a Render "
         "Key Value instance in Frankfurt) and <b>watcher-worker</b> &mdash; a paid Background "
         "Worker running arq apps.api.worker.WorkerSettings &mdash; both exist and are live, the "
         "worker's own logs confirming it started for one function (consume_message) and connected "
         "to Redis. Verified via the Render MCP this session"),
    ]))

story.append(Spacer(1, 10))
story.append(banner(
    "<b>Runs in parallel &mdash; start today.</b> P1 pick the first client (NOW, blocks 3.1) "
    "&bull; P2 PMS sandbox keys (HIGH, 0.5d) &bull; P3 file the platform business verification "
    "(HIGH, 1 hr) &bull; P4 Graphify as a build aid (LOW, optional). Founder time, excluded from "
    "the total &mdash; and P2 and P3 are the two that make Track B's week land on time. "
    "<b>P5 is new and it is 30 minutes:</b> widen the emergency trigger phrases in intents.yaml. "
    "It changes no code, moves no baseline, and it is the cheapest safety work on the board."))

story.append(PageBreak())

# ---------------------------------------------------------------- page 5
story.extend(section(
    "Track 2 &mdash; Make it a receptionist. &nbsp;[2.7 REMAINS]",
    "2.8 landed this session; what remains is a measured prompt (2.7), and its re-record needs a key.",
    [
        ("2.1", "Conversations, tasks and slot filling", "HIGH", "Hard", "spent",
         "<b>WIRED (A5)</b> &mdash; 2.0d spent here, the wiring priced in A5. Slot extraction is "
         "the one part still missing: the model emits no slots, so a task fills only by the "
         "clarifying-turn budget expiring. That is a prompt change and a golden-set change"),
        ("2.2", "The reply path", "HIGH", "Moderate", "spent",
         "<b>WIRED (A6)</b> &mdash; 1.5d spent here, the sender priced in A6"),
        ("2.3", "Autonomy gate", "DONE", "Easy", "1.0",
         "<b>DONE</b> &mdash; RECEPTIONIST_REPLY as the fourth RoutingAction. G3 added a fifth, "
         "EMERGENCY, which is deliberately not a variety of handoff: it was never classified"),
        ("2.4", "Knowledge base", "DONE", "Moderate", "spent",
         "<b>DONE</b> &mdash; a facts table (migration 005) behind row-level security, matched "
         "against a guest's own words with a stopword-stripped rapidfuzz score (D35), a "
         "sensitive flag a fact can carry (D36), and answer_from_knowledge actually dispatched "
         "from the receptionist for the first time &mdash; every other terminal_tool still gets "
         "the old placeholder (roadmap 3.1). Door codes, key codes and unit numbers are excluded "
         "from the table by design, not merely unmarked. Tested against a curated, committed "
         "export of one real property. 486 &rarr; 506 tests; decisions D35&ndash;D37"),
        ("2.5", "Prompt v2 and rewrite the golden set", "DONE", "Moderate", "1.0",
         "<b>DONE</b> &mdash; golden set 8 &rarr; 50 across all 19 intents"),
        ("2.6", "Intent taxonomy unification", "DONE", "Easy", "0.5",
         "<b>DONE</b> &mdash; 19 vocabulary-aligned intents"),
        ("2.7", "Re-record the eval under prompt v3", "MED", "Easy", "0.5",
         "<b>STARTED &mdash; one step still needs a key.</b> scripts/measure_prompt.py (this "
         "session) settles the two numbers on page 9: the system prompt is 21,777 characters, "
         "~5.4k tokens by the chars&divide;4 estimate &mdash; clear of Haiku 4.5's 4,096-token "
         "cache floor, so the cacheable prefix does cache &mdash; and the tool prints the exact "
         "count (via count_tokens) and the per-message cost the moment a key is present. What is "
         "left is the re-record itself: running the classifier over the 50-case golden set against "
         "a live model to regenerate recorded_haiku.jsonl and replace 0.88 (still v2's number) with "
         "the real v3 one, plus adding Franco-Arabic golden cases &mdash; both need the fixtures a "
         "live run produces, so both wait on a key in the run environment (set on Render; not in "
         "this sandbox). G3 does not move the number: an emergency bypasses the classifier"),
        ("2.8", "Many properties per client", "DONE", "Moderate", "1.0",
         "<b>DONE (code)</b> &mdash; a properties table (migration 006) behind the same forced RLS "
         "every tenant table carries, a nullable facts.property_id that scopes a fact to one unit "
         "(NULL = tenant-wide, the common case), and message &rarr; property resolution "
         "(core/property.py): an explicit endpoint hint wins, else a single-property tenant "
         "resolves to its one unit, else tenant-wide facts only &mdash; a guest on a shared number "
         "is never answered from the wrong unit's sheet. The knowledge lookup returns the resolved "
         "property's facts plus the tenant-wide ones. 521 &rarr; 535 tests. Migration 006 applies "
         "to production on deploy, the same step 005 needed (D5's editing UI carries the "
         "door/key/unit-code exclusion forward when it lands)"),
    ]))

story.append(Spacer(1, 8))
story.append(banner(
    "<b>2.4's sensitivity flag is not G1.</b> A fact marked sensitive is withheld from an "
    "unverified guest &mdash; treated exactly like a genuine &ldquo;I don't know&rdquo; &mdash; "
    "but that is one tool refusing to guess, not the reply-path-wide disclosure gate G1 still has "
    "to build. And a door code, a key box code or a unit number does not go into the facts table "
    "at all, sensitive or not: intents.yaml forbids answer_from_knowledge from ever giving one "
    "out, and that is a table an operator (or D5, once it exists) populates by hand.",
    DONE_GREEN))

story.append(Spacer(1, 8))
story.append(banner(
    "<b>The eval is still not measuring the prompt.</b> The gate runs and passes, but it replays "
    "fixtures keyed by message text and recorded under prompt v2, so it reports 88% whatever the "
    "prompt says. Until 2.7's re-record runs against a live model, treat 0.88 as v2's number. "
    "baseline.json says so in the file, and correctly records claude-haiku-4-5-20251001 as the "
    "model that produced those fixtures. scripts/measure_prompt.py (2.7) settled the prompt's size "
    "&mdash; 21,777 characters &mdash; but the accuracy re-record still needs a key.",
    URG["MED"]))

story.append(PageBreak())

# ---------------------------------------------------------------- page 6
story.extend(section(
    "Track G &mdash; Receptionist guardrails &nbsp; 4.0d",
    "Not new features. Every one of these enforces a rule that intents.yaml already states and "
    "schema.py already validates, and that nothing in apps/ honours &mdash; the vocabulary is "
    "obeyed when composing a reply and ignored when deciding whether to send one. <b>G3 is done, "
    "and with it this track leaves the critical path.</b>",
    [
        ("G3", "Emergency detection and the alert path", "DONE", "Moderate", "0",
         "<b>DONE</b> &mdash; core/emergency.py matches the declared triggers before the "
         "classifier and after the media pipeline; the guest is answered immediately in English "
         "and Arabic; the item is filed NEEDS_REVIEW at the top band; an OperatorAlerter seam "
         "reaches a person and reports which channel it used, because the vocabulary asks for a "
         "phone call nothing wired can place. An emergency is never classified. 417 &rarr; 486 "
         "tests; decisions D30&ndash;D34; docs/specs/g3-emergency-path.md"),
        ("G1", "Sensitivity and disclosure gate", "HIGH", "Moderate", "1.5",
         "Facts now carry a sensitivity flag and answer_from_knowledge refuses one to an "
         "unverified guest (2.4) &mdash; but that is one tool, not the reply-path-wide gate: "
         "money and owner matters still reach a confident model with no ceiling, and the "
         "identity-verified flag on conversations is read in exactly one place rather than "
         "everywhere the vocabulary implies it should be"),
        ("G2", "Autonomy ceilings in the reply path", "HIGH", "Easy", "1.0",
         "Money and owner matters must reach a human before confidence is consulted. The gate "
         "exists (2.3) and the vocabulary declares per-intent ceilings; what is missing is the "
         "enforcement that a ceiling cannot be raised by a confident model"),
        ("G4", "Anti-loop and abuse controls", "MED", "Moderate", "1.5",
         "The clarifying-turn budget bounds one task. It does not bound a guest who sends forty "
         "messages, a channel that redelivers, or two tasks that hand off to each other. G3 added "
         "one instance of this deliberately: five messages about the same fire raise five alerts, "
         "which is the right default for a safety path and the wrong one for an operator's phone"),
    ]))

story.append(Spacer(1, 8))
story.append(notes_table([
    ("What G3 settled",
     "<b>The ordering.</b> intents.yaml has said &ldquo;checked before intent, before confidence, "
     "before anything&rdquo; since item 0.3, and that is now literally where the check is. A model "
     "is a network round trip that can be slow, wrong or down, and none of that may stand between "
     "a guest saying <i>smell of gas</i> and an operator"),
    ("What it left open, on purpose",
     "<b>phone_call_to_operator is not satisfied.</b> The alerter delivers a message and says so "
     "&mdash; AlertOutcome.channel beside EmergencyAlert.requested_channel, false today, logged "
     "per emergency and once at startup. A voice alerter is a new implementation of the same "
     "protocol and needs the same dependency as the phone channel"),
    ("What is the operator's to do",
     "<b>Widen the trigger phrases.</b> The detector matches what is written down and does not "
     "paraphrase, which is what makes intents.yaml reviewable by the person carrying the "
     "consequences &mdash; and it means &ldquo;I smell gas&rdquo; matches neither declared gas "
     "phrase. One line of YAML; it touches neither the prompt nor the eval baseline"),
], first_col=132))

story.append(PageBreak())

# ---------------------------------------------------------------- page 7
story.extend(section(
    "Track D &mdash; The control page &nbsp; 13.75d",
    "The largest remaining track, and the one whose shape is most often misread. D2 is three "
    "backend days inside an item everyone reads as frontend work &mdash; the views cannot be built "
    "against endpoints that do not exist. Views follow DESIGN-SPEC §8, re-ordered for the "
    "receptionist: what the product does now is hold conversations, not file messages.",
    [
        ("D0", "Frontend scaffold and design tokens", "MED", "Easy", "1.0",
         "One CSS file and no package.json today. Tokens, type scale and the confidence chip come "
         "straight from DESIGN-SPEC §2&ndash;§6; the chip is the signature component and the bands "
         "are semantic, never reused"),
        ("D1", "Auth and tenant binding", "MED", "Moderate", "1.25",
         "Clerk (D2-a), one auth org to one tenant. This is where the RLS session GUC (B2) gets "
         "its value from an authenticated principal rather than from the message"),
        ("D2", "The REST API behind the page", "HIGH", "Hard", "3.0",
         "~25 endpoints, none written. Inbox list and detail, confirm/correct/route, conversations "
         "and turns, knowledge CRUD, sources, destinations, rules, eval. The hidden half of this "
         "track and the item most likely to be cut by accident"),
        ("D3", "Inbox view", "HIGH", "Hard", "2.0",
         "The triage queue and the product's centre of gravity. Two-pane desktop, single-column "
         "mobile, optimistic confirm. Auto-handled items still appear, marked, for audit &mdash; "
         "and emergencies arrive here at the top band, filed for review with the trigger named"),
        ("D4", "Conversations view", "HIGH", "Moderate", "1.5",
         "The thread, its turns, the task in flight and its slots. Where a human sees why the "
         "receptionist asked what it asked &mdash; and, after G3, why it stopped asking"),
        ("D5", "Knowledge view", "MED", "Moderate", "1.0",
         "The editor for 2.4's facts, including the sensitivity flags G1 enforces. Without it, "
         "changing a check-in time is a database write"),
        ("D6", "Sources view", "LOW", "Easy", "0.75",
         "Opt-out model (addendum §4): a settings page listing threads with a mute toggle, plus "
         "the first-run exclusion pass. Deliberately not a workflow"),
        ("D7", "Destinations and recipes", "MED", "Moderate", "1.0",
         "Sheets/webhook config and field mapping. The tables and the engine survived D24 for "
         "exactly this: routing a message deliberately, by a human"),
        ("D8", "Rules builder", "MED", "Moderate", "1.0",
         "The condition/action builder over rules. SqlAlchemyRulesProvider and the evaluator are "
         "already written and already tested &mdash; this is the surface for them"),
        ("D9", "Admin / eval dashboard", "LOW", "Easy", "0.75",
         "Founder-only, role-gated, visually distinct: accuracy, per-language breakdown, "
         "calibration, confusion matrix, and per-tenant model spend"),
        ("D10", "Arabic, RTL and the accessibility pass", "MED", "Moderate", "0.5",
         "Not cosmetic for this market. Bidi text, mirrored layout, and the §10 baseline (focus "
         "order, contrast, target sizes) applied once across the views rather than per view"),
    ]))

story.append(PageBreak())

# ---------------------------------------------------------------- page 8
story.extend(section(
    "Track E &mdash; What makes it sellable &nbsp; 4.5d",
    "None of this is visible to a guest and none of it can be added after the first paying tenant "
    "without a migration. Sequenced after P1 because onboarding shape depends on who the first "
    "client is.",
    [
        ("E1", "Tenant onboarding and per-tenant secrets", "MED", "Moderate", "1.25",
         "Creating a tenant is currently an INSERT by hand. Includes envelope encryption for WABA "
         "tokens and destination credentials (addendum §3: never plaintext) &mdash; the reason "
         "channel_configs.config is the one table B2 grants a pre-tenant read on"),
        ("E2", "Usage metering", "MED", "Easy", "0.75",
         "usage_events exists, is indexed, and is written to by nothing. Messages, model tokens "
         "and ASR minutes per tenant per period &mdash; the input to both billing and the soft cap"),
        ("E3", "Billing and plan limits", "MED", "Moderate", "1.0",
         "Plans, the soft cap and what happens at it. The number itself is still a founder "
         "decision (DECISIONS.md, still open) &mdash; the mechanism is not"),
        ("E4", "Observability", "HIGH", "Easy", "1.0",
         "Structured logs with tenant and message ids, error tracking, and per-tenant model spend. "
         "Today a failed classification is a line in a Render log and nothing else &mdash; and so "
         "is an undelivered emergency alert, which is the line that most needs to page someone"),
        ("E5", "Retention, residency and subject deletion", "MED", "Moderate", "0.5",
         "Addendum §14: a configurable retention default, hard deletion of raw bodies and media "
         "after it, and a &ldquo;delete everything for this contact&rdquo; operation. GCC "
         "regulated buyers ask for this in the first meeting"),
    ]))

story.append(Spacer(1, 6))
story.extend(section(
    "Track 3 &mdash; Integration and measurement &nbsp; 5.5d",
    "Base360.ai is ruled out as a partner: their product is substantially ours and their client "
    "base is closed to us. Hostaway, Guesty and Cloudbeds all publish APIs, so the capability is "
    "available without the strategic cost.",
    [
        ("3.1", "PropertySystemPort and the first adapter", "MED", "Moderate", "2.5",
         "Build the port, not a Hostaway integration &mdash; the vendor-neutral routes and schemas "
         "already landed (PR #14). Blocked on P1: which PMS the first client runs decides which "
         "adapter gets written. Cache facts; never cache availability"),
        ("3.2", "End to end on a real number, then measure", "HIGH", "Moderate", "1.0",
         "The point at which the eval number stops being a replay of recorded fixtures and starts "
         "being this product's accuracy. <b>G3 is no longer on its list of prerequisites</b>; what "
         "remains is B4 and the send credentials"),
        ("3.3", "Live availability and write-back", "MED", "Hard", "2.0",
         "Reading a calendar is half of it; holding a booking is the half that makes the "
         "receptionist worth paying for, and the half that must never act on a stale cache"),
    ]))

story.append(PageBreak())

# ---------------------------------------------------------------- page 9
story.append(Paragraph("New this revision: what G3 settled, and what it left open", H2))
story.append(notes_table([
    ("The ordering, literally",
     "The emergency check runs in Orchestrator.process, <b>after the media pipeline and before the "
     "classifier</b>. After media, because a voice note saying there is a fire has no text until "
     "it is transcribed. Before the classifier, because a model is a network round trip that can "
     "be slow, wrong or down. Asserted twice: against a classifier that raises if it is called at "
     "all, and end to end through the assembled graph"),
    ("Two scripts, two rules",
     "Arabic matches as a substring, because Arabic attaches its article to the front of the word "
     "&mdash; حريق is الحريق with the article, and a boundary test there invents false negatives "
     "on the most ordinary way to write the sentence. Latin matches on word boundaries, because "
     "<i>fire</i> as a substring fires on &ldquo;fireplace&rdquo;. Digits count as letters, so "
     "Franco-Arabic works and 7ari2 is a word rather than a fragment"),
    ("One trigger needs a clock",
     "locked_out_at_night carries only_between, and the vocabulary's own note says why: locked out "
     "at 2pm is a support request, at 2am it is a person on a street. The window is read in "
     "<b>TenantPolicy.timezone</b> &mdash; the guest's clock, not the container's. Dubai and Cairo "
     "are an hour or two apart, which is an hour of the window either side of midnight"),
    ("Reply and alert, concurrently",
     "Both halves of the exchange are recorded first, then dispatched together. Sequentially, one "
     "waits on the other's retries, and neither &ldquo;the guest hears nothing for ten seconds "
     "while we retry the alert&rdquo; nor the reverse is an acceptable way to spend that time. The "
     "alerter never raises &mdash; a failed alert must not cost the guest the immediate reply"),
    ("The job in flight survives",
     "An emergency reply belongs to the transcript and to no task, so ConversationStore.record_"
     "reply takes an optional one. The active job is left exactly as it was rather than abandoned: "
     "a guest with a gas leak may well return to their booking question, the conversation belongs "
     "to a person either way, and discarding what the receptionist collected only makes the "
     "resumed conversation worse"),
    ("Still not written",
     "<b>Slot extraction.</b> The receptionist receives an empty slot dict because "
     "ClassificationResult has no slots field; adding one is a prompt change and a golden-set "
     "change, which is item 2.x. <b>The declared phrases are narrow</b> &mdash; the detector does "
     "not paraphrase, by design, so widening them is the operator's edit and it is the first thing "
     "to do before a real guest can reach the number"),
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
     "per call, normalised across both providers. Measure before quoting a price. G3 makes one "
     "class of message free: an emergency never reaches a model"),
], first_col=118))

story.append(Paragraph("How the total reconciles", H2))
story.append(Paragraph(
    "A 0 + B 0.25 + D 13.75 + E 4.5 + G 4.0 + (2.7) 0.5 + (3.1, 3.2, 3.3) 5.5 = "
    "<b>28.5</b> &mdash; 2.8 delivered this session leaves the board 'spent', like 2.4 before it", BODY))
story.append(Spacer(1, 4))
story.append(Paragraph(
    "Rows 2.1, 2.2, 2.4, 2.8 and B5 show &ldquo;spent&rdquo; or 0 rather than a number because their "
    "days are already delivered and their wiring was priced in A5, A6 and the sessions that built "
    "them &mdash; adding them would double-count. B4's remaining 0.25 is operational (a plan "
    "upgrade), not engineering; the subscription, the operator number and the worker are done. "
    "P1&ndash;P5 is excluded: "
    "founder and operator time, not engineering. Counting scope loosely is what produced the 5.75 "
    "figure that v2.1 replaced, so the arithmetic is stated rather than implied.", NOTE))

story.append(PageBreak())

# ---------------------------------------------------------------- page 10
story.append(Paragraph("The dates", H2))
dates = Table([
    [Paragraph("<b>Milestone</b>", CELL), Paragraph("<b>v2.8 said</b>", CELL),
     Paragraph("<b>Now</b>", CELL), Paragraph("<b>Status</b>", CELL)],
    [Paragraph("M1 &mdash; answers a real message, safely", CELLB),
     Paragraph("~4 days", NOTE), Paragraph("<b>~0.25 days</b>", NOTE),
     Paragraph("<b>NEXT</b> &mdash; B4's last item alone. It is deployed, it answers, as of G3 it "
               "answers <i>safely</i>, and as of 2.4 it has something true to say. The webhook "
               "subscription, the operator number and the worker are all reported or verified done "
               "this session; what remains is the paid Render upgrade. The positive proof still "
               "worth watching for is the first real inbound message landing a row in messages", NOTE)],
    [Paragraph("M2 &mdash; you can watch and correct it", CELLB),
     Paragraph("+13.75 days", NOTE), Paragraph("+13.75 days", NOTE),
     Paragraph("PENDING &mdash; Track D, six receptionist views", NOTE)],
    [Paragraph("M3 &mdash; sellable", CELLB),
     Paragraph("+16.5 days", NOTE), Paragraph("+16.5 days", NOTE),
     Paragraph("PENDING &mdash; Tracks E, 3 and the rest of G. Blocked on P1", NOTE)],
    [Paragraph("Phone answering", CELLB),
     Paragraph("after M3", NOTE), Paragraph("after M3", NOTE),
     Paragraph("PENDING &mdash; the speech-to-text seam already exists, and G3 added a second "
               "reason to want a voice channel: phone_call_to_operator", NOTE)],
], colWidths=[128, 68, 68, 192], hAlign="LEFT")
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

story.append(Spacer(1, 10))
story.append(banner(
    "<b>Read M1 carefully, because its shape changed rather than only its number.</b> In v2.8 the "
    "first milestone contained a hazard: a deployed receptionist that answered a gas leak with a "
    "note about maintenance. As of v2.9 that was closed; as of 2.4 it also has something true to "
    "say. As of this revision the checklist is down to its last line &mdash; a paid Render plan "
    "&mdash; with the subscription, the operator number and the worker reported or verified done. "
    "The remaining risk on the path to a pilot is <b>operational</b>, and operational risk is the "
    "kind a checklist closes.",
    DONE_GREEN))

story.append(PageBreak())

# ---------------------------------------------------------------- page 11
story.append(Paragraph("What would actually move these dates", H2))
story.append(Paragraph("The risks v2.3 named, re-scored against what session 9 learned.", SUBT))
story.append(Spacer(1, 5))
story.append(notes_table([
    ("A receptionist that answers but cannot recognise an emergency",
     "<b>CLOSED (G3).</b> It was the most serious item on this page in v2.8 and it is now a "
     "detector, an immediate bilingual reply, an operator alert and 69 tests. What replaces it is "
     "narrower and named below"),
    ("The trigger phrases are narrower than they read",
     "<b>NEW, and the successor to the item above.</b> The detector matches phrases an operator "
     "wrote down and deliberately does not paraphrase &mdash; that is what makes intents.yaml "
     "reviewable by the person carrying the consequences. The cost is that &ldquo;I smell "
     "gas&rdquo; matches neither declared gas phrase. A test asserts this so it cannot be "
     "forgotten, and the fix is one line of YAML that touches no code"),
    ("Nobody is told, because nobody is configured",
     "<b>Reported done.</b> Without CONTROL_CHAT_PHONE_E164 the alert is a log line; the process "
     "warns at startup and refuses nothing, which is the right trade for a service that still "
     "ingests and files &mdash; and the wrong state to point a guest's number at. The operator "
     "reports it set this session; the deployed worker still logged the startup warning at last "
     "check, so a redeploy confirming the warning is gone is the positive proof"),
    ("The alert is not the phone call the vocabulary asks for",
     "<b>NEW, bounded and reported.</b> A message can be missed in a way a ringing phone cannot. "
     "The gap is visible in AlertOutcome rather than smoothed over, and closing it needs a voice "
     "provider &mdash; the same dependency as the phone channel"),
    ("Supabase connection pooling",
     "Unmeasured. A2 assumes the transaction pooler and is safe there by default. What is not "
     "known is the cost of that safety &mdash; NullPool trades a handshake per checkout for "
     "correctness. Measure against the real pooler now that a service is running"),
    ("The deployed database path is unproven",
     "Unchanged. SQLAlchemy connects lazily, so startup proves nothing and the first tenant query "
     "is the test. If the first real message fails on DNS or auth, check the pooler host against "
     "Supabase &rarr; Connect before looking anywhere else"),
    ("RLS retrofitted late",
     "<b>CLOSED (B2).</b> Kept on the page for one revision because the trap it names &mdash; "
     "connecting as a role with BYPASSRLS &mdash; is a configuration mistake, not a code one, and "
     "can be reintroduced by a single environment variable"),
    ("D2 is invisible in its own track title",
     "Unchanged, and now the most likely accidental cut on the board. Three of Track D's days are "
     "backend"),
    ("Cost per message",
     "Unmeasured, not unknown. A3 made it measurable and turned prompt caching on. Measure before "
     "quoting a price"),
    ("PMS API access",
     "Unchanged. Docs are public, but sandbox approval and rate limits are theirs to grant. Start "
     "P2 today"),
    ("Roadmap artifacts disagree",
     "<b>RE-OPENED, then closed.</b> v2.8 declared this shut, but its generator was never "
     "committed &mdash; the repository could only produce v2.5, so the PDF everyone was reading "
     "had no source. v2.9 re-derives v2.8 and folds session 9 into it. The rule stands: edit "
     "docs/make_roadmap.py and re-run it; never hand-edit the PDF"),
    ("Two messages arriving at once",
     "<b>Infrastructure now in place.</b> B5's durable queue and its arq worker (watcher-worker) "
     "are deployed and consuming as of this session. Ordering only actually changes once REDIS_URL "
     "is set on watcher-api to switch it to the producer branch &mdash; a deliberate step, taken "
     "after the worker is confirmed consuming, so a message can't be accepted and then never "
     "replied to. Until then the API still runs on the in-process pool it has since A4"),
    ("Webhook subscription was reported done and was not",
     "<b>Reported closed operator-side; positive proof still pending.</b> Session 11 checked it "
     "directly &mdash; zero /webhook requests ever, zero messages rows &mdash; against a genuinely "
     "correct infrastructure (custom domain, both Meta credentials). This session the operator "
     "reports the subscription and the operator number completed. The standing lesson holds: this "
     "line closes on a positive check &mdash; a real handshake or a real message landing a row in "
     "messages &mdash; not on a status carried forward. At check time both were still empty, so the "
     "first inbound message is the thing to watch for"),
], first_col=132))

story.append(Spacer(1, 10))
story.append(banner(
    "<b>If you do only one thing next session: confirm B4 with a real message, then upgrade the "
    "Render plan.</b> Not D, not G1, and not 2.4 or B5 &mdash; all done, and B5's worker is now "
    "live. The receptionist answers, it is deployed, it knows when to stop and fetch a person, it "
    "has something true to say, and the facts table is live in production. The webhook "
    "subscription and the operator number are reported done operator-side; the one engineering-"
    "adjacent item left is moving watcher-api off the free plan so a cold start does not read to "
    "Meta as a timeout. <b>The positive proof to insist on</b> is still the first real WhatsApp "
    "message landing a row in the messages table (or a POST /webhook 200 in Render's logs) &mdash; "
    "at this session's check both were empty, so treat B4 as closed only once one appears. <b>And "
    "cheaply, spend thirty minutes widening the emergency trigger phrases</b> &mdash; it is the "
    "operator's edit, it changes no code, and it is the cheapest safety on the board.",
    URG["NOW"]))

story.append(PageBreak())

# ---------------------------------------------------------------- page 12
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
     Paragraph("PR #19 &mdash; items A5, A6. Track A complete. Conversation and task continuity in "
               "the message path; the clarifying-turn budget honoured; classifications populated "
               "with measured telemetry; process() async; the v1 rules/destinations filer excised "
               "(D24); the outbound sender, and the channel credentials moved behind channels/, "
               "which emptied KNOWN_LEAKS and closed item 1.1", NOTE),
     Paragraph("375 &rarr; 406", NOTE), Paragraph("3.25", CELL)],
    [Paragraph("8", CELL),
     Paragraph("PR #20 &mdash; items B1, B2, B3. Supabase project provisioned, migrated and "
               "stamped, with the channel_configs row tenant resolution raises without; migration "
               "004 &mdash; RLS enabled and forced on all 20 tables behind a watcher_app role that "
               "cannot bypass it, a per-transaction tenant GUC carried by every adapter, and one "
               "narrow SELECT-only exception for the endpoint lookup that runs before a tenant is "
               "known; the container image, whose first build exposed a missing package data file "
               "and a removed Starlette API; deployed to Render, Frankfurt, and serving", NOTE),
     Paragraph("406 &rarr; 417", NOTE), Paragraph("2.75", CELL)],
    [Paragraph("9", CELL),
     Paragraph("PR #21 &mdash; item G3. The emergency path: a detector over the declared "
               "triggers, matched in Arabic, Latin and Franco-Arabic and placed before the "
               "classifier; an immediate bilingual reply; an operator alert on a seam in the core "
               "with its implementation in channels/, reporting which channel it used because the "
               "vocabulary asks for a phone call nothing wired can place; a per-tenant timezone "
               "for the one trigger with a clock, validated at startup. An emergency is never "
               "classified. Decisions D30&ndash;D34", NOTE),
     Paragraph("417 &rarr; 486", NOTE), Paragraph("1.5", CELL)],
    [Paragraph("10", CELLB),
     Paragraph("<b>Item 2.4.</b> A facts table (migration 005) behind row-level security; "
               "core/knowledge.py's stopword-stripped rapidfuzz matching (D35); a sensitivity "
               "flag a fact can carry, enforced narrowly &mdash; an unverified match to one is "
               "treated exactly like no match (D36); answer_from_knowledge dispatched from the "
               "receptionist for the first time, replacing a placeholder reply that had been "
               "silently wrong for every informational intent; a one-off import script and a "
               "committed, curated fixture from one real property, exercised end to end in "
               "test_knowledge_integration.py. REGISTRY wired via configure_knowledge, not "
               "threaded through Orchestrator (D37); an autouse fixture keeps that process-global "
               "state from leaking between tests. Decisions D35&ndash;D37", NOTE),
     Paragraph("486 &rarr; 506", NOTE), Paragraph("2.0", CELL)],
    [Paragraph("11", CELLB),
     Paragraph("<b>Item B5, reconciled with 2.4; B4 re-investigated.</b> Merged the durable "
               "arq/Redis queue built on a branch that had forked before 2.4 (RedisClassificationQueue, "
               "apps/api/worker.py, orchestration/composition.build_consumer as one graph shared by "
               "the API and the worker) onto main after 2.4's merge, moving 2.4's configure_knowledge "
               "call into the shared graph-builder so the arq worker gets the knowledge base too. "
               "Decisions D38&ndash;D40. Separately, re-checked B4's &ldquo;webhook subscribed&rdquo; "
               "claim directly against Render's request logs and the Supabase messages table rather "
               "than repeating it: the custom domain and both Meta credentials are genuinely correct, "
               "but zero requests to /webhook have ever been recorded and zero messages exist &mdash; "
               "the subscription step was not actually done, or was undone. Provisioned watcher-redis, "
               "a free-plan Render Key Value instance, on request; the worker service is the one piece "
               "left unprovisioned &mdash; it needs Render's Background Worker type, which this "
               "session's tooling can only create as a (wrong) Web Service, plus secrets only the "
               "operator can copy over, so REDIS_URL stays unset on watcher-api until it exists", NOTE),
     Paragraph("<b>506 &rarr; 521</b>", NOTE), Paragraph("<b>1.0</b>", CELLB)],
    [Paragraph("12", CELLB),
     Paragraph("<b>B4 and B5 closed on the roadmap; migration 005 applied to production.</b> "
               "Verified the live systems rather than the handoff: watcher-worker &mdash; the arq "
               "Background Worker that was session 11's one open piece &mdash; now exists as a paid "
               "Render service, deployed and consuming (logs confirm it started for consume_message "
               "and connected to Redis), so B5 is fully done. B4's webhook subscription and operator "
               "number are taken as completed operator-side, leaving only the paid watcher-api "
               "upgrade &mdash; B4 down to 0.25d. Found production schema still at 004 with migration "
               "005 (2.4's facts table) never applied, and applied it: the live schema is now "
               "005_facts, RLS forced, tenant-isolation policy and watcher_app grant in place. "
               "Then delivered <b>item 2.8</b> &mdash; a properties table (migration 006), a "
               "nullable facts.property_id scoping a fact to one unit, and message&nbsp;&rarr;"
               "&nbsp;property resolution (core/property.py) &mdash; and <b>started 2.7</b> with "
               "scripts/measure_prompt.py, which settles the prompt's size and prints the exact "
               "token count and cost once a key is present. Decisions D41&ndash;D42", NOTE),
     Paragraph("<b>521 &rarr; 535</b>", NOTE), Paragraph("<b>1.0</b>", CELLB)],
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
    "<b>Cumulative: 23.5 engineering days delivered. ~28.5 remaining.</b> Twelve sessions &mdash; "
    "session 12 closed B4/B5's infrastructure, applied migration 005, and delivered 2.8 &mdash; and "
    "the observed rate holds at 1&ndash;3 days each.", SMALL))


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
    canvas.drawRightString(w - 20 * mm, h - 8 * mm, f"Build Roadmap    {VERSION}    {STAMP}")
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
