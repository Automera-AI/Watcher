"""Tests for the typed configuration layer (roadmap A1).

The tests that matter here are not "does pydantic parse a string". They are the three ways this
layer is supposed to stop a bad deploy: a placeholder must not read as a value, a missing
requirement must name itself, and a secret must not print. The last test in the file is the one
that keeps the layer honest over time — it reads ``.env.example`` and fails if a variable is
documented there and unreadable here, which is exactly the state A1 was written to end.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from pydantic import ValidationError

from apps.api.channels import ConfigError
from apps.api.core.config import Settings

_REPO_ROOT = Path(__file__).resolve().parents[3]

_MINIMAL_ENV = {
    "META_APP_SECRET": "shhh",
    "META_WEBHOOK_VERIFY_TOKEN": "echo-me",
}

#: Module-level so every test builds settings the same way: from a clean process environment.
SettingsFactory = Callable[..., Settings]


@pytest.fixture
def _settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[SettingsFactory]:
    """Build ``Settings`` from an explicit environment and nothing else.

    Both exclusions matter. The machine running the tests may well have ``ANTHROPIC_API_KEY`` or
    ``DATABASE_URL`` exported — a developer's shell, a CI secret — and a test asserting that an
    unset key raises must not pass or fail depending on whose laptop it is on. ``.env`` is
    excluded for the same reason.
    """
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)

    def build(**overrides: str) -> Settings:
        for name, value in overrides.items():
            monkeypatch.setenv(name, value)
        return Settings(_env_file=None)

    yield build


def test_reads_every_group_of_variables(_settings: SettingsFactory) -> None:
    settings = _settings(
        META_APP_ID="1234",
        META_APP_SECRET="shhh",
        META_WEBHOOK_VERIFY_TOKEN="echo-me",
        META_GRAPH_API_VERSION="v22.0",
        WHATSAPP_ACCESS_TOKEN="wa-token",
        WHATSAPP_BUSINESS_ACCOUNT_ID="waba-1",
        WHATSAPP_PHONE_NUMBER_ID="pn-1",
        WHATSAPP_BUSINESS_NUMBER_E164="+971500000000",
        CONTROL_CHAT_PHONE_E164="+971500000001",
        ANTHROPIC_API_KEY="sk-ant",
        OPENAI_API_KEY="sk-oai",
        CLASSIFIER_MODEL_FIRST_PASS="claude-haiku-4-5-20251001",
        CLASSIFIER_CONFIDENCE_ESCALATION_THRESHOLD="0.9",
        ASR_PROVIDER="faster-whisper",
        DATABASE_URL="postgresql+psycopg://user:pw@host:6543/db",
    )

    assert settings.meta_app_id == "1234"
    assert settings.meta_graph_api_version == "v22.0"
    assert settings.whatsapp_business_number_e164 == "+971500000000"
    assert settings.classifier_confidence_escalation_threshold == 0.9
    assert settings.asr_provider == "faster-whisper"
    assert settings.anthropic_api_key is not None
    assert settings.anthropic_api_key.get_secret_value() == "sk-ant"


def test_defaults_match_the_locked_decisions(_settings: SettingsFactory) -> None:
    """D8-a pins the tiering; an unset environment must not invent a different one."""
    settings = _settings()

    assert settings.classifier_model_first_pass == "claude-haiku-4-5"
    assert settings.classifier_model_escalation == "claude-sonnet-5"
    assert settings.classifier_model_fallback == "gpt-4o-mini"
    assert settings.classifier_confidence_escalation_threshold == 0.85
    assert settings.asr_provider == "whisper-api"


def test_default_tenant_vertical_preserves_holiday_home_behavior(
    _settings: SettingsFactory,
) -> None:
    settings = _settings()

    assert settings.tenant_vertical == "holiday_homes"
    assert settings.vocabulary().vertical == "holiday_homes"


def test_clinic_tenant_vertical_resolves_the_shipped_clinic_vocabulary(
    _settings: SettingsFactory,
) -> None:
    settings = _settings(TENANT_VERTICAL="clinics")

    assert settings.vocabulary().vertical == "clinics"
    assert "greeting" in {intent.name for intent in settings.vocabulary().intents}


def test_unknown_tenant_vertical_fails_at_startup(_settings: SettingsFactory) -> None:
    with pytest.raises(ValidationError, match="TENANT_VERTICAL.*unknown_vertical"):
        _settings(TENANT_VERTICAL="unknown_vertical")


def test_clinic_policy_requires_tenant_emergency_copy(_settings: SettingsFactory) -> None:
    settings = _settings(TENANT_VERTICAL="clinics")

    with pytest.raises(ConfigError, match="TENANT_EMERGENCY_REPLY"):
        settings.tenant_policy()


def test_clinic_emergency_copy_placeholder_requires_urgent_contact(
    _settings: SettingsFactory,
) -> None:
    settings = _settings(
        TENANT_VERTICAL="clinics",
        TENANT_EMERGENCY_REPLY="Call our doctor on {contact}. I am alerting them now.",
    )

    with pytest.raises(ConfigError, match="TENANT_URGENT_CONTACT"):
        settings.tenant_policy()


def test_clinic_emergency_copy_without_placeholder_needs_no_contact(
    _settings: SettingsFactory,
) -> None:
    settings = _settings(
        TENANT_VERTICAL="clinics",
        TENANT_EMERGENCY_REPLY="I am alerting our doctor right now.",
    )

    assert settings.tenant_policy().emergency_reply == "I am alerting our doctor right now."


def test_the_tenant_timezone_reaches_the_policy(_settings: SettingsFactory) -> None:
    """G3's one knob: the window on the night-time trigger is read in the guest's local time."""
    assert _settings().tenant_policy().timezone == "Asia/Dubai"
    assert _settings(TENANT_TIMEZONE="Africa/Cairo").tenant_policy().timezone == "Africa/Cairo"


def test_an_unresolvable_timezone_fails_at_startup(_settings: SettingsFactory) -> None:
    """A typo here would otherwise surface on the one trigger that needs a clock, at 2am."""
    with pytest.raises(ValidationError, match="TENANT_TIMEZONE"):
        _settings(TENANT_TIMEZONE="Asia/Dubay")


def test_redis_dsn_is_none_rather_than_an_error_when_unset(_settings: SettingsFactory) -> None:
    """B5: absence is a mode (stay on the in-process queue), not a misconfiguration."""
    assert _settings().redis_dsn() is None


def test_redis_dsn_unwraps_the_secret(_settings: SettingsFactory) -> None:
    settings = _settings(REDIS_URL="redis://user:pw@host:6379/0")
    assert settings.redis_dsn() == "redis://user:pw@host:6379/0"
    assert "pw" not in repr(settings)


def test_angle_bracket_placeholder_reads_as_unset(_settings: SettingsFactory) -> None:
    settings = _settings(ANTHROPIC_API_KEY="<ANTHROPIC_API_KEY>", META_APP_ID="<META_APP_ID>")

    assert settings.anthropic_api_key is None
    assert settings.meta_app_id is None


def test_placeholder_on_a_defaulted_field_falls_back_to_the_default(
    _settings: SettingsFactory,
) -> None:
    """Copying .env.example unedited must not break a field that already has a good answer."""
    settings = _settings(META_GRAPH_API_VERSION="<GRAPH_API_VERSION>")

    assert settings.meta_graph_api_version == "v21.0"


def test_empty_assignment_reads_as_unset(_settings: SettingsFactory) -> None:
    assert _settings(OPENAI_API_KEY="   ").openai_api_key is None


def test_missing_requirements_are_reported_together(_settings: SettingsFactory) -> None:
    with pytest.raises(ConfigError) as excinfo:
        _settings().meta()

    message = str(excinfo.value)
    assert "META_APP_SECRET" in message
    assert "META_WEBHOOK_VERIFY_TOKEN" in message


def test_meta_settings_round_trip(_settings: SettingsFactory) -> None:
    meta = _settings(**_MINIMAL_ENV).meta()

    assert meta.app_secret == "shhh"
    assert meta.webhook_verify_token == "echo-me"


def test_send_credentials_require_both_halves(_settings: SettingsFactory) -> None:
    with pytest.raises(ConfigError, match="WHATSAPP_PHONE_NUMBER_ID"):
        _settings(WHATSAPP_ACCESS_TOKEN="wa-token").send_credentials()

    credentials = _settings(
        WHATSAPP_ACCESS_TOKEN="wa-token", WHATSAPP_PHONE_NUMBER_ID="pn-1"
    ).send_credentials()
    assert (credentials.access_token, credentials.phone_number_id) == ("wa-token", "pn-1")
    assert credentials.graph_api_version == "v21.0"  # the pinned version replies go out on


def test_can_send_is_false_until_both_halves_are_configured(_settings: SettingsFactory) -> None:
    """What the composition root asks before deciding whether this process can reply at all."""
    assert _settings().can_send() is False
    assert _settings(WHATSAPP_ACCESS_TOKEN="wa-token").can_send() is False
    assert (
        _settings(WHATSAPP_ACCESS_TOKEN="wa-token", WHATSAPP_PHONE_NUMBER_ID="pn-1").can_send()
        is True
    )


def test_the_channel_credential_fields_are_declared_by_the_adapter(
    _settings: SettingsFactory,
) -> None:
    """A6's boundary move, asserted structurally rather than by grepping the core.

    ``Settings`` still reads one environment and exposes one object; what changed is which module
    declares that a send needs a token and a number. If someone moves the fields back onto the
    core object this passes-by-accident check fails, and so does ``test_boundary.py``.
    """
    from apps.api.channels.config import ChannelCredentials

    assert issubclass(Settings, ChannelCredentials)
    for field in ("whatsapp_access_token", "whatsapp_phone_number_id", "meta_app_secret"):
        assert field in ChannelCredentials.model_fields
        assert field not in Settings.__annotations__


def test_secrets_do_not_appear_in_repr(_settings: SettingsFactory) -> None:
    settings = _settings(ANTHROPIC_API_KEY="sk-ant-supersecret", META_APP_SECRET="shhh")

    printed = repr(settings)
    assert "sk-ant-supersecret" not in printed
    assert "shhh" not in printed


def test_threshold_outside_zero_to_one_is_rejected(_settings: SettingsFactory) -> None:
    with pytest.raises(ValidationError):
        _settings(CLASSIFIER_CONFIDENCE_ESCALATION_THRESHOLD="1.5")


def test_malformed_phone_number_is_rejected(_settings: SettingsFactory) -> None:
    with pytest.raises(ValidationError):
        _settings(CONTROL_CHAT_PHONE_E164="00971500000000")


def test_llm_credentials_route_by_model_id(_settings: SettingsFactory) -> None:
    settings = _settings(ANTHROPIC_API_KEY="sk-ant", OPENAI_API_KEY="sk-oai")

    claude = settings.llm_credentials("claude-haiku-4-5-20251001")
    assert claude.api_key == "sk-ant"
    assert claude.base_url == "https://api.anthropic.com"

    gpt = settings.llm_credentials("gpt-4o-mini")
    assert gpt.api_key == "sk-oai"
    assert gpt.base_url == "https://api.openai.com/v1"


def test_self_hosted_model_wins_over_the_name_based_route(_settings: SettingsFactory) -> None:
    """A self-hosted Qwen served over the OpenAI dialect needs no key — reachability is the key."""
    settings = _settings(
        SELF_HOSTED_LLM_MODEL="qwen2.5-32b-instruct",
        SELF_HOSTED_LLM_BASE_URL="http://vllm.internal:8000/v1/",
    )

    credentials = settings.llm_credentials("qwen2.5-32b-instruct")
    assert credentials.api_key is None
    assert credentials.base_url == "http://vllm.internal:8000/v1"  # trailing slash normalised


def test_self_hosted_model_without_a_base_url_is_a_config_error(_settings: SettingsFactory) -> None:
    settings = _settings(SELF_HOSTED_LLM_MODEL="qwen2.5-32b-instruct")

    with pytest.raises(ConfigError, match="SELF_HOSTED_LLM_BASE_URL"):
        settings.llm_credentials("qwen2.5-32b-instruct")


def test_llm_credentials_carry_the_transport_limits(_settings: SettingsFactory) -> None:
    settings = _settings(ANTHROPIC_API_KEY="sk-ant", LLM_TIMEOUT_SECONDS="5.5", LLM_MAX_RETRIES="0")

    credentials = settings.llm_credentials("claude-haiku-4-5-20251001")
    assert credentials.timeout_seconds == 5.5
    assert credentials.max_retries == 0


def test_every_documented_variable_is_readable(_settings: SettingsFactory) -> None:
    """``.env.example`` is the contract; a variable documented there must be a field here.

    Before A1, ``.env.example`` listed ~20 variables and the code read two of them. The gap was
    invisible because nothing compared the two lists. This is that comparison.
    """
    documented = {
        line.split("=", 1)[0].strip()
        for line in (_REPO_ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    }
    readable = {name.upper() for name in Settings.model_fields}

    assert documented, "no variables parsed out of .env.example — the parser or the file moved"
    assert documented <= readable, f"documented but unreadable: {sorted(documented - readable)}"


# ── Which vocabulary this deployment speaks (demo step 5) ──────────────────────────────────


def test_the_default_vertical_is_the_one_this_service_shipped_with() -> None:
    """An existing deploy is unchanged by ``TENANT_VERTICAL`` existing."""
    assert Settings().vocabulary().vertical == "holiday_homes"


def test_a_clinic_deploy_gets_the_clinic_vocabulary() -> None:
    """The whole point: the clinic taxonomy was shipped and reachable by nothing before this.

    Without it the classifier describes holiday-home intents to the model, ``decide_autonomy``
    looks up ceilings in the holiday-home file, and a patient asking to book an appointment is
    labelled against a vocabulary that has never heard of one.
    """
    vocab = Settings(tenant_vertical="clinics").vocabulary()
    assert vocab.vertical == "clinics"
    assert {i.name for i in vocab.intents} >= {"booking_enquiry", "clinical_question"}


def test_a_vertical_nobody_shipped_is_refused_at_startup() -> None:
    """Never a fallback to the default. Serving a clinic out of another vertical's safety floor
    is the cross-vertical leak the union taxonomy exists to make fail safe."""
    with pytest.raises(ValidationError, match="TENANT_VERTICAL"):
        Settings(tenant_vertical="dental")


def test_a_price_template_that_cannot_say_the_session_count_is_refused() -> None:
    """The one piece of tenant copy with a rule rather than a preference attached.

    ``quoting.always_state`` requires the currency, the session count and the package scope in
    every quote, and one Primelase session is 3,100 where six are 15,000. A template that drops
    the quantity is not imprecise — it is wrong by a factor of five, in the tenant's own voice,
    and nothing downstream can tell. It is a template somebody pastes into a dashboard, so it is
    checked at startup, where a person is still watching.
    """
    with pytest.raises(ConfigError, match="TENANT_PRICE_QUOTE"):
        Settings(tenant_price_quote="{service} is {price}.").conversation_copy()
    with pytest.raises(ConfigError, match=r"\{price\}"):
        Settings(tenant_price_quote="{service} — {sessions}").conversation_copy()


def test_a_price_template_may_state_the_quantity_in_either_form() -> None:
    """``{sessions}`` reads as English; ``{session_count}`` is the bare number a template in
    another language needs, because "6 جلسات" and "12 جلسة" inflect differently."""
    english = Settings(tenant_price_quote="{service}: {price} for {sessions}.").conversation_copy()
    arabic = Settings(
        tenant_price_quote="{service}: {price}. عدد الجلسات: {session_count}."
    ).conversation_copy()

    assert english.price_quote is not None and arabic.price_quote is not None


def test_an_unset_price_template_keeps_the_neutral_default() -> None:
    """A deploy that configures nothing still quotes correctly — the check is on what is set."""
    assert Settings().conversation_copy().price_quote is None
