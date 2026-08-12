"""Phone adapter. Roadmap item 3.2.

Left here deliberately so the shape is visible from day one. When you build it, the only thing
that changes anywhere else is that this gets a real body. If you find yourself needing to touch
the core to make phone calls work, stop — something has leaked across the boundary.

It is also the argument for why the quick-reply cap is not in the core: a caller cannot see
buttons at all, so this adapter has to speak the options instead. Three would be as wrong here
as six is on WhatsApp.
"""

from __future__ import annotations

from dataclasses import dataclass

from apps.api.schemas.envelope import OutboundAction


@dataclass(frozen=True, slots=True)
class SpokenMessage:
    """What gets handed to a speech synthesiser. No buttons, by definition."""

    text: str


def render(action: OutboundAction) -> SpokenMessage:
    """Flatten an action into something sayable, keeping every option.

    A caller who is read three of six choices has silently lost the other three, so the options
    go into the spoken text rather than being dropped.
    """
    if not action.quick_replies:
        return SpokenMessage(text=action.text)

    *rest, last = action.quick_replies
    options = f"{', '.join(rest)} or {last}" if rest else last
    return SpokenMessage(text=f"{action.text} You can say {options}.")
