"""Generate the Watcher v2 roadmap PDF: urgency, ease, effort, per work item.

**v2.3 — 15 August 2026.** This generator is the single source of the roadmap PDF. v2.2 flagged
that three roadmap artifacts disagreed (this script still emitted the v1.11 / 3 August plan, and
two separate PDFs claimed to be current); regenerating from here is the fix, so edit this file and
re-run it rather than producing a fourth version by hand.

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
VERSION = "v2.3"
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
    "supersedes the v2.2 PDF of the same date", LEAD))
story.append(Spacer(1, 10))

story.append(banner(
    "<b>What this revision does.</b> v2.2 named A2 and A4 as the whole of the critical path and put "
    "the honest remaining total at ~43.75&nbsp;&rarr;&nbsp;41.75 engineering days. Session&nbsp;6 "
    "built both. <b>The application now starts.</b> It reads its configuration, opens a database, "
    "resolves which tenant a message belongs to, classifies it, and files the decision &mdash; end "
    "to end, through the production wiring, for the first time. What it still cannot do is reply: "
    "that is A5 and A6, and those two items are now the whole of Track&nbsp;A. "
    "<b>Remaining: ~40.25 engineering days.</b>"))

story.append(Paragraph("Where we stand today", H2))
story.append(two_col(
    "Built and tested", "Missing &mdash; nothing ships without these",
    "<b>375 passing tests</b>, no DB or network needed<br/>"
    "83 source files across 20 modules (121 with tests)<br/>"
    "19 DB tables + 3 migrations<br/>"
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
    "starts and files a message end to end (A4)",

    f'<font color="{DONE_GREEN.hexval()}"><b>No DB connection &mdash; done (A2)</b></font><br/>'
    f'<font color="{DONE_GREEN.hexval()}"><b>No entrypoint &mdash; done (A4)</b></font><br/>'
    "<b>No outbound</b> &mdash; ChannelSender has no implementation<br/>"
    "<b>No memory across turns</b> &mdash; worker.py still passes task=None<br/>"
    "No Dockerfile &mdash; so cd.yml has never deployed anything<br/>"
    "No Supabase project &mdash; and <b>no channel_configs row</b>, without which "
    "every webhook 500s (B1)<br/>"
    "No RLS &mdash; no policy in any migration<br/>"
    "No control page &mdash; one CSS file, no package.json<br/>"
    "No control-page API &mdash; ~25 endpoints, none written<br/>"
    "Knowledge &mdash; zero tables, zero rows<br/>"
    "Live availability &mdash; no read path to any PMS<br/>"
    "A measured prompt &mdash; the gate replays v2 fixtures, so 88% is not v3's number"))

story.append(Paragraph("What changed in this revision (v2.2 &rarr; v2.3)", H2))
story.append(two_col(
    "Delivered", "Consequences",
    "<b>A2 and A4 delivered</b> &mdash; 1.5 engineering days. "
    "Total 41.75&nbsp;&rarr;&nbsp;40.25<br/>"
    "<b>Track A: 4.75&nbsp;&rarr;&nbsp;3.25 days</b>, and it is now only A5 and A6<br/>"
    "Tests 325 &rarr; <b>375</b> (+50). Source files 79 &rarr; 83<br/>"
    "Five database port implementations replace the last of the in-memory doubles<br/>"
    "Two runtime dependencies: <b>psycopg 3</b> and <b>uvicorn</b>",

    "<b>DATABASE_POOL_MODE</b> is new, and pooler-safe by default &mdash; the Supabase "
    "pgbouncer risk is now a policy rather than an open question<br/>"
    "<b>Migrations connect exactly as the app does</b>, so B1 cannot discover a driver "
    "mismatch on its first upgrade<br/>"
    "<b>Tenant resolution refuses to guess</b> &mdash; B1 must insert a channel_configs row<br/>"
    "<b>This generator is regenerated</b>, so the three-disagreeing-artifacts item is closed<br/>"
    "Unchanged: 2.4 is still the last item before a demo; 3.1 still blocked on P1; the "
    "scope guard still holds &mdash; no PDF handbook ingestion"))

story.append(Spacer(1, 10))
story.append(Paragraph(
    "<b>Totals:</b> ~40.25 engineering days remaining. At the observed 2&ndash;3 engineering days "
    "per working session that is <b>13&ndash;20 sessions</b>; at the six-day week these estimates "
    "assume, ~6.7 weeks of pure build &mdash; so plan <b>~9 weeks to sellable</b> and "
    "<b>~10 days to a real number that answers safely</b>. The critical path is still "
    "Track&nbsp;A, and it is now A5 then A6.", BODY))

story.append(PageBreak())

# ---------------------------------------------------------------- page 2
story.extend(section(
    "Track A &mdash; Make it run. &nbsp;[FOUR OF SIX DONE &mdash; 3.25 DAYS LEFT]",
    "The bottom four layers exist. What remains is the two items that turn filing into answering, "
    "and they are the last thing between this and a receptionist.",
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
        ("A5", "Wire continuity + excise v1 filer", "NOW", "Moderate", "2.25",
         "<b>NEXT</b> &mdash; ConversationRepository is still never called outside tests; the "
         "orchestrator passes task=None and empty slots. Also converts process() to async and "
         "removes the rules/destinations threading (D24). Tables retained, not dropped"),
        ("A6", "Outbound sender", "NOW", "Moderate", "1.0",
         "<b>NOT BUILT</b> &mdash; ChannelSender has no implementation; replies are composed and "
         "never sent. Also the moment to move the channel credential fields out of core/config.py "
         "and clear the last KNOWN_LEAKS entry"),
    ]))

story.append(Spacer(1, 8))
story.append(banner(
    "<b>Why A5 is the whole game now.</b> Items 2.1 and 2.2 are genuinely written &mdash; the "
    "tables, the repository, the task state machine, the reply path. What was never written is the "
    "line joining them, and A4 deliberately did not write it: wiring a receptionist before "
    "continuity exists produces one that forgets the previous turn, and it would have nothing to "
    "send with until A6. The milestone &ldquo;holds a conversation&rdquo; is still not true, and "
    "A5 is what makes it true.", URG["NOW"]))

story.append(PageBreak())

story.extend(section(
    "Track B &mdash; Host it. &nbsp;[Supabase + Render]",
    "Stack locked 15 August 2026. B2 must land before a second client exists. B1 grew one "
    "precondition this session &mdash; see its note.",
    [
        ("B1", "Supabase project and migrations", "NOW", "Easy", "0.5",
         "<b>NOT STARTED</b> &mdash; provision, set DATABASE_URL, run the 3 existing migrations. "
         "<b>And insert one channel_configs row per endpoint</b>: tenant resolution reads that "
         "table and raises rather than guessing, so without the row every inbound message 500s"),
        ("B2", "Row-Level Security", "NOW", "Moderate", "1.5",
         "<b>NOT BUILT</b> &mdash; no policy in any migration, though AGENTS.md calls tenant "
         "isolation non-negotiable. Per-session GUC + a cross-tenant read test"),
        ("B3", "Dockerfile and Render service", "HIGH", "Easy", "0.75",
         "<b>NOT BUILT</b> &mdash; the Dockerfile alone activates the dormant cd.yml image job. "
         "The start command is now a known quantity (uvicorn --factory)"),
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
         "<b>PART-BUILT</b> &mdash; 2.0d spent. The wiring is priced in A5, not here"),
        ("2.2", "The reply path", "HIGH", "Moderate", "spent",
         "<b>PART-BUILT</b> &mdash; 1.5d spent. The sender is priced in A6, not here"),
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
story.append(Paragraph("New this revision: what A2 and A4 settled, and what they left open", H2))
story.append(notes_table([
    ("The connection path",
     "Supabase's application URI is pgbouncer in <b>transaction mode</b>: a client connection is "
     "bound to a server connection for one transaction only. Neither psycopg's server-side "
     "prepared statements nor SQLAlchemy's own connection pool survives that, and both fail "
     "intermittently under load rather than in a test. <b>DATABASE_POOL_MODE defaults to the "
     "pooler-safe policy</b> (NullPool + prepare_threshold=None), which is always correct and "
     "merely slower when unnecessary; <font name='Courier'>session</font> opts out for a direct "
     "connection. It is a variable rather than a guess from the port because a pooler can sit in "
     "front of any host. The load test the last three revisions asked for is now a flip of that "
     "variable, not a code change"),
    ("The driver",
     "A bare <font name='Courier'>postgresql://</font> URI resolves to psycopg 2, which this "
     "project does not ship. It is rewritten onto psycopg 3 so the URI can be pasted from the "
     "dashboard &mdash; and alembic/env.py now goes through the same two functions, because "
     "migrations resolving a different driver from the application is something a deploy discovers "
     "on the day it matters"),
    ("Tenant attribution",
     "An inbound message is attributed through <b>channel_configs</b>, and an endpoint with no "
     "enabled row raises rather than falling back to a default tenant &mdash; guessing writes one "
     "customer's message into another's account. The lookup does not filter by channel kind, so a "
     "phone line needs no code change. <b>B1's checklist grew one line because of this</b>"),
    ("Still not written",
     "<b>classifications rows.</b> The audit row carries the classification snapshot, but that "
     "table wants latency_ms and prompt_version and the orchestrator surfaces neither; model_used "
     "has nowhere to go either. Small, and it belongs with A5, which is already in that code path. "
     "Do not invent the missing telemetry to fill the columns"),
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
    "A 3.25 + B 4.25 + D 13.75 + E 4.5 + G 5.5 + (2.4, 2.7, 2.8) 3.5 + (3.1, 3.2, 3.3) 5.5 = "
    "<b>40.25</b>", BODY))
story.append(Spacer(1, 4))
story.append(Paragraph(
    "Rows 2.1 and 2.2 show &ldquo;spent&rdquo; rather than a number because their days are already "
    "delivered and the outstanding wiring is priced in A5 and A6 &mdash; adding them would "
    "double-count 3.5 days. P1&ndash;P4 is excluded: founder time, not engineering. Counting scope "
    "loosely is what produced the 5.75 figure that v2.1 replaced, so the arithmetic is stated "
    "rather than implied.", NOTE))

story.append(Paragraph("The dates", H2))
dates = Table([
    [Paragraph("<b>Milestone</b>", CELL), Paragraph("<b>v2.2 said</b>", CELL),
     Paragraph("<b>Now</b>", CELL), Paragraph("<b>Status</b>", CELL)],
    [Paragraph("M1 &mdash; answers a real message, safely", CELLB),
     Paragraph("~11.5 days", CELL), Paragraph("<b>~10 days</b>", CELL),
     Paragraph("<b>NEXT</b> &mdash; Track A (3.25 left) + B1&ndash;B4 + G3 + 2.4. The emergency "
               "path is not optional here", NOTE)],
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
story.append(Paragraph("The risks v2.2 named, re-scored against what session 6 learned.", SUBT))
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
     "<b>CLOSED.</b> docs/make_roadmap.py now generates this document into "
     "docs/Watcher_v2_Roadmap.pdf. Edit the generator and re-run it; do not hand-write a fourth "
     "version"),
], first_col=132))

story.append(Spacer(1, 10))
story.append(banner(
    "<b>If you do only one thing next session: A5, then A6.</b> Not 2.4. The receptionist can now "
    "read its configuration, reach a model, connect to a database and start &mdash; and a guest who "
    "messages it gets silence, because nothing joins the conversation to the task and nothing puts "
    "a reply on the wire. A5&nbsp;+&nbsp;A6 is 3.25 days and it is the difference between a system "
    "that files and a receptionist &mdash; and G3 before any real guest can message it.",
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
    [Paragraph("6", CELLB),
     Paragraph("<b>Items A2, A4.</b> Engine and session scope, pooler-safe by default; the "
               "composition root and the five database port implementations it needed; a "
               "process-level worker pool so the webhook still answers before classification; "
               "alembic aligned to the same connection path; this generator regenerated", NOTE),
     Paragraph("<b>325 &rarr; 375</b>", NOTE), Paragraph("1.5", CELLB)],
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
    "<b>Cumulative: 12.0 engineering days delivered. ~40.25 remaining.</b>", SMALL))


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
