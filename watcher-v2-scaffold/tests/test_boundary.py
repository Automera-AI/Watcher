"""The one architectural rule, enforced.

`app/core/` is the receptionist. It must not know how anyone got in touch. The moment it imports
a channel, adding a phone line stops being a two day job.
"""

import pathlib
import re

CORE = pathlib.Path(__file__).parent.parent / "app" / "core"


def test_core_never_imports_a_channel():
    offenders = []
    for path in CORE.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        if re.search(r"^\s*(from|import)\s+app\.channels", source, re.MULTILINE):
            offenders.append(path.name)
    assert not offenders, f"core must stay channel agnostic, but these import channels: {offenders}"


def test_core_mentions_no_provider_by_name():
    banned = ("whatsapp", "twilio", "elevenlabs", "deepgram", "hostaway")
    offenders = []
    for path in CORE.rglob("*.py"):
        source = path.read_text(encoding="utf-8").lower()
        code = "\n".join(
            line for line in source.splitlines() if not line.strip().startswith("#")
        )
        for word in banned:
            # A docstring mention is fine. An identifier is not.
            if re.search(rf"\b{word}\b\s*[.(=]", code):
                offenders.append(f"{path.name}:{word}")
    assert not offenders, offenders
