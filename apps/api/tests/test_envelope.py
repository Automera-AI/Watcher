"""The reply envelope is channel-neutral; channel limits live in the channel adapter (1.2).

**The contradiction this file resolves.** The scaffold version capped replies at three quick-reply
buttons. Three is a *WhatsApp* number. Putting it in the channel-agnostic core is precisely the
mistake `test_boundary.py` exists to catch, and it would have permanently limited every future
channel to the most restrictive one — a phone call has no buttons at all, and a web widget has no
particular limit.

**The decision: cap in the adapter.** The core composes what it wants to say. Each channel adapter
renders that into what the channel can carry, and truncating to three is WhatsApp's business.
Roadmap trap #2 asked for this to be decided rather than inherited; this is the decision.

Everything below the first section is `xfail(strict=True)` because the reply path is item 2.2 and
does not exist yet. Strict matters: when 2.2 lands and these start passing, XPASS fails the suite
and forces the markers off, so the specification cannot quietly drift out of date.

Module names here are the proposal, not a promise. Adjust them in 2.2 and keep the assertions.
"""

from __future__ import annotations

import pytest

WHATSAPP_QUICK_REPLY_LIMIT = 3
"""WhatsApp's interactive-button ceiling. Named here only to be asserted *about*; the constant
itself belongs in the WhatsApp adapter, never in the core."""


# ── live today: the core has no reply vocabulary to be wrong about yet ────────


def test_the_core_does_not_yet_define_a_reply_shape() -> None:
    """Item 2.2 has not landed. Stated as a test so that when it does, this file is revisited
    rather than sitting here asserting nothing."""
    with pytest.raises(ImportError):
        import apps.api.schemas.reply  # noqa: F401


def test_the_three_button_limit_appears_nowhere_in_the_core() -> None:
    """The scaffold's cap must not be reintroduced while nobody is looking.

    A bare ``3`` is far too common to grep for, so this looks for the *names* the limit would
    travel under. If 2.2 wants a cap in the core, this test is the argument to have first.
    """
    from pathlib import Path

    api_root = Path(__file__).resolve().parent.parent
    suspicious = ("quick_reply_limit", "max_buttons", "button_limit", "max_quick_replies")

    offenders = [
        f"{p.relative_to(api_root).as_posix()}:{name}"
        for p in api_root.rglob("*.py")
        if "__pycache__" not in p.parts and "tests" not in p.parts and "ingestion" not in p.parts
        for name in suspicious
        if name in p.read_text(encoding="utf-8").lower()
    ]
    assert not offenders, (
        f"a per-channel rendering limit has appeared in the core: {offenders}. "
        "Cap in the channel adapter — the core should not know what a button is."
    )


# ── specification for item 2.2 ────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="reply path is roadmap item 2.2")
def test_the_envelope_carries_no_channel_fields() -> None:
    """Whatever the core composes must survive being sent down a phone line."""
    from apps.api.schemas.reply import ReplyEnvelope

    fields = set(ReplyEnvelope.model_fields)
    assert not [f for f in fields if f.startswith("wa_")]
    assert "channel" not in fields, (
        "the envelope should not know its own channel; the sender picks the adapter"
    )


@pytest.mark.xfail(strict=True, reason="reply path is roadmap item 2.2")
def test_the_core_accepts_more_quick_replies_than_whatsapp_can_show() -> None:
    """The core is not limited to the most restrictive channel. This is the whole point."""
    from apps.api.schemas.reply import QuickReply, ReplyEnvelope

    envelope = ReplyEnvelope(
        text="Which of these suits you?",
        quick_replies=[QuickReply(label=f"option {i}") for i in range(6)],
    )
    assert len(envelope.quick_replies) == 6


@pytest.mark.xfail(strict=True, reason="WhatsApp adapter is roadmap item 2.2")
def test_the_whatsapp_adapter_truncates_to_three() -> None:
    """...and the adapter is where reality gets applied."""
    from apps.api.channels.whatsapp import render
    from apps.api.schemas.reply import QuickReply, ReplyEnvelope

    envelope = ReplyEnvelope(
        text="Which of these suits you?",
        quick_replies=[QuickReply(label=f"option {i}") for i in range(6)],
    )
    rendered = render(envelope)
    assert len(rendered.buttons) == WHATSAPP_QUICK_REPLY_LIMIT


@pytest.mark.xfail(strict=True, reason="voice adapter is roadmap item 3.2")
def test_a_voice_adapter_drops_quick_replies_entirely() -> None:
    """The case that proves the cap does not belong in the core: on a call, three is also wrong.

    A caller cannot see buttons, so the voice adapter has to speak the options instead. If the
    core had capped at three, this adapter would inherit a number that means nothing to it.
    """
    from apps.api.channels.voice import render
    from apps.api.schemas.reply import QuickReply, ReplyEnvelope

    envelope = ReplyEnvelope(
        text="Which of these suits you?",
        quick_replies=[QuickReply(label=f"option {i}") for i in range(6)],
    )
    spoken = render(envelope)
    assert not hasattr(spoken, "buttons")
    assert "option 5" in spoken.text, "a spoken reply must not silently lose the later options"
