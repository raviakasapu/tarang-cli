from __future__ import annotations

import asyncio

import pytest

from tarang.ws.client import TarangWSClient

from .conftest import MockWebSocket


@pytest.mark.asyncio
async def test_send_tool_result_uses_expected_protocol_payload(mock_websocket):
    """Tool results should include both call_id and request_id for compatibility."""
    client = TarangWSClient(base_url="https://backend.test", token="token", openrouter_key="key")
    client._ws = mock_websocket
    client._connected = True

    await client.send_tool_result("call_123", {"success": True, "content": "ok"})

    assert mock_websocket.sent_messages == [
        {
            "type": "tool_result",
            "call_id": "call_123",
            "request_id": "call_123",
            "result": {"success": True, "content": "ok"},
        }
    ]


@pytest.mark.asyncio
async def test_error_callback_uses_expected_protocol_payload(mock_websocket):
    """Tool errors should preserve the callback identifiers and error message."""
    client = TarangWSClient(base_url="https://backend.test", token="token", openrouter_key="key")
    client._ws = mock_websocket
    client._connected = True

    await client.send_tool_error("call_456", "tool failed")

    assert mock_websocket.sent_messages == [
        {
            "type": "tool_error",
            "call_id": "call_456",
            "request_id": "call_456",
            "error": "tool failed",
        }
    ]


@pytest.mark.asyncio
async def test_callback_payload_format_is_stable(mock_websocket):
    """The callback schema should remain serializable and deterministic."""
    client = TarangWSClient(base_url="https://backend.test", token="token", openrouter_key="key")
    client._ws = mock_websocket
    client._connected = True

    payload = {"success": True, "lines": ["a", "b"], "metadata": {"path": "app.py"}}
    await client.send_tool_result("call_789", payload)

    sent = mock_websocket.sent_messages[0]
    assert sent["type"] == "tool_result"
    assert sent["result"] == payload


@pytest.mark.asyncio
async def test_callback_timeout_can_be_detected_by_caller():
    """A slow callback transport should surface as an awaitable timeout."""
    client = TarangWSClient(base_url="https://backend.test", token="token", openrouter_key="key")
    client._ws = MockWebSocket(send_delay=0.05)
    client._connected = True

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            client.send_tool_result("call_timeout", {"success": True}),
            timeout=0.01,
        )
