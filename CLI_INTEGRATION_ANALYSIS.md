# CLI Backend Integration Analysis

Date: 2026-03-31

## Summary

The CLI currently has two backend integration stacks:

- Active interactive/streaming path: [`src/tarang/stream.py`](/Users/autoai-mini/Documents/axplusb/Tarang/tarang-cli/src/tarang/stream.py) using SSE + REST callbacks against `/api/*`
- Legacy helper clients: [`src/tarang/client/api_client.py`](/Users/autoai-mini/Documents/axplusb/Tarang/tarang-cli/src/tarang/client/api_client.py) and [`src/tarang/ws/client.py`](/Users/autoai-mini/Documents/axplusb/Tarang/tarang-cli/src/tarang/ws/client.py) using `/v2/*`, `/v3/*`, and WebSocket endpoints

The backend repo’s active routes are `/api/execute`, `/api/callback`, `/api/pause/{task_id}`, `/api/resume/{task_id}`, and `/api/cancel/{task_id}`. There is no evidence in the current backend cleanup pass of active `/v2/*`, `/v3/*`, or WebSocket routes.

## Base URL Configuration

- Credentials are stored in `~/.tarang/config.json` via [`src/tarang/client/auth.py`](/Users/autoai-mini/Documents/axplusb/Tarang/tarang-cli/src/tarang/client/auth.py)
- `tarang config --backend-url ...` persists `backend_url` in that file via [`src/tarang/cli.py#L118](/Users/autoai-mini/Documents/axplusb/Tarang/tarang-cli/src/tarang/cli.py#L118)
- No environment variable controls the backend URL in the CLI codebase
- Default backend URL in clients:
  - [`src/tarang/stream.py#L1451](/Users/autoai-mini/Documents/axplusb/Tarang/tarang-cli/src/tarang/stream.py#L1451): Railway URL
  - [`src/tarang/client/api_client.py#L131](/Users/autoai-mini/Documents/axplusb/Tarang/tarang-cli/src/tarang/client/api_client.py#L131): same Railway URL
  - [`src/tarang/ws/client.py#L119](/Users/autoai-mini/Documents/axplusb/Tarang/tarang-cli/src/tarang/ws/client.py#L119): same backend as `wss://...`
- Additional mismatch: some `cli.py` session/history flows fall back to `https://api.tarang.dev` instead of the Railway default:
  - [`src/tarang/cli.py#L892](/Users/autoai-mini/Documents/axplusb/Tarang/tarang-cli/src/tarang/cli.py#L892)
  - [`src/tarang/cli.py#L1021](/Users/autoai-mini/Documents/axplusb/Tarang/tarang-cli/src/tarang/cli.py#L1021)
  - [`src/tarang/cli.py#L1056](/Users/autoai-mini/Documents/axplusb/Tarang/tarang-cli/src/tarang/cli.py#L1056)

## Endpoint Map

### Active SSE/REST client

- [`src/tarang/stream.py#L1546](/Users/autoai-mini/Documents/axplusb/Tarang/tarang-cli/src/tarang/stream.py#L1546): `POST /api/execute`
- [`src/tarang/stream.py#L1699](/Users/autoai-mini/Documents/axplusb/Tarang/tarang-cli/src/tarang/stream.py#L1699): `POST /api/callback`
- [`src/tarang/stream.py#L1786](/Users/autoai-mini/Documents/axplusb/Tarang/tarang-cli/src/tarang/stream.py#L1786): `POST /api/cancel/{task_id}`
- [`src/tarang/stream.py#L1809](/Users/autoai-mini/Documents/axplusb/Tarang/tarang-cli/src/tarang/stream.py#L1809): `POST /api/pause/{task_id}`
- [`src/tarang/stream.py#L1839](/Users/autoai-mini/Documents/axplusb/Tarang/tarang-cli/src/tarang/stream.py#L1839): `POST /api/resume/{task_id}`

### Legacy HTTP client

- [`src/tarang/client/api_client.py#L182](/Users/autoai-mini/Documents/axplusb/Tarang/tarang-cli/src/tarang/client/api_client.py#L182): `POST /v2/execute`
- [`src/tarang/client/api_client.py#L237](/Users/autoai-mini/Documents/axplusb/Tarang/tarang-cli/src/tarang/client/api_client.py#L237): `POST /v2/execute/stream`
- [`src/tarang/client/api_client.py#L274](/Users/autoai-mini/Documents/axplusb/Tarang/tarang-cli/src/tarang/client/api_client.py#L274): `POST /v2/feedback`
- [`src/tarang/client/api_client.py#L295](/Users/autoai-mini/Documents/axplusb/Tarang/tarang-cli/src/tarang/client/api_client.py#L295): `POST /v2/quick`
- [`src/tarang/client/api_client.py#L333](/Users/autoai-mini/Documents/axplusb/Tarang/tarang-cli/src/tarang/client/api_client.py#L333): `POST /v2/sessions`
- [`src/tarang/client/api_client.py#L378](/Users/autoai-mini/Documents/axplusb/Tarang/tarang-cli/src/tarang/client/api_client.py#L378): `PATCH /v2/sessions/{session_id}`
- [`src/tarang/client/api_client.py#L415](/Users/autoai-mini/Documents/axplusb/Tarang/tarang-cli/src/tarang/client/api_client.py#L415): `POST /v2/sessions/{session_id}/events`
- [`src/tarang/client/api_client.py#L452](/Users/autoai-mini/Documents/axplusb/Tarang/tarang-cli/src/tarang/client/api_client.py#L452): `POST /v2/sessions/{session_id}/usage`
- [`src/tarang/client/api_client.py#L479](/Users/autoai-mini/Documents/axplusb/Tarang/tarang-cli/src/tarang/client/api_client.py#L479): `GET /v2/sessions`
- [`src/tarang/client/api_client.py#L506](/Users/autoai-mini/Documents/axplusb/Tarang/tarang-cli/src/tarang/client/api_client.py#L506): `GET /v2/sessions/{session_id}/events`
- [`src/tarang/client/api_client.py#L535](/Users/autoai-mini/Documents/axplusb/Tarang/tarang-cli/src/tarang/client/api_client.py#L535): `POST /v3/pause/{task_id}`
- [`src/tarang/client/api_client.py#L570](/Users/autoai-mini/Documents/axplusb/Tarang/tarang-cli/src/tarang/client/api_client.py#L570): `POST /v3/resume/{task_id}`
- [`src/tarang/client/api_client.py#L594](/Users/autoai-mini/Documents/axplusb/Tarang/tarang-cli/src/tarang/client/api_client.py#L594): `POST /v3/cancel/{task_id}`
- [`src/tarang/client/api_client.py#L779](/Users/autoai-mini/Documents/axplusb/Tarang/tarang-cli/src/tarang/client/api_client.py#L779): `WS /v2/ws/execute`

### Legacy hybrid WebSocket client

- [`src/tarang/ws/client.py#L174](/Users/autoai-mini/Documents/axplusb/Tarang/tarang-cli/src/tarang/ws/client.py#L174): `WS /v2/ws/agent`

## Mismatches

1. The active stream client uses `/api/*`, but `TarangAPIClient` still uses `/v2/*` and `/v3/*`.
2. The active backend does pause/resume/cancel on `/api/*`, not `/v3/*`.
3. The WebSocket clients still expect `/v2/ws/agent` and `/v2/ws/execute`, but the backend work reviewed so far is SSE-only.
4. CLI history/session helpers may hit a different host (`https://api.tarang.dev`) than the main stream client.
5. The stream client recognizes 22 SSE event types, not 21:
   `status`, `session_info`, `tool_request`, `tool_call`, `tool_done`, `thinking`, `plan`, `phase_update`, `phase_summary`, `worker_update`, `phase_start`, `worker_start`, `worker_done`, `delegation`, `change`, `content`, `error`, `complete`, `cancelled`, `paused`, `resumed`, `pause_instruction`

## Test Matrix

| Scenario | CLI Path | Endpoint(s) | Test Status |
| --- | --- | --- | --- |
| SSE stream starts successfully | `TarangStreamClient.execute()` | `/api/execute` | Added skeleton |
| SSE event parsing across full registry | `StreamEvent.from_sse()` | stream payload | Added skeleton |
| Tool callback round-trip | `_handle_tool_request()` | `/api/callback` | Added skeleton |
| Pause/resume/cancel control flow | `pause()/resume()/cancel()` | `/api/pause`, `/api/resume`, `/api/cancel` | Added skeleton |
| Unknown SSE event handling | `StreamEvent.from_sse()` | stream payload | Added skeleton |
| Legacy REST client mismatch visibility | `TarangAPIClient` | `/v2/*`, `/v3/*` | Documented |
| Legacy WebSocket mismatch visibility | `TarangWSClient`, `TarangStreamingClient` | `/v2/ws/*` | Documented |
| Backend URL fallback inconsistency | `cli.py` history/session flows | `backend_url` vs `api.tarang.dev` | Documented |

## Current Gaps

- No integration tests existed before this pass.
- No test currently verifies the legacy clients against the live backend contract.
- No shared endpoint constant/module exists; routes are duplicated across three client implementations.
- No environment-variable-based backend override exists; only file-backed config is supported.
- The CLI still ships both SSE and WebSocket integration stacks, which increases drift risk.

## Recommended Next Steps

1. Consolidate all backend routes into one shared endpoint definition module.
2. Decide whether WebSocket support is still a product requirement; remove or migrate stale WS clients if not.
3. Unify default backend host selection so session/history flows do not silently switch to `https://api.tarang.dev`.
4. Convert the new skeleton tests into full mocks/assertions around response bodies once the final backend contract is frozen.
