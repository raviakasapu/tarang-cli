"""Tarang session management."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.syntax import Syntax

from tarang.core.config import get_api_base_url
from tarang.tools.skeleton import SkeletonGenerator
from tarang.tools.diff_applicator import DiffApplicator
from tarang.tools.shadow_linter import ShadowLinter

console = Console()


@dataclass
class TarangSession:
    """Manages an interactive Tarang session."""

    config: dict[str, Any]
    no_lint: bool = False
    dry_run: bool = False
    verbose: bool = False
    session_id: str | None = None
    conversation_history: list[dict[str, str]] = field(default_factory=list)
    _client: httpx.AsyncClient | None = None
    _skeleton_generator: SkeletonGenerator | None = None
    _diff_applicator: DiffApplicator | None = None
    _shadow_linter: ShadowLinter | None = None

    def __post_init__(self):
        """Initialize session components."""
        self._skeleton_generator = SkeletonGenerator(Path.cwd())
        self._diff_applicator = DiffApplicator(dry_run=self.dry_run)
        if not self.no_lint:
            self._shadow_linter = ShadowLinter(Path.cwd())

    @property
    def client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=get_api_base_url(),
                timeout=120.0,
                headers=self._get_headers(),
            )
        return self._client

    def _get_headers(self) -> dict[str, str]:
        """Get request headers including auth."""
        headers = {"Content-Type": "application/json"}

        if api_key := self.config.get("api_key"):
            headers["Authorization"] = f"Bearer {api_key}"

        if openrouter_key := self.config.get("openrouter_key"):
            headers["X-OpenRouter-Key"] = openrouter_key

        return headers

    async def process_request(self, user_input: str):
        """Process a user request.

        Args:
            user_input: The user's natural language request
        """
        self.conversation_history.append({"role": "user", "content": user_input})

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
            transient=True,
        ) as progress:
            # Step 1: Generate project skeleton
            task = progress.add_task("Analyzing project structure...", total=None)

            skeleton = await self._skeleton_generator.generate()

            # Step 2: Send to backend
            progress.update(task, description="Thinking...")

            try:
                response = await self._send_request(user_input, skeleton)
            except httpx.HTTPError as e:
                console.print(f"\n[red]Error communicating with backend:[/red] {e}")
                return

            # Step 3: Process response
            progress.update(task, description="Processing response...")

            if "error" in response:
                console.print(f"\n[red]Error:[/red] {response['error']}")
                return

            # Handle different response types
            if response.get("type") == "message":
                self._display_message(response)
            elif response.get("type") == "edits":
                await self._apply_edits(response, progress, task)
            elif response.get("type") == "command":
                await self._execute_command(response)

        self.conversation_history.append({
            "role": "assistant",
            "content": response.get("message", ""),
        })

    async def _send_request(
        self, user_input: str, skeleton: dict[str, Any]
    ) -> dict[str, Any]:
        """Send request to the Tarang backend."""
        payload = {
            "session_id": self.session_id,
            "message": user_input,
            "context": {
                "skeleton": skeleton,
                "cwd": str(Path.cwd()),
                "history": self.conversation_history[-10:],  # Last 10 messages
            },
        }

        if project_config := self.config.get("project"):
            payload["context"]["project"] = project_config

        response = await self.client.post("/v2/execute", json=payload)
        response.raise_for_status()

        data = response.json()

        # Update session ID if provided
        if "session_id" in data:
            self.session_id = data["session_id"]

        return data

    def _display_message(self, response: dict[str, Any]):
        """Display a text message response."""
        message = response.get("message", "")
        if message:
            console.print(f"\n{message}\n")

    async def _apply_edits(
        self, response: dict[str, Any], progress: Progress, task: int
    ):
        """Apply code edits from the response."""
        edits = response.get("edits", [])
        if not edits:
            console.print("\n[dim]No changes to apply.[/dim]")
            return

        for i, edit in enumerate(edits, 1):
            file_path = edit.get("file")
            progress.update(task, description=f"Applying edit {i}/{len(edits)}: {file_path}")

            if self.verbose:
                self._show_diff_preview(edit)

            # Apply the edit
            success = await self._diff_applicator.apply(edit)

            if not success:
                console.print(f"\n[red]Failed to apply edit to {file_path}[/red]")
                continue

            # Shadow lint if enabled
            if self._shadow_linter and not self.no_lint:
                progress.update(task, description=f"Linting {file_path}...")
                lint_result = await self._shadow_linter.check(file_path)

                if not lint_result.passed:
                    console.print(
                        f"\n[yellow]Lint warning in {file_path}:[/yellow]\n"
                        f"{lint_result.message}"
                    )

        if self.dry_run:
            console.print("\n[yellow]Dry run mode - no changes were applied.[/yellow]")
        else:
            console.print(f"\n[green]Applied {len(edits)} edit(s) successfully.[/green]")

    def _show_diff_preview(self, edit: dict[str, Any]):
        """Show a preview of the diff."""
        diff = edit.get("diff", "")
        if diff:
            syntax = Syntax(diff, "diff", theme="monokai", line_numbers=True)
            console.print(Panel(syntax, title=f"Changes: {edit.get('file', 'unknown')}", border_style="cyan"))

    async def _execute_command(self, response: dict[str, Any]):
        """Execute a shell command."""
        import subprocess

        command = response.get("command", "")
        if not command:
            return

        console.print(f"\n[dim]Executing:[/dim] {command}")

        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=60,
            )

            if result.stdout:
                console.print(result.stdout)
            if result.stderr:
                console.print(f"[yellow]{result.stderr}[/yellow]")

            if result.returncode != 0:
                console.print(f"\n[yellow]Command exited with code {result.returncode}[/yellow]")

        except subprocess.TimeoutExpired:
            console.print("\n[red]Command timed out after 60 seconds[/red]")
        except Exception as e:
            console.print(f"\n[red]Error executing command:[/red] {e}")

    async def cleanup(self):
        """Clean up session resources."""
        if self._client:
            await self._client.aclose()
            self._client = None
