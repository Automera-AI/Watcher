"""The fact-locked generative renderer, unit level (pre-demo Step 5, plan §9–§10).

These tests pin the renderer's guarantee in isolation: the model may recombine a small, curated,
fact-locked vocabulary around proven placeholders, and *anything else* — a fabricated service or
branch, a made-up day, an efficacy claim, a premature "it's booked", an English leak, a stray
digit, a provider error — returns the deterministic fallback. The lock is an allowlist (safe by
construction), so most of this file is the many ways a generation is rejected, including the exact
adversarial paraphrases a denylist used to let through.
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

# Proven fact sets for the acts under test — the deterministic values code substitutes.
_OFFER = {"service": "برايم ليز", "branch": "المعادي", "date": "بكرة", "times": "17:00 / 18:00"}
_READ_BACK = {"service": "برايم ليز", "branch": "المعادي", "date": "بكرة", "time": "17:00"}
_CONFIRMED = {**_READ_BACK, "booking_reference": "DC-0266"}

# A cooperative, in-vocabulary phrasing for each act (built from the renderer's own exemplars), so
# the accept path is exercised, not only the reject paths.
_GOOD = {
    "ask_missing_slot": "تمام يا فندم، ممكن تقوليلي {slot}؟",
    "offer_times": (
        "تمام يا قمر، متاح {service} في {branch} يوم {date} المواعيد دي {times}. تحبي أنهي واحدة؟"
    ),
    "nothing_free": (
        "معلش يا فندم، مفيش مواعيد فاضية {service} في {branch} يوم {date}. تحبي يوم تاني؟"
    ),
    "read_back": "تمام يا قمر، أأكدلك {service} في {branch} يوم {date} الساعة {time}؟ صح كده؟",
    "booking_confirmed": (
        "تم الحجز يا قمر، {service} في {branch} يوم {date} الساعة {time}. "
        "رقم حجزك {booking_reference}."
    ),
}


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


def test_a_spec_missing_a_required_fact_is_rejected() -> None:
    """Each act needs its full protected-fact set; a partial one cannot be built."""
    with pytest.raises(ValidationError):
        RenderSpec(act="booking_confirmed", facts={"service": "برايم ليز"})
    with pytest.raises(ValidationError):
        # offer_times now requires service/branch/date as well as times.
        RenderSpec(act="offer_times", facts={"times": "17:00 / 18:00"})


def test_a_spec_with_an_unknown_fact_is_rejected() -> None:
    with pytest.raises(ValidationError):
        RenderSpec(act="offer_times", facts={**_OFFER, "price": "3100"})


def test_a_spec_with_a_blank_fact_is_rejected() -> None:
    with pytest.raises(ValidationError):
        RenderSpec(act="offer_times", facts={**_OFFER, "times": ""})


# ── The validator: the accept path, and every way it rejects ─────────────────────────────────


def test_valid_in_vocabulary_generation_passes_and_substitutes() -> None:
    out = substitute_or_reject(_GOOD["offer_times"], RenderSpec(act="offer_times", facts=_OFFER))
    assert out is not None
    assert "برايم ليز" in out and "المعادي" in out and "17:00 / 18:00" in out


def test_a_missing_required_placeholder_is_rejected() -> None:
    """An ``offer_times`` that never surfaces ``{times}`` has invented the availability."""
    spec = RenderSpec(act="offer_times", facts=_OFFER)
    assert substitute_or_reject("تمام، متاح {service} في {branch} يوم {date}؟", spec) is None


def test_an_unknown_placeholder_is_rejected() -> None:
    spec = RenderSpec(act="read_back", facts=_READ_BACK)
    template = "أأكدلك {service} في {branch} يوم {date} الساعة {price}؟"
    assert substitute_or_reject(template, spec) is None


def test_a_malformed_placeholder_brace_is_rejected() -> None:
    spec = RenderSpec(act="offer_times", facts=_OFFER)
    assert substitute_or_reject("متاح {service} في {branch} يوم {date} {times و", spec) is None
    assert substitute_or_reject("متاح {service} في {branch} يوم {date} times}", spec) is None


def test_an_invented_number_is_rejected() -> None:
    """A digit the model typed itself — a fabricated time — is rejected even beside valid slots."""
    spec = RenderSpec(act="offer_times", facts=_OFFER)
    assert (
        substitute_or_reject(
            "متاح {service} في {branch} يوم {date} {times} وكمان الساعة 20:00؟", spec
        )
        is None
    )
    assert (
        substitute_or_reject("متاح {service} في {branch} يوم {date} {times} والساعة ٢٠؟", spec)
        is None
    )


def test_english_prose_is_rejected() -> None:
    spec = RenderSpec(act="offer_times", facts=_OFFER)
    assert substitute_or_reject("We have {service} at {branch} {date}: {times}", spec) is None


# ── The blockers a denylist used to miss: prose facts, efficacy, premature confirmation ───────


def test_a_fabricated_service_branch_or_day_in_prose_is_rejected() -> None:
    """The Codex blocker: valid ``{times}`` beside a made-up service/branch/day in plain Arabic.

    None of ``بوتوكس`` / ``الزمالك`` / ``الخميس`` is in the offer vocabulary, so the whole
    generation falls back rather than telling the patient about a treatment or branch nobody proved.
    """
    spec = RenderSpec(act="offer_times", facts=_OFFER)
    fabricated = "البوتوكس في الزمالك يوم الخميس، المتاح {service} في {branch} يوم {date} {times}"
    assert substitute_or_reject(fabricated, spec) is None


def test_an_efficacy_claim_is_rejected() -> None:
    """ "مضمونة ونتيجتها ممتازة" — a guarantee of results — is not in any vocabulary."""
    spec = RenderSpec(act="offer_times", facts=_OFFER)
    claim = "الجلسة دي مضمونة ونتيجتها ممتازة، متاح {service} في {branch} يوم {date} {times}"
    assert substitute_or_reject(claim, spec) is None


def test_a_premature_confirmation_paraphrase_on_read_back_is_rejected() -> None:
    """A read-back may ask ("أأكدلك") but never claim ("اتأكد"/"تم الحجز") the booking is done."""
    spec = RenderSpec(act="read_back", facts=_READ_BACK)
    assert (
        substitute_or_reject("ميعادك اتأكد {service} في {branch} يوم {date} الساعة {time}", spec)
        is None
    )
    booked = "تم الحجز {service} في {branch} يوم {date} الساعة {time}"
    assert substitute_or_reject(booked, spec) is None


def test_a_clinical_suitability_claim_is_rejected() -> None:
    spec = RenderSpec(act="read_back", facts=_READ_BACK)
    claim = "أأكدلك {service} في {branch} يوم {date} الساعة {time}؟ العلاج ده مناسب ليكي جداً"
    assert substitute_or_reject(claim, spec) is None


def test_the_confirmation_act_may_state_the_booking_is_done() -> None:
    """The one act whose vocabulary includes "تم الحجز" — with the reference that makes it true."""
    out = substitute_or_reject(
        _GOOD["booking_confirmed"], RenderSpec(act="booking_confirmed", facts=_CONFIRMED)
    )
    assert out is not None
    assert "تم الحجز" in out and "DC-0266" in out


def test_an_empty_generation_is_rejected() -> None:
    facts = {"service": "برايم ليز", "branch": "المعادي", "date": "بكرة"}
    assert substitute_or_reject("   ", RenderSpec(act="nothing_free", facts=facts)) is None


# ── The renderers ────────────────────────────────────────────────────────────────────────────


def test_the_template_renderer_returns_the_fallback_and_never_calls_a_provider() -> None:
    """``RESPONSE_STYLE=template`` is the default and makes zero model calls."""
    out = _render(TemplateRenderer(), "offer_times", _OFFER, fallback="النص المحدد مسبقاً")
    assert out == "النص المحدد مسبقاً"


def test_valid_generation_is_returned_by_the_generative_renderer() -> None:
    provider = _Provider(_GOOD["offer_times"])
    out = _render(GenerativeRenderer(provider), "offer_times", _OFFER, fallback="fallback")
    assert "برايم ليز" in out and "17:00 / 18:00" in out
    assert provider.calls == 1


def test_a_rejected_generation_falls_back() -> None:
    provider = _Provider("We have these times: {times}")
    out = _render(GenerativeRenderer(provider), "offer_times", _OFFER, fallback="المواعيد المتاحة")
    assert out == "المواعيد المتاحة"


def test_a_provider_error_falls_back_without_a_second_call() -> None:
    """A timeout or transport error returns the fallback — and there is no repair call."""
    provider = _RaisingProvider()
    out = _render(GenerativeRenderer(provider), "offer_times", _OFFER, fallback="المواعيد المتاحة")
    assert out == "المواعيد المتاحة"
    assert provider.calls == 1


def test_an_unexpected_provider_exception_falls_back() -> None:
    provider = _RaisingProvider(RuntimeError("boom"))
    facts = {"service": "برايم ليز", "branch": "المعادي", "date": "بكرة"}
    out = _render(GenerativeRenderer(provider), "nothing_free", facts, fallback="مفيش مواعيد")
    assert out == "مفيش مواعيد"


def test_a_spec_the_caller_cannot_prove_never_reaches_the_provider() -> None:
    """When the required facts are absent, the renderer falls back *without* a model call."""
    provider = _Provider(_GOOD["booking_confirmed"])
    out = _render(
        GenerativeRenderer(provider), "booking_confirmed", {"service": "برايم ليز"}, fallback="تم"
    )
    assert out == "تم"
    assert provider.calls == 0
