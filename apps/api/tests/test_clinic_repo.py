"""Persisting a catalogue plan (demo steps 3–4), on the real schema.

The plans here are built from domain objects rather than from a workbook: what is under test is
what the database ends up holding, and routing every case through the sheet parser would only make
the failures harder to read. The importer's own tests cover the sheet.

Three behaviours these tests exist for, all of them things a second import could get wrong:
re-importing the same file must not duplicate anything, a plan with errors must not reach the
database at all, and an import must never reopen a slot that a patient has been told they hold.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest

from apps.api.clinic.importer import CataloguePlan, ImportIssue, ImportReport
from apps.api.core.clinic import (
    AvailabilitySlot,
    Booking,
    BookingOutcome,
    Branch,
    Service,
)
from apps.api.db.clinic_repo import CatalogueImportRefused, SqlAlchemyClinicRepository
from apps.api.db.engine import Database
from apps.api.db.models import ClinicAvailabilitySlot, ClinicBooking

TENANT = str(uuid.uuid4())
OTHER_TENANT = str(uuid.uuid4())
CAIRO_MORNING = datetime(2026, 9, 2, 11, 0, tzinfo=UTC)


def _slot(
    external_id: str, *, start: datetime = CAIRO_MORNING, status: str = "open"
) -> AvailabilitySlot:
    return AvailabilitySlot(
        external_id=external_id,
        branch_external_id="B01",
        service_code="DT001",
        starts_at=start,
        ends_at=start + timedelta(minutes=45),
        status="booked" if status == "booked" else "open",
    )


def _plan(
    *,
    branches: tuple[Branch, ...] = (Branch(external_id="B01", name="Riverside"),),
    services: tuple[Service, ...] = (
        Service(code="DT001", name="Deep Facial", price_minor=75_000, duration_minutes=45),
    ),
    slots: tuple[AvailabilitySlot, ...] = (_slot("S00001"),),
    bookings: tuple[Booking, ...] = (),
    issues: tuple[ImportIssue, ...] = (),
) -> CataloguePlan:
    return CataloguePlan(
        branches=branches,
        services=services,
        slots=slots,
        bookings=bookings,
        report=ImportReport(issues=issues),
    )


@pytest.fixture
def repository(database: Database) -> SqlAlchemyClinicRepository:
    return SqlAlchemyClinicRepository(database.tenant_session)


class TestImport:
    def test_a_plan_becomes_rows_that_read_back_as_the_same_objects(
        self, repository: SqlAlchemyClinicRepository
    ) -> None:
        outcome = repository.import_catalogue(TENANT, _plan(), import_version="demo-v1")

        assert (outcome.branches_written, outcome.services_written, outcome.slots_written) == (
            1,
            1,
            1,
        )
        assert repository.list_branches(TENANT) == [Branch(external_id="B01", name="Riverside")]
        assert repository.list_services(TENANT)[0].price_minor == 75_000

        (slot,) = repository.list_slots(TENANT)
        assert (slot.external_id, slot.branch_external_id, slot.service_code) == (
            "S00001",
            "B01",
            "DT001",
        )
        # SQLite hands datetimes back naive; the value is what matters, not the tzinfo. On
        # Postgres the column is TIMESTAMPTZ and the offset survives.
        assert slot.starts_at.replace(tzinfo=UTC) == CAIRO_MORNING
        assert slot.duration_minutes == 45

    def test_provenance_is_stamped_on_every_imported_row(
        self, repository: SqlAlchemyClinicRepository, database: Database
    ) -> None:
        """``imported_catalogue`` promises a source id, an import version and a fetched time."""
        at = datetime(2026, 8, 28, 9, 0, tzinfo=UTC)
        repository.import_catalogue(TENANT, _plan(), import_version="demo-v1", imported_at=at)

        with database.tenant_session(TENANT) as session:
            row = session.query(ClinicAvailabilitySlot).one()
            assert row.import_version == "demo-v1"
            assert row.imported_at is not None

    def test_re_importing_the_same_workbook_updates_rather_than_duplicates(
        self, repository: SqlAlchemyClinicRepository
    ) -> None:
        """A corrected file is re-imported in the days before a demo. It must be safe."""
        repository.import_catalogue(TENANT, _plan(), import_version="demo-v1")
        corrected = _plan(
            services=(
                Service(code="DT001", name="Deep Facial", price_minor=90_000, duration_minutes=45),
            )
        )
        repository.import_catalogue(TENANT, corrected, import_version="demo-v2")

        assert len(repository.list_services(TENANT)) == 1
        assert repository.list_services(TENANT)[0].price_minor == 90_000
        assert len(repository.list_slots(TENANT)) == 1

    def test_a_plan_with_errors_never_reaches_the_database(
        self, repository: SqlAlchemyClinicRepository
    ) -> None:
        """Half a catalogue is neither the old one nor the new one, and nothing says which."""
        broken = _plan(issues=(ImportIssue("error", "services", "price is not a number"),))

        with pytest.raises(CatalogueImportRefused, match="1 validation errors"):
            repository.import_catalogue(TENANT, broken, import_version="demo-v1")

        assert repository.list_branches(TENANT) == []
        assert repository.list_slots(TENANT) == []

    def test_one_tenants_catalogue_is_not_another_tenants(
        self, repository: SqlAlchemyClinicRepository
    ) -> None:
        repository.import_catalogue(TENANT, _plan(), import_version="demo-v1")

        assert repository.list_branches(OTHER_TENANT) == []
        assert repository.list_services(OTHER_TENANT) == []
        assert repository.list_slots(OTHER_TENANT) == []

    def test_the_same_external_ids_belong_to_each_tenant_separately(
        self, repository: SqlAlchemyClinicRepository
    ) -> None:
        repository.import_catalogue(TENANT, _plan(), import_version="demo-v1")
        repository.import_catalogue(OTHER_TENANT, _plan(), import_version="demo-v1")

        assert len(repository.list_branches(TENANT)) == 1
        assert len(repository.list_branches(OTHER_TENANT)) == 1


class TestWhatAnImportRetires:
    def test_a_branch_the_workbook_no_longer_lists_is_deactivated_not_deleted(
        self, repository: SqlAlchemyClinicRepository
    ) -> None:
        repository.import_catalogue(
            TENANT,
            _plan(
                branches=(
                    Branch(external_id="B01", name="Riverside"),
                    Branch(external_id="B02", name="Old Town"),
                )
            ),
            import_version="demo-v1",
        )
        outcome = repository.import_catalogue(TENANT, _plan(), import_version="demo-v2")

        assert outcome.branches_deactivated == 1
        assert [b.external_id for b in repository.list_branches(TENANT)] == ["B01"]
        assert len(repository.list_branches(TENANT, active_only=False)) == 2

    def test_a_service_the_workbook_no_longer_lists_is_deactivated(
        self, repository: SqlAlchemyClinicRepository
    ) -> None:
        two = (
            Service(code="DT001", name="Deep Facial", price_minor=75_000, duration_minutes=45),
            Service(code="DR001", name="Cleanser", price_minor=30_000, duration_minutes=5),
        )
        repository.import_catalogue(TENANT, _plan(services=two), import_version="demo-v1")
        outcome = repository.import_catalogue(TENANT, _plan(), import_version="demo-v2")

        assert outcome.services_deactivated == 1
        assert [s.code for s in repository.list_services(TENANT)] == ["DT001"]

    def test_a_slot_the_workbook_no_longer_lists_is_counted_never_deleted(
        self, repository: SqlAlchemyClinicRepository
    ) -> None:
        """Deleting a slot silently deletes any argument about a booking on it."""
        repository.import_catalogue(
            TENANT, _plan(slots=(_slot("S00001"), _slot("S00002"))), import_version="demo-v1"
        )
        outcome = repository.import_catalogue(TENANT, _plan(), import_version="demo-v2")

        assert outcome.slots_not_in_workbook == 1
        assert len(repository.list_slots(TENANT)) == 2


class TestBookings:
    def test_a_booked_slot_from_the_workbook_becomes_a_booking(
        self, repository: SqlAlchemyClinicRepository
    ) -> None:
        plan = _plan(
            slots=(_slot("S00001", status="booked"),),
            bookings=(
                Booking(
                    reference="DC-0042",
                    slot_external_id="S00001",
                    source="workbook",
                    patient_name="Rana",
                ),
            ),
        )
        outcome = repository.import_catalogue(TENANT, plan, import_version="demo-v1")

        assert outcome.bookings_written == 1
        (booking,) = repository.list_bookings(TENANT)
        assert (booking.reference, booking.slot_external_id) == ("DC-0042", "S00001")
        assert (booking.source, booking.patient_name) == ("workbook", "Rana")
        assert repository.list_slots(TENANT, status="open") == []
        assert len(repository.list_slots(TENANT, status="booked")) == 1

    def test_a_re_import_never_reopens_a_slot_this_system_booked(
        self, repository: SqlAlchemyClinicRepository, database: Database
    ) -> None:
        """The workbook was exported before that appointment existed (module docstring)."""
        repository.import_catalogue(TENANT, _plan(), import_version="demo-v1")
        with database.tenant_session(TENANT) as session:
            slot = session.query(ClinicAvailabilitySlot).one()
            slot.status = "booked"
            session.add(
                ClinicBooking(
                    tenant_id=uuid.UUID(TENANT),
                    reference="DC-9001",
                    slot_id=slot.id,
                    source="bot",
                    idempotency_key=f"{TENANT}:conv-1:S00001",
                )
            )
            session.commit()

        outcome = repository.import_catalogue(TENANT, _plan(), import_version="demo-v2")

        assert outcome.slots_protected == 1
        assert repository.list_slots(TENANT)[0].status == "booked"
        assert repository.list_bookings(TENANT)[0].source == "bot"

    def test_a_workbook_row_does_not_overwrite_a_booking_this_system_made(
        self, repository: SqlAlchemyClinicRepository, database: Database
    ) -> None:
        repository.import_catalogue(TENANT, _plan(), import_version="demo-v1")
        with database.tenant_session(TENANT) as session:
            slot = session.query(ClinicAvailabilitySlot).one()
            session.add(
                ClinicBooking(
                    tenant_id=uuid.UUID(TENANT),
                    reference="DC-0042",
                    slot_id=slot.id,
                    source="bot",
                    patient_name="Rana",
                )
            )
            session.commit()

        plan = _plan(
            slots=(_slot("S00001", status="booked"),),
            bookings=(Booking(reference="DC-0042", slot_external_id="S00001", source="workbook"),),
        )
        outcome = repository.import_catalogue(TENANT, plan, import_version="demo-v2")

        assert outcome.bookings_written == 0
        (booking,) = repository.list_bookings(TENANT)
        assert (booking.source, booking.patient_name) == ("bot", "Rana")

    def test_the_outcome_reads_as_a_line_an_operator_can_compare(
        self, repository: SqlAlchemyClinicRepository
    ) -> None:
        outcome = repository.import_catalogue(TENANT, _plan(), import_version="demo-v1")
        assert "wrote 1 branches, 1 services, 1 slots, 0 bookings" in outcome.summary()


# ── The booking journey (demo step 6) ──────────────────────────────────────────────────────

CAIRO = "Africa/Cairo"
#: 2 September 2026, 14:00 Cairo. Written in UTC because that is what the column holds.
AFTERNOON = datetime(2026, 9, 2, 11, 0, tzinfo=UTC)
CONVERSATION = str(uuid.uuid4())
OTHER_CONVERSATION = str(uuid.uuid4())


def _diary(*starts: datetime) -> CataloguePlan:
    return _plan(slots=tuple(_slot(f"S{i:05d}", start=at) for i, at in enumerate(starts, start=1)))


class TestAvailability:
    def test_open_slots_come_back_for_the_local_day_they_fall_on(
        self, repository: SqlAlchemyClinicRepository
    ) -> None:
        """The day is the *patient's*. 21:30 UTC is the next morning in Cairo and must not appear
        under today, which is exactly how a demo offers an appointment on the wrong date."""
        repository.import_catalogue(
            TENANT,
            _diary(AFTERNOON, datetime(2026, 9, 2, 21, 30, tzinfo=UTC)),
            import_version="v1",
        )
        found = repository.available_slots(
            TENANT,
            service_code="DT001",
            branch_external_id="B01",
            on_date=date(2026, 9, 2),
            timezone=CAIRO,
        )
        assert [slot.external_id for slot in found] == ["S00001"]

    def test_a_slot_held_by_somebody_else_is_not_offered(
        self, repository: SqlAlchemyClinicRepository
    ) -> None:
        repository.import_catalogue(TENANT, _diary(AFTERNOON), import_version="v1")
        assert repository.hold_slot(
            TENANT,
            slot_external_id="S00001",
            conversation_id=OTHER_CONVERSATION,
            until=AFTERNOON + timedelta(minutes=10),
            now=AFTERNOON,
        )
        assert (
            repository.available_slots(
                TENANT,
                service_code="DT001",
                branch_external_id="B01",
                on_date=date(2026, 9, 2),
                timezone=CAIRO,
                now=AFTERNOON,
                conversation_id=CONVERSATION,
            )
            == []
        )

    def test_a_slot_this_conversation_holds_is_still_offered_to_it(
        self, repository: SqlAlchemyClinicRepository
    ) -> None:
        """Otherwise the hold placed to keep a slot for a patient is what takes it from them."""
        repository.import_catalogue(TENANT, _diary(AFTERNOON), import_version="v1")
        repository.hold_slot(
            TENANT,
            slot_external_id="S00001",
            conversation_id=CONVERSATION,
            until=AFTERNOON + timedelta(minutes=10),
            now=AFTERNOON,
        )
        found = repository.available_slots(
            TENANT,
            service_code="DT001",
            branch_external_id="B01",
            on_date=date(2026, 9, 2),
            timezone=CAIRO,
            now=AFTERNOON,
            conversation_id=CONVERSATION,
        )
        assert [slot.external_id for slot in found] == ["S00001"]

    def test_an_expired_hold_is_available_to_anybody(
        self, repository: SqlAlchemyClinicRepository
    ) -> None:
        """Nothing sweeps holds, so the expiry has to be read rather than enforced by a job."""
        repository.import_catalogue(TENANT, _diary(AFTERNOON), import_version="v1")
        repository.hold_slot(
            TENANT,
            slot_external_id="S00001",
            conversation_id=OTHER_CONVERSATION,
            until=AFTERNOON,
            now=AFTERNOON - timedelta(minutes=10),
        )
        found = repository.available_slots(
            TENANT,
            service_code="DT001",
            branch_external_id="B01",
            on_date=date(2026, 9, 2),
            timezone=CAIRO,
            now=AFTERNOON + timedelta(minutes=1),
            conversation_id=CONVERSATION,
        )
        assert [slot.external_id for slot in found] == ["S00001"]


class TestHold:
    def test_two_conversations_cannot_both_hold_one_slot(
        self, repository: SqlAlchemyClinicRepository
    ) -> None:
        repository.import_catalogue(TENANT, _diary(AFTERNOON), import_version="v1")
        until = AFTERNOON + timedelta(minutes=10)
        assert repository.hold_slot(
            TENANT,
            slot_external_id="S00001",
            conversation_id=CONVERSATION,
            until=until,
            now=AFTERNOON,
        )
        assert not repository.hold_slot(
            TENANT,
            slot_external_id="S00001",
            conversation_id=OTHER_CONVERSATION,
            until=until,
            now=AFTERNOON,
        )

    def test_re_holding_your_own_slot_extends_it(
        self, repository: SqlAlchemyClinicRepository
    ) -> None:
        """A hold that expires while the patient is typing their name is the worse failure."""
        repository.import_catalogue(TENANT, _diary(AFTERNOON), import_version="v1")
        assert repository.hold_slot(
            TENANT,
            slot_external_id="S00001",
            conversation_id=CONVERSATION,
            until=AFTERNOON + timedelta(minutes=5),
            now=AFTERNOON,
        )
        assert repository.hold_slot(
            TENANT,
            slot_external_id="S00001",
            conversation_id=CONVERSATION,
            until=AFTERNOON + timedelta(minutes=15),
            now=AFTERNOON + timedelta(minutes=4),
        )


class TestConfirm:
    def _booked(
        self, repository: SqlAlchemyClinicRepository, *, conversation: str = CONVERSATION
    ) -> BookingOutcome:
        return repository.confirm_booking(
            TENANT,
            slot_external_id="S00001",
            conversation_id=conversation,
            reference_prefix="DC",
            patient_name="Rana",
            now=AFTERNOON,
        )

    def test_confirming_writes_an_appointment_and_issues_a_reference(
        self, repository: SqlAlchemyClinicRepository
    ) -> None:
        repository.import_catalogue(TENANT, _diary(AFTERNOON), import_version="v1")
        outcome = self._booked(repository)

        assert outcome.reason == "confirmed"
        assert outcome.booking is not None
        assert outcome.booking.reference == "DC-0001"
        assert outcome.booking.source == "bot"
        assert repository.list_slots(TENANT, status="booked")[0].external_id == "S00001"

    def test_a_reference_never_collides_with_one_the_clinic_already_gave_out(
        self, repository: SqlAlchemyClinicRepository
    ) -> None:
        """The workbook brought in 265 references that patients are already holding."""
        plan = _plan(
            slots=(_slot("S00001"), _slot("S00002", status="booked")),
            bookings=(Booking(reference="DC-0265", slot_external_id="S00002", source="workbook"),),
        )
        repository.import_catalogue(TENANT, plan, import_version="v1")
        outcome = self._booked(repository)
        assert outcome.booking is not None
        assert outcome.booking.reference == "DC-0266"

    def test_the_same_conversation_confirming_twice_gets_one_appointment(
        self, repository: SqlAlchemyClinicRepository
    ) -> None:
        """A duplicate webhook, or a patient tapping send again. One row, one reference."""
        repository.import_catalogue(TENANT, _diary(AFTERNOON), import_version="v1")
        first = self._booked(repository)
        second = self._booked(repository)

        assert second.reason == "already_confirmed"
        assert first.booking is not None and second.booking is not None
        assert second.booking.reference == first.booking.reference
        assert len(repository.list_bookings(TENANT)) == 1

    def test_a_slot_somebody_else_booked_is_refused_rather_than_double_booked(
        self, repository: SqlAlchemyClinicRepository
    ) -> None:
        repository.import_catalogue(TENANT, _diary(AFTERNOON), import_version="v1")
        self._booked(repository)
        second = self._booked(repository, conversation=OTHER_CONVERSATION)

        assert second.reason == "slot_taken"
        assert second.booking is None
        assert len(repository.list_bookings(TENANT)) == 1

    def test_a_slot_another_conversation_is_holding_is_refused(
        self, repository: SqlAlchemyClinicRepository
    ) -> None:
        repository.import_catalogue(TENANT, _diary(AFTERNOON), import_version="v1")
        repository.hold_slot(
            TENANT,
            slot_external_id="S00001",
            conversation_id=OTHER_CONVERSATION,
            until=AFTERNOON + timedelta(minutes=10),
            now=AFTERNOON,
        )
        assert self._booked(repository).reason == "held_by_another"

    def test_an_unknown_slot_is_an_outcome_not_an_exception(
        self, repository: SqlAlchemyClinicRepository
    ) -> None:
        repository.import_catalogue(TENANT, _diary(AFTERNOON), import_version="v1")
        outcome = repository.confirm_booking(
            TENANT,
            slot_external_id="S99999",
            conversation_id=CONVERSATION,
            reference_prefix="DC",
        )
        assert outcome.reason == "slot_unknown"

    def test_the_buffer_stops_us_stacking_our_own_bookings_back_to_back(
        self, repository: SqlAlchemyClinicRepository
    ) -> None:
        repository.import_catalogue(
            TENANT, _diary(AFTERNOON, AFTERNOON + timedelta(minutes=45)), import_version="v1"
        )
        assert self._booked(repository).reason == "confirmed"
        adjacent = repository.confirm_booking(
            TENANT,
            slot_external_id="S00002",
            conversation_id=OTHER_CONVERSATION,
            reference_prefix="DC",
            now=AFTERNOON,
        )
        assert adjacent.reason == "too_close"

    def test_the_buffer_is_not_applied_to_the_clinics_own_imported_diary(
        self, repository: SqlAlchemyClinicRepository
    ) -> None:
        """Decision 3: the workbook is authoritative, 62 back-to-back pairs included.

        Checked against the imported rows as well, the buffer would refuse most of the 407 open
        slots — in an hourly grid nearly every one of them is adjacent to a booked one — and the
        clinic would be told its own diary is invalid.
        """
        plan = _plan(
            slots=(
                _slot("S00001", start=AFTERNOON),
                _slot("S00002", start=AFTERNOON + timedelta(minutes=45), status="booked"),
            ),
            bookings=(Booking(reference="DC-0100", slot_external_id="S00002", source="workbook"),),
        )
        repository.import_catalogue(TENANT, plan, import_version="v1")
        assert self._booked(repository).reason == "confirmed"

    def test_another_tenants_diary_is_invisible(
        self, repository: SqlAlchemyClinicRepository
    ) -> None:
        repository.import_catalogue(TENANT, _diary(AFTERNOON), import_version="v1")
        assert (
            repository.available_slots(
                OTHER_TENANT,
                service_code="DT001",
                branch_external_id="B01",
                on_date=date(2026, 9, 2),
                timezone=CAIRO,
            )
            == []
        )
