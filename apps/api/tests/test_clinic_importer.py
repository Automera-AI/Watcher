"""The workbook importer (demo step 4).

Two things these tests are for, and they are different.

**The header row of the client's actual workbook is pinned here.** ``BRANCH_HEADERS``,
``SERVICE_HEADERS`` and ``SLOT_HEADERS`` are copied verbatim from the file the demo imports. A
column map that stops matching them is the failure mode that produces an empty import with no
error worth reading, and it is invisible to every test written against convenient headers.

**The three deviations are asserted as behaviour, not as constants.** The conditional price
correction, the back-to-back pairs that must survive the import, and the alias resolution that
keeps an availability row's free-text service name from reaching two catalogue entries — each is a
decision the client took (handoff §3, §5) and each is tested by what an import does with a row,
not by reading the constant back.

The data here is invented. The real workbook is one client's, and none of it is needed to test
what this module decides.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, time, timedelta

import pytest

from apps.api.clinic.importer import (
    BRANCH_COLUMNS,
    BRANCH_REQUIRED,
    DEMO_PRICE_CORRECTIONS,
    SERVICE_COLUMNS,
    SERVICE_REQUIRED,
    SLOT_COLUMNS,
    SLOT_REQUIRED,
    CataloguePlan,
    Cell,
    ImportIssue,
    PriceCorrection,
    Row,
    plan_import,
    rows_from_grid,
)

TIMEZONE = "Africa/Cairo"

#: Copied from the client's workbook. See the module docstring.
BRANCH_HEADERS = ("Branch ID", "Branch", "Area", "Address", "Phone", "Hours", "Source (demo)")
SERVICE_HEADERS = (
    "Service",
    "Category",
    "Price (EGP)",
    "Duration est. (min)",
    "Branches",
    "Type",
    "ID",
)
SLOT_HEADERS = (
    "Slot ID",
    "Date",
    "Day",
    "Branch",
    "Service",
    "Category",
    "Start",
    "End",
    "Duration (min)",
    "Status",
    "Booking Ref",
    "Patient (first name)",
    "Phone (masked)",
    "Channel",
)

BRANCH_ROW = ("B01", "Riverside", "Riverside, Cairo", "1 Nile St", "010 00•• ••01", "11–20", "real")
SERVICE_ROW = ("Deep Facial", "Facial", 750, 45, "All branches", "Treatment", "DT001")
SLOT_ROW = (
    "S00001",
    "2026-09-02",
    "Wed",
    "Riverside",
    "Deep Facial",
    "Facial",
    "11:00",
    "11:45",
    45,
    "Open",
    None,
    None,
    None,
    None,
)


def _rows(
    headers: Sequence[Cell],
    data: Sequence[Sequence[Cell]],
    columns: object,
    required: object,
    kind: str,
) -> tuple[tuple[Row, ...], tuple[ImportIssue, ...]]:
    return rows_from_grid(headers, data, columns, required, sheet=kind)  # type: ignore[arg-type]


def _plan(
    *,
    branches: Sequence[Sequence[Cell]] = (BRANCH_ROW,),
    services: Sequence[Sequence[Cell]] = (SERVICE_ROW,),
    slots: Sequence[Sequence[Cell]] = (SLOT_ROW,),
    **kwargs: object,
) -> CataloguePlan:
    branch_rows, branch_issues = _rows(
        BRANCH_HEADERS, branches, BRANCH_COLUMNS, BRANCH_REQUIRED, "branches"
    )
    service_rows, service_issues = _rows(
        SERVICE_HEADERS, services, SERVICE_COLUMNS, SERVICE_REQUIRED, "services"
    )
    slot_rows, slot_issues = _rows(SLOT_HEADERS, slots, SLOT_COLUMNS, SLOT_REQUIRED, "availability")
    return plan_import(
        branch_rows,
        service_rows,
        slot_rows,
        timezone=TIMEZONE,
        extra_issues=(*branch_issues, *service_issues, *slot_issues),
        **kwargs,  # type: ignore[arg-type]
    )


def _messages(plan: CataloguePlan, severity: str) -> str:
    return " | ".join(i.message for i in plan.report.issues if i.severity == severity)


# ── Reading a sheet ────────────────────────────────────────────────────────────────────────


class TestRowsFromGrid:
    def test_the_client_workbooks_own_headers_map(self) -> None:
        rows, issues = _rows(SLOT_HEADERS, [SLOT_ROW], SLOT_COLUMNS, SLOT_REQUIRED, "availability")
        assert rows[0]["external_id"] == "S00001"
        assert rows[0]["branch"] == "Riverside"
        assert rows[0]["start_time"] == "11:00"
        assert not [i for i in issues if i.severity == "error"]

    def test_unmapped_columns_are_named_once_as_information(self) -> None:
        _, issues = _rows(SLOT_HEADERS, [SLOT_ROW], SLOT_COLUMNS, SLOT_REQUIRED, "availability")
        ignored = [i for i in issues if i.message.startswith("columns ignored")]
        assert len(ignored) == 1
        assert "Channel" in ignored[0].message and "Day" in ignored[0].message

    def test_a_missing_required_column_names_the_headers_it_did_find(self) -> None:
        """The fix for a renamed column should be one glance, not a bisect."""
        headers = tuple(h for h in SLOT_HEADERS if h != "Slot ID")
        rows, issues = _rows(headers, [SLOT_ROW[1:]], SLOT_COLUMNS, SLOT_REQUIRED, "availability")
        assert rows == ()
        (error,) = (i for i in issues if i.severity == "error")
        assert "external_id" in error.message and "Booking Ref" in error.message

    def test_case_and_punctuation_in_a_header_do_not_matter(self) -> None:
        headers = ("BRANCH_ID", "branch", "area", "address", "phone", "hours", "source demo")
        rows, _ = _rows(headers, [BRANCH_ROW], BRANCH_COLUMNS, BRANCH_REQUIRED, "branches")
        assert rows[0]["external_id"] == "B01"

    def test_blank_rows_are_dropped_and_row_numbers_stay_the_sheets_own(self) -> None:
        rows, _ = _rows(
            SLOT_HEADERS,
            [SLOT_ROW, (None,) * len(SLOT_HEADERS), ("S00003", *SLOT_ROW[1:])],
            SLOT_COLUMNS,
            SLOT_REQUIRED,
            "availability",
        )
        assert [row["_row"] for row in rows] == [2, 4]

    def test_a_row_shorter_than_the_header_reads_as_empty_cells(self) -> None:
        rows, _ = _rows(SLOT_HEADERS, [SLOT_ROW[:10]], SLOT_COLUMNS, SLOT_REQUIRED, "availability")
        assert rows[0]["booking_ref"] is None


# ── Branches ───────────────────────────────────────────────────────────────────────────────


class TestBranches:
    def test_every_branch_is_imported_including_the_placeholders(self) -> None:
        """Decision 5: all of them are in the demo, marked, not filtered."""
        plan = _plan(
            branches=(
                BRANCH_ROW,
                ("B02", "Old Town", None, None, None, None, "given"),
                ("B03", "Seaside", None, None, None, None, "placeholder"),
            ),
            slots=(),
        )
        assert [b.external_id for b in plan.branches] == ["B01", "B02", "B03"]
        assert [b.placeholder for b in plan.branches] == [False, False, True]

    def test_given_is_not_folded_into_placeholder(self) -> None:
        """A branch supplied without a full record is real; conflating them overstates the gap."""
        plan = _plan(branches=(("B02", "Old Town", None, None, None, None, "given"),), slots=())
        assert plan.branches[0].placeholder is False

    def test_the_provenance_split_is_reported(self) -> None:
        plan = _plan(
            branches=(BRANCH_ROW, ("B03", "Seaside", None, None, None, None, "placeholder")),
            slots=(),
        )
        assert "placeholder: 1" in _messages(plan, "info")
        assert "real: 1" in _messages(plan, "info")

    def test_without_a_provenance_column_nothing_is_guessed(self) -> None:
        headers = ("Branch ID", "Branch")
        rows, _ = _rows(
            headers, [("B01", "Riverside")], BRANCH_COLUMNS, BRANCH_REQUIRED, "branches"
        )
        plan = plan_import(rows, (), (), timezone=TIMEZONE)
        assert plan.branches[0].placeholder is False
        assert "no placeholder or provenance column" in _messages(plan, "info")

    def test_a_duplicate_branch_id_is_an_error(self) -> None:
        plan = _plan(branches=(BRANCH_ROW, BRANCH_ROW), slots=())
        assert "duplicate branch id B01" in _messages(plan, "error")
        assert not plan.report.ok

    def test_a_branch_without_a_name_is_an_error(self) -> None:
        plan = _plan(branches=(("B01", None, None, None, None, None, "real"),), slots=())
        assert "branch id and name are required" in _messages(plan, "error")


# ── Services, and deviations 1 and 3 ───────────────────────────────────────────────────────


class TestServices:
    def test_price_is_stored_in_minor_units(self) -> None:
        """A price a patient is quoted has not been through a float."""
        plan = _plan(slots=())
        assert plan.services[0].price_minor == 75_000
        assert plan.services[0].currency == "EGP"

    @pytest.mark.parametrize("cell", ["15,000", "EGP 15,000", "15000.00", 15000])
    def test_a_price_is_read_however_the_sheet_spells_it(self, cell: object) -> None:
        plan = _plan(
            services=(("Package", "Laser", cell, 45, "All", "Treatment", "DT100"),), slots=()
        )
        assert plan.services[0].price_minor == 1_500_000

    def test_retail_rows_are_not_in_the_catalogue(self) -> None:
        """Decision 6. The Type column decides, not the Category one."""
        plan = _plan(
            services=(SERVICE_ROW, ("Cleanser", "Skin", 300, 1, "All", "Retail", "DR001")),
            slots=(),
        )
        assert [s.code for s in plan.services] == ["DT001"]
        assert plan.report.skipped_retail == 1

    def test_a_session_count_in_the_name_becomes_the_package_quantity(self) -> None:
        """Every quote has to say whether an amount covers one session or a package."""
        plan = _plan(
            services=(
                ("Laser Package - 6 Sessions", "Laser", 15000, 45, "All", "Treatment", "DT029"),
            ),
            slots=(),
        )
        assert plan.services[0].session_count == 6
        assert "session count 6 read from the name" in _messages(plan, "info")

    def test_a_sessions_column_wins_over_the_name(self) -> None:
        headers = (*SERVICE_HEADERS, "Sessions")
        rows, _ = _rows(
            headers,
            [("Laser Package - 6 Sessions", "Laser", 15000, 45, "All", "Treatment", "DT029", 12)],
            SERVICE_COLUMNS,
            SERVICE_REQUIRED,
            "services",
        )
        plan = plan_import((), rows, (), timezone=TIMEZONE)
        assert plan.services[0].session_count == 12

    def test_a_package_with_no_countable_sessions_is_flagged(self) -> None:
        plan = _plan(
            services=(
                ("Annual Unlimited Sessions", "Laser", 18700, 45, "All", "Treatment", "DT026"),
            ),
            slots=(),
        )
        assert "states no fixed session count" in _messages(plan, "warning")

    def test_a_duplicate_service_id_is_an_error(self) -> None:
        plan = _plan(services=(SERVICE_ROW, SERVICE_ROW), slots=())
        assert "duplicate service id DT001" in _messages(plan, "error")

    @pytest.mark.parametrize(
        ("price", "duration", "expected"),
        [("n/a", 45, "price is not a number"), (750, 0, "duration is not a positive number")],
    )
    def test_an_unusable_price_or_duration_is_an_error(
        self, price: object, duration: object, expected: str
    ) -> None:
        plan = _plan(
            services=(("Deep Facial", "Facial", price, duration, "All", "Treatment", "DT001"),),
            slots=(),
        )
        assert expected in _messages(plan, "error")


class TestPriceCorrection:
    """Deviation 1 (decision 4), and the reason it is conditional."""

    CORRECTION = PriceCorrection("DT029", 150_000, 1_500_000, "priced below the single session")

    def _six_session_package(self, price: object) -> CataloguePlan:
        return _plan(
            services=(
                ("Laser Package - 6 Sessions", "Laser", price, 45, "All", "Treatment", "DT029"),
            ),
            slots=(),
            price_corrections=(self.CORRECTION,),
        )

    def test_the_known_wrong_price_is_forced(self) -> None:
        plan = self._six_session_package(1500)
        assert plan.services[0].price_minor == 1_500_000
        assert "price forced from 1500.00 to 15000.00" in _messages(plan, "info")

    def test_a_file_that_already_holds_the_corrected_price_is_left_alone(self) -> None:
        """The state the workbook is actually in today — reported, not re-forced."""
        plan = self._six_session_package(15000)
        assert plan.services[0].price_minor == 1_500_000
        assert "already holds the corrected price" in _messages(plan, "info")
        assert plan.report.ok

    def test_a_third_value_stops_the_import_rather_than_being_overwritten(self) -> None:
        """A stale correction must not outlive the mistake and silently re-break a price."""
        plan = self._six_session_package(12000)
        assert not plan.report.ok
        assert "The correction is stale" in _messages(plan, "error")

    def test_the_demo_correction_is_a_default_argument_not_a_reachable_constant(self) -> None:
        """One client's correction to one file. An import can be run with a different set."""
        plan = _plan(
            services=(
                ("Laser Package - 6 Sessions", "Laser", 1500, 45, "All", "Treatment", "DT029"),
            ),
            slots=(),
            price_corrections=(),
        )
        assert plan.services[0].price_minor == 150_000
        assert DEMO_PRICE_CORRECTIONS[0].code == "DT029"


class TestAmbiguousNames:
    """Deviation 3: one name must reach one service; look-alikes are reported, not resolved."""

    def test_two_services_sharing_a_name_is_an_error(self) -> None:
        plan = _plan(
            services=(
                ("Deep Facial", "Facial", 750, 45, "All", "Treatment", "DT001"),
                ("deep  facial", "Facial", 900, 45, "All", "Treatment", "DT002"),
            ),
            slots=(),
        )
        assert not plan.report.ok
        assert "reaches DT001, DT002" in _messages(plan, "error")

    def test_an_alias_colliding_with_another_services_name_is_an_error(self) -> None:
        headers = (*SERVICE_HEADERS, "Aliases")
        rows, _ = _rows(
            headers,
            [
                ("Deep Facial", "Facial", 750, 45, "All", "Treatment", "DT001", ""),
                ("Peeling", "Facial", 750, 45, "All", "Treatment", "DT004", "Deep Facial"),
            ],
            SERVICE_COLUMNS,
            SERVICE_REQUIRED,
            "services",
        )
        plan = plan_import((), rows, (), timezone=TIMEZONE)
        assert not plan.report.ok

    def test_services_a_patient_cannot_tell_apart_are_a_warning_not_a_refusal(self) -> None:
        """The client's catalogue really does price three 12-session packages identically."""
        plan = _plan(
            services=(
                ("Annual 12 Sessions", "Laser", 16350, 45, "All", "Treatment", "DT019"),
                ("Full Body 12 Sessions", "Laser", 16350, 45, "All", "Treatment", "DT021"),
            ),
            slots=(),
        )
        assert plan.report.ok
        assert "2 services are identical to a patient" in _messages(plan, "warning")


# ── Availability ───────────────────────────────────────────────────────────────────────────


def _slot_row(**overrides: object) -> tuple[Cell, ...]:
    row = dict(zip(SLOT_HEADERS, SLOT_ROW, strict=True))
    row.update({k.replace("_", " "): v for k, v in overrides.items()})
    return tuple(row.values())


class TestAvailability:
    def test_a_slot_lands_in_the_zone_it_was_told_to_use(self) -> None:
        """The sheet holds a wall clock and names no zone; the default one is a different city."""
        plan = _plan()
        starts_at = plan.slots[0].starts_at
        assert (starts_at.hour, starts_at.minute) == (11, 0)
        assert starts_at.utcoffset() is not None
        assert starts_at.tzinfo is not None and str(starts_at.tzinfo) == TIMEZONE

    def test_a_branch_resolves_by_name_or_by_id(self) -> None:
        by_id = tuple(
            v if h != "Branch" else "B01" for h, v in zip(SLOT_HEADERS, SLOT_ROW, strict=True)
        )
        plan = _plan(slots=(SLOT_ROW, tuple([*by_id[:0], *by_id])[: len(SLOT_ROW)]))
        assert {s.branch_external_id for s in plan.slots} == {"B01"}

    def test_a_service_resolves_through_an_alias(self) -> None:
        """Deviation 3's canonical id: the sheet's free text reaches one catalogue entry."""
        service_headers = (*SERVICE_HEADERS, "Aliases")
        service_rows, _ = _rows(
            service_headers,
            [("Deep Facial", "Facial", 750, 45, "All", "Treatment", "DT001", "Facial; فيشيال")],
            SERVICE_COLUMNS,
            SERVICE_REQUIRED,
            "services",
        )
        branch_rows, _ = _rows(
            BRANCH_HEADERS, [BRANCH_ROW], BRANCH_COLUMNS, BRANCH_REQUIRED, "branches"
        )
        slot_rows, _ = _rows(
            SLOT_HEADERS,
            [_slot_row(Service="فيشيال")],
            SLOT_COLUMNS,
            SLOT_REQUIRED,
            "availability",
        )
        plan = plan_import(branch_rows, service_rows, slot_rows, timezone=TIMEZONE)
        assert plan.report.ok
        assert plan.slots[0].service_code == "DT001"

    def test_a_service_name_that_reaches_nothing_stops_the_import(self) -> None:
        plan = _plan(slots=(_slot_row(Service="Cryo Sculpt"),))
        assert not plan.report.ok
        assert "resolves to no catalogue entry" in _messages(plan, "error")

    def test_an_unknown_branch_stops_the_import(self) -> None:
        plan = _plan(slots=(_slot_row(Branch="Seaside"),))
        assert "is not in the branches sheet" in _messages(plan, "error")

    def test_a_duplicate_slot_id_is_an_error(self) -> None:
        plan = _plan(slots=(SLOT_ROW, SLOT_ROW))
        assert "duplicate slot id S00001" in _messages(plan, "error")

    def test_the_end_is_derived_from_the_service_when_the_sheet_omits_it(self) -> None:
        plan = _plan(slots=(_slot_row(End=None),))
        assert plan.slots[0].duration_minutes == 45

    def test_a_slot_that_ends_before_it_starts_is_an_error(self) -> None:
        plan = _plan(slots=(_slot_row(End="10:00"),))
        assert "ends at or before it starts" in _messages(plan, "error")

    @pytest.mark.parametrize(("cell", "expected"), [("Open", "open"), ("Booked", "booked")])
    def test_the_workbooks_own_status_words(self, cell: str, expected: str) -> None:
        ref = "DC-0001" if expected == "booked" else None
        plan = _plan(slots=(_slot_row(Status=cell, **{"Booking Ref": ref}),))
        assert plan.slots[0].status == expected

    def test_a_status_that_is_neither_open_nor_booked_is_an_error(self) -> None:
        plan = _plan(slots=(_slot_row(Status="Pencilled in"),))
        assert "is neither open nor booked" in _messages(plan, "error")

    def test_a_booked_slot_with_no_reference_is_an_error(self) -> None:
        plan = _plan(slots=(_slot_row(Status="Booked"),))
        assert "booked with no booking reference" in _messages(plan, "error")

    def test_an_open_slot_carrying_a_reference_is_an_error(self) -> None:
        """Contradictory: one of the two cells is wrong and guessing which loses an appointment."""
        plan = _plan(slots=(_slot_row(**{"Booking Ref": "DC-0001"}),))
        assert "open but carries booking reference" in _messages(plan, "error")

    def test_a_booked_slot_becomes_a_booking_with_the_clinics_own_reference(self) -> None:
        plan = _plan(
            slots=(
                _slot_row(
                    Status="Booked",
                    **{"Booking Ref": "DC-0042", "Patient (first name)": "Rana"},
                ),
            )
        )
        (booking,) = plan.bookings
        assert (booking.reference, booking.slot_external_id) == ("DC-0042", "S00001")
        assert (booking.source, booking.patient_name) == ("workbook", "Rana")

    def test_a_duplicate_booking_reference_is_an_error(self) -> None:
        plan = _plan(
            slots=(
                _slot_row(Status="Booked", **{"Booking Ref": "DC-0042"}),
                _slot_row(
                    **{"Slot ID": "S00002", "Start": "12:00", "End": "12:45"},
                    Status="Booked",
                    **{"Booking Ref": "DC-0042"},
                ),
            )
        )
        assert "duplicate booking reference DC-0042" in _messages(plan, "error")

    def test_the_highest_reference_already_taken_is_reported(self) -> None:
        """Step 6 issues after it; a reissued number is two patients holding one appointment."""
        plan = _plan(
            slots=(
                _slot_row(Status="Booked", **{"Booking Ref": "DC-0042"}),
                _slot_row(
                    **{"Slot ID": "S2", "Start": "12:00", "End": "12:45"},
                    Status="Booked",
                    **{"Booking Ref": "DC-0265"},
                ),
            )
        )
        assert plan.report.highest_reference == {"DC": 265}

    def test_a_reference_the_clinic_wrote_in_its_own_words_is_kept_verbatim(self) -> None:
        plan = _plan(slots=(_slot_row(Status="Booked", **{"Booking Ref": "walk-in Tuesday"}),))
        assert plan.bookings[0].reference == "walk-in Tuesday"
        assert plan.report.highest_reference == {}


class TestBufferAndOverlaps:
    """Deviation 2 (decision 3): the workbook is authoritative for what is already in the diary."""

    BACK_TO_BACK = (
        _slot_row(**{"Slot ID": "S1", "Start": "11:00", "End": "12:00"}),
        _slot_row(**{"Slot ID": "S2", "Start": "12:00", "End": "13:00"}),
    )

    def test_back_to_back_slots_are_imported_not_rejected(self) -> None:
        plan = _plan(slots=self.BACK_TO_BACK)
        assert plan.report.ok
        assert len(plan.slots) == 2
        assert plan.report.back_to_back_pairs == 1
        assert "imported as the workbook has them" in _messages(plan, "info")

    def test_slots_in_different_branches_are_not_a_pair(self) -> None:
        plan = _plan(
            branches=(BRANCH_ROW, ("B02", "Old Town", None, None, None, None, "real")),
            slots=(
                _slot_row(**{"Slot ID": "S1", "Start": "11:00", "End": "12:00"}),
                _slot_row(**{"Slot ID": "S2", "Start": "12:00", "End": "13:00"}, Branch="Old Town"),
            ),
        )
        assert plan.report.back_to_back_pairs == 0

    def test_two_slots_covering_the_same_minute_stop_the_import(self) -> None:
        plan = _plan(
            slots=(
                _slot_row(**{"Slot ID": "S1", "Start": "11:00", "End": "12:00"}),
                _slot_row(**{"Slot ID": "S2", "Start": "11:30", "End": "12:30"}),
            )
        )
        assert not plan.report.ok
        assert "overlap in branch B01" in _messages(plan, "error")

    def test_a_clinic_that_really_runs_two_rooms_has_a_switch(self) -> None:
        plan = _plan(
            slots=(
                _slot_row(**{"Slot ID": "S1", "Start": "11:00", "End": "12:00"}),
                _slot_row(**{"Slot ID": "S2", "Start": "11:30", "End": "12:30"}),
            ),
            allow_overlaps=True,
        )
        assert plan.report.ok
        assert plan.report.overlapping_pairs == 1


class TestReport:
    def test_the_counts_a_reviewer_compares_against_the_last_import(self) -> None:
        plan = _plan(
            slots=(
                _slot_row(),
                _slot_row(
                    **{
                        "Slot ID": "S2",
                        "Start": "12:00",
                        "End": "12:45",
                        "Date": "2026-09-03",
                        "Booking Ref": "DC-0002",
                    },
                    Status="Booked",
                ),
            )
        )
        report = plan.report
        assert (report.branches, report.services, report.slots, report.bookings) == (1, 1, 2, 1)
        assert report.slots_by_date == {"2026-09-02": 1, "2026-09-03": 1}
        assert report.slots_by_status == {"booked": 1, "open": 1}
        assert "1 branches, 1 services, 2 slots, 1 bookings" in report.summary()

    def test_one_error_is_enough_for_the_plan_not_to_be_ok(self) -> None:
        plan = _plan(slots=(_slot_row(Status="Pencilled in"),))
        assert not plan.report.ok
        assert plan.report.warnings == () or plan.report.errors

    def test_an_issue_reads_as_a_line_an_operator_can_act_on(self) -> None:
        issue = ImportIssue("error", "availability", "S1: something", row=42)
        assert str(issue) == "[error] availability row 42: S1: something"
        assert str(ImportIssue("info", "services", "no rows")) == "[info] services: no rows"


class TestCellsAsASpreadsheetHandsThemOver:
    """The same column arrives as text one week and as a typed cell the next.

    A workbook that has been through Excel, Numbers and a Google Sheets round-trip gives dates as
    strings, as ``date``s and as ``datetime``s, and times as strings, ``time``s and durations.
    None of that is worth an import failing over, and all of it is worth being explicit about.
    """

    def test_a_blank_header_cell_is_skipped_rather_than_mapped(self) -> None:
        headers = ("Branch ID", None, "Branch", "   ")
        rows, _ = _rows(
            headers, [("B01", "", "Riverside", "")], BRANCH_COLUMNS, BRANCH_REQUIRED, "b"
        )
        assert rows[0]["name"] == "Riverside"

    @pytest.mark.parametrize(
        "cell",
        ["2026-09-02", "02/09/2026", "02-09-2026", "2026/09/02", date(2026, 9, 2)],
    )
    def test_a_date_is_read_however_the_cell_holds_it(self, cell: object) -> None:
        plan = _plan(slots=(_slot_row(Date=cell),))
        assert plan.report.ok
        assert plan.slots[0].starts_at.date() == date(2026, 9, 2)

    def test_a_datetime_cell_contributes_only_its_date(self) -> None:
        plan = _plan(slots=(_slot_row(Date=datetime(2026, 9, 2, 23, 30)),))
        assert plan.slots[0].starts_at.date() == date(2026, 9, 2)
        assert plan.slots[0].starts_at.hour == 11

    @pytest.mark.parametrize(
        "cell",
        [
            "11:00",
            "11:00:00",
            "11:00 AM",
            time(11, 0),
            datetime(2026, 9, 2, 11, 0),
            timedelta(hours=11),
        ],
    )
    def test_a_start_time_is_read_however_the_cell_holds_it(self, cell: object) -> None:
        plan = _plan(slots=(_slot_row(Start=cell, End=None),))
        assert plan.report.ok
        assert (plan.slots[0].starts_at.hour, plan.slots[0].starts_at.minute) == (11, 0)

    @pytest.mark.parametrize("cell", ["not a date", "", None, "31/02/2026"])
    def test_an_unreadable_date_stops_that_slot(self, cell: object) -> None:
        plan = _plan(slots=(_slot_row(Date=cell),))
        assert "date or start time unreadable" in _messages(plan, "error")
        assert plan.slots == ()

    def test_an_unreadable_start_time_stops_that_slot(self) -> None:
        plan = _plan(slots=(_slot_row(Start="elevenish"),))
        assert "date or start time unreadable" in _messages(plan, "error")

    def test_a_slot_with_no_id_is_an_error(self) -> None:
        plan = _plan(slots=(_slot_row(**{"Slot ID": None}),))
        assert "slot id is required" in _messages(plan, "error")

    def test_a_service_with_no_id_is_an_error(self) -> None:
        plan = _plan(
            services=(("Deep Facial", "Facial", 750, 45, "All", "Treatment", None),), slots=()
        )
        assert "service id and name are required" in _messages(plan, "error")

    @pytest.mark.parametrize("cell", [None, "-", "", "n/a", "1.2.3"])
    def test_a_price_that_is_not_a_number_is_an_error(self, cell: object) -> None:
        plan = _plan(
            services=(("Deep Facial", "Facial", cell, 45, "All", "Treatment", "DT001"),), slots=()
        )
        assert "price is not a number" in _messages(plan, "error")

    def test_a_duration_written_as_text_is_still_a_duration(self) -> None:
        plan = _plan(
            services=(("Deep Facial", "Facial", 750, "45 min", "All", "Treatment", "DT001"),),
            slots=(),
        )
        assert plan.services[0].duration_minutes == 45

    @pytest.mark.parametrize("cell", [None, "about an hour", "-"])
    def test_a_duration_that_is_not_a_number_is_an_error(self, cell: object) -> None:
        plan = _plan(
            services=(("Deep Facial", "Facial", 750, cell, "All", "Treatment", "DT001"),), slots=()
        )
        assert "duration is not a positive number" in _messages(plan, "error")

    def test_an_explicit_placeholder_column_wins_over_the_provenance_one(self) -> None:
        headers = (*BRANCH_HEADERS, "Placeholder")
        rows, _ = _rows(
            headers,
            [(*BRANCH_ROW, "yes")],
            BRANCH_COLUMNS,
            BRANCH_REQUIRED,
            "branches",
        )
        plan = plan_import(rows, (), (), timezone=TIMEZONE)
        assert plan.branches[0].placeholder is True
