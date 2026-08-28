"""Turn the clinic's availability workbook into validated catalogue records (demo step 4).

**What this is for.** The client's source of truth for branches, treatments, prices and the diary
is one spreadsheet. Step 4 is the one-way door between that file and the database: after this, the
assistant quotes from ``clinic_services`` and offers times from ``clinic_availability_slots``, and
nothing re-reads the sheet. So this module's job is not "parse the file" — it is to refuse to
import a file it cannot vouch for, and to say exactly why.

**The three deviations, applied here and nowhere else.** The client's review of the source data
settled three differences between what the file says and what should be imported (handoff §5):

1. **One price is wrong in the file and is forced on import.** A treatment package reads at a tenth
   of its price, between a single session and the twelve-session package that bracket it. The
   correction is data (:data:`DEMO_PRICE_CORRECTIONS`), not a rule, and it is *conditional*: it
   fires only if the cell still holds the value the client confirmed was wrong. If the file has
   since been fixed the import says so and changes nothing; if it holds some third value the
   import fails rather than overwriting a number nobody has looked at. A blind override is how a
   stale correction outlives the mistake it was written for and quietly re-breaks a price.

2. **The 15-minute buffer is not applied to what is already in the diary.** The workbook is
   authoritative (decision 3), including every 60-minute service booked back-to-back in an hourly
   grid. This module counts those pairs so the number can be compared against the last review, and
   rejects none of them. The buffer constrains *new* bookings, which is step 6's.

3. **Ambiguous service names need a canonical id.** Two names for one treatment, and several
   packages priced identically, are what make an availability row's free-text service name fail to
   resolve — and what makes the assistant burn its two clarifying turns on a distinction the
   catalogue does not make. Aliases resolve the first (a name that reaches two codes is an error
   here, not a coin toss at conversation time); the second is reported as a warning listing the
   services a patient cannot tell apart, because the fix is a catalogue decision, not an import
   one.

Retail is excluded (decision 6): the catalogue is treatments only.

**Demo scope.** :data:`DEMO_PRICE_CORRECTIONS` is one client's correction to one file for one
demo. It is a default argument, not a constant reached into: an import can be run with a different
set, or with none.

**Header handling is deliberately tolerant, and deliberately loud.** Sheets get re-saved, columns
get renamed, a header row moves. Column matching folds case, punctuation and whitespace and accepts
a small set of spellings per field (:data:`BRANCH_COLUMNS` and friends). What it never does is
guess: a required column that matches nothing is an error that names every header it did find, so
the fix is one glance rather than a bisect.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Literal
from zoneinfo import ZoneInfo

from apps.api.core.clinic import (
    BOOKING_BUFFER_MINUTES,
    AvailabilitySlot,
    Booking,
    BookingReference,
    Branch,
    Service,
    SlotStatus,
    normalise_service_name,
)

#: One cell, as a spreadsheet reader hands it over: text, a number, a date/time, or empty.
Cell = object
#: One row keyed by canonical field name (the output of :func:`rows_from_grid`).
Row = Mapping[str, Cell]

Severity = Literal["error", "warning", "info"]


# ── Issues and the report ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ImportIssue:
    """One thing worth saying about the import. ``row`` is the workbook's own row number."""

    severity: Severity
    sheet: str
    message: str
    row: int | None = None

    def __str__(self) -> str:
        where = f"{self.sheet}" + (f" row {self.row}" if self.row is not None else "")
        return f"[{self.severity}] {where}: {self.message}"


@dataclass(frozen=True, slots=True)
class ImportReport:
    """What the import found. Read the counts; act on the issues.

    ``back_to_back_pairs`` is deviation 2's number — adjacent slots in one branch with less than
    the buffer between them. It is reported, never enforced: the last review counted 62 and the
    client accepted them. A number that has moved a lot means the diary's shape changed, which is
    worth a look before a demo, and is not something this module is entitled to decide.
    """

    issues: tuple[ImportIssue, ...] = ()
    branches: int = 0
    services: int = 0
    slots: int = 0
    bookings: int = 0
    skipped_retail: int = 0
    back_to_back_pairs: int = 0
    overlapping_pairs: int = 0
    slots_by_date: Mapping[str, int] = field(default_factory=dict)
    slots_by_status: Mapping[str, int] = field(default_factory=dict)
    #: The highest reference serial already taken, per prefix. What step 6 issues from.
    highest_reference: Mapping[str, int] = field(default_factory=dict)

    @property
    def errors(self) -> tuple[ImportIssue, ...]:
        return tuple(i for i in self.issues if i.severity == "error")

    @property
    def warnings(self) -> tuple[ImportIssue, ...]:
        return tuple(i for i in self.issues if i.severity == "warning")

    @property
    def ok(self) -> bool:
        """Whether the plan is fit to persist. One error is enough for it not to be."""
        return not self.errors

    def summary(self) -> str:
        return (
            f"{self.branches} branches, {self.services} services, {self.slots} slots, "
            f"{self.bookings} bookings; {self.skipped_retail} retail rows skipped; "
            f"{self.back_to_back_pairs} back-to-back pairs; "
            f"{len(self.errors)} errors, {len(self.warnings)} warnings"
        )


@dataclass(frozen=True, slots=True)
class CataloguePlan:
    """Everything one workbook says, validated, in the shape the repository writes.

    A plan with errors is still returned rather than raised: an operator running the import wants
    all twelve unresolvable service names at once, not the first one twelve times. The caller is
    what refuses — see ``scripts/import_clinic_workbook.py`` and
    ``db/clinic_repo.py``'s ``import_catalogue``, which will not write a plan whose report is not
    ``ok``.
    """

    branches: tuple[Branch, ...] = ()
    services: tuple[Service, ...] = ()
    slots: tuple[AvailabilitySlot, ...] = ()
    bookings: tuple[Booking, ...] = ()
    report: ImportReport = field(default_factory=ImportReport)


# ── Deviation 1: the conditional price correction ──────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PriceCorrection:
    """A price the client has confirmed is wrong in the file, and what it should be.

    ``expected_minor`` is the guard: the correction applies only to a cell that still reads what
    was reviewed. See the module docstring for why an unconditional override is not what this is.
    """

    code: str
    expected_minor: int
    corrected_minor: int
    note: str


#: Deviation 1 (handoff §5, decision 4). Amounts are in minor units — 150,000 piastres is 1,500
#: EGP, the value in the file; 1,500,000 is the 15,000 EGP the client confirmed. The single
#: session and the twelve-session package on either side of it are what make the file's figure
#: impossible rather than merely surprising.
DEMO_PRICE_CORRECTIONS: tuple[PriceCorrection, ...] = (
    PriceCorrection(
        code="DT029",
        expected_minor=150_000,
        corrected_minor=1_500_000,
        note="six-session package priced below the single session in the source file",
    ),
)


# ── Column mapping ─────────────────────────────────────────────────────────────────────────

#: canonical field -> the header spellings accepted for it, already folded by ``_fold_header``.
ColumnMap = Mapping[str, tuple[str, ...]]

BRANCH_COLUMNS: ColumnMap = {
    "external_id": ("branch id", "branchid", "id", "code", "branch code"),
    "name": ("branch", "branch name", "name"),
    "area": ("area", "district", "city", "governorate"),
    "address": ("address", "location"),
    "phone": ("phone", "telephone", "mobile", "contact", "branch phone"),
    "timezone": ("timezone", "time zone", "tz"),
    # The other names this branch answers to, semicolon- or comma-separated, exactly as
    # ``SERVICE_COLUMNS`` takes them for a service. This is where the Arabic a patient actually
    # types lives: the sheet writes "Maadi" and a patient writes "المعادي", and the mapping
    # between them is the clinic's to write down rather than this repository's to guess.
    "aliases": ("aliases", "alias", "also known as", "other names", "arabic"),
    "placeholder": ("placeholder", "is placeholder", "tbc", "provisional"),
    # The workbook records provenance per branch — "real example", "given", "placeholder" — and
    # that column, not a heuristic over missing fields, is what sets ``Branch.placeholder``.
    "source": ("source demo", "source", "provenance"),
    "active": ("active", "enabled"),
}
BRANCH_REQUIRED = ("external_id", "name")

SERVICE_COLUMNS: ColumnMap = {
    "code": ("service id", "serviceid", "id", "code", "service code"),
    "name": ("service", "service name", "name", "treatment"),
    "category": ("category", "group"),
    # Treatment or Retail. Separate from ``category`` (Facial, Laser, Injectables…) because the
    # workbook carries both and it is this one that decides whether a row belongs in the
    # catalogue at all (decision 6).
    "kind": ("type", "service type", "record type"),
    "price": ("price", "price egp", "amount", "cost", "rate"),
    "currency": ("currency", "ccy"),
    "duration_minutes": (
        "duration",
        "duration min",
        "duration minutes",
        "duration mins",
        "duration est min",
        "duration est minutes",
        "minutes",
    ),
    "session_count": ("sessions", "session count", "no of sessions", "quantity", "package size"),
    "aliases": ("aliases", "alias", "also known as", "other names", "synonyms"),
    "active": ("active", "enabled"),
}
SERVICE_REQUIRED = ("code", "name", "price", "duration_minutes")

SLOT_COLUMNS: ColumnMap = {
    "external_id": ("slot id", "slotid", "id", "availability id"),
    "branch": ("branch", "branch id", "branch name", "location"),
    "service": ("service", "service name", "treatment", "service id"),
    "date": ("date", "day", "appointment date"),
    "start_time": ("start", "start time", "from", "time"),
    "end_time": ("end", "end time", "to", "finish"),
    "status": ("status", "state", "availability"),
    "booking_ref": ("booking ref", "booking reference", "reference", "ref", "booking id"),
    "patient_name": (
        "patient",
        "patient name",
        "patient first name",
        "customer",
        "customer name",
        "client name",
    ),
    "patient_phone": ("patient phone", "phone", "phone masked", "mobile", "contact number"),
}
SLOT_REQUIRED = ("external_id", "branch", "service", "date", "start_time", "status")

#: What a status cell may say for each slot state. Folded before lookup.
_STATUS_WORDS: Mapping[str, SlotStatus] = {
    "open": "open",
    "available": "open",
    "free": "open",
    "متاح": "open",
    "booked": "booked",
    "reserved": "booked",
    "taken": "booked",
    "محجوز": "booked",
}

#: A row whose category says this is retail, and the catalogue is treatments only (decision 6).
_RETAIL_WORDS = frozenset({"retail", "product", "products", "shop", "منتج", "منتجات"})

_HEADER_PUNCTUATION = re.compile(r"[^0-9a-z؀-ۿ]+")
_SESSIONS_IN_NAME = re.compile(r"(\d+)\s*[- ]?\s*(?:sessions?|جلسات|جلسة)", re.IGNORECASE)
#: A package sold by time rather than by a countable number of sessions.
_UNLIMITED_IN_NAME = re.compile(r"unlimited|غير محدود", re.IGNORECASE)
_ALIAS_SEPARATORS = re.compile(r"[|;\n]+|,(?![^()]*\))")


def _fold_header(text: str) -> str:
    """Fold a header cell so "Branch ID", "branch_id" and "BRANCH-ID" are one name."""
    folded = _HEADER_PUNCTUATION.sub(" ", str(text).strip().casefold())
    return " ".join(folded.split())


def rows_from_grid(
    header_cells: Sequence[Cell],
    data_rows: Iterable[Sequence[Cell]],
    columns: ColumnMap,
    required: Sequence[str],
    *,
    sheet: str,
    first_data_row: int = 2,
) -> tuple[tuple[Row, ...], tuple[ImportIssue, ...]]:
    """Key a sheet's rows by canonical field name.

    Kept here rather than in the ``openpyxl`` script because this is the part that is wrong when an
    import produces nothing: it is worth having under test with the header spellings the client's
    file might plausibly use, and worth being able to run without a spreadsheet library.

    Blank rows are dropped silently — a sheet almost always has some — and a row is blank when
    every cell of it is. Unmapped columns are ignored and named once, as information.
    """
    issues: list[ImportIssue] = []
    by_index: dict[int, str] = {}
    unmapped: list[str] = []

    for index, cell in enumerate(header_cells):
        if cell is None or not str(cell).strip():
            continue
        folded = _fold_header(str(cell))
        for canonical, spellings in columns.items():
            if folded in spellings and canonical not in by_index.values():
                by_index[index] = canonical
                break
        else:
            unmapped.append(str(cell).strip())

    if unmapped:
        issues.append(ImportIssue("info", sheet, f"columns ignored: {', '.join(sorted(unmapped))}"))

    missing = [name for name in required if name not in by_index.values()]
    if missing:
        found = ", ".join(str(c).strip() for c in header_cells if c is not None and str(c).strip())
        issues.append(
            ImportIssue(
                "error",
                sheet,
                f"required columns not found: {', '.join(missing)}. "
                f"Headers present: {found or '(none)'}",
            )
        )
        return (), tuple(issues)

    rows: list[Row] = []
    for offset, cells in enumerate(data_rows):
        if all(cell is None or not str(cell).strip() for cell in cells):
            continue
        row: dict[str, Cell] = {"_row": first_data_row + offset}
        for index, canonical in by_index.items():
            row[canonical] = cells[index] if index < len(cells) else None
        rows.append(row)

    return tuple(rows), tuple(issues)


# ── Cell coercion ──────────────────────────────────────────────────────────────────────────


def _text(row: Row, field_name: str) -> str | None:
    value = row.get(field_name)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _row_number(row: Row) -> int | None:
    number = row.get("_row")
    return number if isinstance(number, int) else None


def _flag(row: Row, field_name: str, *, default: bool) -> bool:
    text = _text(row, field_name)
    if text is None:
        return default
    return _fold_header(text) in ("1", "true", "yes", "y", "active", "نعم")


def _minor_units(value: Cell) -> int | None:
    """A money cell in minor units, or ``None`` if it is not a number.

    Via ``Decimal`` on the text: a price that has been through a float is a price that can be
    quoted as 749.9999. Currency symbols, thousands separators and a stray ``EGP`` are stripped.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value * 100
    text = re.sub(r"[^0-9.\-]", "", str(value))
    if not text or text in ("-", "."):
        return None
    try:
        return int((Decimal(text) * 100).to_integral_value())
    except (InvalidOperation, ArithmeticError):
        return None


def _whole(value: Cell) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    text = re.sub(r"[^0-9\-]", "", str(value))
    if not text or text == "-":
        return None
    try:
        return int(text)
    except ValueError:  # pragma: no cover - the regex above leaves only digits
        return None


def _as_date(value: Cell) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip() if value is not None else ""
    if not text:
        return None
    for pattern in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


def _as_time(value: Cell) -> time | None:
    if isinstance(value, datetime):
        return value.time()
    if isinstance(value, time):
        return value
    if isinstance(value, timedelta):  # openpyxl gives a duration for some time-formatted cells
        seconds = int(value.total_seconds())
        return time(hour=(seconds // 3600) % 24, minute=(seconds // 60) % 60)
    text = str(value).strip() if value is not None else ""
    if not text:
        return None
    for pattern in ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M%p", "%I %p"):
        try:
            return datetime.strptime(text.upper(), pattern).time()
        except ValueError:
            continue
    return None


def _split_aliases(text: str | None) -> tuple[str, ...]:
    if not text:
        return ()
    parts = (part.strip() for part in _ALIAS_SEPARATORS.split(text))
    return tuple(part for part in parts if part)


# ── The import ─────────────────────────────────────────────────────────────────────────────


def plan_import(
    branch_rows: Sequence[Row],
    service_rows: Sequence[Row],
    slot_rows: Sequence[Row],
    *,
    timezone: str,
    price_corrections: Sequence[PriceCorrection] = DEMO_PRICE_CORRECTIONS,
    allow_overlaps: bool = False,
    extra_issues: Sequence[ImportIssue] = (),
) -> CataloguePlan:
    """Validate three sheets' rows into one catalogue plan. See the module docstring.

    ``timezone`` has no default and is not read from settings. The workbook holds a wall clock and
    names no zone; ``TENANT_TIMEZONE`` still ships defaulting to a zone an hour off the demo's, and
    a default here is how 672 slots land an hour out with nothing to notice it by.

    ``allow_overlaps`` downgrades overlapping slots in one branch from an error to a warning. It is
    off because the reviewed file has none and a file that suddenly does has changed in a way
    nobody approved — but a clinic that genuinely runs two rooms at once is a real thing, and when
    it says so, this is the switch rather than a code change.
    """
    zone = ZoneInfo(timezone)
    issues: list[ImportIssue] = list(extra_issues)

    branches = _plan_branches(branch_rows, issues)
    services, skipped_retail = _plan_services(service_rows, price_corrections, issues)
    slots, bookings = _plan_slots(slot_rows, branches, services, zone, issues)

    overlapping = _check_overlaps(slots, issues, allow_overlaps=allow_overlaps)
    back_to_back = _count_back_to_back(slots)
    if back_to_back:
        issues.append(
            ImportIssue(
                "info",
                "availability",
                f"{back_to_back} adjacent pairs are less than {BOOKING_BUFFER_MINUTES} minutes "
                "apart; imported as the workbook has them (decision 3). The buffer applies to new "
                "bookings only.",
            )
        )

    report = ImportReport(
        issues=tuple(issues),
        branches=len(branches),
        services=len(services),
        slots=len(slots),
        bookings=len(bookings),
        skipped_retail=skipped_retail,
        back_to_back_pairs=back_to_back,
        overlapping_pairs=overlapping,
        slots_by_date=dict(sorted(Counter(s.starts_at.date().isoformat() for s in slots).items())),
        slots_by_status=dict(sorted(Counter(s.status for s in slots).items())),
        highest_reference=_highest_references(bookings),
    )
    return CataloguePlan(
        branches=tuple(branches.values()),
        services=tuple(services.values()),
        slots=slots,
        bookings=bookings,
        report=report,
    )


def _plan_branches(rows: Sequence[Row], issues: list[ImportIssue]) -> dict[str, Branch]:
    """The branches, keyed by ``external_id``. All of them, placeholders included (decision 5)."""
    branches: dict[str, Branch] = {}
    provenance: Counter[str] = Counter()

    for row in rows:
        line = _row_number(row)
        external_id = _text(row, "external_id")
        name = _text(row, "name")
        if external_id is None or name is None:
            issues.append(ImportIssue("error", "branches", "branch id and name are required", line))
            continue
        if external_id in branches:
            issues.append(
                ImportIssue("error", "branches", f"duplicate branch id {external_id}", line)
            )
            continue
        source = _text(row, "source")
        if source is not None:
            provenance[_fold_header(source)] += 1
        branches[external_id] = Branch(
            external_id=external_id,
            name=name,
            area=_text(row, "area"),
            address=_text(row, "address"),
            phone=_text(row, "phone"),
            timezone=_text(row, "timezone"),
            aliases=_split_aliases(_text(row, "aliases")),
            placeholder=_is_placeholder(row, source),
            active=_flag(row, "active", default=True),
        )

    if not branches:
        return branches
    if provenance:
        counted = ", ".join(f"{word}: {count}" for word, count in sorted(provenance.items()))
        issues.append(ImportIssue("info", "branches", f"branch provenance — {counted}"))
    else:
        issues.append(
            ImportIssue(
                "info",
                "branches",
                "no placeholder or provenance column: every branch imported as a real location. "
                "Which branches are stand-ins is then the client pack's to record, not this "
                "import's to guess.",
            )
        )
    return branches


def _is_placeholder(row: Row, source: str | None) -> bool:
    """Whether a branch is a stand-in for one whose real details are not in yet.

    A dedicated ``placeholder`` column wins. Otherwise the provenance column decides, and only its
    literal ``placeholder`` value counts: the workbook also marks branches "given", which are real
    locations supplied without a full record, and folding those two together would understate how
    much of the branch list is real. All fourteen are in the demo either way (decision 5).
    """
    if _text(row, "placeholder") is not None:
        return _flag(row, "placeholder", default=False)
    return source is not None and _fold_header(source) == "placeholder"


def _plan_services(
    rows: Sequence[Row],
    corrections: Sequence[PriceCorrection],
    issues: list[ImportIssue],
) -> tuple[dict[str, Service], int]:
    """The treatment catalogue, keyed by code. Retail is skipped; deviation 1 is applied here."""
    services: dict[str, Service] = {}
    by_correction = {correction.code: correction for correction in corrections}
    skipped_retail = 0

    for row in rows:
        line = _row_number(row)
        code = _text(row, "code")
        name = _text(row, "name")
        category = _text(row, "category")

        kind = _text(row, "kind") or category
        if kind is not None and _fold_header(kind) in _RETAIL_WORDS:
            skipped_retail += 1
            continue
        if code is None or name is None:
            issues.append(
                ImportIssue("error", "services", "service id and name are required", line)
            )
            continue
        if code in services:
            issues.append(ImportIssue("error", "services", f"duplicate service id {code}", line))
            continue

        price_minor = _minor_units(row.get("price"))
        duration = _whole(row.get("duration_minutes"))
        if price_minor is None or price_minor < 0:
            issues.append(ImportIssue("error", "services", f"{code}: price is not a number", line))
            continue
        if duration is None or duration <= 0:
            issues.append(
                ImportIssue("error", "services", f"{code}: duration is not a positive number", line)
            )
            continue

        price_minor = _corrected_price(code, price_minor, by_correction.get(code), issues, line)
        sessions = _whole(row.get("session_count")) or _sessions_from_name(name, issues, line)
        if _UNLIMITED_IN_NAME.search(name):
            issues.append(
                ImportIssue(
                    "warning",
                    "services",
                    f"{code}: {name!r} states no fixed session count. Stored as {sessions}; a "
                    "quote must state the package, never a session count the catalogue does not "
                    "give.",
                    line,
                )
            )

        services[code] = Service(
            code=code,
            name=name,
            price_minor=price_minor,
            duration_minutes=duration,
            currency=(_text(row, "currency") or "EGP").upper(),
            session_count=sessions,
            category=category,
            aliases=_split_aliases(_text(row, "aliases")),
            active=_flag(row, "active", default=True),
        )

    _check_name_collisions(services, issues)
    _report_indistinguishable(services, issues)
    return services, skipped_retail


def _corrected_price(
    code: str,
    price_minor: int,
    correction: PriceCorrection | None,
    issues: list[ImportIssue],
    line: int | None,
) -> int:
    """Deviation 1, conditionally. See :class:`PriceCorrection` and the module docstring."""
    if correction is None:
        return price_minor
    if price_minor == correction.expected_minor:
        issues.append(
            ImportIssue(
                "info",
                "services",
                f"{code}: price forced from {price_minor / 100:.2f} to "
                f"{correction.corrected_minor / 100:.2f} — {correction.note} (decision 4)",
                line,
            )
        )
        return correction.corrected_minor
    if price_minor == correction.corrected_minor:
        issues.append(
            ImportIssue(
                "info",
                "services",
                f"{code}: the file already holds the corrected price; nothing forced. The "
                "correction can be retired once the client confirms the file is the new source.",
                line,
            )
        )
        return price_minor
    issues.append(
        ImportIssue(
            "error",
            "services",
            f"{code}: expected the known-wrong {correction.expected_minor / 100:.2f} or the "
            f"corrected {correction.corrected_minor / 100:.2f}, found {price_minor / 100:.2f}. "
            "The correction is stale — confirm the price with the client before importing.",
            line,
        )
    )
    return price_minor


def _sessions_from_name(name: str, issues: list[ImportIssue], line: int | None) -> int:
    """The package quantity a name states, or 1.

    Every quote has to say whether an amount covers one session or a package, so a package whose
    quantity is only in its name still has to end up in the ``session_count`` column. Derived
    quietly is not good enough: it is reported, because a name this reads wrongly is a quote that
    is wrong in the one way the vocabulary's ``quoting`` block singles out.
    """
    found = _SESSIONS_IN_NAME.search(name)
    if found is None:
        return 1
    count = int(found.group(1))
    issues.append(
        ImportIssue(
            "info",
            "services",
            f"session count {count} read from the name {name!r}; no sessions column",
            line,
        )
    )
    return count


def _check_name_collisions(services: Mapping[str, Service], issues: list[ImportIssue]) -> None:
    """Deviation 3, the half that is an error: one name must reach one service.

    A name shared by two codes cannot be resolved from an availability row or from a patient's
    message, and picking one is how a patient is booked for the treatment they did not ask for.
    The fix is in the catalogue — distinct names, or an alias on one of them.
    """
    reached: dict[str, list[str]] = defaultdict(list)
    for service in services.values():
        for name in service.names:
            reached[normalise_service_name(name)].append(service.code)

    for name, codes in sorted(reached.items()):
        if len(codes) > 1:
            issues.append(
                ImportIssue(
                    "error",
                    "services",
                    f"the name {name!r} reaches {', '.join(sorted(codes))}. Give each a distinct "
                    "name or move the shared one to the aliases of exactly one.",
                )
            )


def _report_indistinguishable(services: Mapping[str, Service], issues: list[ImportIssue]) -> None:
    """Deviation 3, the half that is a warning: services a patient cannot tell apart.

    Same price, same duration, same quantity, different codes. Nothing here is wrong — the client's
    catalogue really does list a treatment twice and really does price three laser packages
    identically — but it is what the assistant will ask a clarifying question about and get an
    answer that does not narrow anything. Naming them in the report is what lets that be decided
    once, in the catalogue, instead of live.
    """
    grouped: dict[tuple[int, int, int], list[str]] = defaultdict(list)
    for service in services.values():
        grouped[(service.price_minor, service.duration_minutes, service.session_count)].append(
            f"{service.code} {service.name}"
        )

    for key, members in sorted(grouped.items()):
        if len(members) > 1:
            price, duration, sessions = key
            issues.append(
                ImportIssue(
                    "warning",
                    "services",
                    f"{len(members)} services are identical to a patient "
                    f"({price / 100:.2f}, {duration} min, {sessions} session(s)): "
                    f"{'; '.join(sorted(members))}",
                )
            )


def _plan_slots(
    rows: Sequence[Row],
    branches: Mapping[str, Branch],
    services: Mapping[str, Service],
    zone: ZoneInfo,
    issues: list[ImportIssue],
) -> tuple[tuple[AvailabilitySlot, ...], tuple[Booking, ...]]:
    """The diary. Every row resolves to a branch and a service, or the import fails."""
    by_branch_key = _branch_index(branches)
    by_service_key = _service_index(services)

    slots: list[AvailabilitySlot] = []
    bookings: list[Booking] = []
    seen_slots: set[str] = set()
    seen_references: set[str] = set()

    for row in rows:
        line = _row_number(row)
        external_id = _text(row, "external_id")
        if external_id is None:
            issues.append(ImportIssue("error", "availability", "slot id is required", line))
            continue
        if external_id in seen_slots:
            issues.append(
                ImportIssue("error", "availability", f"duplicate slot id {external_id}", line)
            )
            continue

        branch = by_branch_key.get(normalise_service_name(_text(row, "branch") or ""))
        service = by_service_key.get(normalise_service_name(_text(row, "service") or ""))
        if branch is None:
            issues.append(
                ImportIssue(
                    "error",
                    "availability",
                    f"{external_id}: branch {_text(row, 'branch')!r} is not in the branches sheet",
                    line,
                )
            )
            continue
        if service is None:
            issues.append(
                ImportIssue(
                    "error",
                    "availability",
                    f"{external_id}: service {_text(row, 'service')!r} resolves to no catalogue "
                    "entry. Add it to the catalogue or to an existing service's aliases.",
                    line,
                )
            )
            continue

        on_date = _as_date(row.get("date"))
        starts = _as_time(row.get("start_time"))
        if on_date is None or starts is None:
            issues.append(
                ImportIssue(
                    "error", "availability", f"{external_id}: date or start time unreadable", line
                )
            )
            continue

        starts_at = datetime.combine(on_date, starts, tzinfo=zone)
        ends = _as_time(row.get("end_time"))
        ends_at = (
            datetime.combine(on_date, ends, tzinfo=zone)
            if ends is not None
            else starts_at + timedelta(minutes=service.duration_minutes)
        )
        if ends_at <= starts_at:
            issues.append(
                ImportIssue(
                    "error", "availability", f"{external_id}: ends at or before it starts", line
                )
            )
            continue

        status = _STATUS_WORDS.get(_fold_header(_text(row, "status") or ""))
        if status is None:
            issues.append(
                ImportIssue(
                    "error",
                    "availability",
                    f"{external_id}: status {_text(row, 'status')!r} is neither open nor booked",
                    line,
                )
            )
            continue

        reference = _text(row, "booking_ref")
        if status == "booked" and reference is None:
            issues.append(
                ImportIssue(
                    "error",
                    "availability",
                    f"{external_id}: booked with no booking reference",
                    line,
                )
            )
            continue
        if status == "open" and reference is not None:
            issues.append(
                ImportIssue(
                    "error",
                    "availability",
                    f"{external_id}: open but carries booking reference {reference}",
                    line,
                )
            )
            continue

        seen_slots.add(external_id)
        slots.append(
            AvailabilitySlot(
                external_id=external_id,
                branch_external_id=branch.external_id,
                service_code=service.code,
                starts_at=starts_at,
                ends_at=ends_at,
                status=status,
            )
        )

        if reference is None:
            continue
        if reference in seen_references:
            issues.append(
                ImportIssue(
                    "error", "availability", f"duplicate booking reference {reference}", line
                )
            )
            continue
        seen_references.add(reference)
        bookings.append(
            Booking(
                reference=reference,
                slot_external_id=external_id,
                source="workbook",
                patient_name=_text(row, "patient_name"),
                patient_phone=_text(row, "patient_phone"),
            )
        )

    return tuple(slots), tuple(bookings)


def _branch_index(branches: Mapping[str, Branch]) -> dict[str, Branch]:
    """Branches by both their id and their name: an availability sheet uses either."""
    index: dict[str, Branch] = {}
    for branch in branches.values():
        index.setdefault(normalise_service_name(branch.external_id), branch)
        index.setdefault(normalise_service_name(branch.name), branch)
    return index


def _service_index(services: Mapping[str, Service]) -> dict[str, Service]:
    """Services by code, name and every alias — deviation 3's canonical-id map (module docstring).

    Collisions between two services' names are already an error from
    :func:`_check_name_collisions`; ``setdefault`` here only keeps this index deterministic while
    that error is being reported, since a plan with errors is never persisted.
    """
    index: dict[str, Service] = {}
    for service in services.values():
        index.setdefault(normalise_service_name(service.code), service)
        for name in service.names:
            index.setdefault(normalise_service_name(name), service)
    return index


def _by_branch(slots: Sequence[AvailabilitySlot]) -> dict[str, list[AvailabilitySlot]]:
    grouped: dict[str, list[AvailabilitySlot]] = defaultdict(list)
    for slot in slots:
        grouped[slot.branch_external_id].append(slot)
    for branch_slots in grouped.values():
        branch_slots.sort(key=lambda slot: (slot.starts_at, slot.ends_at))
    return grouped


def _check_overlaps(
    slots: Sequence[AvailabilitySlot], issues: list[ImportIssue], *, allow_overlaps: bool
) -> int:
    """Two slots covering the same minute in one branch. See ``allow_overlaps`` in `plan_import`."""
    severity: Severity = "warning" if allow_overlaps else "error"
    count = 0
    for branch_slots in _by_branch(slots).values():
        for earlier, later in zip(branch_slots, branch_slots[1:], strict=False):
            if earlier.overlaps(later):
                count += 1
                issues.append(
                    ImportIssue(
                        severity,
                        "availability",
                        f"{earlier.external_id} and {later.external_id} overlap in branch "
                        f"{earlier.branch_external_id}",
                    )
                )
    return count


def _count_back_to_back(slots: Sequence[AvailabilitySlot]) -> int:
    """Deviation 2's count. Never a rejection — see the module docstring."""
    count = 0
    for branch_slots in _by_branch(slots).values():
        for earlier, later in zip(branch_slots, branch_slots[1:], strict=False):
            gap = earlier.gap_minutes_before(later)
            if 0 <= gap < BOOKING_BUFFER_MINUTES:
                count += 1
    return count


def _highest_references(bookings: Sequence[Booking]) -> dict[str, int]:
    """The highest structured reference serial per prefix, for step 6 to issue after.

    References the clinic wrote in its own format are simply not counted: they are the clinic's
    identifiers, kept verbatim, and this is only about not re-issuing a number already in use.
    """
    highest: dict[str, int] = {}
    for booking in bookings:
        parsed = BookingReference.match(booking.reference)
        if parsed is not None:
            highest[parsed.prefix] = max(highest.get(parsed.prefix, 0), parsed.serial)
    return highest
