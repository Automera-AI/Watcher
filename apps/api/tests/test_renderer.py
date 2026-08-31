"""The fact-locked generative renderer, unit level (pre-demo Step 5, plan §9–§10).

These tests pin the renderer's two guarantees in isolation, before the receptionist tests exercise
it end to end: the model may phrase, but every protected value is code's, and *any* failure returns
the deterministic fallback unchanged. The validator is the safety boundary, so most of the file is
the ways a generation is rejected — a missing placeholder, an unknown one, an invented number,
English prose, a clinical claim — each of which must fall back rather than reach a patient.
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from apps.api.classifier.provider import ProviderError
from apps.api.conversations.renderer import (
    GenerativeRenderer,
    RenderAct,
    RenderSpec,
    TemplateRenderer,
    substitute_or_reject,
)


def _render(
    renderer: GenerativeRenderer | TemplateRenderer,
    act: RenderAct,
    facts: dict[str, str],
    fallback: str,
) -> str:
    return asyncio.run(renderer.render(act, facts, fallback=fallback))


class _Provider:
    """A render provider that returns a canned string and counts how often it was called."""

    def __init__(self, output: str) -> None:
        self._output = output
        self.calls = 0

    def complete(self, spec: RenderSpec) -> str:
        self.calls += 1
        return self._output


class _RaisingProvider:
    """A provider that fails the one call the way the transport does — a ``ProviderError``."""

    def __init__(self, exc: Exception | None = None) -> None:
        self._exc = exc or ProviderError("provider unreachable after 1 attempts — timeout")
        self.calls = 0

    def complete(self, spec: RenderSpec) -> str:
        self.calls += 1
        raise self._exc


# ── The spec (the minimal Pydantic v2 render specification) ──────────────────────────────────


def test_a_spec_missing_a_required_placeholder_fact_is_rejected() -> None:
    """``booking_confirmed`` needs a reference; a spec without one cannot be built."""
    with pytest.raises(ValidationError):
        RenderSpec(act="booking_confirmed", facts={"service": "برايم ليز"})


def test_a_spec_with_an_unknown_fact_is_rejected() -> None:
    with pytest.raises(ValidationError):
        RenderSpec(act="offer_times", facts={"times": "17:00 / 18:00", "price": "3100"})


def test_a_spec_with_a_blank_fact_is_rejected() -> None:
    with pytest.raises(ValidationError):
        RenderSpec(act="offer_times", facts={"times": ""})


# ── The validator: what passes, and every way it rejects ─────────────────────────────────────


def test_valid_placeholder_generation_passes_and_substitutes() -> None:
    spec = RenderSpec(
        act="offer_times",
        facts={"branch": "المعادي", "date": "بكرة", "times": "17:00 / 18:00"},
    )
    out = substitute_or_reject(
        "تمام، عندي مواعيد في {branch} {date}: {times}. تحبي أنهي واحدة؟", spec
    )
    assert out == "تمام، عندي مواعيد في المعادي بكرة: 17:00 / 18:00. تحبي أنهي واحدة؟"


def test_a_missing_required_placeholder_is_rejected() -> None:
    """An ``offer_times`` that never surfaces ``{times}`` has invented the availability."""
    spec = RenderSpec(act="offer_times", facts={"branch": "المعادي", "times": "17:00 / 18:00"})
    assert substitute_or_reject("تمام، عندنا مواعيد في {branch}. تحبي إمتى؟", spec) is None


def test_an_unknown_placeholder_is_rejected() -> None:
    spec = RenderSpec(act="offer_times", facts={"times": "17:00 / 18:00"})
    assert substitute_or_reject("عندنا {times} في {branch}؟", spec) is None


def test_a_malformed_placeholder_brace_is_rejected() -> None:
    spec = RenderSpec(act="offer_times", facts={"times": "17:00 / 18:00"})
    assert substitute_or_reject("عندنا {times} و {غير مكتمل", spec) is None
    assert substitute_or_reject("عندنا times}", spec) is None


def test_an_invented_number_is_rejected() -> None:
    """A digit the model typed itself — a fabricated time — is rejected even beside a valid slot."""
    spec = RenderSpec(act="offer_times", facts={"times": "17:00 / 18:00"})
    assert substitute_or_reject("عندنا {times}، وكمان الساعة 20:00؟", spec) is None
    # Arabic-Indic digits are caught too.
    assert substitute_or_reject("عندنا {times} والساعة ٢٠؟", spec) is None


def test_english_prose_is_rejected() -> None:
    """A reply that leaks English is not the Egyptian-Arabic voice the demo is built on."""
    spec = RenderSpec(act="offer_times", facts={"times": "17:00 / 18:00"})
    assert substitute_or_reject("We have these slots available: {times}", spec) is None


def test_a_clinical_claim_is_rejected() -> None:
    """Suitability / recommendation language is a clinician's call, never the renderer's."""
    spec = RenderSpec(
        act="read_back",
        facts={"service": "برايم ليز", "branch": "المعادي", "date": "بكرة", "time": "17:00"},
    )
    template = "تأكيد {service} في {branch} {date} {time}. العلاج ده مناسب ليكي جداً — صح كده؟"
    assert substitute_or_reject(template, spec) is None


def test_a_confirmation_claim_on_a_non_confirmation_act_is_rejected() -> None:
    """A read-back is still *asking*; it may not say the appointment is already booked."""
    spec = RenderSpec(
        act="read_back",
        facts={"service": "برايم ليز", "branch": "المعادي", "date": "بكرة", "time": "17:00"},
    )
    template = "تم الحجز {service} في {branch} {date} {time} ✅"
    assert substitute_or_reject(template, spec) is None


def test_the_confirmation_act_may_state_the_booking_is_done() -> None:
    """The one act where a confirmation claim is allowed — with the reference it makes true."""
    spec = RenderSpec(
        act="booking_confirmed",
        facts={"service": "برايم ليز", "branch": "المعادي", "booking_reference": "DC-0266"},
    )
    out = substitute_or_reject("تم الحجز ✅ {service} في {branch}. رقمك {booking_reference}", spec)
    assert out == "تم الحجز ✅ برايم ليز في المعادي. رقمك DC-0266"


def test_an_empty_generation_is_rejected() -> None:
    spec = RenderSpec(act="nothing_free", facts={"service": "برايم ليز"})
    assert substitute_or_reject("   ", spec) is None


# ── The renderers ────────────────────────────────────────────────────────────────────────────


def test_the_template_renderer_returns_the_fallback_and_never_calls_a_provider() -> None:
    """``RESPONSE_STYLE=template`` is the default and makes zero model calls.

    The ``TemplateRenderer`` holds no provider at all — the fallback the receptionist composed is
    returned verbatim, so there is nothing that *could* reach a model.
    """
    renderer = TemplateRenderer()
    out = _render(renderer, "offer_times", {"times": "17:00 / 18:00"}, fallback="النص المحدد مسبقاً")
    assert out == "النص المحدد مسبقاً"


def test_valid_generation_is_returned_by_the_generative_renderer() -> None:
    provider = _Provider("تمام، عندنا {times}. تحبي أنهي واحدة؟")
    renderer = GenerativeRenderer(provider)
    out = _render(renderer, "offer_times", {"times": "17:00 / 18:00"}, fallback="fallback")
    assert out == "تمام، عندنا 17:00 / 18:00. تحبي أنهي واحدة؟"
    assert provider.calls == 1


def test_a_rejected_generation_falls_back() -> None:
    """An English generation is rejected and the deterministic fallback stands."""
    provider = _Provider("We have these times: {times}")
    renderer = GenerativeRenderer(provider)
    out = _render(renderer, "offer_times", {"times": "17:00 / 18:00"}, fallback="المواعيد المتاحة")
    assert out == "المواعيد المتاحة"


def test_a_provider_error_falls_back_without_a_second_call() -> None:
    """A timeout or transport error returns the fallback — and there is no repair call."""
    provider = _RaisingProvider()
    renderer = GenerativeRenderer(provider)
    out = _render(renderer, "offer_times", {"times": "17:00 / 18:00"}, fallback="المواعيد المتاحة")
    assert out == "المواعيد المتاحة"
    assert provider.calls == 1  # exactly one attempt, no retry loop


def test_an_unexpected_provider_exception_falls_back() -> None:
    """Any exception, not only ``ProviderError``, must be caught — a renderer never takes the
    conversation down."""
    provider = _RaisingProvider(RuntimeError("boom"))
    renderer = GenerativeRenderer(provider)
    out = _render(renderer, "nothing_free", {"service": "برايم ليز"}, fallback="مفيش مواعيد")
    assert out == "مفيش مواعيد"


def test_a_spec_the_caller_cannot_prove_never_reaches_the_provider() -> None:
    """When the required facts are absent, the renderer falls back *without* a model call.

    A ``booking_confirmed`` with no reference is a spec that cannot be built, so the generative
    renderer must not spend a call implying a booking it cannot prove.
    """
    provider = _Provider("تم الحجز {booking_reference}")
    renderer = GenerativeRenderer(provider)
    out = _render(renderer, "booking_confirmed", {"service": "برايم ليز"}, fallback="تم الحجز")
    assert out == "تم الحجز"
    assert provider.calls == 0
