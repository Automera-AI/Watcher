"""The fact-locked generative renderer (pre-demo Step 5, plan §8–§10).

The deterministic system already decides the *act* and proves the *facts* — which service, which
branch, which day, which times the diary returned, which reference the scheduling system issued.
This module lets a model phrase that act naturally in Egyptian Arabic **without ever owning a
protected value**. The model writes the sentence around placeholders (``{times}``, ``{branch}``,
``{booking_reference}`` …) and code substitutes the deterministic values afterwards; the model
never controls the values themselves.

**This is deliberately smaller than the post-demo renderer architecture.** No ``MessageFact``, no
fact-IDs, no phrase library, no ``RenderPlan``. One short call, one hard timeout, no retry loop.
Any failure — a provider error, a timeout, a template that references a value it was not given,
an invented number, an English leak, a clinical claim — returns the deterministic Arabic fallback
the caller already composed (Step 4), unchanged. Generation can only ever make the receptionist
*sound* better; it can never make the transaction *wrong*.

**Where it is not allowed to run at all.** The receptionist calls the renderer only on the five
eligible acts (``ask_missing_slot``, ``offer_times``, ``nothing_free``, ``read_back``,
``booking_confirmed``). The excluded safety surfaces — the clinical block, the emergency reply,
the generic hand-off, the unbuilt-tool fallback, a price quote, any transactional failure needing
exact deterministic wording — never reach this module, because the receptionist never asks it to
phrase them (plan §8 "Never eligible"). The default renderer is the no-op ``TemplateRenderer``, so
``RESPONSE_STYLE=template`` (the default) makes **zero** model calls.

**Persistence invariant.** Generation happens inside the receptionist, before the outbound reply
is persisted, so the one ``OutboundAction`` the receptionist returns is the exact text that is
recorded to conversation history, delivered on the channel, and carried on
``ProcessOutcome.outbound_action`` — the same object down all three paths.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any, Literal, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, model_validator

from apps.api.classifier.provider import ProviderError
from apps.api.classifier.transport import post_json

if TYPE_CHECKING:
    from apps.api.core.config import Settings

logger = logging.getLogger(__name__)

#: The acts the renderer may phrase. Everything else is a deterministic safety surface and is never
#: routed here (plan §8 "Never eligible"): clinical block, emergency, generic hand-off, unbuilt
#: fallback, price quote, and any transactional failure that needs exact deterministic wording.
RenderAct = Literal[
    "ask_missing_slot",
    "offer_times",
    "nothing_free",
    "read_back",
    "booking_confirmed",
]

#: Every protected placeholder the model is allowed to reference. A fact the caller does not supply
#: for a given render is simply not in that request's ``facts``, and a template that references it
#: is rejected — the model may only place values the deterministic layer proved.
_KNOWN_PLACEHOLDERS = frozenset({"service", "branch", "date", "time", "times", "booking_reference"})

#: Which placeholders each act *must* surface. These are the protected values whose omission would
#: let the model imply something untrue: an ``offer_times`` that never shows ``{times}`` has made
#: the availability up, and a ``booking_confirmed`` without ``{booking_reference}`` is the "All
#: set!" bug wearing a nicer sentence. ``ask_missing_slot`` and ``nothing_free`` require none —
#: the first asks for what is missing and the second states an absence, so neither carries a
#: protected number that has to appear.
_REQUIRED_PLACEHOLDERS: dict[str, frozenset[str]] = {
    "ask_missing_slot": frozenset(),
    "offer_times": frozenset({"times"}),
    "nothing_free": frozenset(),
    "read_back": frozenset({"service", "branch", "date", "time"}),
    "booking_confirmed": frozenset({"booking_reference"}),
}

#: A ``{placeholder}`` token. The inner name is captured so an unknown or malformed one is caught.
_TOKEN = re.compile(r"\{([^{}]*)\}")

#: Any digit the model might have typed itself — ASCII, Arabic-Indic (٠-٩) or the extended
#: Arabic-Indic range (۰-۹). Every appointment time, date and reference arrives through a
#: placeholder and is substituted *after* validation, so a digit surviving in the model's own
#: template is a number it invented and the whole generation is rejected.
_DIGIT = re.compile(r"[0-9٠-٩۰-۹]")

#: A run of Latin letters long enough to be English prose rather than a stray character. The reply
#: must be Egyptian Arabic; a Latin service name or reference reaches the patient only through a
#: substituted placeholder, never through the model writing it out.
_LATIN_WORD = re.compile(r"[A-Za-z]{3,}")

#: Clinical / suitability / recommendation language the renderer must never emit — deciding a
#: treatment is suitable, safe, advisable or risky is a clinician's call, and the model phrasing a
#: booking confirmation has no business making it. Matched case-insensitively as substrings; the
#: Egyptian-Arabic terms are matched directly. Kept tight so an ordinary offer or read-back never
#: trips it.
_CLINICAL_MARKERS_EN = (
    "recommend",
    "suitable",
    "safe",
    "risk",
    "advise",
    "pregnan",
    "medical",
    "treatment is",
)
_CLINICAL_MARKERS_AR = (
    "مناسب",
    "أنصح",
    "ننصح",
    "بننصح",
    "آمن",
    "خطر",
    "حامل",
    "استشير",
)

#: Phrases that claim a booking already exists. Allowed only on ``booking_confirmed``; on any other
#: act (an offer, a read-back that is still *asking* permission) a claim that the appointment is
#: done is a lie the renderer must not tell, so it is rejected and the deterministic wording stands.
_CONFIRMATION_MARKERS = (
    "تم الحجز",
    "اتحجز",
    "حجزتلك",
    "تم تأكيد الحجز",
    "booked",
    "confirmed",
)


class RenderSpec(BaseModel):
    """A minimal render request: the proven act, and the proven values it may place.

    ``facts`` maps a protected placeholder name to the deterministic value code will substitute for
    it. The model is told the placeholder names and phrases around them; it never sees this as a
    value it may alter. The validator enforces the contract before a single token is sent: every
    fact key is a known placeholder, and every placeholder the act *requires* is present — so a
    caller that has not proven the protected values for an act cannot ask the model to imply them.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    act: RenderAct
    facts: dict[str, str]

    @model_validator(mode="after")
    def _known_and_complete(self) -> RenderSpec:
        unknown = set(self.facts) - _KNOWN_PLACEHOLDERS
        if unknown:
            raise ValueError(f"unknown placeholder facts: {sorted(unknown)}")
        if blank := sorted(k for k, v in self.facts.items() if not v):
            raise ValueError(f"blank fact values: {blank}")
        missing = self.required_placeholders - set(self.facts)
        if missing:
            raise ValueError(f"act {self.act} is missing required facts: {sorted(missing)}")
        return self

    @property
    def required_placeholders(self) -> frozenset[str]:
        """The placeholders this act's template must contain."""
        return _REQUIRED_PLACEHOLDERS[self.act]

    @property
    def allowed_placeholders(self) -> frozenset[str]:
        """The placeholders this act's template may contain — exactly the facts it was given."""
        return frozenset(self.facts)


def substitute_or_reject(template: str, spec: RenderSpec) -> str | None:
    """Validate the model's phrasing against the fact lock, then substitute — or reject it.

    Returns the final patient-ready text on success, or ``None`` when the template breaks any rule,
    in which case the caller sends its deterministic Arabic fallback instead. Every check runs on
    the model's *own* output, before the protected values are inserted, so the Latin/digit/clinical
    rules judge what the model wrote rather than what code is about to place. The rules, in order:

    * balanced, well-formed ``{placeholder}`` syntax;
    * no unknown placeholder (every token is one of the facts this act was given);
    * every placeholder the act requires is present;
    * no digit the model typed itself (invented time / date / reference);
    * no English prose (the reply must be Egyptian Arabic);
    * no clinical / suitability / recommendation language;
    * no claim the booking is confirmed unless the act *is* the confirmation.
    """
    text = template.strip()
    if not text:
        return None

    stripped = _TOKEN.sub("", text)
    if "{" in stripped or "}" in stripped:
        return None  # a stray or unbalanced brace — malformed placeholder syntax

    tokens = _TOKEN.findall(text)
    allowed = spec.allowed_placeholders
    for token in tokens:
        if token not in allowed:  # unknown, or a malformed inner name (spaces, digits, casing)
            return None
    if not spec.required_placeholders <= set(tokens):
        return None

    if _DIGIT.search(stripped):
        return None
    if _LATIN_WORD.search(stripped):
        return None

    lowered = text.lower()
    if any(marker in lowered for marker in _CLINICAL_MARKERS_EN):
        return None
    if any(marker in text for marker in _CLINICAL_MARKERS_AR):
        return None

    if spec.act != "booking_confirmed" and any(
        marker in lowered for marker in ("booked", "confirmed")
    ):
        return None
    if spec.act != "booking_confirmed" and any(marker in text for marker in _CONFIRMATION_MARKERS):
        return None

    try:
        return text.format(**spec.facts)
    except (KeyError, IndexError, ValueError):
        return None


# ── The model call ───────────────────────────────────────────────────────────────────────────


class RenderProvider(Protocol):
    """One short call that returns the model's raw phrasing for a spec, or raises.

    Structural so a test can inject a canned or raising provider without a network, and so the one
    concrete implementation stays a thin wrapper over the shared transport.
    """

    def complete(self, spec: RenderSpec) -> str: ...


#: The renderer's whole instruction. Everything variable (the act, the placeholders it may use)
#: goes in the user turn; this stays byte-identical so it could be cached, and — more importantly —
#: so the fact-lock rules the validator enforces are stated to the model up front.
_SYSTEM_PROMPT = (
    "You are a warm Egyptian-Arabic receptionist at a dermatology clinic. You write ONE short, "
    "natural chat message in Egyptian Arabic (عامية مصرية).\n\n"
    "Absolute rules:\n"
    "- Use ONLY the placeholders you are given, written exactly as {name}. Do not invent "
    "placeholders.\n"
    "- Never write any number, time, date, or booking reference yourself — those only ever appear "
    "through a placeholder. Do not type digits.\n"
    "- Write in Egyptian Arabic only. Do not write English words or sentences.\n"
    "- Never give medical advice, and never say a treatment is suitable, safe, recommended or "
    "risky.\n"
    "- Only say a booking is confirmed if you are explicitly asked to confirm it.\n"
    "- Reply with the message text only — no quotes, no labels, no explanation."
)

#: What each eligible act is asking the model to phrase, in the user turn. Concrete but placeholder-
#: only: the model is told the shape of the sentence, never a real value.
_ACT_BRIEF: dict[str, str] = {
    "ask_missing_slot": "Ask the patient warmly for the missing booking detail (a service, a "
    "branch, a day, or a time). Keep it to one friendly question.",
    "offer_times": "Tell the patient the available appointment times and ask which one suits them.",
    "nothing_free": "Gently tell the patient there is nothing free for what they asked, and offer "
    "to look at another day.",
    "read_back": "Read the booking details back to the patient and ask them to confirm, without "
    "claiming it is booked yet.",
    "booking_confirmed": "Warmly tell the patient the appointment is confirmed and give them the "
    "booking reference.",
}


def _user_prompt(spec: RenderSpec) -> str:
    """The per-request turn: the act's brief plus the exact placeholders it may use."""
    required = spec.required_placeholders
    listed = ", ".join(
        f"{{{name}}}" + (" (must be used)" if name in required else "") for name in spec.facts
    )
    placeholders = listed or "(none — use no placeholders)"
    return (
        f"{_ACT_BRIEF[spec.act]}\n\n"
        f"Placeholders you may use: {placeholders}.\n"
        "Write the Egyptian-Arabic message now."
    )


#: Pinned per Anthropic's versioning header contract; not a model version. Same value the classifier
#: transport uses — this is the same Messages API.
_ANTHROPIC_API_VERSION = "2023-06-01"

#: One short sentence. A tight ceiling keeps a degenerate response from becoming a bill, and the
#: reply is a single chat line either way.
_MAX_OUTPUT_TOKENS = 256


class AnthropicRenderProvider:
    """One phrasing call to a Claude model over the shared transport. No retry loop.

    Reuses ``classifier/transport.post_json`` with ``max_retries=0`` so the call is made exactly
    once and a failure (timeout, connection reset, 4xx/5xx) raises ``ProviderError`` immediately —
    the "hard timeout, no retry" the plan requires, enforced by the same battle-tested plumbing the
    classifier uses rather than a second HTTP path.
    """

    def __init__(
        self,
        model_id: str,
        api_key: str,
        client: httpx.Client,
        *,
        base_url: str,
        timeout_seconds: float,
    ) -> None:
        self._model_id = model_id
        self._api_key = api_key
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds

    def complete(self, spec: RenderSpec) -> str:
        body = post_json(
            self._client,
            f"{self._base_url}/v1/messages",
            headers={
                "x-api-key": self._api_key,
                "anthropic-version": _ANTHROPIC_API_VERSION,
                "content-type": "application/json",
            },
            payload={
                "model": self._model_id,
                "max_tokens": _MAX_OUTPUT_TOKENS,
                "system": _SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": _user_prompt(spec)}],
            },
            timeout=self._timeout,
            max_retries=0,
        )
        return _text_from(body)


def _text_from(body: dict[str, Any]) -> str:
    """Join the text blocks of a Messages API response, or ``""`` if there are none."""
    parts: list[str] = []
    for block in body.get("content", []):
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts)


# ── The renderers ────────────────────────────────────────────────────────────────────────────


class Renderer(Protocol):
    """Turns an eligible act and its proven facts into patient-ready text — or the fallback."""

    async def render(self, act: RenderAct, facts: dict[str, str], *, fallback: str) -> str: ...


class TemplateRenderer:
    """The no-op renderer: always the deterministic fallback, and never a model call.

    This is the default, so ``RESPONSE_STYLE=template`` makes zero renderer calls — the demo's
    safe mode, where every word a patient reads is deterministic Egyptian Arabic.
    """

    async def render(self, act: RenderAct, facts: dict[str, str], *, fallback: str) -> str:
        return fallback


class GenerativeRenderer:
    """One constrained model call per eligible act, validated, or the deterministic fallback.

    The failure path is the whole point: an invalid spec (the caller had not proven the required
    facts), a provider error, a timeout, or a phrasing that breaks any fact-lock rule all return
    the fallback the receptionist already composed. There is no second, repair call — a single
    failed render must never delay the conversation materially (plan §10).
    """

    def __init__(self, provider: RenderProvider) -> None:
        self._provider = provider

    async def render(self, act: RenderAct, facts: dict[str, str], *, fallback: str) -> str:
        try:
            spec = RenderSpec(act=act, facts=dict(facts))
        except ValueError:
            # The caller has not proven the values this act requires. Never ask the model to imply
            # them — fall back deterministically without a call.
            return fallback
        try:
            raw = self._provider.complete(spec)
        except ProviderError as exc:
            logger.warning("renderer provider error on %s, using fallback: %s", act, exc)
            return fallback
        except Exception:  # noqa: BLE001 - a renderer must never take the conversation down
            logger.exception("renderer failed unexpectedly on %s, using fallback", act)
            return fallback
        rendered = substitute_or_reject(raw, spec)
        if rendered is None:
            logger.info("renderer output rejected on %s, using fallback", act)
            return fallback
        return rendered


# ── Process-global wiring (the same service-locator pattern as ``current_copy``) ──────────────

_RENDERER: Renderer = TemplateRenderer()


def current_renderer() -> Renderer:
    """The renderer this process is configured with; the ``TemplateRenderer`` until set."""
    return _RENDERER


def configure_renderer(renderer: Renderer) -> None:
    """Swap in the process's renderer (``orchestration/composition.py``).

    The same named-seam pattern as ``configure_conversation_copy``: the receptionist is a module of
    functions with nowhere to hold an instance, so it reads this global. Default is
    ``TemplateRenderer`` (deterministic, zero calls), so a process that never calls this — or a test
    — behaves exactly as it did before Step 5.
    """
    global _RENDERER
    _RENDERER = renderer


async def render_reply(act: RenderAct, facts: dict[str, str], *, fallback: str) -> str:
    """The one entry point the receptionist calls. Delegates to the configured renderer."""
    return await current_renderer().render(act, facts, fallback=fallback)


def build_renderer(settings: Settings, client: httpx.Client | None = None) -> Renderer:
    """The renderer named by ``RESPONSE_STYLE`` — ``template`` (default) or ``generative``.

    ``template`` is the no-op renderer and builds no HTTP client. ``generative`` wires one
    ``claude-haiku-4-5`` call through the existing Anthropic configuration
    (``Settings.llm_credentials``); if the Anthropic key is missing it degrades to the template
    renderer with a warning rather than failing the boot — a demo that forgot the key should still
    run deterministically, not refuse to start.
    """
    if settings.response_style != "generative":
        return TemplateRenderer()
    model_id = settings.response_renderer_model
    try:
        credentials = settings.llm_credentials(model_id)
    except Exception:  # noqa: BLE001 - missing key etc.; never fail boot over the renderer
        logger.warning(
            "RESPONSE_STYLE=generative but credentials for %s are unavailable; "
            "falling back to the deterministic template renderer",
            model_id,
        )
        return TemplateRenderer()
    if credentials.api_key is None:
        logger.warning(
            "RESPONSE_STYLE=generative but no API key for %s; using the template renderer",
            model_id,
        )
        return TemplateRenderer()
    http = client if client is not None else httpx.Client()
    provider = AnthropicRenderProvider(
        model_id,
        credentials.api_key,
        http,
        base_url=credentials.base_url,
        timeout_seconds=settings.response_renderer_timeout_seconds,
    )
    logger.info("renderer wired: style=generative model=%s", model_id)
    return GenerativeRenderer(provider)
