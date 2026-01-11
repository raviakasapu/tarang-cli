"""
Message Handlers for WebSocket Events.

Handles different event types from the backend:
- UI updates (thinking, progress, milestones)
- Tool requests and approvals
- Completion and errors

Integrates with Rich console for beautiful output.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskID, TimeElapsedColumn
from rich.table import Table
from rich.syntax import Syntax

from tarang.ws.client import EventType, WSEvent
from tarang.ws.executor import ToolExecutor

logger = logging.getLogger(__name__)


@dataclass
class ExecutionState:
    """Tracks execution state for UI."""
    current_phase: int = 0
    total_phases: int = 0
    phase_name: str = ""
    milestones: List[str] = field(default_factory=list)
    completed_milestones: List[str] = field(default_factory=list)
    in_progress_milestone: str = ""
    files_changed: List[str] = field(default_factory=list)
    error: Optional[str] = None
    job_id: Optional[str] = None
    thinking_message: str = ""


# Type for approval UI callback
ApprovalUICallback = Callable[[str, str, Dict[str, Any]], bool]


class MessageHandlers:
    """
    Handles WebSocket messages and updates UI.

    Usage:
        handlers = MessageHandlers(
            console=console,
            executor=executor,
            on_approval=lambda tool, desc, args: ui.confirm(desc),
        )

        async for event in ws_client.execute(instruction, cwd):
            await handlers.handle(event, ws_client)
    """

    def __init__(
        self,
        console: Console,
        executor: ToolExecutor,
        on_approval: Optional[ApprovalUICallback] = None,
        verbose: bool = False,
        auto_approve: bool = False,
    ):
        self.console = console
        self.executor = executor
        self.on_approval = on_approval
        self.verbose = verbose
        self.auto_approve = auto_approve

        self.state = ExecutionState()
        self._progress: Optional[Progress] = None
        self._phase_task_id: Optional[TaskID] = None
        self._milestone_task_id: Optional[TaskID] = None
        self._live: Optional[Live] = None

    def _create_progress_display(self) -> Progress:
        """Create a progress display with phase and milestone tracking."""
        return Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.fields[phase_name]}[/bold blue]"),
            BarColumn(bar_width=30),
            TextColumn("{task.percentage:.0f}%"),
            TextColumn("[dim]{task.fields[milestone]}[/dim]"),
            TimeElapsedColumn(),
            console=self.console,
            transient=False,
        )

    def _build_status_panel(self) -> Panel:
        """Build a status panel showing current progress."""
        if not self.state.phase_name:
            return Panel(
                f"[dim cyan]{self.state.thinking_message or 'Initializing...'}[/dim cyan]",
                title="[bold] Status[/bold]",
                border_style="blue",
            )

        # Build milestone list with checkboxes
        milestone_lines = []
        for m in self.state.milestones:
            if m in self.state.completed_milestones:
                milestone_lines.append(f"  [green][/green] {m}")
            elif m == self.state.in_progress_milestone:
                milestone_lines.append(f"  [yellow][/yellow] {m}...")
            else:
                milestone_lines.append(f"  [dim][ ][/dim] {m}")

        phase_progress = f"Phase {self.state.current_phase}/{self.state.total_phases}"
        completed = len(self.state.completed_milestones)
        total = len(self.state.milestones)

        content = f"[bold]{self.state.phase_name}[/bold] ({phase_progress})\n"
        content += "\n".join(milestone_lines) if milestone_lines else ""

        if self.state.files_changed:
            content += f"\n\n[dim]Files: {len(self.state.files_changed)}[/dim]"

        return Panel(
            content,
            title=f"[bold blue] {self.state.phase_name}[/bold blue]",
            border_style="blue",
            subtitle=f"[dim]{completed}/{total} milestones[/dim]",
        )

    async def handle(self, event: WSEvent, ws_client) -> bool:
        """
        Handle a WebSocket event.

        Args:
            event: The event to handle
            ws_client: WebSocket client for sending responses

        Returns:
            True if execution should continue, False to stop
        """
        handler = getattr(self, f"_handle_{event.type.value}", None)

        if handler:
            return await handler(event, ws_client)
        else:
            if self.verbose:
                logger.debug(f"Unhandled event type: {event.type}")
            return True

    async def _handle_connected(self, event: WSEvent, ws_client) -> bool:
        """Handle connection established."""
        session_id = event.data.get("session_id", "")
        if self.verbose:
            self.console.print(f"[dim]Connected: {session_id}[/dim]")
        return True

    async def _handle_thinking(self, event: WSEvent, ws_client) -> bool:
        """Handle thinking/processing status."""
        message = event.data.get("message", "Thinking...")
        self.state.thinking_message = message
        self.console.print(f"[dim cyan]{message}[/dim cyan]")
        return True

    async def _handle_phase_start(self, event: WSEvent, ws_client) -> bool:
        """Handle new phase starting."""
        phase = event.data.get("phase", 0)
        total = event.data.get("total_phases", 1)
        name = event.data.get("name", "")
        milestones = event.data.get("milestones", [])

        self.state.current_phase = phase
        self.state.total_phases = total
        self.state.phase_name = name
        self.state.milestones = milestones
        self.state.completed_milestones = []
        self.state.in_progress_milestone = ""

        # Calculate overall progress
        phases_done = phase - 1
        progress_percent = int((phases_done / total) * 100) if total > 0 else 0

        self.console.print()

        # Draw progress bar
        bar_width = 30
        filled = int(bar_width * phases_done / total) if total > 0 else 0
        bar = "" * filled + "" * (bar_width - filled)

        self.console.print(
            f"[bold blue]Phase {phase}/{total}[/bold blue] [dim]{bar}[/dim] {progress_percent}%"
        )
        self.console.print(
            Panel(
                f"[bold]{name}[/bold]",
                border_style="blue",
            )
        )

        if milestones:
            for m in milestones:
                self.console.print(f"  [dim][ ][/dim] {m}")

        return True

    async def _handle_milestone_update(self, event: WSEvent, ws_client) -> bool:
        """Handle milestone status change."""
        milestone = event.data.get("milestone", "")
        status = event.data.get("status", "")

        if status == "completed":
            if milestone not in self.state.completed_milestones:
                self.state.completed_milestones.append(milestone)
            if self.state.in_progress_milestone == milestone:
                self.state.in_progress_milestone = ""
            self.console.print(f"  [green][/green] {milestone}")
        elif status == "in_progress":
            self.state.in_progress_milestone = milestone
            self.console.print(f"  [yellow][/yellow] {milestone}...")
        elif status == "failed":
            self.state.in_progress_milestone = ""
            self.console.print(f"  [red][/red] {milestone}")

        return True

    async def _handle_progress(self, event: WSEvent, ws_client) -> bool:
        """Handle progress update."""
        percent = event.data.get("percent", 0)
        message = event.data.get("message", "")
        phase = event.data.get("phase", 0)
        total = event.data.get("total_phases", 1)

        if self.verbose:
            self.console.print(
                f"[dim]Progress: {percent}% - {message}[/dim]"
            )

        return True

    async def _handle_tool_request(self, event: WSEvent, ws_client) -> bool:
        """Handle tool execution request from backend."""
        request_id = event.request_id or event.data.get("request_id", "")
        tool = event.data.get("tool", "")
        args = event.data.get("args", {})

        # Show tool call with icon
        tool_icons = {
            "read_file": "",
            "list_files": "",
            "search_files": "",
            "write_file": "",
            "edit_file": "",
            "delete_file": "",
            "shell": "",
            "get_file_info": "",
        }
        icon = tool_icons.get(tool, "")

        # Build display info
        if tool == "read_file":
            display = f"{args.get('file_path', '')}"
        elif tool == "list_files":
            path = args.get('path', '.')
            pattern = args.get('pattern', '')
            display = f"{path}" + (f" ({pattern})" if pattern else "")
        elif tool == "search_files":
            display = f"'{args.get('pattern', '')}'"
        elif tool == "write_file":
            display = f"{args.get('file_path', '')}"
        elif tool == "edit_file":
            display = f"{args.get('file_path', '')}"
        elif tool == "shell":
            cmd = args.get('command', '')[:50]
            display = f"`{cmd}`"
        else:
            display = ""

        self.console.print(f"  [dim cyan]{icon} {tool}[/dim cyan] {display}")

        try:
            # Execute tool locally
            result = await self.executor.execute(tool, args)

            # Send result back
            await ws_client.send_tool_result(request_id, result)

            # Track file changes
            if tool in ("write_file", "edit_file") and result.get("success"):
                file_path = result.get("file_path", args.get("file_path", ""))
                if file_path and file_path not in self.state.files_changed:
                    self.state.files_changed.append(file_path)

            # Show result summary for verbose mode
            if self.verbose:
                if result.get("error"):
                    self.console.print(f"    [red]Error: {result['error']}[/red]")
                elif tool == "read_file":
                    lines = result.get("lines_returned", 0)
                    self.console.print(f"    [dim]Read {lines} lines[/dim]")
                elif tool == "list_files":
                    count = len(result.get("files", []))
                    self.console.print(f"    [dim]Found {count} files[/dim]")
                elif tool == "search_files":
                    count = result.get("total_matches", 0)
                    self.console.print(f"    [dim]Found {count} matches[/dim]")

        except Exception as e:
            logger.exception(f"Tool execution error: {tool}")
            self.console.print(f"    [red]Error: {e}[/red]")
            await ws_client.send_tool_error(request_id, str(e))

        return True

    async def _handle_approval_request(self, event: WSEvent, ws_client) -> bool:
        """Handle approval request for destructive operations."""
        request_id = event.request_id or event.data.get("request_id", "")
        tool = event.data.get("tool", "")
        args = event.data.get("args", {})
        description = event.data.get("description", "")

        # Show what's being requested
        self._show_approval_request(tool, args, description)

        # Auto-approve if flag is set
        if self.auto_approve:
            approved = True
            self.console.print("[dim green]Auto-approved[/dim green]")
        elif self.on_approval:
            approved = self.on_approval(tool, description, args)
        else:
            # Default: ask via console
            self.console.print("[yellow]Approve this operation?[/yellow] (y/n): ", end="")
            response = input().strip().lower()
            approved = response in ("y", "yes")

        if approved:
            # Execute and send result
            try:
                result = await self.executor.execute(tool, args)
                await ws_client.send_tool_result(request_id, result)

                # Track file changes
                if tool in ("write_file", "edit_file") and result.get("success"):
                    file_path = result.get("file_path", args.get("file_path", ""))
                    if file_path and file_path not in self.state.files_changed:
                        self.state.files_changed.append(file_path)
                        self.console.print(f"  [green] Applied: {file_path}[/green]")

            except Exception as e:
                await ws_client.send_tool_error(request_id, str(e))
        else:
            # Send rejection
            await ws_client.send_approval(request_id, False)
            self.console.print("  [yellow] Skipped[/yellow]")

        return True

    def _get_language_from_path(self, file_path: str) -> str:
        """Detect language from file extension for syntax highlighting."""
        ext_map = {
            ".py": "python",
            ".js": "javascript",
            ".jsx": "jsx",
            ".ts": "typescript",
            ".tsx": "tsx",
            ".json": "json",
            ".yaml": "yaml",
            ".yml": "yaml",
            ".md": "markdown",
            ".html": "html",
            ".css": "css",
            ".scss": "scss",
            ".sql": "sql",
            ".sh": "bash",
            ".bash": "bash",
            ".zsh": "bash",
            ".go": "go",
            ".rs": "rust",
            ".rb": "ruby",
            ".java": "java",
            ".kt": "kotlin",
            ".swift": "swift",
            ".c": "c",
            ".cpp": "cpp",
            ".h": "c",
            ".hpp": "cpp",
        }
        import os
        _, ext = os.path.splitext(file_path)
        return ext_map.get(ext.lower(), "text")

    def _show_approval_request(
        self,
        tool: str,
        args: Dict[str, Any],
        description: str,
    ):
        """Display approval request with details and syntax highlighting."""
        self.console.print()

        if tool == "write_file":
            file_path = args.get("file_path", "")
            content = args.get("content", "")
            language = self._get_language_from_path(file_path)

            self.console.print(f"[bold cyan]╭─ ✏️  Create: {file_path}[/bold cyan]")
            if description:
                self.console.print(f"[bold cyan]│[/bold cyan]  [dim]{description}[/dim]")

            # Show syntax-highlighted preview
            lines = content.split("\n")
            preview_lines = lines[:20]
            preview = "\n".join(preview_lines)

            try:
                syntax = Syntax(
                    preview,
                    language,
                    theme="monokai",
                    line_numbers=True,
                    word_wrap=True,
                )
                self.console.print(Panel(
                    syntax,
                    border_style="green",
                    title="[green]+ New File[/green]",
                    subtitle=f"[dim]{len(lines)} lines[/dim]" if len(lines) > 20 else None,
                ))
            except Exception:
                # Fallback to simple display
                for line in preview_lines:
                    self.console.print(f"  [green]+ {line}[/green]")

            if len(lines) > 20:
                self.console.print(f"  [dim]... and {len(lines) - 20} more lines[/dim]")

        elif tool == "edit_file":
            file_path = args.get("file_path", "")
            search = args.get("search", "")
            replace = args.get("replace", "")
            language = self._get_language_from_path(file_path)

            self.console.print(f"[bold cyan]╭─ ✏️  Edit: {file_path}[/bold cyan]")
            if description:
                self.console.print(f"[bold cyan]│[/bold cyan]  [dim]{description}[/dim]")

            # Build unified diff display
            search_lines = search.split("\n")
            replace_lines = replace.split("\n")

            # Show removal
            if search_lines:
                self.console.print("[bold cyan]│[/bold cyan]")
                self.console.print("[bold cyan]│[/bold cyan] [red]Remove:[/red]")
                for line in search_lines[:10]:
                    self.console.print(f"[bold cyan]│[/bold cyan]   [red]- {line}[/red]")
                if len(search_lines) > 10:
                    self.console.print(f"[bold cyan]│[/bold cyan]   [dim]... ({len(search_lines)} lines total)[/dim]")

            # Show addition
            if replace_lines:
                self.console.print("[bold cyan]│[/bold cyan]")
                self.console.print("[bold cyan]│[/bold cyan] [green]Add:[/green]")
                for line in replace_lines[:10]:
                    self.console.print(f"[bold cyan]│[/bold cyan]   [green]+ {line}[/green]")
                if len(replace_lines) > 10:
                    self.console.print(f"[bold cyan]│[/bold cyan]   [dim]... ({len(replace_lines)} lines total)[/dim]")

            self.console.print("[bold cyan]╰─[/bold cyan]")

        elif tool == "delete_file":
            file_path = args.get("file_path", "")
            self.console.print(f"[bold red]╭─ 🗑️  Delete: {file_path}[/bold red]")
            if description:
                self.console.print(f"[bold red]│[/bold red]  [dim]{description}[/dim]")
            self.console.print("[bold red]╰─ This action cannot be undone![/bold red]")

        elif tool == "shell":
            command = args.get("command", "")
            cwd = args.get("cwd", "")
            timeout = args.get("timeout", 60)

            self.console.print(f"[bold yellow]╭─ 💻 Shell Command[/bold yellow]")
            if description:
                self.console.print(f"[bold yellow]│[/bold yellow]  [dim]{description}[/dim]")
            self.console.print(f"[bold yellow]│[/bold yellow]")

            try:
                syntax = Syntax(command, "bash", theme="monokai")
                self.console.print(Panel(
                    syntax,
                    border_style="yellow",
                    title="[yellow]Command[/yellow]",
                ))
            except Exception:
                self.console.print(f"[bold yellow]│[/bold yellow]  $ {command}")

            if cwd:
                self.console.print(f"[bold yellow]│[/bold yellow]  [dim]Directory: {cwd}[/dim]")
            self.console.print(f"[bold yellow]╰─[/bold yellow] [dim]Timeout: {timeout}s[/dim]")

        else:
            self.console.print(f"[bold]╭─ {tool}[/bold]")
            if description:
                self.console.print(f"[bold]│[/bold]  [dim]{description}[/dim]")
            self.console.print(f"[bold]╰─[/bold]")

    async def _handle_complete(self, event: WSEvent, ws_client) -> bool:
        """Handle execution completed."""
        summary = event.data.get("summary", "Completed")
        files = event.data.get("files_changed", [])
        phases = event.data.get("phases_completed", 0)
        milestones = event.data.get("milestones_completed", 0)

        self.console.print()
        self.console.print(
            Panel(
                f"[green]{summary}[/green]\n\n"
                f"[dim]Files changed: {len(files)}[/dim]\n"
                f"[dim]Phases: {phases} | Milestones: {milestones}[/dim]",
                title="[bold green] Complete[/bold green]",
                border_style="green",
            )
        )

        if files:
            for f in files[:10]:
                self.console.print(f"  [dim]{f}[/dim]")
            if len(files) > 10:
                self.console.print(f"  [dim]... and {len(files) - 10} more[/dim]")

        return False  # Stop iteration

    async def _handle_error(self, event: WSEvent, ws_client) -> bool:
        """Handle error event."""
        message = event.data.get("message", "Unknown error")
        recoverable = event.data.get("recoverable", True)

        self.state.error = message

        self.console.print()
        self.console.print(
            Panel(
                f"[red]{message}[/red]",
                title="[bold red] Error[/bold red]",
                border_style="red",
            )
        )

        return False  # Stop iteration

    async def _handle_paused(self, event: WSEvent, ws_client) -> bool:
        """Handle job paused (e.g., disconnect)."""
        job_id = event.data.get("job_id", "")
        resume_cmd = event.data.get("resume_command", "")
        phase = event.data.get("phase", 0)
        milestone = event.data.get("milestone", "")

        self.console.print()
        self.console.print(
            Panel(
                f"[yellow]Job paused at phase {phase}[/yellow]\n"
                f"[dim]Milestone: {milestone}[/dim]\n\n"
                f"[cyan]Resume with:[/cyan]\n"
                f"  {resume_cmd or f'tarang resume {job_id}'}",
                title="[bold yellow] Paused[/bold yellow]",
                border_style="yellow",
            )
        )

        return False  # Stop iteration

    async def _handle_heartbeat(self, event: WSEvent, ws_client) -> bool:
        """Handle heartbeat - just acknowledge."""
        return True

    async def _handle_pong(self, event: WSEvent, ws_client) -> bool:
        """Handle pong response to our ping."""
        return True

    def get_summary(self) -> Dict[str, Any]:
        """Get execution summary."""
        return {
            "files_changed": self.state.files_changed,
            "phases_completed": self.state.current_phase,
            "milestones_completed": len(self.state.completed_milestones),
            "error": self.state.error,
        }
