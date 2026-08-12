"""The receptionist intent vocabulary (roadmap item 0.3).

Holiday homes, Dubai and Egypt, guests only. The vocabulary itself is data — ``intents.yaml``
plus a file per client under ``clients/`` — so a client can differ without a deploy. ``schema``
validates it, ``compile`` writes the JSON the application loads.

Nothing here is imported by the request path yet. It unblocks 1.2, 2.4, 2.5 and the golden set.
"""

from __future__ import annotations

from packages.intents.schema import (
    MONEY_INTENTS,
    MUST_HAND_OFF,
    MUST_VERIFY,
    ClientOverride,
    Intent,
    Vocabulary,
    load,
    load_client,
    load_compiled,
)

__all__ = [
    "MONEY_INTENTS",
    "MUST_HAND_OFF",
    "MUST_VERIFY",
    "ClientOverride",
    "Intent",
    "Vocabulary",
    "load",
    "load_client",
    "load_compiled",
]
