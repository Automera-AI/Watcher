from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.core.envelope import InboundTurn, OutboundAction


def test_a_turn_without_an_idempotency_key_is_rejected():
    with pytest.raises(ValueError, match="idempotency_key"):
        InboundTurn(
            tenant_id=uuid4(), channel="whatsapp", channel_thread_id="971500000000",
            channel_identity="+971500000000", modality="text", text="hello",
            received_at=datetime.now(UTC), idempotency_key="",
        )


def test_whatsapp_allows_at_most_three_buttons():
    with pytest.raises(ValueError, match="3 quick reply"):
        OutboundAction(kind="ask", text="pick", quick_replies=["a", "b", "c", "d"])
