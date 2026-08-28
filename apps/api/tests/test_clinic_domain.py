"""The clinic domain's value objects and the two policies that ride on them (demo step 3).

Small, but not trivial: ``BookingReference`` is what a patient reads back over the phone, and the
overlap/gap pair is what decides whether the import calls 672 slots clean or refuses them.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from apps.api.core.clinic import (
    BOOKING_BUFFER_MINUTES,
    AvailabilitySlot,
    BookingReference,
    BookingReferenceError,
    Service,
    booking_idempotency_key,
    normalise_service_name,
)


def _slot(external_id: str, start: str, minutes: int, branch: str = "B1") -> AvailabilitySlot:
    starts_at = datetime.fromisoformat(f"2026-09-02T{start}").replace(tzinfo=UTC)
    return AvailabilitySlot(
        external_id=external_id,
        branch_external_id=branch,
        service_code="DT001",
        starts_at=starts_at,
        ends_at=starts_at + timedelta(minutes=minutes),
    )


class TestBookingReference:
    def test_renders_the_padded_form_a_patient_is_given(self) -> None:
        assert str(BookingReference("DC", 42)) == "DC-0042"

    def test_round_trips_through_its_own_text(self) -> None:
        assert BookingReference.parse("DC-0042") == BookingReference("DC", 42)

    def test_parsing_is_case_insensitive_and_ignores_surrounding_space(self) -> None:
        assert BookingReference.parse("  dc-0265  ") == BookingReference("DC", 265)

    @pytest.mark.parametrize("text", ["", "DC0042", "DC-42", "0042", "D-0042", "DC-", "DC-004x"])
    def test_rejects_what_is_not_a_reference(self, text: str) -> None:
        with pytest.raises(BookingReferenceError):
            BookingReference.parse(text)

    def test_match_returns_none_rather_than_raising(self) -> None:
        """The import reads a column that also holds whatever a clinic historically typed."""
        assert BookingReference.match("walk-in, Tuesday") is None
        assert BookingReference.match("DC-0001") == BookingReference("DC", 1)

    def test_next_is_the_serial_after_this_one(self) -> None:
        assert str(BookingReference.parse("DC-0265").next()) == "DC-0266"

    @pytest.mark.parametrize(("prefix", "serial"), [("D", 1), ("TOOLONGX", 1), ("DC1", 1)])
    def test_rejects_a_prefix_that_is_not_two_to_six_letters(
        self, prefix: str, serial: int
    ) -> None:
        with pytest.raises(BookingReferenceError):
            BookingReference(prefix, serial)

    def test_rejects_a_serial_below_one(self) -> None:
        with pytest.raises(BookingReferenceError):
            BookingReference("DC", 0)

    def test_no_prefix_is_baked_in(self) -> None:
        """The prefix is one client's initials. A default here would be quoted at the next one."""
        with pytest.raises(TypeError):
            BookingReference("DC")  # type: ignore[call-arg]


class TestNormaliseServiceName:
    def test_separators_do_not_distinguish_two_spellings_of_one_service(self) -> None:
        assert normalise_service_name("Primelase Laser Package - 6 Sessions") == (
            normalise_service_name("primelase laser package 6 sessions")
        )

    def test_arabic_folding_comes_from_the_shared_normaliser(self) -> None:
        assert normalise_service_name("حقن التكميم") == normalise_service_name("حقن التكميم")
        assert normalise_service_name("إزالة الشعر") == normalise_service_name("ازالة الشعر")

    def test_two_genuinely_different_names_stay_different(self) -> None:
        assert normalise_service_name("Basic Facial") != normalise_service_name("Facial")


class TestServiceNames:
    def test_a_service_answers_to_its_name_and_every_alias(self) -> None:
        service = Service(
            code="DT002",
            name="Facial",
            price_minor=75_000,
            duration_minutes=45,
            aliases=("Classic Facial", "فيشيال"),
        )
        assert service.names == ("Facial", "Classic Facial", "فيشيال")


class TestSlotArithmetic:
    def test_slots_in_different_branches_never_overlap(self) -> None:
        assert not _slot("S1", "11:00", 60).overlaps(_slot("S2", "11:00", 60, branch="B2"))

    def test_touching_is_not_overlapping(self) -> None:
        """The 62 accepted back-to-back pairs are exactly this shape (decision 3)."""
        assert not _slot("S1", "11:00", 60).overlaps(_slot("S2", "12:00", 60))

    def test_sharing_a_minute_is_overlapping(self) -> None:
        assert _slot("S1", "11:00", 60).overlaps(_slot("S2", "11:30", 60))

    def test_the_gap_between_back_to_back_slots_is_zero(self) -> None:
        assert _slot("S1", "11:00", 60).gap_minutes_before(_slot("S2", "12:00", 60)) == 0
        assert 0 < BOOKING_BUFFER_MINUTES  # the pair above is what the buffer would have refused

    def test_the_gap_is_negative_when_they_overlap(self) -> None:
        assert _slot("S1", "11:00", 60).gap_minutes_before(_slot("S2", "11:30", 60)) == -30

    def test_duration_is_read_from_the_interval(self) -> None:
        assert _slot("S1", "11:00", 45).duration_minutes == 45


class TestIdempotencyKey:
    def test_one_conversation_booking_one_slot_has_one_key(self) -> None:
        first = booking_idempotency_key("tenant-a", "conv-1", "S00042")
        again = booking_idempotency_key("tenant-a", "conv-1", "S00042")
        assert first == again

    def test_the_tenant_is_part_of_the_key(self) -> None:
        """It is read in logs and compared by people, often out of the context that scopes it."""
        assert booking_idempotency_key("tenant-a", "conv-1", "S1") != booking_idempotency_key(
            "tenant-b", "conv-1", "S1"
        )

    def test_a_different_slot_is_a_different_booking(self) -> None:
        assert booking_idempotency_key("t", "c", "S1") != booking_idempotency_key("t", "c", "S2")
