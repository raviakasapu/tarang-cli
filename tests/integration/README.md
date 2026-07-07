# CLI Integration Tests

Tests for the Tarang CLI ↔ Backend integration contract.

## Running Tests

```bash
cd /Users/autoai-mini/Documents/axplusb/Tarang/tarang-cli
python -m pytest tests/integration/ -v
```

## Test Files

| File | Coverage | Tests |
|---|---|---|
| `test_backend_integration.py` | End-to-end SSE parsing, callback routing, route validation | 7 tests |
| `test_sse_events.py` | Individual parsing for all 22 SSE event types + edge cases | 26 tests |
| `test_tool_callback.py` | Callback payload, errors, sequential calls | 6 tests |
| `test_pause_resume.py` | Control events, endpoint URLs, state transitions | 10 tests |
| `test_error_handling.py` | Auth errors, HTTP errors, malformed SSE | 9 tests |

## Mocking Strategy

- **No real API calls.** All tests mock `httpx.AsyncClient` via `conftest.py` fixtures.
- `MockStreamResponse` simulates SSE streams with configurable status + body.
- `MockJSONResponse` simulates REST responses for callbacks and control endpoints.
- `MockAsyncClient` records all requests in `request_log` for assertion.
- `mock_async_client_factory` monkeypatches `httpx.AsyncClient` globally.

## Key Fixtures (conftest.py)

| Fixture | Purpose |
|---|---|
| `project_context` | Standard `ProjectContext` for test requests |
| `all_sse_event_names` | List of all 22 `EventType` values |
| `sse_payload_factory` | Generates raw SSE text from event names |
| `mock_async_client_factory` | Patches httpx, returns request log |

## Adding New Tests

1. If testing a new SSE event type: add to `test_sse_events.py` and update the `tested_types` guard set.
2. If testing a new endpoint: add to `test_backend_integration.py` or create a new file.
3. If testing tool execution: add to `test_tool_callback.py` using the `FakeHTTP` helper.
4. Always use `@pytest.mark.asyncio` for async tests.

## Backend Contract

The CLI SSE stream client (`stream.py`) uses these backend routes:

| Operation | URL | Method |
|---|---|---|
| Start task | `/api/execute` | POST (returns SSE stream) |
| Tool callback | `/api/callback` | POST |
| Pause | `/api/pause/{task_id}` | POST |
| Resume | `/api/resume/{task_id}` | POST |
| Cancel | `/api/cancel/{task_id}` | POST |

The legacy REST client (`api_client.py`) uses `/v2/` and `/v3/` prefixes for the same operations. The backend now serves all three prefixes (`/api/`, `/v2/`, `/v3/`).
