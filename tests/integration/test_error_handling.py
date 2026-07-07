from __future__ import annotations

import httpx
import pytest

from tarang.stream import EventType, TarangStreamClient

from .conftest import MockStreamResponse


class ConnectErrorAsyncClient:
    """AsyncClient stub that always raises a connection error."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    def stream(self, method, url, **kwargs):
        raise httpx.ConnectError("backend offline")


class TimeoutAsyncClient:
    """AsyncClient stub that always raises a timeout."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    def stream(self, method, url, **kwargs):
        raise httpx.TimeoutException("request timed out")


@pytest.mark.asyncio
async def test_connection_errors_surface_as_error_events(monkeypatch, project_context):
    """Connection failures should yield a user-visible error event."""
    monkeypatch.setattr(httpx, "AsyncClient", ConnectErrorAsyncClient)
    client = TarangStreamClient(base_url="https://backend.test", token="token", openrouter_key="key")

    events = [event async for event in client.execute("run", project_context)]
    assert len(events) == 1
    assert events[0].type is EventType.ERROR
    assert "Connection failed" in events[0].data["message"]


@pytest.mark.asyncio
async def test_timeout_errors_surface_as_error_events(monkeypatch, project_context):
    """Timeouts should yield a user-visible error event."""
    monkeypatch.setattr(httpx, "AsyncClient", TimeoutAsyncClient)
    client = TarangStreamClient(base_url="https://backend.test", token="token", openrouter_key="key")

    events = [event async for event in client.execute("run", project_context)]
    assert len(events) == 1
    assert events[0].type is EventType.ERROR
    assert "timed out" in events[0].data["message"]


@pytest.mark.asyncio
async def test_invalid_backend_responses_surface_as_error_events(
    project_context,
    mock_async_client_factory,
):
    """Non-200 backend responses should become error events."""
    mock_async_client_factory(
        stream_response=MockStreamResponse(status_code=500, text='{"detail":"boom"}')
    )
    client = TarangStreamClient(base_url="https://backend.test", token="token", openrouter_key="key")

    events = [event async for event in client.execute("run", project_context)]
    assert len(events) == 1
    assert events[0].type is EventType.ERROR
    assert "Request failed: 500" in events[0].data["message"]


@pytest.mark.asyncio
async def test_client_can_recover_on_a_subsequent_successful_attempt(
    monkeypatch,
    project_context,
    sse_payload_factory,
):
    """A later request should succeed after a previous failure with a fresh client patch."""
    monkeypatch.setattr(httpx, "AsyncClient", ConnectErrorAsyncClient)
    failed_client = TarangStreamClient(base_url="https://backend.test", token="token", openrouter_key="key")
    failed_events = [event async for event in failed_client.execute("run", project_context)]
    assert failed_events[0].type is EventType.ERROR

    class RecoveryAsyncClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        def stream(self, method, url, **kwargs):
            return MockStreamResponse(
                status_code=200,
                text=sse_payload_factory(["status", "complete"]),
                headers={"X-Task-ID": "task_recovered"},
            )

    monkeypatch.setattr(httpx, "AsyncClient", RecoveryAsyncClient)
    recovered_client = TarangStreamClient(base_url="https://backend.test", token="token", openrouter_key="key")
    recovered_events = [event async for event in recovered_client.execute("run", project_context)]

    assert [event.type.value for event in recovered_events] == ["status", "complete"]
