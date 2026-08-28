"""Typed application configuration — one object over the whole of ``.env`` (roadmap A1).

Before this, exactly two variables were read anywhere in the tree: ``META_APP_SECRET`` and
``META_WEBHOOK_VERIFY_TOKEN``, via a ``from_env`` classmethod on an adapter. Everything else in
``.env.example`` — model IDs, API keys, the number identifiers, the ASR choice — was documented
config that no code could see. This module is the seam that makes those values reachable, and it is
a prerequisite for the composition root (A4): a process cannot be wired from configuration it
cannot read.

Three rules the design follows:

* **Nothing is required to *import*.** Every field is optional or has a default, so the eval
  runner, the tests, and a shell session can construct ``Settings()`` without an account of any
  kind. What is required is required *per subsystem*, checked at the point of use —
  :meth:`~apps.api.channels.config.ChannelCredentials.meta` raises for a missing app secret,
  :meth:`Settings.llm_credentials` for a missing key — and the error names every missing variable
  at once rather than one per restart.
* **Placeholders are absence.** ``.env.example`` ships ``<META_APP_ID>``-style placeholders and a
  half-filled ``.env`` is the normal state of a machine mid-setup. A value still wrapped in angle
  brackets is treated as unset, so it fails as "missing META_APP_ID" rather than reaching a
  provider as a literal string and failing as a 401 an hour later. See ``core/settings_base.py``.
* **Secrets are :class:`~pydantic.SecretStr`.** ``Settings`` ends up in tracebacks, log lines and
  ``/debug``-shaped endpoints; ``repr`` of a key must not be the key. Call
  ``.get_secret_value()`` deliberately, at the call site that needs it.

**Where the per-channel variables went (A6).** ``Settings`` extends
:class:`~apps.api.channels.config.ChannelCredentials`, which declares the credential fields for the
channels and the accessors that validate them. One object still reads one environment; what changed
is that this file no longer has to know what a channel needs in order to hold it, which is what
finally emptied ``KNOWN_LEAKS`` in the boundary test.

``.env`` is read when present (dev convenience) and the process environment always wins over it.
Unknown variables are ignored rather than rejected: a container image gets ``PATH``, ``HOME`` and
whatever the platform injects, and none of that is our business.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from packages.intents.schema import Vocabulary, shipped_vocabularies
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import SettingsConfigDict

from apps.api.channels.config import ChannelCredentials
from apps.api.conversations.tools import ConversationCopy
from apps.api.core.emergency import DEFAULT_TIMEZONE, timezone_is_known
from apps.api.core.policy import TenantPolicy
from apps.api.schemas.common import HIGH_CONFIDENCE_THRESHOLD, PhoneE164

#: Wire protocol default for Anthropic. Overridable so the regulated tier can point at a gateway
#: inside its own perimeter without a code change (AGENTS.md: no unreviewed egress).
DEFAULT_ANTHROPIC_BASE_URL = "https://api.anthropic.com"

#: Wire protocol default for OpenAI. The same field points the OpenAI-shaped provider at a
#: self-hosted vLLM server, which is why ``SELF_HOSTED_LLM_BASE_URL`` needs no second client.
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"


@dataclass(frozen=True, slots=True)
class LLMCredentials:
    """What a provider needs to reach one model: a key (sometimes), an endpoint, and limits."""

    api_key: str | None
    """``None`` for a self-hosted server that authenticates by network position, not by token."""

    base_url: str
    timeout_seconds: float
    max_retries: int


class Settings(ChannelCredentials):
    """Every variable in ``.env.example``, typed, with the placeholders treated as unset.

    The channel credential fields and their accessors are inherited (see the module docstring);
    everything declared below belongs to the core and would be true of any channel.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
        # `secret` fields print as `SecretStr('**********')`; this keeps the rest short too.
        str_strip_whitespace=True,
    )

    # ── The operator's own number for the control chat (addendum §10) ──────────────────────
    #
    # G3 gave this variable a second job and a much sharper edge: it is also the number an
    # emergency alert goes to. Unset, the process still detects a gas leak, still answers the
    # guest and still files the item — but the only alert is a log line. `build_alerter` says so
    # loudly at startup.
    control_chat_phone_e164: PhoneE164 | None = None

    # ── Demo deployment vertical ───────────────────────────────────────────────────────────
    #
    # One process serves one fixed vertical for its lifetime. This is deliberately not tenant
    # database state: the DermaClub demo runs its API and worker with `clinics`, while an existing
    # deployment that does not set the variable keeps the holiday-home behavior.
    tenant_vertical: str = "holiday_homes"

    # ── Where the properties are (roadmap G3) ─────────────────────────────────────────────
    #
    # An IANA zone name, because one emergency trigger fires only between 22:00 and 07:00 and
    # "night" is the guest's clock, not the server's. Defaulted rather than required: a deploy
    # that does not say lands in the first market's zone, which is a defensible hour or two of
    # error, whereas refusing to start over a timezone would be a safety feature that stops the
    # service running.
    tenant_timezone: str = DEFAULT_TIMEZONE

    # ── The number customers may ring directly in an emergency (roadmap G3) ───────────────
    #
    # Distinct from `control_chat_phone_e164`, which is where the *alert* goes: this is what the
    # customer is told. For a clinic they are rarely the same — the alert wakes whoever is on
    # call, while the number a patient rings for a filler occlusion is a named clinician. Unset,
    # the emergency reply names no clinic number and points only at public emergency services.
    tenant_urgent_contact: str | None = None

    # Replaces the default emergency wording entirely. May contain {contact}. Set this for any
    # tenant that must NOT direct customers to public emergency services — a clinic routes to its
    # own clinician, because deciding that something is an ambulance case is triage and triage is
    # not the receptionist's to do. Unset, the default wording is used.
    tenant_emergency_reply: str | None = None

    # ── The receptionist's own words (roadmap 2.x) ────────────────────────────────────────
    #
    # The two ends of a conversation, in the client's voice. Configuration rather than code
    # because each names the client, its receptionist and its brand tone — the hardcoding
    # `tests/test_no_client_name.py` exists to prevent. Unset, the tools answer in neutral
    # English that names nobody and is still correct.
    #
    # `tenant_closing_booking_confirmed` is the one that can lie: it states an appointment
    # exists. It is rendered only when the scheduling system returned a durable reference to
    # put in it, and `CloseConversation.run` falls back to the generic closing otherwise.
    tenant_greeting_opening: str | None = None
    tenant_greeting_opening_named: str | None = None
    tenant_closing: str | None = None
    tenant_closing_booking_confirmed: str | None = None

    # ── Classifier tiering (addendum §8 / D8-a) ────────────────────────────────────────────
    anthropic_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    anthropic_base_url: str = DEFAULT_ANTHROPIC_BASE_URL
    openai_base_url: str = DEFAULT_OPENAI_BASE_URL

    # Defaults mirror DECISIONS.md D8-a. They are defaults rather than requirements because a
    # deploy that forgets to pin a model should run the pinned-by-decision one, not refuse to
    # start; a deploy that means to change tier says so in its environment.
    #
    # The cheap tier stays on Haiku 4.5 because there is no Haiku in the Claude 5 family and it
    # is still the current cheap tier — it carries first-pass traffic, which is every inbound
    # message, so moving it up a tier is a cost decision rather than a version bump.
    classifier_model_first_pass: str = "claude-haiku-4-5"
    classifier_model_escalation: str = "claude-sonnet-5"
    classifier_model_fallback: str = "gpt-4o-mini"
    classifier_confidence_escalation_threshold: float = Field(
        default=HIGH_CONFIDENCE_THRESHOLD, ge=0.0, le=1.0
    )

    # ── Self-hosted / regulated tier (Phase 5, addendum §8) ────────────────────────────────
    self_hosted_llm_base_url: str | None = None
    self_hosted_llm_model: str | None = None

    # ── Transport limits shared by every provider ──────────────────────────────────────────
    # A classifier call sits in the path of a guest waiting for a reply, so the timeout is a
    # product decision, not a library default. Retries here are transport-level (429, 5xx, a
    # dropped connection); schema-invalid *output* is retried by the classifier service (§8).
    llm_timeout_seconds: float = Field(default=30.0, gt=0.0)
    llm_max_retries: int = Field(default=2, ge=0, le=10)

    # ── Media pipeline (addendum §6 / D3-a) ────────────────────────────────────────────────
    asr_provider: Literal["whisper-api", "faster-whisper"] = "whisper-api"
    asr_model: str = "whisper-1"

    # ── Persistence (A2; alembic/env.py reads the raw variable for migrations) ─────────────
    database_url: SecretStr | None = None

    # Supabase's application URI (port 6543) is pgbouncer in transaction mode, where server-side
    # prepared statements do not survive between transactions. `transaction` is the connection
    # policy that is safe there and merely slower anywhere else, so it is the default; set
    # `session` when connecting directly to Postgres. See apps/api/db/engine.py for what each
    # mode actually changes — this is the one fact about the connection path that cannot be
    # derived from the URL, since a pooler can be put in front of any host on any port.
    database_pool_mode: Literal["transaction", "session"] = "transaction"

    # ── Durable queue (roadmap B5) ─────────────────────────────────────────────────────────
    #
    # Unset by default, and that is a real mode rather than a half-configured one: the
    # composition root (main.py) reads its absence as "stay on the in-process
    # ThreadPoolClassificationQueue", the same fast-200 path this service has run on since A4.
    # Setting it switches the API to a thin producer and moves classification to a separate
    # arq worker process (`apps/api/worker.py`) — the durability B5 exists for is that swap.
    redis_url: SecretStr | None = None

    @field_validator("tenant_timezone")
    @classmethod
    def _resolvable_zone(cls, value: str) -> str:
        """Refuse a zone name this machine cannot resolve.

        The exception to "nothing is required to import": this field has a working default, so a
        value being *present and wrong* is never a half-configured machine — it is a typo, and the
        only place it would otherwise surface is the one trigger that needs a clock, at 2am. A
        misspelled ``TENANT_TIMEZONE`` fails at startup where somebody is watching.
        """
        if not timezone_is_known(value):
            raise ValueError(
                f"TENANT_TIMEZONE={value!r} is not an IANA zone name this machine can resolve "
                f"(e.g. {DEFAULT_TIMEZONE}, Africa/Cairo)"
            )
        return value

    @field_validator("tenant_vertical")
    @classmethod
    def _shipped_vertical(cls, value: str) -> str:
        known = shipped_vocabularies()
        if value not in known:
            raise ValueError(
                f"TENANT_VERTICAL={value!r} is not a shipped vocabulary. Known: {sorted(known)}"
            )
        return value

    # ── Per-subsystem accessors: this is where "required" is decided ───────────────────────

    def tenant_policy(self) -> TenantPolicy:
        """The default routing policy, with the one knob the environment can move applied.

        ``core/policy.py`` exists to keep the classifier's escalation cutoff and the routing
        bands converged: a message is escalated because it is not confident enough to act on, and
        the band that decides whether to auto-route says the same thing about the same number.
        Configuring one and not the other reintroduces exactly the split that module was written
        to remove, so the configured threshold is applied to both here — the composition root
        being where a constant becomes a deployment's value.

        Per-*tenant* overrides are a different thing and land with the control page; this is the
        process default for every tenant it serves.
        """
        return TenantPolicy(
            high_confidence_threshold=self.classifier_confidence_escalation_threshold,
            timezone=self.tenant_timezone,
            urgent_contact=self.tenant_urgent_contact,
            emergency_reply=self.tenant_emergency_reply,
        )

    def vocabulary(self) -> Vocabulary:
        """The one shipped vocabulary selected for this process lifetime."""
        return shipped_vocabularies()[self.tenant_vertical]

    def conversation_copy(self) -> ConversationCopy:
        """The tenant's opening and closing wording, as the tools take it."""
        return ConversationCopy(
            opening=self.tenant_greeting_opening,
            opening_named=self.tenant_greeting_opening_named,
            closing=self.tenant_closing,
            closing_booking_confirmed=self.tenant_closing_booking_confirmed,
        )

    def database_dsn(self) -> str:
        """The connection string, or :class:`ConfigError` — A2's point of use for ``DATABASE_URL``.

        Returned as a plain string because that is what SQLAlchemy takes. It carries the password,
        so it is unwrapped here and nowhere else; never log the return value.
        """
        (dsn,) = self._require(DATABASE_URL=self.database_url)
        return dsn

    def pool_mode(self) -> Literal["transaction", "session"]:
        """The connection-path policy. Always answerable — it has a safe default (see the field)."""
        return self.database_pool_mode

    def redis_dsn(self) -> str | None:
        """The queue's broker, unwrapped — or ``None``, which is a mode, not an error (B5).

        Unlike :meth:`database_dsn`, absence does not raise: nothing downstream of this call is
        required to have a durable queue, and the composition root reads ``None`` as "use the
        in-process pool instead" rather than as a misconfiguration.
        """
        return self.redis_url.get_secret_value() if self.redis_url is not None else None

    def llm_credentials(self, model_id: str) -> LLMCredentials:
        """Credentials for whichever provider owns ``model_id`` (see ``classifier/factory``).

        Routing by model ID rather than by an explicit provider variable keeps one pinned name
        in the environment per tier: changing ``CLASSIFIER_MODEL_ESCALATION`` from a Claude model
        to a GPT one is a config edit, not a deploy that also has to remember to change which
        provider it names.
        """
        if self.self_hosted_llm_model is not None and model_id == self.self_hosted_llm_model:
            (base_url,) = self._require(SELF_HOSTED_LLM_BASE_URL=self.self_hosted_llm_base_url)
            # A vLLM server behind a private network is authenticated by reachability. Send the
            # OpenAI key only if one happens to be configured; never require it here.
            key = self.openai_api_key
            return self._llm_credentials(key.get_secret_value() if key else None, base_url)

        if model_id.startswith("claude"):
            (api_key,) = self._require(ANTHROPIC_API_KEY=self.anthropic_api_key)
            return self._llm_credentials(api_key, self.anthropic_base_url)

        (api_key,) = self._require(OPENAI_API_KEY=self.openai_api_key)
        return self._llm_credentials(api_key, self.openai_base_url)

    def _llm_credentials(self, api_key: str | None, base_url: str) -> LLMCredentials:
        return LLMCredentials(
            api_key=api_key,
            base_url=base_url.rstrip("/"),
            timeout_seconds=self.llm_timeout_seconds,
            max_retries=self.llm_max_retries,
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """The process-wide settings, read once.

    Cached because ``Settings()`` re-reads ``.env`` from disk on every construction, and because
    a process should not be able to change its own configuration halfway through a request.
    Tests that need a different environment construct ``Settings(...)`` directly instead of
    reaching through this; nothing in the tree should call it except the composition root.
    """
    return Settings()
