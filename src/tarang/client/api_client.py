"""
Tarang API Client - HTTP client for the Orchestrator backend.

Handles communication with the hosted Tarang backend service.
Implements the thin-client architecture where:
- CLI: Sends context, executes returned instructions locally
- Backend: Reasoning/planning, returns instructions
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx
from pydantic import BaseModel


class SearchReplace(BaseModel):
    """Search and replace instruction."""
    search: str
    replace: str


class EditInstruction(BaseModel):
    """Edit instruction from backend.

    Supports three modes:
    - content: Full file write (create/overwrite)
    - search_replace: Find and replace text
    - diff: Apply unified diff patch
    """
    file: str
    diff: Optional[str] = None
    content: Optional[str] = None
    search_replace: Optional[SearchReplace] = None
    # Legacy fields for backwards compatibility
    search: Optional[str] = None
    replace: Optional[str] = None
    description: str = ""

    def get_search(self) -> Optional[str]:
        """Get search text from either format."""
        if self.search_replace:
            return self.search_replace.search
        return self.search

    def get_replace(self) -> Optional[str]:
        """Get replace text from either format."""
        if self.search_replace:
            return self.search_replace.replace
        return self.replace


class CommandInstruction(BaseModel):
    """Shell command instruction from backend."""
    command: str
    working_dir: Optional[str] = None
    description: str = ""
    require_confirmation: bool = False
    timeout: int = 60


# Alias for backwards compatibility
ShellCommand = CommandInstruction


class TarangResponse(BaseModel):
    """Response from Tarang backend.

    Response types:
    - message: Text response, no execution needed
    - edits: File edit instructions for CLI to execute
    - command: Shell command instructions for CLI to execute
    - error: Error occurred during processing
    """
    session_id: str
    type: str = "message"  # message, edits, command, error
    message: str = ""
    edits: List[EditInstruction] = []
    commands: List[CommandInstruction] = []
    command: Optional[str] = None  # Legacy single command field
    thought_process: Optional[str] = None
    error: Optional[str] = None
    recoverable: bool = True

    # Metadata
    model_used: Optional[str] = None
    tokens_used: int = 0


@dataclass
class LocalContext:
    """Local context to send to backend.

    Contains project information for the backend to reason about.
    """
    project_root: str
    skeleton: Dict[str, Any] = field(default_factory=dict)
    file_contents: Dict[str, str] = field(default_factory=dict)
    active_files: List[Dict[str, str]] = field(default_factory=list)
    git_status: Optional[str] = None
    history: List[Dict[str, str]] = field(default_factory=list)

    @property
    def cwd(self) -> str:
        """Alias for project_root (for backend compatibility)."""
        return self.project_root

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cwd": self.project_root,
            "skeleton": self.skeleton,
            "file_contents": self.file_contents,
            "history": self.history,
        }

    def add_file(self, file_path: str, content: str) -> None:
        """Add a file's content to the context."""
        self.file_contents[file_path] = content


class TarangAPIClient:
    """
    Thin client for Tarang Orchestrator API.

    Handles authentication, requests, and streaming responses.
    """

    DEFAULT_BASE_URL = "https://tarang-backend-intl-web-app-production.up.railway.app"

    def __init__(self, base_url: Optional[str] = None):
        self.base_url = base_url or self.DEFAULT_BASE_URL
        self.token: Optional[str] = None
        self.openrouter_key: Optional[str] = None

    def _build_headers(self) -> Dict[str, str]:
        """Build request headers."""
        headers = {
            "Content-Type": "application/json",
            "X-Tarang-Protocol-Version": "3.0",  # Updated protocol version
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if self.openrouter_key:
            headers["X-OpenRouter-Key"] = self.openrouter_key
        return headers

    async def execute(
        self,
        instruction: str,
        context: LocalContext,
        session_id: Optional[str] = None,
        file_content: Optional[str] = None,
        file_path: Optional[str] = None,
    ) -> TarangResponse:
        """
        Send instruction to Orchestrator and get response.

        Args:
            instruction: User's instruction/request
            context: Local project context (skeleton, file_contents)
            session_id: Optional session ID for continuity
            file_content: Optional focused file content
            file_path: Optional file path being edited

        Returns:
            TarangResponse with edits, commands, or messages
        """
        payload = {
            "message": instruction,
            "context": context.to_dict(),
            "session_id": session_id,
            "file_content": file_content,
            "file_path": file_path,
        }

        async with httpx.AsyncClient(timeout=300) as client:
            try:
                response = await client.post(
                    f"{self.base_url}/v2/execute",
                    json=payload,
                    headers=self._build_headers(),
                )
                response.raise_for_status()
                return TarangResponse.model_validate(response.json())

            except httpx.ConnectError:
                return TarangResponse(
                    session_id=session_id or "",
                    type="error",
                    error="Cannot reach Tarang server. Check your internet connection.",
                    recoverable=False,
                )
            except httpx.HTTPStatusError as e:
                error_detail = ""
                try:
                    error_data = e.response.json()
                    error_detail = error_data.get("detail", "")
                except Exception:
                    pass
                return TarangResponse(
                    session_id=session_id or "",
                    type="error",
                    error=f"Server error: {e.response.status_code}. {error_detail}",
                    recoverable=True,
                )
            except Exception as e:
                return TarangResponse(
                    session_id=session_id or "",
                    type="error",
                    error=str(e),
                    recoverable=True,
                )

    async def execute_stream(
        self,
        instruction: str,
        context: LocalContext,
        session_id: Optional[str] = None,
    ) -> AsyncIterator[TarangResponse]:
        """
        Stream responses from Orchestrator (SSE).

        For long-running tasks, the backend streams intermediate results.
        """
        payload = {
            "message": instruction,
            "context": context.to_dict(),
            "session_id": session_id,
        }

        async with httpx.AsyncClient(timeout=300) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/v2/execute/stream",
                json=payload,
                headers=self._build_headers(),
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data = json.loads(line[6:])
                        yield TarangResponse.model_validate(data)

    async def report_feedback(
        self,
        session_id: str,
        success: bool,
        applied_edits: Optional[List[str]] = None,
        error_message: Optional[str] = None,
        lint_output: Optional[str] = None,
    ) -> TarangResponse:
        """
        Report execution results back to Orchestrator.

        Args:
            session_id: Current session ID
            success: Whether changes applied successfully
            applied_edits: List of files that were edited
            error_message: Error message if failed
            lint_output: Lint output if there were errors
        """
        payload = {
            "session_id": session_id,
            "success": success,
            "error_message": error_message,
            "lint_output": lint_output,
        }

        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{self.base_url}/v2/feedback",
                json=payload,
                headers=self._build_headers(),
            )
            response.raise_for_status()
            return TarangResponse.model_validate(response.json())

    async def quick_ask(self, query: str) -> str:
        """
        Quick question without code generation.

        Args:
            query: Simple question

        Returns:
            Answer string
        """
        payload = {"query": query}

        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{self.base_url}/v2/quick",
                json=payload,
                headers=self._build_headers(),
            )
            response.raise_for_status()
            data = response.json()
            return data.get("answer", "")

    # ==========================================
    # SESSION TRACKING
    # ==========================================

    async def create_session(
        self,
        instruction: str,
        project_name: Optional[str] = None,
        project_path: Optional[str] = None,
    ) -> Optional[str]:
        """
        Create a new session in the backend.

        Args:
            instruction: User's instruction
            project_name: Name of the project
            project_path: Path to the project

        Returns:
            Session ID if successful, None otherwise
        """
        payload = {
            "instruction": instruction,
            "project_name": project_name,
            "project_path": project_path,
        }

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    f"{self.base_url}/v2/sessions",
                    json=payload,
                    headers=self._build_headers(),
                )
                response.raise_for_status()
                data = response.json()
                return data.get("id")
        except Exception:
            # Session tracking is optional, don't fail the request
            return None

    async def update_session(
        self,
        session_id: str,
        status: Optional[str] = None,
        current_thought: Optional[str] = None,
        error_message: Optional[str] = None,
        applied_files: Optional[List[str]] = None,
    ) -> bool:
        """
        Update session status.

        Args:
            session_id: Session ID to update
            status: New status (thinking, executing, done, failed, etc.)
            current_thought: Current thought/action
            error_message: Error message if failed
            applied_files: List of files that were modified

        Returns:
            True if successful
        """
        payload = {}
        if status:
            payload["status"] = status
        if current_thought:
            payload["current_thought"] = current_thought
        if error_message:
            payload["error_message"] = error_message
        if applied_files:
            payload["applied_files"] = applied_files

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.patch(
                    f"{self.base_url}/v2/sessions/{session_id}",
                    json=payload,
                    headers=self._build_headers(),
                )
                response.raise_for_status()
                return True
        except Exception:
            return False

    async def add_session_event(
        self,
        session_id: str,
        event_type: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Add an event to a session.

        Args:
            session_id: Session ID
            event_type: Type of event (thought, action, result, error)
            content: Event content
            metadata: Optional metadata

        Returns:
            True if successful
        """
        payload = {
            "type": event_type,
            "content": content,
            "metadata": metadata or {},
        }

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    f"{self.base_url}/v2/sessions/{session_id}/events",
                    json=payload,
                    headers=self._build_headers(),
                )
                response.raise_for_status()
                return True
        except Exception:
            return False

    async def update_session_usage(
        self,
        session_id: str,
        input_tokens: int,
        output_tokens: int,
        cached_tokens: int = 0,
    ) -> bool:
        """
        Update token usage for a session.

        Args:
            session_id: Session ID
            input_tokens: Number of input tokens
            output_tokens: Number of output tokens
            cached_tokens: Number of cached tokens

        Returns:
            True if successful
        """
        payload = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cached_tokens": cached_tokens,
        }

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    f"{self.base_url}/v2/sessions/{session_id}/usage",
                    json=payload,
                    headers=self._build_headers(),
                )
                response.raise_for_status()
                return True
        except Exception:
            return False


def collect_relevant_files(
    project_path: Path,
    instruction: str,
    skeleton: Dict[str, Any],
    max_files: int = 10,
    max_size: int = 50000,
) -> Dict[str, str]:
    """
    Collect relevant file contents based on instruction.

    This helps the backend have context for reasoning.

    Args:
        project_path: Project root path
        instruction: User instruction (used to find relevant files)
        skeleton: Project skeleton
        max_files: Maximum number of files to include
        max_size: Maximum total size in characters

    Returns:
        Dict of file_path -> content
    """
    file_contents = {}
    total_size = 0

    # Extract file paths mentioned in instruction
    mentioned_files = []
    instruction_lower = instruction.lower()

    def find_files_in_skeleton(node: Dict[str, Any], prefix: str = "") -> List[str]:
        """Recursively find all files in skeleton."""
        files = []
        for name, value in node.items():
            path = f"{prefix}/{name}".lstrip("/") if prefix else name
            if isinstance(value, dict):
                files.extend(find_files_in_skeleton(value, path))
            else:
                files.append(path)
        return files

    def parse_file_tree(file_tree: str) -> List[str]:
        """Parse ASCII file tree to extract file paths."""
        files = []
        path_stack = []

        for line in file_tree.split("\n"):
            if not line.strip():
                continue

            # Remove tree characters and get the name
            # Handles: "├── ", "└── ", "│   ", "    "
            clean_line = line
            for char in ["├", "└", "│", "─", " "]:
                clean_line = clean_line.replace(char, "")

            name = clean_line.strip()
            if not name:
                continue

            # Calculate depth based on indentation
            # Each level is typically 4 chars ("│   " or "    ")
            stripped = line.lstrip("│ ")
            indent = len(line) - len(stripped)
            depth = indent // 4

            # Adjust path stack
            while len(path_stack) > depth:
                path_stack.pop()

            if name.endswith("/"):
                # It's a directory
                path_stack.append(name.rstrip("/"))
            else:
                # It's a file
                full_path = "/".join(path_stack + [name]) if path_stack else name
                files.append(full_path)

        return files

    # Check if skeleton uses new format (file_tree string) or old format (nested dict)
    if "file_tree" in skeleton:
        all_files = parse_file_tree(skeleton.get("file_tree", ""))
    else:
        all_files = find_files_in_skeleton(skeleton)

    # Find files mentioned in instruction
    for file_path in all_files:
        file_name = Path(file_path).name.lower()
        if file_name in instruction_lower or file_path.lower() in instruction_lower:
            mentioned_files.append(file_path)

    # Also include key files
    key_files = [
        "package.json", "pyproject.toml", "requirements.txt",
        "tsconfig.json", "vite.config.ts", "next.config.js",
        "README.md", "src/App.tsx", "src/main.tsx", "src/index.ts",
        "app.py", "main.py", "__init__.py",
    ]

    for key_file in key_files:
        for file_path in all_files:
            if file_path.endswith(key_file):
                if file_path not in mentioned_files:
                    mentioned_files.append(file_path)

    # Read file contents
    for file_path in mentioned_files[:max_files]:
        full_path = project_path / file_path
        if full_path.exists() and full_path.is_file():
            try:
                content = full_path.read_text(encoding="utf-8", errors="replace")
                if len(content) + total_size <= max_size:
                    file_contents[file_path] = content
                    total_size += len(content)
            except Exception:
                pass

    return file_contents


class StreamingEvent:
    """Event received from WebSocket stream."""

    def __init__(self, event_type: str, data: Dict[str, Any]):
        self.type = event_type
        self.data = data

    def __repr__(self) -> str:
        return f"StreamingEvent(type={self.type}, data={self.data})"


class TarangStreamingClient:
    """WebSocket client for real-time streaming from Tarang backend."""

    def __init__(self, base_url: Optional[str] = None):
        """Initialize the streaming client.

        Args:
            base_url: Backend base URL (will convert http to ws)
        """
        http_url = base_url or TarangAPIClient.DEFAULT_BASE_URL
        # Convert http(s) to ws(s)
        if http_url.startswith("https://"):
            self.ws_url = http_url.replace("https://", "wss://")
        elif http_url.startswith("http://"):
            self.ws_url = http_url.replace("http://", "ws://")
        else:
            self.ws_url = f"wss://{http_url}"

        self.token: Optional[str] = None
        self.openrouter_key: Optional[str] = None

    async def stream_execute(
        self,
        instruction: str,
        context: LocalContext,
        session_id: Optional[str] = None,
    ) -> AsyncIterator[StreamingEvent]:
        """
        Execute instruction with streaming events.

        Yields events as they arrive from the backend.

        Args:
            instruction: User instruction
            context: Project context
            session_id: Optional session ID

        Yields:
            StreamingEvent for each event from backend
        """
        import websockets

        # Build WebSocket URL with auth
        ws_endpoint = f"{self.ws_url}/v2/ws/execute"
        params = []
        if self.token:
            params.append(f"token={self.token}")
        if self.openrouter_key:
            params.append(f"openrouter_key={self.openrouter_key}")
        if params:
            ws_endpoint = f"{ws_endpoint}?{'&'.join(params)}"

        try:
            async with websockets.connect(
                ws_endpoint,
                ping_interval=30,
                ping_timeout=10,
                close_timeout=5,
            ) as websocket:
                # Wait for connected event
                response = await websocket.recv()
                event = json.loads(response)
                yield StreamingEvent(event.get("type", "unknown"), event.get("data", {}))

                # Send execute request
                execute_request = {
                    "type": "execute",
                    "message": instruction,
                    "context": {
                        "skeleton": context.skeleton,
                        "cwd": context.cwd,
                        "history": context.history,
                        "file_contents": context.file_contents,
                    },
                }
                await websocket.send(json.dumps(execute_request))

                # Stream events
                while True:
                    try:
                        response = await websocket.recv()
                        event = json.loads(response)
                        event_type = event.get("type", "unknown")
                        event_data = event.get("data", {})

                        yield StreamingEvent(event_type, event_data)

                        # Check for terminal events
                        if event_type in ("complete", "error"):
                            break

                    except websockets.exceptions.ConnectionClosed:
                        break

        except Exception as e:
            yield StreamingEvent("error", {"message": str(e)})

    async def send_approval(
        self,
        websocket,
        approved: bool,
    ) -> None:
        """Send approval response for an edit."""
        import websockets

        await websocket.send(json.dumps({
            "type": "approve",
            "approved": approved,
        }))
