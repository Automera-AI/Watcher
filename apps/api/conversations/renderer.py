"""The fact-locked generative renderer (pre-demo Step 5, plan §8–§10).

The deterministic system already decides the *act* and proves the *facts* — which service, which
branch, which day, which times the diary returned, which reference the scheduling system issued.
This module lets a model phrase that act naturally in Egyptian Arabic **without ever owning a
protected value, and without being able to assert an unproven one in prose**.

**Safe by construction: the output must be one of a few approved sentence skeletons.** A word
allowlist bounds *vocabulary* but not *meaning* — the same safe words reordered can attach "يناسبك"
to the treatment (a suitability claim), drop the "مفيش" that makes "nothing free" mean nothing free,
or end a read-back on a statement instead of a question. So the lock is structural, not lexical:

1. **Full placeholder coverage.** Every protected value an act carries must appear as a placeholder
   (``{service}``/``{branch}``/``{date}``/``{time}``/``{times}``/``{booking_reference}``), so the
   model never *needs* to write a fact — it is handed the real one to place.
2. **Complete-output character gate.** After approved placeholders are removed, every remaining
   character must be Arabic, whitespace, or one of the four inert punctuation marks the exemplars
   use (``،؟.!``). Latin letters, emoji, and every other symbol reject the whole generation.
3. **Exemplar-skeleton match.** The surviving phrasing, once punctuation and diacritics are stripped
   and words are lightly normalised, must equal — word for word, in order, with the placeholders in
   their exact positions — one of a small set of hand-written, fact-locked skeletons for that act
   (see ``_EXEMPLARS``). Each skeleton *is* the meaning: the ``nothing_free`` skeletons carry the
   negation, the ask and read-back skeletons are interrogative, only ``booking_confirmed`` carries
   "تم الحجز". A reordering, an omission, an inserted "أكيد", a fabricated service or branch, an
   efficacy claim — anything that is not one of the approved sentences — does not match, so the
   generation is rejected and the deterministic Step-4 fallback stands. Over-rejection is safe (it
   falls back); over-acceptance is impossible because only the vetted skeletons are accepted.

The slot a missing-slot question is about is itself a proven fact (``{slot}``), so the model asks
the question the deterministic layer chose, not one it picked. Generation here is effectively the
model choosing (and lightly punctuating) among approved phrasings — bounded on purpose. A looser
grammar is post-demo work (plan §18); this is the strict pre-demo form Codex asked for.

**This is deliberately smaller than the post-demo renderer architecture.** No ``MessageFact``, no
fact-IDs, no ``RenderPlan``. One short call, one hard timeout, no retry loop. Any failure — a
provider error, a timeout, an unmatched skeleton, a missing placeholder, an invented number, an
English leak — returns the deterministic Arabic fallback the caller already composed, unchanged.
Generation can make the receptionist *sound* better; it can never make the transaction *wrong*. The
default renderer is the no-op ``TemplateRenderer``, so ``RESPONSE_STYLE=template`` (the default)
makes **zero** model calls.

**Where it is not allowed to run at all.** The receptionist calls the renderer only on the five
eligible acts. The excluded safety surfaces — clinical block, emergency, generic hand-off,
unbuilt-tool fallback, price quote, ambiguous service, any transactional failure — never reach this
module, because the receptionist never asks it to phrase them (plan §8 "Never eligible").

**Persistence invariant.** Generation happens inside the receptionist, before the outbound reply
is persisted, so the one ``OutboundAction`` the receptionist returns is the exact text that is
recorded to conversation history, delivered on the channel, and carried on
``ProcessOutcome.outbound_action`` — the same object down all three paths.
"""

from __future__ import annotations

import asyncio
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

#: Every placeholder the model may reference. ``slot`` is the deterministic descriptor of the one
#: detail a missing-slot question is about ("الخدمة اللي تحبي تحجزيها" …), a proven fact like the
#: rest, so the model asks the question the task chose rather than one it picked.
_KNOWN_PLACEHOLDERS = frozenset(
    {"service", "branch", "date", "time", "times", "booking_reference", "slot"}
)

#: Which placeholders each act must surface — its **full** protected-fact set, not just the numeric
#: one. Requiring only ``{times}`` on an offer let the model name a fabricated service/branch/day in
#: prose beside it; requiring every fact as a placeholder means the model is handed each real value
#: to place and never needs to write one. Paired with the closed vocabulary below, this is what
#: makes the fact lock structural: proven values in, nothing else admitted.
_REQUIRED_PLACEHOLDERS: dict[str, frozenset[str]] = {
    "ask_missing_slot": frozenset({"slot"}),
    "offer_times": frozenset({"service", "branch", "date", "times"}),
    "nothing_free": frozenset({"service", "branch", "date"}),
    "read_back": frozenset({"service", "branch", "date", "time"}),
    "booking_confirmed": frozenset({"service", "branch", "date", "time", "booking_reference"}),
}

#: The approved sentence skeletons per act — the *only* phrasings a generation may take (up to
#: punctuation, diacritics and the light word-normalisation below). Each is a complete, hand-vetted,
#: fact-locked Egyptian-Arabic sentence whose meaning is safe by inspection: the ``nothing_free``
#: skeletons carry the "مفيش" negation, the ask and read-back skeletons are questions, and only
#: ``booking_confirmed`` states "تم الحجز"/"الحجز اتأكد". They also few-shot the model. Adding a
#: skeleton widens what the model may say; nothing else does. To stay matchable, a skeleton uses no
#: service/branch/weekday/relative-day/number word and no booking-status word outside
#: ``booking_confirmed`` — the facts are only ever the placeholders.
_EXEMPLARS: dict[str, tuple[str, ...]] = {
    "ask_missing_slot": (
        "تمام يا فندم، ممكن تقوليلي {slot}؟",
        "أكيد، محتاجة أعرف {slot} عشان أكمّلك؟",
        "تمام، تحبي تقوليلي {slot} لو سمحتي؟",
    ),
    "offer_times": (
        "تمام يا قمر، متاح {service} في {branch} يوم {date} المواعيد دي {times}. تحبي أنهي واحدة؟",
        "أكيد، عندنا {service} في {branch} يوم {date} المواعيد دي {times}. أنهي ميعاد يناسبك؟",
    ),
    "nothing_free": (
        "معلش يا فندم، مفيش مواعيد فاضية {service} في {branch} يوم {date}. تحبي أشوفلك يوم تاني؟",
        "للأسف مفيش حاجة فاضية {service} في {branch} يوم {date}. تحبي أدوّرلك على يوم تاني؟",
    ),
    "read_back": (
        "تمام يا قمر، أأكدلك {service} في {branch} يوم {date} الساعة {time}؟ صح كده؟",
        "أكيد، تحبي أأكد {service} في {branch} يوم {date} الساعة {time}؟",
    ),
    "booking_confirmed": (
        "تم الحجز يا قمر، {service} في {branch} يوم {date} الساعة {time}. "
        "رقم حجزك {booking_reference}. مستنيينك في الفرع!",
        "الحجز اتأكد يا فندم، {service} في {branch} يوم {date} الساعة {time}. "
        "رقم حجزك {booking_reference}.",
    ),
}

#: A ``{placeholder}`` token. The inner name is captured so an unknown or malformed one is caught.
_TOKEN = re.compile(r"\{([^{}]*)\}")

#: Any digit the model might have typed itself — ASCII, Arabic-Indic (٠-٩) or extended Arabic-Indic
#: (۰-۹). Every time, date and reference arrives through a placeholder and is substituted *after*
#: validation, so a digit surviving in the model's own template is a number it invented.
_DIGIT = re.compile(r"[0-9٠-٩۰-۹]")

#: Any model-authored Latin letter rejects the candidate, including a one-letter label.
_LATIN_LETTER = re.compile(r"[A-Za-z]")

#: Complete patient-visible character allowlist after approved placeholders are removed. The four
#: punctuation marks are exactly the inert punctuation used by ``_EXEMPLARS``; emoji and arbitrary
#: symbols are deliberately not admitted in the pre-demo renderer.
_UNAPPROVED_MODEL_CHARACTER = re.compile(r"[^ء-يٱـً-ْٰ\s،؟.!]")

#: Arabic diacritics and tatweel, stripped before tokenising so "مَواعيد" and "مواعيدـ" match
#: "مواعيد". (``ـ`` tatweel, ``ً-ْ`` harakat, ``ٰ`` superscript alef.)
_ARABIC_MARKS = re.compile(r"[ـً-ْٰ]")

#: Anything that is not an Arabic letter or whitespace → a word separator. Digits, Latin, emoji and
#: punctuation all become boundaries, so tokenising yields Arabic words only. The range ``ء``
#: (hamza) to ``ي`` (yeh) is every standard Arabic letter; ``ٱ`` is alef wasla.
_NON_ARABIC_LETTER = re.compile(r"[^ء-يٱ\s]")


def _normalise_words(text: str) -> list[str]:
    """The Arabic content words of ``text``, lightly normalised for allowlist matching.

    Diacritics and tatweel are removed, non-letters become boundaries, and a leading conjunction
    ``و`` and/or definite article ``ال`` are stripped so "والمواعيد"/"المواعيد" both match the
    exemplar word "مواعيد". The normalisation is deliberately conservative: any form it fails to
    fold simply is not found in the allowlist and the generation falls back, which is safe. It can
    never fold a fabricated word *into* the allowlist, because the allowlist holds only the exemplar
    words themselves.
    """
    cleaned = _NON_ARABIC_LETTER.sub(" ", _ARABIC_MARKS.sub("", text))
    words: list[str] = []
    for raw in cleaned.split():
        word = raw
        if word.startswith("و") and len(word) > 2:
            word = word[1:]
        if word.startswith("ال") and len(word) > 3:
            word = word[2:]
        if word:
            words.append(word)
    return words


def _canonical_tokens(template: str) -> tuple[str, ...]:
    """The template as an ordered sequence of normalised words and placeholder markers.

    Placeholders become their own ``{name}`` tokens in position; the Arabic between them is
    normalised word by word (``_normalise_words``), so approved punctuation, diacritics and tatweel
    fall away. Two phrasings that differ only in approved punctuation or a stripped clitic
    canonicalise to the same sequence; a reordering, an omission or an inserted word does not. This
    is what makes the match test one of *meaning-bearing structure*, not just of the words present.
    """
    parts: list[str] = []
    cursor = 0
    for match in _TOKEN.finditer(template):
        parts.extend(_normalise_words(template[cursor : match.start()]))
        parts.append("{" + match.group(1) + "}")
        cursor = match.end()
    parts.extend(_normalise_words(template[cursor:]))
    return tuple(parts)


#: The approved canonical token sequences per act. A generation is accepted only if its own
#: canonical form is one of these — the ordered, structural lock the word allowlist could not be.
_SKELETONS: dict[str, frozenset[tuple[str, ...]]] = {
    act: frozenset(_canonical_tokens(exemplar) for exemplar in exemplars)
    for act, exemplars in _EXEMPLARS.items()
}


class RenderSpec(BaseModel):
    """A minimal render request: the proven act, and the proven values it may place.

    ``facts`` maps a placeholder name to the deterministic value code will substitute for it. The
    validator enforces the contract before a single token is sent: every fact key is a known
    placeholder, no value is blank, and every placeholder the act *requires* is present — so a
    caller that has not proven an act's full protected-fact set cannot ask the model to imply it.
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
        """The placeholders this act's template must contain — its full protected-fact set."""
        return _REQUIRED_PLACEHOLDERS[self.act]

    @property
    def allowed_placeholders(self) -> frozenset[str]:
        """The placeholders this act's template may contain — exactly the facts it was given."""
        return frozenset(self.facts)


def substitute_or_reject(template: str, spec: RenderSpec) -> str | None:
    """Validate the model's phrasing against the fact lock, then substitute — or reject it.

    Returns the final patient-ready text on success, or ``None`` when the template breaks any rule,
    in which case the caller sends its deterministic Arabic fallback instead. Every check runs on
    the model's *own* output, before the protected values are inserted. The rules, in order:

    * well-formed, balanced ``{placeholder}`` syntax;
    * no unknown placeholder (every token is one of the facts this act was given);
    * every placeholder the act requires is present (its full protected-fact set);
    * no digit the model typed itself (invented time / date / reference);
    * no model-authored Latin letter;
    * no character outside Arabic letters/marks, whitespace, and ``،؟.!``;
    * **the phrasing canonicalises to one of the act's approved skeletons** — the ordered,
      meaning-bearing structural lock. A fabricated service/branch/day, a dropped negation, a
      reordering that moves "يناسبك" onto the treatment, a read-back that ends on a statement, or a
      premature "تم الحجز" all produce a sequence that is not an approved skeleton, and fall back.

    On success the *original* text is what is substituted and returned, but only after this complete
    patient-visible output has passed both the character gate and the structural lock.
    """
    text = template.strip()
    if not text:
        return None

    stripped = _TOKEN.sub(" ", text)
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
        return None  # a number the model typed itself
    if _LATIN_LETTER.search(stripped):
        return None  # even a one-letter model-authored Latin label
    if _UNAPPROVED_MODEL_CHARACTER.search(stripped):
        return None  # emoji or arbitrary patient-visible punctuation/symbols

    if _canonical_tokens(text) not in _SKELETONS[spec.act]:
        # Not one of the approved sentences for this act — reordered, missing a word, or carrying
        # one it was not given. Its meaning is not provably safe, so fall back.
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


#: The renderer's whole instruction. Everything variable (the act, its placeholders, its exemplars)
#: goes in the user turn; this states the fact-lock rules the validator enforces up front.
_SYSTEM_PROMPT = (
    "You are a warm Egyptian-Arabic receptionist at a dermatology clinic. You write ONE short, "
    "natural chat message in Egyptian Arabic (عامية مصرية).\n\n"
    "Absolute rules:\n"
    "- Use ONLY the placeholders you are given, written exactly as {name}, and use every one that "
    "is marked required. Do not invent placeholders.\n"
    "- Never write a service name, branch name, day, time, number, or booking reference yourself — "
    "those appear ONLY through their placeholders. Do not type digits.\n"
    "- Choose ONE of the example phrasings. Preserve its words, word order, and placeholder "
    "positions exactly. Vary only light punctuation and spacing. Do not add other words, labels, "
    "symbols, or emoji.\n"
    "- Write in Egyptian Arabic only. No English.\n"
    "- Never give medical advice or say a treatment is suitable, safe, recommended, guaranteed, or "
    "effective.\n"
    "- Only say a booking is confirmed if you are explicitly asked to confirm it.\n"
    "- Reply with the message text only — no quotes, no labels, no explanation."
)

#: What each eligible act is asking the model to phrase, in the user turn.
_ACT_BRIEF: dict[str, str] = {
    "ask_missing_slot": "Ask the patient warmly for the one missing booking detail named by "
    "{slot}. Ask about {slot} and nothing else.",
    "offer_times": "Tell the patient the available appointment times and ask which one suits them.",
    "nothing_free": "Gently tell the patient there is nothing free for what they asked, and offer "
    "to look at another day.",
    "read_back": "Read the booking details back to the patient and ask them to confirm — do NOT "
    "say it is booked yet.",
    "booking_confirmed": "Warmly tell the patient the appointment is now confirmed and give them "
    "the booking reference.",
}


def _user_prompt(spec: RenderSpec) -> str:
    """The per-request turn: the act's brief, the placeholders to use, and the safe exemplars."""
    required = spec.required_placeholders
    listed = ", ".join(
        f"{{{name}}}" + (" (required)" if name in required else "") for name in spec.facts
    )
    examples = "\n".join(f"- {exemplar}" for exemplar in _EXEMPLARS[spec.act])
    return (
        f"{_ACT_BRIEF[spec.act]}\n\n"
        f"Placeholders you may use: {listed}.\n\n"
        "Choose ONE of the example phrasings. Preserve its words, word order, and placeholder "
        "positions exactly. Vary only light punctuation and spacing. Do not add other words, "
        f"labels, symbols, or emoji.\n\nExample phrasings:\n{examples}\n\n"
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

    The provider call is synchronous but offloaded with ``asyncio.to_thread``, so the ~2.5s ceiling
    a slow render can spend never blocks other work sharing the event loop.
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
            raw = await asyncio.to_thread(self._provider.complete, spec)
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

    ``template`` is the no-op renderer and builds no HTTP client. ``generative`` wires one short
    Claude call through the existing Anthropic configuration (``Settings.llm_credentials``). It
    degrades to the template renderer — never failing the boot — when it cannot run correctly: a
    non-clinic vertical (the phrasing vocabulary is Egyptian-Arabic clinic wording), a non-Claude
    model id (this path speaks only the Anthropic Messages API), or a missing Anthropic key. A demo
    that is misconfigured should still run deterministically, not refuse to start or emit the wrong
    voice.
    """
    if settings.response_style != "generative":
        return TemplateRenderer()
    if settings.tenant_vertical != "clinics":
        logger.warning(
            "RESPONSE_STYLE=generative on vertical=%s; the renderer voice is clinic Egyptian "
            "Arabic, so using the template renderer instead",
            settings.tenant_vertical,
        )
        return TemplateRenderer()
    model_id = settings.response_renderer_model
    if not model_id.startswith("claude"):
        logger.warning(
            "RESPONSE_RENDERER_MODEL=%s is not a Claude model, but the renderer speaks the "
            "Anthropic Messages API; using the template renderer",
            model_id,
        )
        return TemplateRenderer()
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
