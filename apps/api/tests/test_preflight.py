"""The startup check that receiving and sending are configured for the same endpoint."""

from __future__ import annotations

import logging

import pytest

from apps.api.channels.config import ChannelCredentials
from apps.api.ingestion.ports import UnknownEndpoint
from apps.api.ingestion.preflight import warn_on_unclaimed_endpoints


def test_a_claimed_endpoint_is_silent(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        warn_on_unclaimed_endpoints(lambda _e: "tenant-a", ["PNID"])
    assert caplog.records == []


def test_an_unclaimed_endpoint_warns_with_the_id_and_the_fix(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The failure this catches is silent by construction, so the warning has to be actionable.

    A process whose send credentials name an endpoint no tenant claims boots clean, holds valid
    credentials, and drops every message that arrives on it. Without this line the first person to
    notice is a guest whose message went unanswered.
    """

    def unclaimed(_endpoint: str | None) -> str:
        raise UnknownEndpoint("no enabled channel_configs row")

    with caplog.at_level(logging.WARNING):
        warn_on_unclaimed_endpoints(unclaimed, ["PNID"])

    assert len(caplog.records) == 1
    assert "PNID" in caplog.records[0].getMessage()
    assert "channel_configs" in caplog.records[0].getMessage()


def test_an_unreachable_database_warns_once_and_does_not_raise(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A diagnostic that can stop a deploy is worse than the mistake it looks for."""

    def unreachable(_endpoint: str | None) -> str:
        raise RuntimeError("connection refused")

    with caplog.at_level(logging.WARNING):
        warn_on_unclaimed_endpoints(unreachable, ["ONE", "TWO"])

    assert len(caplog.records) == 1  # gives up after the first, rather than retrying per endpoint
    assert "unreachable" in caplog.records[0].getMessage()


def test_no_send_credentials_means_nothing_to_check() -> None:
    """A process that only ingests is not required to hold an endpoint to check."""
    assert ChannelCredentials(whatsapp_phone_number_id=None).configured_endpoints() == ()
    assert ChannelCredentials(whatsapp_phone_number_id="PNID").configured_endpoints() == ("PNID",)
