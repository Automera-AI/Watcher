"""The core must not know which channel a message arrived on (roadmap 1.1, ported in 1.2).

Watcher answers WhatsApp today and a phone line in week 4. A phone call has no chat id, so
every place the core says ``wa_`` is a place the phone connector has to lie or special-case.
This test is what stops that surface growing while 1.1 is still outstanding.

**Why the scaffold version of this file did not work.** It banned the strings ``whatsapp``,
``twilio`` and friends. The actual leak is ``wa_message_id`` and ``wa_chat_id``, and
``whatsapp`` never matches ``wa_`` — so the test written to prevent this exact bug would not
have noticed it. ``wa_`` is in ``BANNED`` below and it is the reason this file exists.

**How it passes today, before 1.1.** It does not pretend the core is clean. ``KNOWN_LEAKS``
names every file that still carries channel vocabulary and exactly which tokens, so:

  * a *new* leak, or a leak in any file not on the list, fails immediately;
  * a listed file that picks up an *additional* token fails;
  * an entry that is no longer true fails as stale, so 1.1 cannot half-land and be forgotten.

The list only shrinks. When it is empty, 1.1 is done and the ``KNOWN_LEAKS`` machinery can go.
"""

from __future__ import annotations

from pathlib import Path

import pytest

API_ROOT = Path(__file__).resolve().parent.parent

#: Vocabulary that ties the core to one channel. ``wa_`` is the one that matters — it is the
#: prefix on the real fields, and the one the previous version of this test missed.
BANNED: tuple[str, ...] = ("wa_", "whatsapp", "twilio", "pywa", "telegram")

#: Packages allowed to name a channel: they *are* the channel adapters. Everything else is core.
ADAPTER_PACKAGES: frozenset[str] = frozenset({"ingestion", "channels"})

#: A **permanent** exception, not 1.1 debt. ``schemas/envelope.py`` declares the closed set of
#: channels a turn can arrive on, and you cannot have a channel-neutral envelope without a
#: channel field. Naming the set is fine; *behaving* differently per channel is not, which is
#: why the quick-reply cap was moved out of that file and into the WhatsApp adapter.
#:
#: Kept separate from KNOWN_LEAKS so the two never get confused: this one does not shrink.
CHANNEL_REGISTRY: dict[str, set[str]] = {
    "schemas/envelope.py": {"whatsapp"},
    "schemas/message.py": {"whatsapp"},
    "db/models.py": {"whatsapp"},
}

#: Core files that still leak, and what they still leak, pending 1.1. Delete entries as they are
#: cleaned; the suite fails if an entry becomes untrue, so this cannot rot.
#:
#: ``wa_message_id`` → ``external_id``, ``wa_chat_id`` → ``thread_id``, plus a channel field.
#: The ``whatsapp`` hits are prose in docstrings and one prompt string, cheaper to fix in the
#: same pass than to argue about separately.
#:
#: ``core/config.py`` holds the WhatsApp/Meta credential fields (``WHATSAPP_ACCESS_TOKEN`` and
#: friends). It is debt rather than a permanent exception: the settings object has no per-channel
#: *behaviour*, but the fields themselves belong to the channel, and A6's outbound sender is the
#: natural moment to move them behind ``channels/`` and have the core ask the adapter for its own
#: configuration. Until then the entry stays here, where the list is expected to shrink — not in
#: CHANNEL_REGISTRY, which would assert this is how it should stay.
KNOWN_LEAKS: dict[str, set[str]] = {
    "core/config.py": {"whatsapp"},
}


def _core_files() -> list[Path]:
    """Every non-test source file outside the channel adapters."""
    return sorted(
        p
        for p in API_ROOT.rglob("*.py")
        if "__pycache__" not in p.parts
        and "tests" not in p.parts
        and not (ADAPTER_PACKAGES & set(p.relative_to(API_ROOT).parts))
    )


def _tokens_in(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8").lower()
    return {token for token in BANNED if token in text}


def _rel(path: Path) -> str:
    return path.relative_to(API_ROOT).as_posix()


@pytest.mark.parametrize("path", _core_files(), ids=_rel)
def test_no_new_channel_vocabulary_in_the_core(path: Path) -> None:
    """A core file may only carry the channel tokens 1.1 has not reached yet."""
    found = _tokens_in(path)
    allowed = KNOWN_LEAKS.get(_rel(path), set()) | CHANNEL_REGISTRY.get(_rel(path), set())
    assert found <= allowed, (
        f"{_rel(path)} names {sorted(found - allowed)}, which ties the core to one channel. "
        "A phone call has no chat id. Put this behind the ingestion adapter, or if it is part "
        "of 1.1's remaining surface, add it to KNOWN_LEAKS with a reason."
    )


def test_known_leaks_are_all_still_real() -> None:
    """The allowlist may only shrink. A stale entry means 1.1 progressed without tidying up.

    Without this, KNOWN_LEAKS quietly becomes a list of things that used to be wrong, and the
    next person cannot tell how much of 1.1 is actually left.
    """
    stale: dict[str, set[str]] = {}
    for rel, tokens in KNOWN_LEAKS.items():
        path = API_ROOT / rel
        assert path.exists(), f"KNOWN_LEAKS names {rel}, which no longer exists"
        if gone := tokens - _tokens_in(path):
            stale[rel] = gone

    assert not stale, (
        f"these KNOWN_LEAKS entries are already clean: {stale}. "
        "Remove them — the list is the remaining 1.1 surface, not a history of it."
    )


def test_the_wa_prefix_is_actually_banned() -> None:
    """Guards the bug in the test itself.

    The scaffold banned ``whatsapp`` and believed it covered ``wa_message_id``. It does not.
    If someone 'tidies' BANNED back down to channel names, this fails.
    """
    assert "wa_" in BANNED
    assert not any(
        "wa_message_id".startswith(t) for t in BANNED if t != "wa_"
    ), "no token other than 'wa_' matches the real field names — do not remove it"


def test_the_permanent_exception_never_covers_the_wa_prefix() -> None:
    """Naming the set of channels is legitimate. Carrying WhatsApp's field names is not.

    Without this, ``CHANNEL_REGISTRY`` becomes a back door for exactly the leak 1.1 is removing.
    """
    for rel, tokens in CHANNEL_REGISTRY.items():
        assert "wa_" not in tokens, (
            f"{rel} is permanently excused for {sorted(tokens)}, but 'wa_' is never a channel "
            "name — it is the field prefix, and it belongs in KNOWN_LEAKS or nowhere"
        )
        assert (API_ROOT / rel).exists(), f"CHANNEL_REGISTRY names {rel}, which does not exist"


def test_the_quick_reply_cap_lives_in_the_adapter_not_the_core() -> None:
    """Trap #2, pinned structurally rather than by grepping prose.

    The scaffold enforced this from the core and its own boundary test passed, because the
    offence sat inside a string literal. Asserting on where the constant is *defined* cannot be
    fooled by a docstring that merely talks about it.
    """
    from apps.api.channels import whatsapp

    assert whatsapp.QUICK_REPLY_LIMIT == 3

    core_defining_it = [
        _rel(p) for p in _core_files() if "QUICK_REPLY_LIMIT" in p.read_text(encoding="utf-8")
    ]
    assert not core_defining_it, (
        f"a per-channel rendering limit is defined in the core: {core_defining_it}. "
        "It belongs in the adapter for the channel it is true of."
    )


def test_the_adapter_is_allowed_to_speak_whatsapp() -> None:
    """The point is not that the words vanish. It is that they live in one place.

    If ingestion ever comes back clean, either the adapter moved or this test is checking the
    wrong directory, and both are worth failing over.
    """
    adapter_files = [
        p
        for p in API_ROOT.rglob("*.py")
        if "__pycache__" not in p.parts and ADAPTER_PACKAGES & set(p.relative_to(API_ROOT).parts)
    ]
    assert adapter_files, "no adapter files found — ADAPTER_PACKAGES is out of date"
    assert any(_tokens_in(p) for p in adapter_files), (
        "the ingestion adapter names no channel at all, which means the WhatsApp specifics "
        "have moved somewhere else. Find out where."
    )
