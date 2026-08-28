"""Persistence for the clinic catalogue and diary (demo steps 3–6).

The write side is one method — :meth:`SqlAlchemyClinicRepository.import_catalogue` — and it is the
only thing that turns a validated :class:`~apps.api.clinic.importer.CataloguePlan` into rows. Three
properties it has to have, and each is a decision rather than an implementation detail:

**It upserts on the clinic's own keys.** A branch is matched by ``external_id``, a service by
``code``, a slot by ``external_id``, a booking by ``reference``. Running the same workbook twice
updates what it wrote the first time, so a corrected file is re-imported rather than merged by
hand — which is what will actually happen in the days before a demo.

**It refuses a plan it was told not to trust.** A plan whose report carries an error does not
reach the database at all. The alternative — write the valid rows and skip the rest — leaves a
catalogue that is neither the old one nor the new one, and no one can tell which by looking.

**It never releases a booking this system made.** A re-import must not reopen a slot that a
patient has been told they hold. A slot whose booking came from anywhere but the workbook keeps
its status and its times; the import counts those and reports them instead. This is the one place
where the workbook is *not* authoritative, and it is deliberate: decision 2 makes the workbook the
source of truth for the clinic's hours and its own diary, not for appointments made after it was
exported.

**Step 6 adds the write side of a conversation**, and it obeys one rule the import does not: it
never decides anything it can lose a race on. :meth:`SqlAlchemyClinicRepository.hold_slot` is a
conditional ``UPDATE`` and reads its own row count; :meth:`confirm_booking` inserts against the
``(tenant, slot)`` and ``(tenant, idempotency_key)`` constraints and treats the integrity error as
an answer rather than a failure. Two patients messaging about the last 18:00 at the same moment is
not a hypothetical on a demo where one number is handed round a room, and "check then write" would
tell both of them they have it.

Rows the plan does not mention are never deleted. Branches and services absent from it are
deactivated (``active=False``), which is what the read paths already filter on; slots absent from
it are left alone and counted, because deleting a slot silently deletes any booking argument about
it, and a demo week's diary is not worth that.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from apps.api.clinic.importer import CataloguePlan
from apps.api.core.clinic import (
    BOOKING_BUFFER_MINUTES,
    AvailabilitySlot,
    Booking,
    BookingOutcome,
    BookingReference,
    BookingSource,
    Branch,
    Service,
    SlotStatus,
    booking_idempotency_key,
)
from apps.api.db.engine import TenantScope
from apps.api.db.models import (
    ClinicAvailabilitySlot,
    ClinicBooking,
    ClinicBranch,
    ClinicService,
)


class CatalogueImportRefused(RuntimeError):
    """The plan carried validation errors and was not written. See ``report.errors``."""


@dataclass(frozen=True, slots=True)
class ImportOutcome:
    """What one import did. Counts, so a re-run can be compared against the last one."""

    branches_written: int = 0
    services_written: int = 0
    slots_written: int = 0
    bookings_written: int = 0
    branches_deactivated: int = 0
    services_deactivated: int = 0
    #: Slots left untouched because a booking this system made holds them.
    slots_protected: int = 0
    #: Slots already stored that the workbook no longer mentions. Not deleted; look at them.
    slots_not_in_workbook: int = 0

    def summary(self) -> str:
        return (
            f"wrote {self.branches_written} branches, {self.services_written} services, "
            f"{self.slots_written} slots, {self.bookings_written} bookings; "
            f"deactivated {self.branches_deactivated} branches and "
            f"{self.services_deactivated} services; protected {self.slots_protected} booked "
            f"slots; {self.slots_not_in_workbook} stored slots are not in this workbook"
        )


class SqlAlchemyClinicRepository:
    """A tenant's clinic catalogue and diary. One session per call (``TenantScope``, B2)."""

    def __init__(self, scope: TenantScope) -> None:
        self._scope = scope

    # ── write ──────────────────────────────────────────────────────────────────────────────

    def import_catalogue(
        self,
        tenant_id: str,
        plan: CataloguePlan,
        *,
        import_version: str,
        imported_at: datetime | None = None,
    ) -> ImportOutcome:
        """Write a validated plan. Raises :class:`CatalogueImportRefused` if it is not ``ok``.

        ``import_version`` and ``imported_at`` are the provenance the clinic vocabulary's
        ``imported_catalogue`` system promises to carry on every item it returns. The version is
        the operator's — the workbook's own name is the obvious thing to pass.
        """
        if not plan.report.ok:
            raise CatalogueImportRefused(
                f"{len(plan.report.errors)} validation errors; nothing written. "
                + "; ".join(str(issue) for issue in plan.report.errors[:5])
            )

        stamped_at = imported_at or datetime.now(UTC)
        tenant = uuid.UUID(tenant_id)

        with self._scope(tenant_id) as session:
            branch_ids = self._write_branches(
                session, tenant, plan.branches, import_version, stamped_at
            )
            service_ids = self._write_services(
                session, tenant, plan.services, import_version, stamped_at
            )
            slot_ids, protected = self._write_slots(
                session, tenant, plan.slots, branch_ids, service_ids, import_version, stamped_at
            )
            bookings_written = self._write_bookings(
                session, tenant, plan.bookings, slot_ids, import_version, stamped_at
            )
            deactivated_branches = self._deactivate_branches(session, tenant, branch_ids)
            deactivated_services = self._deactivate_services(session, tenant, service_ids)
            stored_slots = session.execute(
                select(ClinicAvailabilitySlot.external_id).where(
                    ClinicAvailabilitySlot.tenant_id == tenant
                )
            ).scalars()
            not_in_workbook = sum(1 for key in stored_slots if key not in slot_ids)

            session.commit()

        return ImportOutcome(
            branches_written=len(branch_ids),
            services_written=len(service_ids),
            slots_written=len(slot_ids) - protected,
            bookings_written=bookings_written,
            branches_deactivated=deactivated_branches,
            services_deactivated=deactivated_services,
            slots_protected=protected,
            slots_not_in_workbook=not_in_workbook,
        )

    def _write_branches(
        self,
        session: Session,
        tenant: uuid.UUID,
        branches: Sequence[Branch],
        import_version: str,
        stamped_at: datetime,
    ) -> dict[str, uuid.UUID]:
        existing = {
            row.external_id: row
            for row in session.execute(
                select(ClinicBranch).where(ClinicBranch.tenant_id == tenant)
            ).scalars()
        }
        written: dict[str, uuid.UUID] = {}
        for branch in branches:
            row = existing.get(branch.external_id)
            if row is None:
                row = ClinicBranch(tenant_id=tenant, external_id=branch.external_id)
                session.add(row)
            row.name = branch.name
            row.area = branch.area
            row.address = branch.address
            row.phone = branch.phone
            row.timezone = branch.timezone
            row.aliases = list(branch.aliases)
            row.placeholder = branch.placeholder
            row.active = branch.active
            row.import_version = import_version
            row.imported_at = stamped_at
            session.flush()
            written[branch.external_id] = row.id
        return written

    def _write_services(
        self,
        session: Session,
        tenant: uuid.UUID,
        services: Sequence[Service],
        import_version: str,
        stamped_at: datetime,
    ) -> dict[str, uuid.UUID]:
        existing = {
            row.code: row
            for row in session.execute(
                select(ClinicService).where(ClinicService.tenant_id == tenant)
            ).scalars()
        }
        written: dict[str, uuid.UUID] = {}
        for service in services:
            row = existing.get(service.code)
            if row is None:
                row = ClinicService(tenant_id=tenant, code=service.code)
                session.add(row)
            row.name = service.name
            row.category = service.category
            row.price_minor = service.price_minor
            row.currency = service.currency
            row.duration_minutes = service.duration_minutes
            row.session_count = service.session_count
            row.aliases = list(service.aliases)
            row.active = service.active
            row.import_version = import_version
            row.imported_at = stamped_at
            session.flush()
            written[service.code] = row.id
        return written

    def _write_slots(
        self,
        session: Session,
        tenant: uuid.UUID,
        slots: Sequence[AvailabilitySlot],
        branch_ids: dict[str, uuid.UUID],
        service_ids: dict[str, uuid.UUID],
        import_version: str,
        stamped_at: datetime,
    ) -> tuple[dict[str, uuid.UUID], int]:
        existing = {
            row.external_id: row
            for row in session.execute(
                select(ClinicAvailabilitySlot).where(ClinicAvailabilitySlot.tenant_id == tenant)
            ).scalars()
        }
        protected_slot_ids = {
            booking.slot_id
            for booking in session.execute(
                select(ClinicBooking).where(
                    ClinicBooking.tenant_id == tenant,
                    ClinicBooking.source != "workbook",
                )
            ).scalars()
        }

        written: dict[str, uuid.UUID] = {}
        protected = 0
        for slot in slots:
            row = existing.get(slot.external_id)
            if row is not None and row.id in protected_slot_ids:
                # A booking this system made holds this slot. The workbook was exported before it
                # existed and must not reopen it. See the module docstring.
                protected += 1
                written[slot.external_id] = row.id
                continue
            if row is None:
                row = ClinicAvailabilitySlot(tenant_id=tenant, external_id=slot.external_id)
                session.add(row)
            row.branch_id = branch_ids[slot.branch_external_id]
            row.service_id = service_ids[slot.service_code]
            # Stored in UTC, always. The importer builds these in the clinic's own zone, and a
            # backend that keeps the offset it was handed (Postgres) and one that compares the
            # wall clock as text (SQLite) disagree about which day a 00:30 Cairo slot falls on —
            # so the disagreement is removed here rather than tested around.
            row.starts_at = slot.starts_at.astimezone(UTC)
            row.ends_at = slot.ends_at.astimezone(UTC)
            row.status = slot.status
            row.import_version = import_version
            row.imported_at = stamped_at
            session.flush()
            written[slot.external_id] = row.id
        return written, protected

    def _write_bookings(
        self,
        session: Session,
        tenant: uuid.UUID,
        bookings: Sequence[Booking],
        slot_ids: dict[str, uuid.UUID],
        import_version: str,
        stamped_at: datetime,
    ) -> int:
        existing = {
            row.reference: row
            for row in session.execute(
                select(ClinicBooking).where(ClinicBooking.tenant_id == tenant)
            ).scalars()
        }
        written = 0
        for booking in bookings:
            row = existing.get(booking.reference)
            if row is not None and row.source != "workbook":
                # Not the workbook's row to rewrite, whatever the reference says.
                continue
            if row is None:
                row = ClinicBooking(tenant_id=tenant, reference=booking.reference)
                session.add(row)
            row.slot_id = slot_ids[booking.slot_external_id]
            row.patient_name = booking.patient_name
            row.patient_phone = booking.patient_phone
            row.status = booking.status
            row.source = booking.source
            row.notes = booking.notes
            row.import_version = import_version
            row.imported_at = stamped_at
            session.flush()
            written += 1
        return written

    @staticmethod
    def _deactivate_branches(
        session: Session, tenant: uuid.UUID, present: dict[str, uuid.UUID]
    ) -> int:
        """Retire branches the workbook no longer lists. Deactivated, never deleted."""
        rows = session.execute(
            select(ClinicBranch).where(
                ClinicBranch.tenant_id == tenant, ClinicBranch.active.is_(True)
            )
        ).scalars()
        count = 0
        for row in rows:
            if row.external_id not in present:
                row.active = False
                count += 1
        return count

    @staticmethod
    def _deactivate_services(
        session: Session, tenant: uuid.UUID, present: dict[str, uuid.UUID]
    ) -> int:
        """Retire services the workbook no longer lists. The read paths filter on ``active``.

        Two near-identical loops rather than one over a model type: the key is a different column
        on each table (``external_id`` and ``code``), and the generic version has to be told which
        by a parameter no caller can get wrong in an interesting way.
        """
        rows = session.execute(
            select(ClinicService).where(
                ClinicService.tenant_id == tenant, ClinicService.active.is_(True)
            )
        ).scalars()
        count = 0
        for row in rows:
            if row.code not in present:
                row.active = False
                count += 1
        return count

    # ── read ───────────────────────────────────────────────────────────────────────────────

    def list_branches(self, tenant_id: str, *, active_only: bool = True) -> list[Branch]:
        with self._scope(tenant_id) as session:
            statement = select(ClinicBranch).where(ClinicBranch.tenant_id == uuid.UUID(tenant_id))
            if active_only:
                statement = statement.where(ClinicBranch.active.is_(True))
            return [
                Branch(
                    external_id=row.external_id,
                    name=row.name,
                    area=row.area,
                    address=row.address,
                    phone=row.phone,
                    timezone=row.timezone,
                    aliases=tuple(row.aliases),
                    placeholder=row.placeholder,
                    active=row.active,
                )
                for row in session.execute(statement).scalars()
            ]

    def list_services(self, tenant_id: str, *, active_only: bool = True) -> list[Service]:
        with self._scope(tenant_id) as session:
            statement = select(ClinicService).where(ClinicService.tenant_id == uuid.UUID(tenant_id))
            if active_only:
                statement = statement.where(ClinicService.active.is_(True))
            return [
                Service(
                    code=row.code,
                    name=row.name,
                    price_minor=row.price_minor,
                    duration_minutes=row.duration_minutes,
                    currency=row.currency,
                    session_count=row.session_count,
                    category=row.category,
                    aliases=tuple(row.aliases),
                    active=row.active,
                )
                for row in session.execute(statement).scalars()
            ]

    def list_slots(
        self, tenant_id: str, *, status: SlotStatus | None = None
    ) -> list[AvailabilitySlot]:
        """The diary, oldest first. ``status`` narrows to open, held or booked."""
        tenant = uuid.UUID(tenant_id)
        with self._scope(tenant_id) as session:
            statement = (
                select(ClinicAvailabilitySlot, ClinicBranch.external_id, ClinicService.code)
                .join(ClinicBranch, ClinicBranch.id == ClinicAvailabilitySlot.branch_id)
                .join(ClinicService, ClinicService.id == ClinicAvailabilitySlot.service_id)
                .where(ClinicAvailabilitySlot.tenant_id == tenant)
                .order_by(ClinicAvailabilitySlot.starts_at, ClinicAvailabilitySlot.external_id)
            )
            if status is not None:
                statement = statement.where(ClinicAvailabilitySlot.status == status)
            return [
                AvailabilitySlot(
                    external_id=row.external_id,
                    branch_external_id=branch_key,
                    service_code=service_code,
                    starts_at=row.starts_at,
                    ends_at=row.ends_at,
                    status=_slot_status(row.status),
                )
                for row, branch_key, service_code in session.execute(statement)
            ]

    def list_bookings(self, tenant_id: str) -> list[Booking]:
        tenant = uuid.UUID(tenant_id)
        with self._scope(tenant_id) as session:
            statement = (
                select(ClinicBooking, ClinicAvailabilitySlot.external_id)
                .join(
                    ClinicAvailabilitySlot,
                    ClinicAvailabilitySlot.id == ClinicBooking.slot_id,
                )
                .where(ClinicBooking.tenant_id == tenant)
                .order_by(ClinicBooking.reference)
            )
            return [
                Booking(
                    reference=row.reference,
                    slot_external_id=slot_key,
                    source=_booking_source(row.source),
                    patient_name=row.patient_name,
                    patient_phone=row.patient_phone,
                    status="cancelled" if row.status == "cancelled" else "confirmed",
                    conversation_id=str(row.conversation_id) if row.conversation_id else None,
                    idempotency_key=row.idempotency_key,
                    notes=row.notes,
                )
                for row, slot_key in session.execute(statement)
            ]

    # ── the booking journey (demo step 6) ──────────────────────────────────────────────────

    def available_slots(
        self,
        tenant_id: str,
        *,
        service_code: str,
        branch_external_id: str,
        on_date: date,
        timezone: str,
        now: datetime | None = None,
        conversation_id: str | None = None,
    ) -> list[AvailabilitySlot]:
        """Open slots for one service at one branch on one local day, earliest first.

        ``on_date`` is a *local* calendar day and the stored times are absolute, so the day is
        turned into an interval in the tenant's zone before it touches the query. Doing it the
        other way round — comparing a stored UTC timestamp's date against a local date — is how a
        20:00 appointment in Cairo is offered as tomorrow's.

        A slot **held by this conversation is still available to it**. Otherwise a patient who is
        offered 18:00, is asked to confirm, and says yes is told the slot they were just offered is
        gone, by the hold that was placed to keep it for them. An expired hold is available to
        anybody: nothing sweeps them, so the expiry is read rather than enforced by a job that
        might not be running.
        """
        zone = ZoneInfo(timezone)
        # The local day, expressed in UTC — the same representation the rows are stored in. See
        # ``_write_slots``: comparing a stored UTC value against a Cairo-offset bound is a text
        # comparison on SQLite and an instant comparison on Postgres, and only one of them puts a
        # 00:30 Cairo slot on the right day.
        day_start = datetime.combine(on_date, time.min, tzinfo=zone).astimezone(UTC)
        day_end = day_start + timedelta(days=1)
        at = now or datetime.now(UTC)
        held_by_me = uuid.UUID(conversation_id) if conversation_id else None

        tenant = uuid.UUID(tenant_id)
        with self._scope(tenant_id) as session:
            statement = (
                select(ClinicAvailabilitySlot, ClinicBranch.external_id, ClinicService.code)
                .join(ClinicBranch, ClinicBranch.id == ClinicAvailabilitySlot.branch_id)
                .join(ClinicService, ClinicService.id == ClinicAvailabilitySlot.service_id)
                .where(
                    ClinicAvailabilitySlot.tenant_id == tenant,
                    ClinicBranch.external_id == branch_external_id,
                    ClinicService.code == service_code,
                    ClinicAvailabilitySlot.starts_at >= day_start,
                    ClinicAvailabilitySlot.starts_at < day_end,
                    ClinicAvailabilitySlot.status != "booked",
                    or_(
                        ClinicAvailabilitySlot.status == "open",
                        ClinicAvailabilitySlot.held_until.is_(None),
                        ClinicAvailabilitySlot.held_until <= at,
                        ClinicAvailabilitySlot.held_by_conversation_id == held_by_me,
                    ),
                )
                .order_by(ClinicAvailabilitySlot.starts_at, ClinicAvailabilitySlot.external_id)
            )
            return [
                AvailabilitySlot(
                    external_id=row.external_id,
                    branch_external_id=branch_key,
                    service_code=service_code_read,
                    starts_at=_aware(row.starts_at),
                    ends_at=_aware(row.ends_at),
                    status=_slot_status(row.status),
                )
                for row, branch_key, service_code_read in session.execute(statement)
            ]

    def hold_slot(
        self,
        tenant_id: str,
        *,
        slot_external_id: str,
        conversation_id: str,
        until: datetime,
        now: datetime | None = None,
    ) -> bool:
        """Reserve a slot for one conversation until ``until``. ``False`` if somebody else has it.

        One conditional ``UPDATE`` whose ``WHERE`` clause *is* the check, and the row count is the
        answer. Reading the slot and then writing it would leave a window between the two in which
        the other conversation's hold lands, and both patients get offered the same 18:00.

        Re-holding a slot this conversation already holds extends the hold rather than failing —
        the patient is still in the same conversation about the same appointment, and a hold that
        expires while they are typing their name is a worse outcome than one that lasts a little
        longer than intended.
        """
        at = now or datetime.now(UTC)
        conversation = uuid.UUID(conversation_id)
        with self._scope(tenant_id) as session:
            result = session.execute(
                update(ClinicAvailabilitySlot)
                .where(
                    ClinicAvailabilitySlot.tenant_id == uuid.UUID(tenant_id),
                    ClinicAvailabilitySlot.external_id == slot_external_id,
                    ClinicAvailabilitySlot.status != "booked",
                    or_(
                        ClinicAvailabilitySlot.status == "open",
                        ClinicAvailabilitySlot.held_until.is_(None),
                        ClinicAvailabilitySlot.held_until <= at,
                        ClinicAvailabilitySlot.held_by_conversation_id == conversation,
                    ),
                )
                .values(status="held", held_until=until, held_by_conversation_id=conversation)
            )
            session.commit()
            # `Session.execute` is typed as returning `Result`, which has no row count; a DML
            # statement always returns a `CursorResult`, which does. Narrowed rather than ignored
            # so the assumption is written down where it is made.
            assert isinstance(result, CursorResult)
            return bool(result.rowcount)

    def confirm_booking(
        self,
        tenant_id: str,
        *,
        slot_external_id: str,
        conversation_id: str,
        reference_prefix: str,
        patient_name: str | None = None,
        patient_phone: str | None = None,
        now: datetime | None = None,
        buffer_minutes: int = BOOKING_BUFFER_MINUTES,
    ) -> BookingOutcome:
        """Turn a held slot into an appointment, once, whatever happens twice.

        The order of the checks is the order in which they can be wrong. Idempotency first, so a
        retry never reaches the rest of this at all. Then the slot's own state, then the buffer,
        and only then a write — which is still guarded, because everything above this line is a
        read and the row can change underneath it. ``IntegrityError`` on the unique constraints is
        the real answer, not a failure: whoever else got there is holding the appointment.
        """
        at = now or datetime.now(UTC)
        tenant = uuid.UUID(tenant_id)
        key = booking_idempotency_key(tenant_id, conversation_id, slot_external_id)

        with self._scope(tenant_id) as session:
            existing = self._booking_by_key(session, tenant, key)
            if existing is not None:
                return BookingOutcome("already_confirmed", existing)

            slot = session.execute(
                select(ClinicAvailabilitySlot).where(
                    ClinicAvailabilitySlot.tenant_id == tenant,
                    ClinicAvailabilitySlot.external_id == slot_external_id,
                )
            ).scalar_one_or_none()
            if slot is None:
                return BookingOutcome("slot_unknown")
            if slot.status == "booked":
                return BookingOutcome("slot_taken")
            if (
                slot.held_by_conversation_id is not None
                and slot.held_by_conversation_id != uuid.UUID(conversation_id)
                and slot.held_until is not None
                and _aware(slot.held_until) > at
            ):
                return BookingOutcome("held_by_another")
            if self._too_close_to_another_booking(session, tenant, slot, buffer_minutes):
                return BookingOutcome("too_close")

            row = ClinicBooking(
                tenant_id=tenant,
                reference=str(self._next_reference(session, tenant, reference_prefix)),
                slot_id=slot.id,
                patient_name=patient_name,
                patient_phone=patient_phone,
                status="confirmed",
                source="bot",
                conversation_id=uuid.UUID(conversation_id),
                idempotency_key=key,
            )
            session.add(row)
            slot.status = "booked"
            slot.held_until = None
            slot.held_by_conversation_id = None
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                # Somebody committed between the reads above and this write. Whichever constraint
                # caught it, the honest answer is the state that is now in the table.
                already = self._booking_by_key(session, tenant, key)
                if already is not None:
                    return BookingOutcome("already_confirmed", already)
                return BookingOutcome("slot_taken")

            return BookingOutcome(
                "confirmed",
                Booking(
                    reference=row.reference,
                    slot_external_id=slot_external_id,
                    source="bot",
                    patient_name=patient_name,
                    patient_phone=patient_phone,
                    conversation_id=conversation_id,
                    idempotency_key=key,
                ),
            )

    @staticmethod
    def _booking_by_key(session: Session, tenant: uuid.UUID, key: str) -> Booking | None:
        found = session.execute(
            select(ClinicBooking, ClinicAvailabilitySlot.external_id)
            .join(ClinicAvailabilitySlot, ClinicAvailabilitySlot.id == ClinicBooking.slot_id)
            .where(ClinicBooking.tenant_id == tenant, ClinicBooking.idempotency_key == key)
        ).first()
        if found is None:
            return None
        row, slot_key = found
        return Booking(
            reference=row.reference,
            slot_external_id=slot_key,
            source=_booking_source(row.source),
            patient_name=row.patient_name,
            patient_phone=row.patient_phone,
            status="cancelled" if row.status == "cancelled" else "confirmed",
            conversation_id=str(row.conversation_id) if row.conversation_id else None,
            idempotency_key=row.idempotency_key,
            notes=row.notes,
        )

    @staticmethod
    def _too_close_to_another_booking(
        session: Session,
        tenant: uuid.UUID,
        slot: ClinicAvailabilitySlot,
        buffer_minutes: int,
    ) -> bool:
        """Whether ``slot`` leaves less than the buffer clear of an appointment *this system made*.

        **The buffer is checked against our own bookings, never against the imported diary**, and
        that is the only reading of the two client decisions that leaves the demo bookable.
        Decision 3 makes the workbook authoritative for what is already in the diary, 62
        back-to-back pairs included — every 60-minute service in an hourly grid. The buffer
        constrains *new* bookings. Applied against the imported rows as well, it would refuse most
        of the 407 open slots, because in an hourly grid nearly every one of them is adjacent to a
        booked one; the clinic would be told its own diary is invalid.

        So: the clinic's exported diary stands exactly as exported, and what this system adds to it
        does not stack back-to-back against what this system already added. **The client has not
        been asked to confirm this reading** — it is recorded in the handoff as a decision to put
        to them.
        """
        if buffer_minutes <= 0:
            return False
        gap = timedelta(minutes=buffer_minutes)
        window_start = _aware(slot.starts_at) - gap
        window_end = _aware(slot.ends_at) + gap
        neighbours = session.execute(
            select(ClinicAvailabilitySlot)
            .join(ClinicBooking, ClinicBooking.slot_id == ClinicAvailabilitySlot.id)
            .where(
                ClinicAvailabilitySlot.tenant_id == tenant,
                ClinicAvailabilitySlot.branch_id == slot.branch_id,
                ClinicAvailabilitySlot.id != slot.id,
                ClinicBooking.source != "workbook",
                ClinicBooking.status == "confirmed",
            )
        ).scalars()
        return any(
            _aware(other.starts_at) < window_end and window_start < _aware(other.ends_at)
            for other in neighbours
        )

    @staticmethod
    def _next_reference(session: Session, tenant: uuid.UUID, prefix: str) -> BookingReference:
        """The reference after the highest one this tenant has issued under ``prefix``.

        Read from the table rather than a counter, because the 265 references the workbook brought
        in are references this clinic has already given to patients — issuing ``DC-0001`` again
        would hand a new patient a number somebody else is holding.
        """
        taken = session.execute(
            select(ClinicBooking.reference).where(ClinicBooking.tenant_id == tenant)
        ).scalars()
        highest = 0
        for reference in taken:
            parsed = BookingReference.match(reference)
            if parsed is not None and parsed.prefix == prefix.upper():
                highest = max(highest, parsed.serial)
        return BookingReference(prefix=prefix.upper(), serial=highest + 1)


def _slot_status(value: str) -> SlotStatus:
    """Narrow a stored status to the domain's. Anything unrecognised reads as booked.

    Failing closed, not open: a status this code does not know is not a slot to offer a patient.
    """
    return "open" if value == "open" else "held" if value == "held" else "booked"


def _booking_source(value: str) -> BookingSource:
    """Narrow a stored source to the domain's. See :func:`_slot_status` for the same shape.

    An unrecognised source reads as ``bot``, which is the value that makes an import *protect* the
    row rather than overwrite it — the safe way to be wrong about where a booking came from.
    """
    return "workbook" if value == "workbook" else "staff" if value == "staff" else "bot"


def _aware(value: datetime) -> datetime:
    """A stored timestamp as an absolute time.

    SQLite gives back what it was given, without the zone; Postgres gives back an aware value. The
    columns are ``DateTime(timezone=True)`` and everything written to them is UTC, so reading a
    naive value as UTC is reading it as what it is — and it keeps the comparisons in this module
    from raising on one backend and passing on the other.
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
