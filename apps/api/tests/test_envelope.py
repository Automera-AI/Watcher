"""The channel contract (ported from the v2 scaffold, roadmap 1.2).

The scaffold's two tests are both here. The second one is inverted on purpose, and that is the
whole substance of this file.

**Trap #2, resolved.** The scaffold asserted that ``OutboundAction`` *raises* on a fourth
quick-reply button — a WhatsApp limit enforced in the channel-agnostic core. That is exactly the
mistake ``test_boundary.py`` exists to prevent, and it hid well: the scaffold's boundary test
passes on that file, because it only catches provider names used as identifiers and the offence
was a string literal.

The decision taken is **cap in the adapter**. So the core now accepts as many options as it
likes, ``channels/whatsapp.py`` truncates to three, and ``channels/voice.py`` speaks them
instead — which is the case that proves the point, because on a call three is as wrong as six.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from apps.api.channels import voice, whatsapp
from apps.api.schemas.envelope import InboundTurn, OutboundAction

# ── ported unchanged from the scaffold ────────────────────────────────────────


def test_a_turn_without_an_idempotency_key_is_rejected() -> None:
    with pytest.raises(ValueError, match="idempotency_key"):
        InboundTurn(
            tenant_id=uuid4(),
            channel="whatsapp",
            channel_thread_id="971500000000",
            channel_identity="+971500000000",
            modality="text",
            text="hello",
            received_at=datetime.now(UTC),
            idempotency_key="",
        )


def test_a_text_turn_must_carry_text() -> None:
    with pytest.raises(ValueError, match="must carry text"):
        InboundTurn(
            tenant_id=uuid4(),
            channel="voice",
            channel_thread_id="call-1",
            channel_identity="+971500000000",
            modality="audio",
            text=None,
            received_at=datetime.now(UTC),
            idempotency_key="k",
        )


# ── inverted from the scaffold: the cap is not the core's business ────────────


def _four_options() -> OutboundAction:
    return OutboundAction(kind="ask", text="pick", quick_replies=["a", "b", "c", "d"])


def test_the_core_composes_as_many_options_as_it_likes() -> None:
    """The scaffold raised here. It should not: nothing at compose time knows the channel yet."""
    action = _four_options()
    assert action.quick_replies == ["a", "b", "c", "d"]


def test_whatsapp_truncates_to_three_at_render_time() -> None:
    rendered = whatsapp.render(_four_options())
    assert rendered.buttons == ("a", "b", "c")
    assert rendered.truncated is True


def test_whatsapp_leaves_three_or_fewer_alone() -> None:
    rendered = whatsapp.render(OutboundAction(kind="ask", text="pick", quick_replies=["a", "b"]))
    assert rendered.buttons == ("a", "b")
    assert rendered.truncated is False


def test_voice_keeps_every_option_because_it_has_no_buttons() -> None:
    """The case that proves the cap does not belong in the core.

    A caller read three of four choices has silently lost one, so the voice adapter speaks them
    all. Had the core capped at three, this adapter would inherit a number meaning nothing to it.
    """
    spoken = voice.render(_four_options())
    assert not hasattr(spoken, "buttons")
    for option in ("a", "b", "c", "d"):
        assert option in spoken.text


def test_a_template_action_still_validates_in_the_core() -> None:
    """Not every rule is channel-specific. ``send_template`` without a name is incoherent
    everywhere, so that one stays."""
    with pytest.raises(ValueError, match="template_name"):
        OutboundAction(kind="send_template", text="hi")


# ── the boundary actually validates (PR #12 review) ───────────────────────────


def test_a_typo_in_the_channel_is_rejected() -> None:
    """These were frozen dataclasses, ported straight from the scaffold. A ``Literal`` on a
    dataclass is a hint to the type checker and nothing whatsoever at runtime, so an adapter
    could normalise a webhook into ``channel="whatsap"`` and everything downstream would trust
    it. This is the provider boundary; it is the one place that must validate."""
    with pytest.raises(ValidationError):
        InboundTurn(
            tenant_id=uuid4(),
            channel="whatsap",
            channel_thread_id="x",
            channel_identity="+971500000000",
            modality="text",
            text="hi",
            received_at=datetime.now(UTC),
            idempotency_key="k",
        )


def test_a_tenant_id_that_is_not_a_uuid_is_rejected() -> None:
    """Multi-tenancy is non-negotiable (AGENTS.md), so an unvalidated tenant id is the worst of
    the fields that used to pass unchecked."""
    with pytest.raises(ValidationError):
        InboundTurn(
            tenant_id="not-a-uuid",
            channel="whatsapp",
            channel_thread_id="x",
            channel_identity="+971500000000",
            modality="text",
            text="hi",
            received_at=datetime.now(UTC),
            idempotency_key="k",
        )


def test_an_unmodelled_field_is_rejected_rather_than_dropped() -> None:
    """Silently dropping it is how a channel-specific detail leaks into the core unnoticed."""
    with pytest.raises(ValidationError):
        OutboundAction(kind="say", text="hi", channel_specific_thing=1)  # type: ignore[call-arg]
