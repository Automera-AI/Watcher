"""Settle the two numbers roadmap 2.7 leaves open: the system prompt's real token count, and the
cost of classifying one message.

Both are named on the roadmap's "Two numbers nobody has measured" page and both have been carried
as estimates ("~5k, chars/4") since session 5 because settling them needs the real tokenizer, and
the real tokenizer is a network call. This is the operator-run tool that makes the call — the same
category as ``docs/make_roadmap.py`` and ``scripts/import_property_facts.py``: not imported by the
app or CI, and needing a dependency (``anthropic``) that nothing shipped imports.

    pip install anthropic
    ANTHROPIC_API_KEY=sk-ant-... python scripts/measure_prompt.py

**Why ``count_tokens`` and not a local estimate.** Anthropic's tokenizer is not public, and a
``len(text) // 4`` guess is exactly what the roadmap says to stop trusting — Arabic and
Franco-Arabic (a large share of this product's traffic) tokenize worse than Latin text, so the
estimate is biased low in precisely the direction that matters for the cache-floor question below.
``client.messages.count_tokens`` returns the number the API itself will bill. Without a key this
script still prints the deterministic facts (character count, fingerprint) and says what it could
not measure, rather than printing a guess dressed up as a measurement.

**The cache-floor question this answers.** Haiku 4.5 will not cache a prompt prefix below 4,096
tokens (``docs`` / the prompt module say ~5k, an estimate that clears the floor by ~30%). The
classifier marks its system block cacheable; if the true count is below 4,096 the cache silently
never activates and every inbound message pays full input price for the whole prompt. This script
reports the real count so that "it caches" stops being an assumption.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Run as a file from the repo root (`python scripts/measure_prompt.py`): put the repo root on the
# path so the locked schemas in ``apps/api`` import, the same way ``python -m packages.eval`` gets
# them for free from the working directory.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apps.api.classifier.prompt import (  # noqa: E402
    CLASSIFICATION_TOOL_DESCRIPTION,
    CLASSIFICATION_TOOL_NAME,
    CLASSIFICATION_TOOL_SCHEMA,
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    SYSTEM_PROMPT_FINGERPRINT,
    render_user_prompt,
)
from apps.api.classifier.types import ClassificationInput  # noqa: E402
from apps.api.schemas.enums import MessageType  # noqa: E402

#: The model the fixtures were recorded under (``packages/eval/baseline.json``) and the one the
#: classifier defaults to for the cheap first pass. Pricing is Anthropic first-party, US$/1M tokens,
#: from the model table in the ``claude-api`` reference (cached 2026-06-24). Cache read is ~0.1x
#: input; a cache write is ~1.25x. Update these two rows, not the arithmetic, when pricing moves.
MODEL = "claude-haiku-4-5"
PRICE_INPUT_PER_MTOK = 1.00
PRICE_OUTPUT_PER_MTOK = 5.00
PRICE_CACHE_READ_PER_MTOK = 0.10  # ~0.1x input
CACHE_FLOOR_TOKENS = 4096  # Haiku 4.5 will not cache a prefix shorter than this

#: A representative inbound message: the volatile, per-message part that is *not* in the cached
#: prefix. Short and English on purpose — it is the cheapest case, so the per-message cost it
#: produces is a floor, not an average (an Arabic voice-note transcript with history costs more).
_SAMPLE_MESSAGE = "hi, is the 2-bed in marina free from the 4th to the 9th?"

#: A typical structured classification is ~150 output tokens (19-intent label + four confidences +
#: a one-line summary). Used only for the output half of the per-message cost estimate.
_ASSUMED_OUTPUT_TOKENS = 150


def _sample_user_turn() -> str:
    return render_user_prompt(
        ClassificationInput(
            text=_SAMPLE_MESSAGE, modality=MessageType.TEXT, sender_phone="+971500000000"
        )
    )


def _deterministic_report() -> None:
    chars = len(SYSTEM_PROMPT)
    print(f"prompt version:      {PROMPT_VERSION}")
    print(f"fingerprint:         {SYSTEM_PROMPT_FINGERPRINT}")
    print(f"system prompt chars: {chars:,}")
    print(f"chars // 4 estimate: {chars // 4:,} tokens (the guess 2.7 is meant to replace)")


def _cost_line(label: str, dollars: float) -> str:
    return f"{label:<34} ${dollars:.6f}  (${dollars * 1000:.3f} / 1,000 messages)"


def main() -> int:
    _deterministic_report()

    try:
        import anthropic
    except ImportError:
        print("\nanthropic is not installed — `pip install anthropic` to measure the token count.")
        return 0

    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        print(
            "\nNo ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN in the environment, so the exact token "
            "count and cost were not measured. Set one and re-run. The keys live on the deployed "
            "Render services; this script runs wherever you have a key locally."
        )
        return 0

    client = anthropic.Anthropic()
    tools = [
        {
            "name": CLASSIFICATION_TOOL_NAME,
            "description": CLASSIFICATION_TOOL_DESCRIPTION,
            "input_schema": CLASSIFICATION_TOOL_SCHEMA,
        }
    ]

    # The cached prefix = tools + system, with no message. count_tokens bills the whole request, so
    # the marginal per-message input is (prefix + sample) − prefix.
    prefix = client.messages.count_tokens(
        model=MODEL, system=SYSTEM_PROMPT, tools=tools, messages=[{"role": "user", "content": "."}]
    ).input_tokens
    with_sample = client.messages.count_tokens(
        model=MODEL,
        system=SYSTEM_PROMPT,
        tools=tools,
        messages=[{"role": "user", "content": _sample_user_turn()}],
    ).input_tokens
    per_message_input = max(with_sample - prefix, 0)

    caches = prefix >= CACHE_FLOOR_TOKENS
    print(f"\nmeasured prefix:     {prefix:,} tokens (tools + system, model {MODEL})")
    print(
        f"cache floor:         {CACHE_FLOOR_TOKENS:,} tokens — "
        + ("clears it, the prefix caches" if caches else "BELOW IT: the prefix never caches")
    )
    print(f"per-message input:   ~{per_message_input:,} tokens (the volatile user turn)")

    # Two costs worth telling apart: the first message a warm prefix has not paid for yet (full
    # input price on the whole prefix) and the steady state (prefix served from cache).
    prefix_full = prefix / 1_000_000 * PRICE_INPUT_PER_MTOK
    prefix_cached = (prefix / 1_000_000 * PRICE_CACHE_READ_PER_MTOK) if caches else prefix_full
    per_msg_in = per_message_input / 1_000_000 * PRICE_INPUT_PER_MTOK
    out = _ASSUMED_OUTPUT_TOKENS / 1_000_000 * PRICE_OUTPUT_PER_MTOK

    print("\ncost per message (Haiku 4.5 first pass):")
    print(_cost_line("  cold (prefix uncached):", prefix_full + per_msg_in + out))
    print(_cost_line("  warm (prefix cached):", prefix_cached + per_msg_in + out))
    print(
        f"\n  assumes ~{_ASSUMED_OUTPUT_TOKENS} output tokens; an emergency is free (never reaches "
        "a model, G3); a low-confidence message costs a second, larger-model pass on top."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
