"""
Tarang API Client - HTTP client for the Orchestrator backend.

Handles communication with the hosted Tarang backend service.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx
from pydantic import BaseModel


class EditInstruction(BaseModel):
    """Edit instruction from backend."""
    file: str
    diff: Optional[str] = None
    content: Optional[str] = None
    search: Optional[str] = None
    replace: Optional[str] = None
    description: str = ""


class ShellCommand(BaseModel):
    """Shell command from backend."""
    command: str
    working_dir: str = "."
    timeout: int = 30
    description: str = ""


class TarangResponse(BaseModel):
    """Response from Tarang backend."""
    session_id: str
    type: str = "message"  # message, edits, command, error, done
    message: str = ""
    edits: List[EditInstruction] = []
    commands: List[ShellCommand] = []
    thought_process: Optional[str] = None
    error: Optional[str] = None
    recoverable: bool = True


@dataclass
class LocalContext:
    """Local context to send to backend."""
    project_root: str
    skeleton: Dict[str, Any] = field(default_factory=dict)
    active_files: List[Dict[str, str]] = field(default_factory=list)
    git_status: Optional[str] = None
    history: List[Dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cwd": self.project_root,
            "skeleton": self.skeleton,
            "history": self.history,
        }


class TarangAPIClient:
    """
    Thin client for Tarang Orchestrator API.

    Handles authentication, requests, and streaming responses.
    """

    DEFAULT_BASE_URL = "https://api.devtarang.ai"

    def __init__(self, base_url: Optional[str] = None):
        self.base_url = base_url or self.DEFAULT_BASE_URL
        self.token: Optional[str] = None
        self.openrouter_key: Optional[str] = None

    def _build_headers(self) -> Dict[str, str]:
        """Build request headers."""
        headers = {
            "Content-Type": "application/json",
            "X-Tarang-Protocol-Version": "2.0",
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
            context: Local project context
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
                return TarangResponse(
                    session_id=session_id or "",
                    type="error",
                    error=f"Server error: {e.response.status_code}",
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
