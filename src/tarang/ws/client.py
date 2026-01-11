"""
WebSocket Client for Hybrid Agent Architecture.

Manages bidirectional WebSocket communication with the Tarang backend:
- Receives tool requests from backend
- Executes tools locally
- Sends results back to backend
- Handles progress events and UI updates
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator, Callable, Dict, Optional

import websockets
from websockets.client import WebSocketClientProtocol

logger = logging.getLogger(__name__)


class EventType(str, Enum):
    """WebSocket event types."""
    CONNECTED = "connected"
    THINKING = "thinking"
    TOOL_REQUEST = "tool_request"
    APPROVAL_REQUEST = "approval_request"
    PHASE_START = "phase_start"
    MILESTONE_UPDATE = "milestone_update"
    PROGRESS = "progress"
    COMPLETE = "complete"
    ERROR = "error"
    PAUSED = "paused"
    HEARTBEAT = "heartbeat"
    PONG = "pong"


@dataclass
class WSEvent:
    """A WebSocket event from backend."""
    type: EventType
    data: Dict[str, Any] = field(default_factory=dict)
    request_id: Optional[str] = None

    @classmethod
    def from_json(cls, data: Dict[str, Any]) -> "WSEvent":
        """Create event from JSON data."""
        event_type = data.get("type", "")
        try:
            etype = EventType(event_type)
        except ValueError:
            etype = EventType.ERROR

        return cls(
            type=etype,
            data=data.get("data", data),
            request_id=data.get("request_id"),
        )


# Type alias for tool executor callback
ToolExecutorCallback = Callable[[str, Dict[str, Any]], Any]

# Type alias for approval callback (returns True if approved)
ApprovalCallback = Callable[[str, Dict[str, Any]], bool]


class TarangWSClient:
    """
    WebSocket client for hybrid agent communication.

    Usage:
        async with TarangWSClient(base_url, token, openrouter_key) as client:
            async for event in client.execute(instruction, cwd):
                # Handle event
                if event.type == EventType.TOOL_REQUEST:
                    result = execute_tool(event.data)
                    await client.send_tool_result(event.request_id, result)
    """

    DEFAULT_BASE_URL = "wss://tarang-backend-intl-web-app-production.up.railway.app"

    def __init__(
        self,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        openrouter_key: Optional[str] = None,
        reconnect_attempts: int = 3,
        reconnect_delay: float = 2.0,
        auto_reconnect: bool = True,
    ):
        self.base_url = (base_url or self.DEFAULT_BASE_URL).replace("https://", "wss://").replace("http://", "ws://")
        self.token = token
        self.openrouter_key = openrouter_key
        self.reconnect_attempts = reconnect_attempts
        self.reconnect_delay = reconnect_delay
        self.auto_reconnect = auto_reconnect

        self._ws: Optional[WebSocketClientProtocol] = None
        self._session_id: Optional[str] = None
        self._connected = False
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._current_job_id: Optional[str] = None
        self._reconnect_callback: Optional[Callable[[], None]] = None

    @property
    def session_id(self) -> Optional[str]:
        return self._session_id

    @property
    def current_job_id(self) -> Optional[str]:
        return self._current_job_id

    @property
    def is_connected(self) -> bool:
        return self._connected and self._ws is not None

    def set_reconnect_callback(self, callback: Callable[[], None]):
        """Set callback to be called on reconnection."""
        self._reconnect_callback = callback

    async def connect(self) -> str:
        """
        Connect to the WebSocket endpoint.

        Returns:
            Session ID from backend
        """
        if not self.token:
            raise ValueError("Token is required")

        if not self.openrouter_key:
            raise ValueError("OpenRouter key is required")

        # Build WebSocket URL
        ws_url = f"{self.base_url}/v2/ws/agent?token={self.token}&openrouter_key={self.openrouter_key}"

        logger.debug(f"Connecting to {self.base_url}/v2/ws/agent")

        # Connect with retry
        last_error = None
        for attempt in range(self.reconnect_attempts):
            try:
                self._ws = await websockets.connect(
                    ws_url,
                    ping_interval=30,
                    ping_timeout=60,  # Increased for slow LLM responses
                    close_timeout=10,
                )

                # Wait for connected event
                raw = await asyncio.wait_for(self._ws.recv(), timeout=10.0)
                data = json.loads(raw)

                if data.get("type") == "connected":
                    self._session_id = data.get("data", {}).get("session_id")
                    self._connected = True
                    logger.info(f"Connected to Tarang (session: {self._session_id})")

                    # Start heartbeat
                    self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

                    return self._session_id
                else:
                    raise ConnectionError(f"Unexpected response: {data}")

            except Exception as e:
                last_error = e
                logger.warning(f"Connection attempt {attempt + 1} failed: {e}")
                if attempt < self.reconnect_attempts - 1:
                    await asyncio.sleep(self.reconnect_delay)

        raise ConnectionError(f"Failed to connect after {self.reconnect_attempts} attempts: {last_error}")

    async def disconnect(self):
        """Disconnect from WebSocket."""
        self._connected = False

        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            self._heartbeat_task = None

        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

        logger.info("Disconnected from Tarang")

    async def reconnect(self) -> bool:
        """
        Attempt to reconnect after a disconnection.

        Returns:
            True if reconnection successful, False otherwise
        """
        logger.info("Attempting to reconnect...")

        # Clean up old connection
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            self._heartbeat_task = None

        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

        self._connected = False

        # Try to reconnect
        for attempt in range(self.reconnect_attempts):
            try:
                await self.connect()
                logger.info(f"Reconnected (attempt {attempt + 1})")

                if self._reconnect_callback:
                    self._reconnect_callback()

                return True

            except Exception as e:
                logger.warning(f"Reconnect attempt {attempt + 1} failed: {e}")
                if attempt < self.reconnect_attempts - 1:
                    await asyncio.sleep(self.reconnect_delay * (attempt + 1))

        logger.error("Failed to reconnect after all attempts")
        return False

    async def __aenter__(self) -> "TarangWSClient":
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.disconnect()

    async def execute(
        self,
        instruction: str,
        cwd: str,
        job_id: Optional[str] = None,
    ) -> AsyncIterator[WSEvent]:
        """
        Execute an instruction and yield events.

        This is the main entry point for hybrid execution:
        1. Send execute message to backend
        2. Yield events as they arrive
        3. Caller handles tool requests and sends results

        Args:
            instruction: User instruction
            cwd: Current working directory
            job_id: Optional job ID to resume

        Yields:
            WSEvent objects for each backend message
        """
        if not self._ws or not self._connected:
            raise ConnectionError("Not connected")

        # Track current job for potential resume
        self._current_job_id = job_id

        # Send execute request
        await self._ws.send(json.dumps({
            "type": "execute",
            "instruction": instruction,
            "cwd": cwd,
            "job_id": job_id,
        }))

        logger.debug(f"Sent execute request: {instruction[:50]}...")

        # Yield events until complete or error
        reconnect_attempts = 0
        max_reconnect_during_exec = 2

        try:
            while self._connected or (self.auto_reconnect and reconnect_attempts < max_reconnect_during_exec):
                try:
                    raw = await asyncio.wait_for(self._ws.recv(), timeout=60.0)
                    data = json.loads(raw)
                    event = WSEvent.from_json(data)

                    # Track job_id from connected/progress events
                    if event.data.get("job_id"):
                        self._current_job_id = event.data["job_id"]

                    yield event

                    # Stop on complete or error
                    if event.type in (EventType.COMPLETE, EventType.ERROR, EventType.PAUSED):
                        self._current_job_id = None
                        break

                except asyncio.TimeoutError:
                    # No message in 60s - might be waiting for tool result
                    continue

                except websockets.ConnectionClosed as e:
                    logger.warning(f"Connection closed during execution: {e}")
                    self._connected = False

                    if not self.auto_reconnect:
                        yield WSEvent(type=EventType.ERROR, data={"message": f"Connection closed: {e}"})
                        break

                    # Try to reconnect and resume
                    reconnect_attempts += 1
                    yield WSEvent(
                        type=EventType.PROGRESS,
                        data={"message": f"Connection lost. Reconnecting (attempt {reconnect_attempts})..."}
                    )

                    if await self.reconnect():
                        # Resume the job if we have a job_id
                        if self._current_job_id:
                            logger.info(f"Resuming job {self._current_job_id}")
                            await self._ws.send(json.dumps({
                                "type": "resume",
                                "job_id": self._current_job_id,
                                "cwd": cwd,
                            }))
                            yield WSEvent(
                                type=EventType.PROGRESS,
                                data={"message": "Reconnected. Resuming..."}
                            )
                        else:
                            yield WSEvent(
                                type=EventType.ERROR,
                                data={"message": "Reconnected but no job to resume"}
                            )
                            break
                    else:
                        yield WSEvent(
                            type=EventType.ERROR,
                            data={"message": "Failed to reconnect after multiple attempts"}
                        )
                        break

        except websockets.ConnectionClosed as e:
            logger.warning(f"Connection closed: {e}")
            self._connected = False
            yield WSEvent(type=EventType.ERROR, data={"message": f"Connection closed: {e}"})

    async def send_tool_result(
        self,
        request_id: str,
        result: Dict[str, Any],
    ):
        """Send tool execution result to backend."""
        if not self._ws or not self._connected:
            raise ConnectionError("Not connected")

        await self._ws.send(json.dumps({
            "type": "tool_result",
            "request_id": request_id,
            "result": result,
        }))

        logger.debug(f"Sent tool result for {request_id}")

    async def send_tool_error(
        self,
        request_id: str,
        error: str,
    ):
        """Send tool error to backend."""
        if not self._ws or not self._connected:
            raise ConnectionError("Not connected")

        await self._ws.send(json.dumps({
            "type": "tool_error",
            "request_id": request_id,
            "error": error,
        }))

        logger.debug(f"Sent tool error for {request_id}: {error}")

    async def send_approval(
        self,
        request_id: str,
        approved: bool,
    ):
        """Send approval response to backend."""
        if not self._ws or not self._connected:
            raise ConnectionError("Not connected")

        await self._ws.send(json.dumps({
            "type": "approval",
            "request_id": request_id,
            "approved": approved,
        }))

        logger.debug(f"Sent approval for {request_id}: {approved}")

    async def cancel(self):
        """Cancel current execution."""
        if not self._ws or not self._connected:
            return

        await self._ws.send(json.dumps({"type": "cancel"}))
        logger.info("Sent cancel request")

    async def _heartbeat_loop(self, interval: float = 25.0):
        """Send periodic heartbeat pings."""
        try:
            while self._connected and self._ws:
                await asyncio.sleep(interval)
                try:
                    await self._ws.send(json.dumps({"type": "ping"}))
                except Exception:
                    break
        except asyncio.CancelledError:
            pass


class WSClientPool:
    """
    Connection pool for WebSocket clients.

    For future use with multiple concurrent connections.
    """

    def __init__(self, max_connections: int = 5):
        self.max_connections = max_connections
        self._clients: Dict[str, TarangWSClient] = {}

    async def get_client(
        self,
        base_url: str,
        token: str,
        openrouter_key: str,
    ) -> TarangWSClient:
        """Get or create a client for the given credentials."""
        key = f"{base_url}:{token[:8]}"

        if key in self._clients and self._clients[key].is_connected:
            return self._clients[key]

        client = TarangWSClient(
            base_url=base_url,
            token=token,
            openrouter_key=openrouter_key,
        )

        await client.connect()
        self._clients[key] = client

        return client

    async def close_all(self):
        """Close all connections."""
        for client in self._clients.values():
            await client.disconnect()
        self._clients.clear()
