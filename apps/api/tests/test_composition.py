"""Tests for clinic runtime-tool registration in the composition root (pre-demo Step 1).

``build_consumer`` decides whether to wire the clinic booking tools by looking at the vocabulary.
The bug this file pins down: the decision required all four *runtime* tools to be declared as
intent terminal tools, but ``hold_slot`` is an internal booking operation no intent ever names,
so the subset test could never pass — ``configure_clinic`` never ran and a valid availability
request fell through the receptionist to the unbuilt-tool hand-off. The fix splits the terminal
*capabilities* that decide support from the runtime *tools* that get registered.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from packages.intents.schema import vocabulary_for

from apps.api.classifier.service import Classifier
from apps.api.conversations.tools import (
    REGISTRY,
    CheckAvailability,
    ConfirmBooking,
    HoldSlot,
    QuotePrice,
)
from apps.api.core.config import Settings
from apps.api.db.engine import Database
from apps.api.orchestration.composition import (
    _CLINIC_RUNTIME_TOOLS,
    _CLINIC_TERMINAL_CAPABILITIES,
    build_consumer,
)

#: The four tool classes the clinic flow must resolve to once wired. Their *presence* under the
#: right names is exactly what keeps availability from reaching ``_UNBUILT_TEXT``.
_RUNTIME_TOOL_TYPES: dict[str, type] = {
    "check_availability": CheckAvailability,
    "quote_price": QuotePrice,
    "hold_slot": HoldSlot,
    "confirm_booking": ConfirmBooking,
}


def _clinic_settings() -> Settings:
    return Settings(
        _env_file=None,
        tenant_vertical="clinics",
        tenant_emergency_reply="I am alerting our doctor right now.",
    )


def test_hold_slot_is_not_a_terminal_capability_of_any_clinic_intent() -> None:
    """The premise of the bug, asserted directly: ``hold_slot`` is never an intent's endpoint.

    So a decision keyed on all four runtime tools being terminal tools can never be true — which is
    why the terminal capabilities the decision uses must not include it.
    """
    clinic = vocabulary_for("clinics")
    terminal_tools = {intent.terminal_tool for intent in clinic.intents}

    assert "hold_slot" not in terminal_tools
    assert "hold_slot" in _CLINIC_RUNTIME_TOOLS
    assert "hold_slot" not in _CLINIC_TERMINAL_CAPABILITIES
    # The capabilities that *do* decide support are all really declared by clinic intents.
    assert _CLINIC_TERMINAL_CAPABILITIES <= terminal_tools


def test_clinic_composition_registers_all_four_runtime_tools(database: Database) -> None:
    """A clinic consumer wires every runtime tool — the release-blocking startup state.

    Each name resolves to its concrete clinic tool bound to the imported catalogue, so a valid
    availability request crosses the real composition boundary instead of falling through to the
    unbuilt-tool hand-off (``_UNBUILT_TEXT``).
    """
    build_consumer(
        _clinic_settings(),
        database,
        cast(Classifier, SimpleNamespace()),
        vocabulary=vocabulary_for("clinics"),
    )

    assert _CLINIC_RUNTIME_TOOLS <= set(REGISTRY)
    for name, tool_type in _RUNTIME_TOOL_TYPES.items():
        assert isinstance(REGISTRY.get(name), tool_type)


def test_non_clinic_composition_registers_no_clinic_tools(database: Database) -> None:
    """A holiday-home consumer wires none of them: an intent naming one still hands off."""
    settings = Settings(_env_file=None, tenant_vertical="holiday_homes")

    build_consumer(
        settings,
        database,
        cast(Classifier, SimpleNamespace()),
        vocabulary=vocabulary_for("holiday_homes"),
    )

    assert not (_CLINIC_RUNTIME_TOOLS & set(REGISTRY))


def test_clinic_startup_diagnostic_reports_tools_registered(
    database: Database, caplog: Any
) -> None:
    """The live startup line: ``clinic_tools_registered=True`` with nothing missing."""
    import logging

    with caplog.at_level(logging.INFO, logger="apps.api.orchestration.composition"):
        build_consumer(
            _clinic_settings(),
            database,
            cast(Classifier, SimpleNamespace()),
            vocabulary=vocabulary_for("clinics"),
        )

    assert "clinic_tools_registered=True" in caplog.text
    assert "missing_clinic_tools=[]" in caplog.text
