"""Generate the Watcher v2 roadmap PDF: urgency, ease, effort, per work item."""

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

OUT = "/home/user/Watcher/docs/Watcher_v2_Roadmap.pdf"

INK = colors.HexColor("#12171f")
MUTED = colors.HexColor("#5b6675")
RULE = colors.HexColor("#d8dee7")
BAND = colors.HexColor("#f2f5f9")
ACCENT = colors.HexColor("#1f4e79")

URG = {
    "NOW": colors.HexColor("#c0392b"),
    "HIGH": colors.HexColor("#d67200"),
    "MED": colors.HexColor("#1f6fb2"),
    "LOW": colors.HexColor("#7b8794"),
}
EASE = {
    "Trivial": colors.HexColor("#1e8449"),
    "Easy": colors.HexColor("#4a9c48"),
    "Moderate": colors.HexColor("#c08a00"),
    "Hard": colors.HexColor("#b8442c"),
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
    return Paragraph(
        f'<font color="{palette[text].hexval()}"><b>{text}</b></font>', CELL
    )


def track_table(rows):
    """rows: (num, work, urgency, ease, days, note)"""
    data = [[
        Paragraph("<b>#</b>", CELL), Paragraph("<b>Work item</b>", CELL),
        Paragraph("<b>Urgency</b>", CELL), Paragraph("<b>Ease</b>", CELL),
        Paragraph("<b>Days</b>", CELL), Paragraph("<b>Why / what it unblocks</b>", CELL),
    ]]
    for n, w, u, e, d, note in rows:
        data.append([
            Paragraph(n, CELL), Paragraph(w, CELLB),
            chip(u, URG), chip(e, EASE),
            Paragraph(d, CELL), Paragraph(note, NOTE),
        ])
    t = Table(data, colWidths=[26, 128, 44, 50, 34, 174], repeatRows=1, hAlign="LEFT")
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


story = []

# ---------------------------------------------------------------- page 1
story.append(Paragraph("Watcher v2 &mdash; Build Roadmap", H1))
story.append(Paragraph(
    "From a message&nbsp;filer to a receptionist. Every item scored for urgency and ease. "
    "&nbsp;&bull;&nbsp; 3 August 2026", LEAD))
story.append(Spacer(1, 10))

hdr = Table([[Paragraph(
    "<b>The whole gap in one line:</b> the pipeline can listen and file, but it cannot reply. "
    "Three outcomes exist and all three mean &ldquo;put this somewhere&rdquo;. Adding a fourth "
    "&mdash; <b>answer the customer</b> &mdash; is the project.", BODY)]],
    colWidths=[456])
hdr.setStyle(TableStyle([
    ("BACKGROUND", (0, 0), (-1, -1), BAND),
    ("LINEBEFORE", (0, 0), (0, -1), 2.5, ACCENT),
    ("LEFTPADDING", (0, 0), (-1, -1), 10),
    ("RIGHTPADDING", (0, 0), (-1, -1), 10),
    ("TOPPADDING", (0, 0), (-1, -1), 8),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
]))
story.append(hdr)

story.append(Paragraph("Where we stand today", H2))
stand = Table([
    [Paragraph("<b>Built and tested</b>", CELL), Paragraph("<b>Missing</b>", CELL)],
    [Paragraph(
        "86 passing tests, no DB or network needed<br/>"
        "66 Python files, 10 modules<br/>"
        "11 DB tables + migration<br/>"
        "Every external system behind a swappable seam<br/>"
        "Eval runner + job queue (unmerged branch)", CELL),
     Paragraph(
        "A reply path &mdash; it cannot talk back<br/>"
        "Memory across turns &mdash; each message judged alone<br/>"
        "Knowledge &mdash; zero tables, zero rows<br/>"
        "A channel-neutral core &mdash; it still speaks WhatsApp<br/>"
        "Live availability &mdash; no read path to any PMS", CELL)],
], colWidths=[228, 228], hAlign="LEFT")
stand.setStyle(TableStyle([
    ("BACKGROUND", (0, 0), (-1, 0), BAND),
    ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ("BOX", (0, 0), (-1, -1), 0.4, RULE),
    ("INNERGRID", (0, 0), (-1, -1), 0.4, RULE),
    ("LEFTPADDING", (0, 0), (-1, -1), 7),
    ("TOPPADDING", (0, 0), (-1, -1), 6),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
]))
story.append(stand)

story.append(Paragraph("How to read the scores", H2))
leg = Table([
    [chip("NOW", URG), Paragraph("Today or tomorrow. Something is actively bleeding, or it blocks other work.", NOTE)],
    [chip("HIGH", URG), Paragraph("This sprint. The demo does not exist without it.", NOTE)],
    [chip("MED", URG), Paragraph("Needed before a real client, not before a demo.", NOTE)],
    [chip("LOW", URG), Paragraph("Do it when convenient. Nothing waits on it.", NOTE)],
    [chip("Trivial", EASE), Paragraph("Minutes. A setting, a click, a delete.", NOTE)],
    [chip("Easy", EASE), Paragraph("Understood problem, no design decisions left open.", NOTE)],
    [chip("Moderate", EASE), Paragraph("Real work, but the pattern already exists in the repo to copy.", NOTE)],
    [chip("Hard", EASE), Paragraph("Genuinely new. Nothing in the repo to copy from.", NOTE)],
], colWidths=[52, 404], hAlign="LEFT")
leg.setStyle(TableStyle([
    ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ("TOPPADDING", (0, 0), (-1, -1), 2.5),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
    ("LINEBELOW", (0, 3), (-1, 3), 0.4, RULE),
]))
story.append(leg)

story.append(Spacer(1, 10))
story.append(Paragraph(
    "<b>Totals:</b> ~14.5 engineering days. At six days a week that is a demo in about two weeks "
    "and a real client's guests on it in about three. Meta verification no longer sets the date "
    "&mdash; they allow starting unverified &mdash; so <b>engineering is the critical path</b>.", BODY))

# ---------------------------------------------------------------- page 2
story.extend(section(
    "Track 0 &mdash; Today. Hours, not days.",
    "Nothing here takes a full day, and three of the five unblock everything downstream. "
    "If only one thing happens today, make it 0.3.",
    [
        ("0.1", "Set default branch to <font name='Courier'>main</font>",
         "NOW", "Trivial", "1 min",
         "Anyone cloning today lands on a feature branch. Harmless now, confusing the moment branches drift."),
        ("0.2", "Remove the client name from the golden set and fixtures",
         "NOW", "Easy", "0.25",
         "A real client and address sit in a public repo, breaking your own anonymisation rule. Decide separately whether to rewrite history."),
        ("0.3", "Decide the receptionist intent vocabulary",
         "NOW", "Easy", "1 hr",
         "A founder call, not code. <b>Blocks four items</b> (1.2, 2.4, 2.5, and the golden set). Cheapest and most blocking thing on this page."),
        ("0.4", "Merge the eval branch",
         "NOW", "Trivial", "0.25",
         "Verified conflict-free and green: +15 tests, 86 to 101, all passing. Runner, five measures, reports, recorded fixtures, plus the job queue. A merge commit, not a fast-forward &mdash; main has moved since."),
        ("0.5", "Delete the stale <font name='Courier'>nifty-johnson</font> branch",
         "LOW", "Trivial", "1 min",
         "Housekeeping your own roadmap already flags."),
    ]))

story.extend(section(
    "Track 1 &mdash; Foundations. Week 1.",
    "Do 1.1 before anything else is built. Every week it waits adds more call sites to change.",
    [
        ("1.1", "Stop the core speaking WhatsApp",
         "NOW", "Moderate", "1.5",
         "<font name='Courier'>wa_message_id</font> to <font name='Courier'>external_id</font>, "
         "<font name='Courier'>wa_chat_id</font> to <font name='Courier'>thread_id</font>, add a channel field. "
         "A phone call has no chat id. Strict typing plus 86 tests point at every site, so this is a rename, not a rewrite."),
        ("1.2", "Port the four kept scaffold files",
         "HIGH", "Moderate", "1.0",
         "<b>Not a copy-paste.</b> They import the wrong package root, and the boundary test bans the word "
         "&ldquo;whatsapp&rdquo; while the actual leak is the <font name='Courier'>wa_</font> prefix &mdash; "
         "so the test meant to prevent this bug would not catch it. Needs 0.3 first."),
        ("1.3", "Python 3.12 to 3.13",
         "LOW", "Easy", "0.5",
         "Baseline corrected: the repo is on 3.12, not 3.10. Upgrade surface checked and clean &mdash; no removed stdlib, no deprecated datetime calls. Do it after 1.1 so a breakage has one suspect, not two."),
    ]))

# ---------------------------------------------------------------- page 3
story.extend(section(
    "Track 2 &mdash; Make it a receptionist. Week 2.",
    "This is the product. 2.1 is the only item with nothing in the repo to copy from.",
    [
        ("2.1", "Conversations, tasks and slot filling",
         "HIGH", "Hard", "2.0",
         "Hold a goal across several messages instead of judging each alone. New tables, new migration, wired into the orchestrator. The one genuinely new piece of domain work."),
        ("2.2", "The reply path",
         "HIGH", "Moderate", "1.5",
         "A fourth outcome, a composer, and sending back out through the connector. Retries and dead-lettering already exist for CRM writes &mdash; this adds the direction, not the machinery."),
        ("2.3", "Autonomy gate, and raise the acting floor",
         "HIGH", "Easy", "1.0",
         "Small code, important thinking. High acts, medium acts and tells someone, low fetches a human. Half-confident is fine for filing, not for holding a booking. Money and owner matters always reach a person, checked <i>before</i> confidence."),
        ("2.4", "Knowledge base",
         "HIGH", "Moderate", "2.0",
         "Facts table with sensitivity flags, prose in pgvector, and a real &ldquo;I don't know&rdquo; that fetches a human. Door codes are not ordinary facts. For the demo, one property's facts fit in the prompt &mdash; no retrieval needed."),
        ("2.5", "Prompt v2 and rewrite the golden set",
         "MED", "Moderate", "1.0",
         "Receptionist vocabulary, 8 cases rewritten and ~50 added. The runner from 0.4 already exists to score them. Needs 0.3."),
    ]))

story.extend(section(
    "Track 3 &mdash; Integration and launch. Week 3.",
    "Base360.ai is ruled out as a partner: their product is substantially ours and their client "
    "base is closed to us. Hostaway, Guesty and Cloudbeds all publish APIs, so the capability is "
    "available without the strategic cost.",
    [
        ("3.1", "<font name='Courier'>PropertySystemPort</font> plus the first adapter",
         "MED", "Moderate", "2.5",
         "<b>Build the port, not a Hostaway integration.</b> Verified: <font name='Courier'>crm_cache</font> "
         "already has <font name='Courier'>external_record_id</font> and <font name='Courier'>last_synced_at</font>, "
         "but delivery exposes only <font name='Courier'>post()</font> &mdash; write-only, so this needs a new read port. "
         "Cache facts; never cache availability."),
        ("3.2", "End to end on a real number, then measure",
         "HIGH", "Moderate", "1.0",
         "The point at which the eval number becomes real rather than recorded."),
    ]))

# ---------------------------------------------------------------- page 4
story.append(Paragraph("Runs in parallel &mdash; start on day one", H2))
story.append(Paragraph(
    "None of these are engineering. All of them can quietly become the reason a date slips.", SMALL))
story.append(Spacer(1, 5))
story.append(track_table([
    ("P1", "Pick the first client",
     "NOW", "Easy", "&mdash;",
     "Decides which PMS adapter gets built first. Without it, 3.1 is a guess."),
    ("P2", "Read the Hostaway / Guesty / Cloudbeds API docs, get sandbox keys",
     "HIGH", "Easy", "0.5",
     "Public docs, usually self-service sandboxes. Shape the port from what two or three of them offer <i>in common</i>."),
    ("P3", "File Meta business verification",
     "MED", "Easy", "1 hr",
     "No longer blocking &mdash; unverified start is allowed, and a receptionist mostly replies inside the 24-hour window. File it anyway before volume makes it bind."),
    ("P4", "Graphify as a build aid (optional)",
     "LOW", "Easy", "0.5",
     "Maps the repo for the coding agents during the 1.1 rename. Local parsing, no vector store, nothing leaves the machine. Install from the GitHub repo &mdash; several lookalike domains exist."),
]))

story.append(Paragraph("The dates", H2))
tl = Table([
    [Paragraph("<b>Milestone</b>", CELL), Paragraph("<b>When</b>", CELL)],
    [Paragraph("Eval merged, name leak fixed, vocabulary decided", CELL), Paragraph("End of day 1", CELLB)],
    [Paragraph("Core stops speaking WhatsApp; scaffold files ported", CELL), Paragraph("End of week 1", CELLB)],
    [Paragraph("Holds a conversation and replies &mdash; rough demo", CELL), Paragraph("Middle of week 2", CELLB)],
    [Paragraph("Knowledge and safety rules in &mdash; <b>demo-ready</b>", CELL), Paragraph("End of week 2", CELLB)],
    [Paragraph("Live availability, measured, on a real number &mdash; <b>pilot-ready</b>", CELL), Paragraph("Middle of week 3", CELLB)],
    [Paragraph("Phone answering", CELL), Paragraph("Week 4", CELLB)],
], colWidths=[336, 120], hAlign="LEFT")
tl.setStyle(TableStyle([
    ("BACKGROUND", (0, 0), (-1, 0), BAND),
    ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ("LINEBELOW", (0, 0), (-1, -2), 0.35, RULE),
    ("TOPPADDING", (0, 0), (-1, -1), 5),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ("LEFTPADDING", (0, 0), (-1, -1), 7),
]))
story.append(tl)
story.append(Spacer(1, 4))
story.append(Paragraph(
    "Phone lands in week 4 rather than later because the speech-to-text seam already exists in "
    "<font name='Courier'>media/pipeline.py</font>. It was built for voice notes and turns out to be "
    "most of what a phone connector needs.", NOTE))

story.append(Paragraph("What would actually move these dates", H2))
for head, txt in [
    ("The vocabulary decision drifting",
     "One hour of founder time that blocks four items. If it slips a week, everything slips a week. This is the single highest-leverage hour on the whole plan."),
    ("Knowledge base scope creep",
     "The 2.0 days assumes a structured intake form plus PMS sync. &ldquo;Can it read our PDF handbook?&rdquo; is a different and much larger project. Say no for now."),
    ("Quality, not features",
     "Getting to <i>working</i> is two weeks. Getting to <i>trustworthy</i> &mdash; where it never invents a check-in time &mdash; is measured by the eval runner, and that number is not fully under your control. Budget a tuning tail."),
    ("PMS API access",
     "Docs are public, but sandbox approval and rate limits are theirs to grant. Start P2 on day one; it is the new version of the Meta-verification lesson."),
]:
    story.append(Paragraph(f"<b>{head}.</b> {txt}", BODY))
    story.append(Spacer(1, 4))

story.append(Spacer(1, 8))
foot = Table([[Paragraph(
    "<b>If you do only one thing today:</b> decide the intent vocabulary (0.3) and merge the eval "
    "branch (0.4). One is an hour of thinking, the other is a code review. Together they unblock "
    "roughly half of everything below them.", BODY)]], colWidths=[456])
foot.setStyle(TableStyle([
    ("BACKGROUND", (0, 0), (-1, -1), BAND),
    ("LINEBEFORE", (0, 0), (0, -1), 2.5, URG["NOW"]),
    ("LEFTPADDING", (0, 0), (-1, -1), 10),
    ("RIGHTPADDING", (0, 0), (-1, -1), 10),
    ("TOPPADDING", (0, 0), (-1, -1), 8),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
]))
story.append(foot)


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
    canvas.drawRightString(w - 20 * mm, h - 8 * mm, "Build Roadmap  •  urgency and ease")
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
    title="Watcher v2 - Build Roadmap", author="Watcher",
    subject="Roadmap with urgency and implementation ease per work item",
)
frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="f")
doc.addPageTemplates([PageTemplate(id="main", frames=[frame], onPage=decorate)])
doc.build(story)
print("wrote", OUT)
