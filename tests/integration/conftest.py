from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterable
from typing import Any

import httpx
import pytest

from tarang.context_collector import ProjectContext

try:
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse, StreamingResponse
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover - optional dev dependency
    FastAPI = None
    JSONResponse = None
    StreamingResponse = None
    TestClient = None


PRIMARY_SSE_EVENT_NAMES = [
    "status",
    "session_info",
    "tool_call",
    "tool_done",
    "thinking",
    "plan",
    "phase_update",
    "phase_summary",
    "worker_update",
    "phase_start",
    "worker_start",
    "worker_done",
    "delegation",
    "change",
    "content",
    "error",
    "complete",
    "cancelled",
    "paused",
    "resumed",
    "pause_instruction",
]


@pytest.fixture
def project_context() -> ProjectContext:
    """Minimal project context for mocked CLI execution tests."""
    return ProjectContext(
        cwd="/tmp/project",
        files=["app.py", "README.md"],
        relevant_files=[],
    )


@pytest.fixture
def session_fixture() -> dict[str, str]:
    """Stable mock session identifiers for integration fixtures."""
    return {
        "task_id": "task_123",
        "job_id": "job_123",
        "session_id": "session_123",
        "call_id": "call_123",
    }


@pytest.fixture
def primary_sse_event_names() -> list[str]:
    """Current primary SSE contract used by the CLI test skeleton."""
    return list(PRIMARY_SSE_EVENT_NAMES)


@pytest.fixture
def sse_payload_factory():
    """Build an SSE payload from ordered event names and payload dictionaries."""

    def factory(
        event_names: Iterable[str],
        data_factory: Any | None = None,
    ) -> str:
        chunks: list[str] = []
        for index, name in enumerate(event_names):
            payload = (
                data_factory(name, index)
                if callable(data_factory)
                else {"event": name, "index": index}
            )
            chunks.append(f"event: {name}")
            chunks.append(f"data: {json.dumps(payload)}")
            chunks.append("")
        return "\n".join(chunks) + "\n"

    return factory


@pytest.fixture
def mock_tool_callback_handler():
    """Capture callback payloads and return success/error responses."""
    captured: list[dict[str, Any]] = []

    def handler(payload: dict[str, Any], *, ok: bool = True) -> dict[str, Any]:
        captured.append(payload)
        if ok:
            return {"status": "ok", "received": payload}
        return {"status": "error", "received": payload}

    return captured, handler


@pytest.fixture
def mock_backend_server(sse_payload_factory, session_fixture):
    """Optional FastAPI mock backend for future end-to-end CLI integration tests."""
    if FastAPI is None:
        pytest.skip("fastapi is not installed in this environment")

    app = FastAPI()
    callback_payloads: list[dict[str, Any]] = []
    stream_text = sse_payload_factory(
        ["session_info", "status", "complete"],
        lambda name, index: {
            "event": name,
            "index": index,
            "task_id": session_fixture["task_id"],
            "job_id": session_fixture["job_id"],
            "session_id": session_fixture["session_id"],
        },
    )

    @app.post("/api/execute")
    async def execute():
        return StreamingResponse(iter([stream_text]), media_type="text/event-stream")

    @app.post("/api/callback")
    async def callback(payload: dict[str, Any]):
        callback_payloads.append(payload)
        return JSONResponse({"status": "ok"})

    @app.post("/api/pause/{task_id}")
    async def pause(task_id: str):
        return JSONResponse({"status": "paused", "task_id": task_id})

    @app.post("/api/resume/{task_id}")
    async def resume(task_id: str):
        return JSONResponse({"status": "resumed", "task_id": task_id})

    @app.post("/api/cancel/{task_id}")
    async def cancel(task_id: str):
        return JSONResponse({"status": "cancelled", "task_id": task_id})

    return app, callback_payloads


@pytest.fixture
def mock_backend_client(mock_backend_server):
    """FastAPI TestClient wrapper for the mock backend fixture."""
    app, callback_payloads = mock_backend_server
    if TestClient is None:
        pytest.skip("fastapi test client is not installed in this environment")
    with TestClient(app) as client:
        yield client, callback_payloads


class MockStreamResponse:
    """Async streaming response used to patch httpx.AsyncClient."""

    def __init__(
        self,
        status_code: int = 200,
        text: str = "",
        headers: dict[str, str] | None = None,
    ):
        self.status_code = status_code
        self._text = text
        self.headers = headers or {}

    async def __aenter__(self) -> "MockStreamResponse":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def aiter_lines(self) -> AsyncIterator[str]:
        for line in self._text.splitlines():
            yield line

    async def aread(self) -> bytes:
        return self._text.encode()


class MockJSONResponse:
    """Small JSON response stub for patched HTTP interactions."""

    def __init__(self, status_code: int = 200, payload: dict[str, Any] | None = None):
        self.status_code = status_code
        self._payload = payload or {"status": "ok"}
        self.text = json.dumps(self._payload)

    def json(self) -> dict[str, Any]:
        return self._payload


class MockAsyncClient:
    """Patchable httpx.AsyncClient replacement for mock-only tests."""

    def __init__(
        self,
        stream_response: MockStreamResponse | None = None,
        post_responses: list[MockJSONResponse] | None = None,
        request_log: list[tuple[str, str, dict[str, Any]]] | None = None,
        **_: Any,
    ):
        self.stream_response = stream_response or MockStreamResponse()
        self.post_responses = post_responses or [MockJSONResponse()]
        self.request_log = request_log if request_log is not None else []

    async def __aenter__(self) -> "MockAsyncClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    def stream(self, method: str, url: str, **kwargs: Any) -> MockStreamResponse:
        self.request_log.append((method, url, kwargs))
        return self.stream_response

    async def post(self, url: str, **kwargs: Any) -> MockJSONResponse:
        self.request_log.append(("POST", url, kwargs))
        if self.post_responses:
            return self.post_responses.pop(0)
        return MockJSONResponse()


@pytest.fixture
def mock_async_client_factory(monkeypatch):
    """Patch httpx.AsyncClient and capture outgoing requests."""

    def factory(
        *,
        stream_response: MockStreamResponse | None = None,
        post_responses: list[MockJSONResponse] | None = None,
    ) -> list[tuple[str, str, dict[str, Any]]]:
        request_log: list[tuple[str, str, dict[str, Any]]] = []

        def builder(*args: Any, **kwargs: Any) -> MockAsyncClient:
            return MockAsyncClient(
                stream_response=stream_response,
                post_responses=list(post_responses or [MockJSONResponse()]),
                request_log=request_log,
                **kwargs,
            )

        monkeypatch.setattr(httpx, "AsyncClient", builder)
        return request_log

    return factory


class MockWebSocket:
    """In-memory WebSocket stub for protocol-level callback tests."""

    def __init__(self, *, send_delay: float = 0.0):
        self.send_delay = send_delay
        self.sent_messages: list[dict[str, Any]] = []

    async def send(self, payload: str) -> None:
        if self.send_delay:
            await asyncio.sleep(self.send_delay)
        self.sent_messages.append(json.loads(payload))


@pytest.fixture
def mock_websocket() -> MockWebSocket:
    """Default WebSocket stub used by callback protocol tests."""
    return MockWebSocket()
