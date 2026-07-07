from __future__ import annotations

import pytest

from tarang.stream import TarangStreamClient

from .conftest import MockJSONResponse


@pytest.mark.asyncio
async def test_pause_flow_calls_backend_and_returns_true(mock_async_client_factory):
    """Pause should hit the pause endpoint and accept a paused response."""
    request_log = mock_async_client_factory(
        post_responses=[MockJSONResponse(payload={"status": "paused"})]
    )
    client = TarangStreamClient(base_url="https://backend.test", token="token", openrouter_key="key")
    client.current_task_id = "task_123"

    assert await client.pause() is True
    assert request_log[0][1] == "https://backend.test/api/pause/task_123"


@pytest.mark.asyncio
async def test_resume_flow_sends_optional_instruction(mock_async_client_factory):
    """Resume should include the injected instruction when provided."""
    request_log = mock_async_client_factory(
        post_responses=[MockJSONResponse(payload={"status": "resumed"})]
    )
    client = TarangStreamClient(base_url="https://backend.test", token="token", openrouter_key="key")
    client.current_task_id = "task_123"

    assert await client.resume("skip tests") is True
    assert request_log[0][1] == "https://backend.test/api/resume/task_123"
    assert request_log[0][2]["json"] == {"instruction": "skip tests"}


@pytest.mark.asyncio
async def test_cancel_flow_calls_backend_and_returns_true(mock_async_client_factory):
    """Cancel should set the client-side flag and notify the backend."""
    request_log = mock_async_client_factory(
        post_responses=[MockJSONResponse(payload={"status": "cancelled"})]
    )
    client = TarangStreamClient(base_url="https://backend.test", token="token", openrouter_key="key")
    client.current_task_id = "task_123"

    assert await client.cancel() is True
    assert client._cancelled is True
    assert request_log[0][1] == "https://backend.test/api/cancel/task_123"


@pytest.mark.asyncio
async def test_state_transitions_require_an_active_task():
    """Pause and resume should fail cleanly when no active task exists."""
    client = TarangStreamClient(base_url="https://backend.test", token="token", openrouter_key="key")

    assert await client.pause() is False
    assert await client.resume() is False
