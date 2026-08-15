# Spec — A1 Configuration layer · A3 Concrete LLM providers

**Status:** Implemented. Roadmap v2.1 Track A, items A1 (0.5d) and A3 (1.5d).
**Why these two first:** the roadmap's own conclusion — "until the app can start and call a model, no
other item can be demonstrated at all." A1 is what lets a process read its own deployment; A3 is the
first code in the repository that talks to a model. Together they are the bottom two layers of the
new critical path, and A4's composition root is the next thing that can be written on top of them.

---

## A1 — Configuration layer

### The problem

`.env.example` documented ~20 variables. Exactly two were read anywhere in the tree
(`META_APP_SECRET`, `META_WEBHOOK_VERIFY_TOKEN`, via `MetaSettings.from_env`), and nothing compared
the two lists, so the gap was invisible. Model IDs were pinned in a decision record and in a file no
code opened.

### Module boundary

`apps/api/core/config.py` — `Settings`, a `pydantic-settings` model, plus `get_settings()` for the
composition root. Sources, highest priority first: constructor arguments → process environment →
`.env` → field defaults. Unknown variables are ignored.

### Contract

| Behaviour | Rule |
|---|---|
| Import safety | Every field is optional or defaulted; `Settings()` always constructs. |
| Requirement | Enforced per subsystem at the point of use: `meta()`, `whatsapp_send_credentials()`, `llm_credentials(model_id)`. |
| Missing values | One `ConfigError` naming **every** missing variable, not the first. |
| Placeholders | `<LIKE_THIS>` and empty assignments are dropped before validation, so a defaulted field keeps its default and a required one reports itself missing. |
| Secrets | `SecretStr` for keys, tokens and `DATABASE_URL`; unwrapped only at the call site. |
| Defaults | D8-a's pinned model IDs and the 0.85 escalation threshold are the defaults, so a deploy that forgets them runs the decided configuration rather than refusing to start. |

`llm_credentials` routes by model ID rather than by a separate provider variable: two facts that can
disagree with each other would be discovered in production.

### New variables

`DATABASE_URL` (already read by `alembic/env.py`, previously undocumented — A2 consumes it),
`ANTHROPIC_BASE_URL` / `OPENAI_BASE_URL` (gateway support for the no-egress tier),
`LLM_TIMEOUT_SECONDS`, `LLM_MAX_RETRIES`.

### Boundary note

`core/config.py` names `whatsapp` — a deployment's environment names the channels it speaks, and
there is no channel-neutral spelling of `WHATSAPP_ACCESS_TOKEN`. It is registered in
`test_boundary.py`'s `CHANNEL_REGISTRY` (the permanent list) rather than `KNOWN_LEAKS` (the 1.1 debt
list): the rule forbids per-channel *behaviour*, and the settings object has no branches.

### Tests — `apps/api/tests/test_config.py` (16)

Placeholder handling in both directions, collected missing-variable errors, secret redaction, phone
and threshold validation, model-ID routing including the self-hosted override, and
`test_every_documented_variable_is_readable`, which parses `.env.example` and fails if a documented
variable has no field. That last one is the test that keeps A1 true after A1.

---

## A3 — Concrete LLM providers

### Module boundary

| File | Contents |
|---|---|
| `classifier/transport.py` | Shared HTTP: retry policy, backoff, error mapping, `TokenUsage`. |
| `classifier/anthropic.py` | `AnthropicProvider` — Messages API, tool-call mode, cached system block. |
| `classifier/openai.py` | `OpenAIProvider` — chat completions; also the self-hosted vLLM path. |
| `classifier/factory.py` | `Settings` → wired `Classifier`. The join between A1 and A3. |

Both providers satisfy the existing `LLMProvider` protocol structurally; `service.py` is unchanged.

### Decisions

**HTTP, not vendor SDKs.** One POST with one JSON body per provider. Two SDKs would add two
dependency trees, two retry policies and two timeout defaults behind a seam that already defines its
own. `httpx` was already a dependency.

**Tool-call mode with `CLASSIFICATION_TOOL_SCHEMA`.** The schema is generated from
`ClassificationResult`, so what the model is constrained by and what the service validates cannot
drift. `tool_choice` forces the tool. The tool's name and description live in `prompt.py` with the
rest of the prompt surface — worded differently per provider, they would make the cross-provider
eval a comparison of two prompts rather than two models. The generated schema uses `$defs`/`$ref`
for the enums; both providers accept that, but it is the first thing to check if the first live call
returns a 400 about the tool definition (item 2.7 is the first run with a key).

**Prompt caching is the cost story.** Anthropic: the system block carries
`cache_control: {"type": "ephemeral"}`. OpenAI: caching is prefix-based and automatic, earned by
putting the static block first and never varying it. `prompt.py` already renders everything variable
into the user turn; the tests assert that invariant rather than trusting it. `last_usage` reports
`cached_input_tokens` separately from `input_tokens` — normalised across providers, since OpenAI
counts cached tokens inside `prompt_tokens` and Anthropic reports them alongside — so a cache hit
ratio falling to zero is visible and not merely expensive.

**The pinned tiers are Claude 5, and the family changes the request.** D8-a's escalation tier
moved from Sonnet 4.6 to `claude-sonnet-5`; the cheap tier stays on Haiku 4.5, which has no Claude
5 equivalent and carries every inbound message. Two consequences, both handled in `factory.py` so
that re-pinning a tier stays a config edit:

* **No `temperature`.** The Claude 5 family rejects a non-default sampling parameter with a 400,
  and it never guaranteed identical output twice on the models that did accept it.
* **Thinking is off explicitly.** Claude 5 models think when `thinking` is omitted, and
  `max_tokens` bounds thinking *and* the tool call together — so a classification could spend its
  budget reasoning and be truncated before emitting the tool call, which reads downstream as
  invalid output rather than as truncation. `_thinking_policy` sends `{"type": "disabled"}` to the
  models that accept it, omits it on older models that predate the value, and raises the output
  ceiling for Fable/Mythos, which reject `disabled` and think regardless.

`effort` is deliberately left at its default. It is the other spend lever on Claude 5, but accuracy
is the product metric and 2.7 is what measures accuracy — tuning it first is how 0.88 stopped
meaning anything.

**Unusable output is not an error.** A response with no tool call, or arguments that will not parse,
returns `{}`. That fails validation, and validation failure is already a defined path: retry once on
the same tier, then unclear → inbox (§8). `ProviderError` is reserved for "could not reach the
provider", which deserves a different answer from the caller.

**Retry policy.** 408/409/425/429/5xx and transport failures retry with exponential backoff (0.5s,
1s, 2s …), honouring `Retry-After` capped at 8s — a guest is waiting, so an hour-long back-off is a
reason to fail the message. 4xx other than those raises immediately: a revoked key does not improve
on the third attempt.

### Error cases

| Case | Result |
|---|---|
| 401 / 400 | `ProviderError`, no retry |
| 429 / 5xx / timeout / reset | Retried; `ProviderError` when exhausted |
| Non-JSON body | `ProviderError` |
| No tool call, or unparseable arguments | `{}` → §8 retry-then-unclear |

### Tests — `apps/api/tests/test_llm_providers.py` (21)

All against `httpx.MockTransport`: no key, no network. Request shape for both providers, the cache
assertions above, the full retry matrix with an injected clock, usage normalisation, and four
factory tests ending with a model that answers in prose reaching `is_unclear` through the real
provider code rather than a double.

---

## Open, for 2.7

**The system prompt's token count is an estimate, and one number depends on it.** It is ~5k tokens
by a characters÷4 approximation — never measured, because measuring needs a key. That matters in
one place: Haiku 4.5 will not cache a prefix below **4,096 tokens**, and the cheap tier is where
caching pays for itself. The estimate clears the floor by roughly 30% and the true count is
probably higher (Arabic and Franco-Arabic tokenize worse than the approximation), so caching
almost certainly activates — but if the vocabulary ever shrinks, it stops silently: no error, just
a larger bill. 2.7 has a key in hand; count the assembled prompt then, record the real number, and
pin it with a test if the margin is thinner than it looks. Sonnet 5's floor is 1,024, so the
escalation tier is not at risk either way.

## What this does not do

Not in A1/A3, and deliberately: no DB engine (A2), no `main.py` (A4), no outbound sender (A6), no
live eval re-record (2.7 — unblocked by A3, but it needs a key and a decision to spend it). The
classifier still has no production caller; `build_classifier(get_settings())` is the one line A4
will need.
