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


def test_whatsapp_send_credentials_require_both_halves(_settings: SettingsFactory) -> None:
    with pytest.raises(ConfigError, match="WHATSAPP_PHONE_NUMBER_ID"):
        _settings(WHATSAPP_ACCESS_TOKEN="wa-token").whatsapp_send_credentials()

    token, phone_number_id = _settings(
        WHATSAPP_ACCESS_TOKEN="wa-token", WHATSAPP_PHONE_NUMBER_ID="pn-1"
    ).whatsapp_send_credentials()
    assert (token, phone_number_id) == ("wa-token", "pn-1")


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
