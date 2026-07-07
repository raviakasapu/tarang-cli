from __future__ import annotations

import pytest

from tarang.stream import EventType, StreamEvent, TarangStreamClient

from .conftest import MockStreamResponse


def test_all_primary_sse_event_types_are_parsed_correctly(primary_sse_event_names):
    """Each primary SSE event name should resolve to the matching EventType."""
    for event_name in primary_sse_event_names:
        event = StreamEvent.from_sse(event_name, '{"message": "ok"}')
        assert event.type.value == event_name
        assert event.data["message"] == "ok"


def test_event_data_is_extracted_properly():
    """JSON payloads should be preserved exactly when parsing SSE data."""
    event = StreamEvent.from_sse("phase_update", '{"phase_index": 1, "status": "running"}')
    assert event.type is EventType.PHASE_UPDATE
    assert event.data == {"phase_index": 1, "status": "running"}


def test_malformed_event_payloads_are_handled_gracefully():
    """Malformed event data should not crash parsing."""
    event = StreamEvent.from_sse("status", "not-json")
    assert event.type is EventType.STATUS
    assert event.data == {"message": "not-json"}


@pytest.mark.asyncio
async def test_sse_event_ordering_is_preserved(
    project_context,
    primary_sse_event_names,
    sse_payload_factory,
    mock_async_client_factory,
):
    """The stream client should yield events in backend order."""
    payload = sse_payload_factory(primary_sse_event_names)
    mock_async_client_factory(
        stream_response=MockStreamResponse(
            status_code=200,
            text=payload,
            headers={"X-Task-ID": "task_123"},
        )
    )

    client = TarangStreamClient(
        base_url="https://backend.test",
        token="token",
        openrouter_key="key",
        project_root="/tmp/project",
        on_tool_execute=lambda tool, args: {"success": True},
    )

    events = [event async for event in client.execute("run", project_context)]
    assert [event.type.value for event in events] == primary_sse_event_names
