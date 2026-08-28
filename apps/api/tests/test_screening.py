"""The clinical gate on booking (demo step 7).

Two halves and one property. The property is the point of the whole step: a receptionist that can
write an appointment can write one for a filler injection into a pregnant patient and confirm it
with a reference number, and nothing before this stopped it.

The trigger phrases here are chosen to appear **nowhere in the YAML**, for the same reason
``test_emergency.py`` does it: a test that checks the declared phrases fire is satisfied completely
by a vocabulary that declares only phrases nobody would ever type. What has to hold is that real
messages match and real questions do not.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from packages.intents.schema import ScreeningTrigger, vocabulary_for

from apps.api.conversations.receptionist import handle
from apps.api.conversations.task import Task, TaskStatus
from apps.api.core.screening import screen
from apps.api.schemas.envelope import InboundTurn

CLINICS = vocabulary_for("clinics")
HOLIDAY_HOMES = vocabulary_for("holiday_homes")
TENANT_ID = uuid.uuid4()


class TestDisclosures:
    @pytest.mark.parametrize(
        ("written", "trigger"),
        [
            ("أنا حامل في الشهر التالت وعايزة أحجز", "pregnancy"),
            ("ana 7amel w 3ayza a7gez laser", "pregnancy"),
            ("hi, i'm pregnant — can i book a facial?", "pregnancy"),
            ("عايزة ليزر بس باخد روأكيوتان من شهرين", "isotretinoin"),
            ("i'm on accutane at the moment, is thursday free?", "isotretinoin"),
            ("عايزة أحجز بس باخد مسيلات دم", "anticoagulant"),
            ("i have epilepsy, can i book the laser?", "epilepsy"),
            ("عندي هربس دلوقتي", "active_infection"),
        ],
    )
    def test_a_patient_talking_about_themselves_stops_the_booking(
        self, written: str, trigger: str
    ) -> None:
        block = screen(written, vocabulary=CLINICS)
        assert block is not None
        assert block.reason == "disclosure"
        assert block.trigger_id == trigger
        assert block.action == "handoff_to_human"

    @pytest.mark.parametrize(
        "written",
        [
            "الليزر ينفع للحوامل؟",
            "هل الليزر آمن مع الحمل؟",
            "is laser safe during pregnancy?",
            "do you treat patients on roaccutane?",
            "بتعملوا حقن للي عندهم صرع؟",
            "عايزة أحجز ليزر بكرة",
            "الفاشيال بكام؟",
        ],
    )
    def test_a_question_about_the_world_is_not_a_disclosure(self, written: str) -> None:
        """ "Is laser safe in pregnancy" is a question a clinician answers — it reaches a person
        through ``clinical_question``, not through this gate. Matching it here would file every
        curious enquiry as a blocked booking and make the gate's own numbers meaningless."""
        assert screen(written, vocabulary=CLINICS) is None

    def test_the_disclosure_is_recorded_without_the_message(self) -> None:
        """The snapshot names the trigger and the phrase, never what the patient wrote.

        The message is already on the row this is filed against. A disclosure copied into a second
        place is patient medical information stored somewhere nobody decided to store it.
        """
        block = screen("أنا حامل في الشهر التالت وعايزة أحجز", vocabulary=CLINICS)
        assert block is not None
        snapshot = block.snapshot()
        assert snapshot["trigger_id"] == "pregnancy"
        assert "الشهر التالت" not in str(snapshot)


class TestScreenedTreatments:
    @pytest.mark.parametrize("category", ["Injectables", "Skin", "injectables"])
    def test_a_treatment_only_a_clinician_may_book_is_blocked_with_nothing_disclosed(
        self, category: str
    ) -> None:
        """No disclosure, no symptom, no reason to be suspicious. Filler is a medical procedure."""
        block = screen(None, service_category=category, vocabulary=CLINICS)
        assert block is not None
        assert block.reason == "screened_treatment"

    @pytest.mark.parametrize("category", ["Laser", "Facial", "Body", "Hair"])
    def test_the_rest_of_the_catalogue_is_bookable(self, category: str) -> None:
        """Laser is deliberately not gated by category: routine hair-removal booking is the demo,
        and its contraindications are disclosures rather than properties of the treatment."""
        assert screen(None, service_category=category, vocabulary=CLINICS) is None

    def test_a_disclosure_wins_over_a_category(self) -> None:
        """A pregnant patient asking for filler is blocked for being pregnant.

        Both are true and only one is the reason a clinician would give.
        """
        block = screen("i am pregnant", service_category="Injectables", vocabulary=CLINICS)
        assert block is not None and block.reason == "disclosure"


class TestVerticals:
    def test_a_vertical_with_nothing_to_screen_screens_nothing(self) -> None:
        """A guest booking a two-bed is not being assessed for anything."""
        assert HOLIDAY_HOMES.screening is None
        assert screen("i am pregnant", vocabulary=HOLIDAY_HOMES) is None

    def test_the_clinic_vertical_declares_a_gate_at_all(self) -> None:
        """Absence means "nothing is gated", so a clinic that declares none has no gate.

        This is the assertion that the clinic vocabulary has not quietly lost its ``screening``
        block — deleting it would leave every test above passing and every booking ungated.
        """
        rules = CLINICS.screening
        assert rules is not None
        assert rules.screened_categories
        assert len(rules.triggers) >= 8

    def test_a_gate_whose_action_nobody_implements_is_refused(self) -> None:
        """A vocabulary is data, and data naming a tool the file does not declare is a gate that
        silently does nothing — which reads exactly like a gate that decided to allow it."""
        with pytest.raises(ValueError, match="not a gate"):
            CLINICS.model_validate(
                {
                    **CLINICS.model_dump(),
                    "screening": {
                        "action": "escalate_to_clinician",
                        "screened_categories": ["Injectables"],
                        "triggers": [{"id": "pregnancy", "any_of": ["i am pregnant"]}],
                    },
                }
            )

    def test_a_trigger_phrase_mixing_alphabets_is_refused(self) -> None:
        """The lookalike check the emergency triggers already carry.

        A Cyrillic character inside a Latin phrase is visually identical and would never match —
        a silent no-match on a screening trigger books the patient it was written to protect.
        """
        with pytest.raises(ValueError, match="lookalike"):
            ScreeningTrigger(id="pregnancy", any_of=["i am pregnаnt"])  # Cyrillic а


class TestTheGateInAConversation:
    def _turn(self, text: str) -> InboundTurn:
        return InboundTurn(
            tenant_id=TENANT_ID,
            channel="whatsapp",
            channel_thread_id="thread-1",
            channel_identity="+201000000000",
            modality="text",
            text=text,
            received_at=datetime(2026, 9, 1, 9, 0, tzinfo=UTC),
            idempotency_key=f"key-{text}",
        )

    def _say(self, text: str, intent: str, task: Task | None = None) -> tuple[object, Task]:
        return asyncio.run(
            handle(
                self._turn(text),
                intent,
                0.95,
                {},
                task,
                vocabulary=CLINICS,
                conversation_id=str(uuid.uuid4()),
            )
        )

    def test_a_disclosure_hands_the_booking_over_to_a_person(self) -> None:
        action, task = self._say("عايزة أحجز ليزر بكرة، أنا حامل", "booking_enquiry")
        assert action.kind == "handoff"  # type: ignore[attr-defined]
        assert task.status is TaskStatus.HANDED_OFF

    def test_a_disclosure_mid_booking_stops_it_even_at_the_last_turn(self) -> None:
        """A patient thinks of it while answering a question about something else.

        The gate runs on every turn of a booking rather than once at the start, which is why this
        one is caught on the message that would otherwise have confirmed the appointment.
        """
        collected = Task(
            intent="booking_enquiry",
            slots={
                "service": "Basic Facial",
                "branch": "Maadi",
                "requested_date": "2026-09-02",
                "requested_time": "18:00",
            },
            vocabulary=CLINICS,
        )
        action, task = self._say("أيوه تمام، بس أنا حامل", "booking_enquiry", collected)
        assert action.kind == "handoff"  # type: ignore[attr-defined]
        assert task.status is TaskStatus.HANDED_OFF

    def test_what_the_patient_already_told_us_is_kept_for_the_person_taking_over(self) -> None:
        """The hand-off is not a reset. Whoever picks this up should not start from nothing."""
        collected = Task(
            intent="booking_enquiry",
            slots={"service": "Basic Facial", "branch": "Maadi"},
            vocabulary=CLINICS,
        )
        _action, task = self._say("i'm pregnant", "booking_enquiry", collected)
        assert task.slots["service"] == "Basic Facial"
        assert task.slots["branch"] == "Maadi"

    def test_the_reply_says_nothing_clinical(self) -> None:
        """Naming the disclosure back at the patient is the receptionist stating a clinical fact
        about them, and asking a follow-up implies the answer would change the outcome."""
        action, _task = self._say("عايزة أحجز فيلر، باخد روأكيوتان", "booking_enquiry")
        text = action.text or ""  # type: ignore[attr-defined]
        for clinical in ("حامل", "روأكيوتان", "pregnan", "accutane", "roaccutane"):
            assert clinical not in text.lower()

    def test_a_price_question_from_the_same_patient_is_still_answered(self) -> None:
        """The gate is on booking. Asking what a facial costs is not a clinical judgement, and
        blocking it would make the receptionist useless to anybody who has ever been pregnant."""
        action, task = self._say("أنا حامل، الفاشيال بكام؟", "price_enquiry")
        assert action.kind != "handoff"  # type: ignore[attr-defined]
        assert task.status is not TaskStatus.HANDED_OFF
