"""
Shared output formatter for consistent tool display across CLI.

This module provides a unified interface for displaying tool execution,
approvals, results, and diffs. Used by both SSE (stream.py) and WebSocket
(ws/handlers.py) implementations.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

from dataclasses import dataclass, field
from typing import List

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text
from rich.table import Table


@dataclass
class ToolCall:
    """Record of a single tool call."""
    tool: str
    args: Dict[str, Any]
    result: Optional[Dict[str, Any]] = None
    duration_ms: int = 0


@dataclass
class ToolCallTracker:
    """
    Tracks all tool calls during a session for visibility.

    Provides:
    - Running count of calls by tool type
    - Files read/written tracking
    - Context accumulation stats
    - Summary display at end of session
    """
    console: Console
    calls: List[ToolCall] = field(default_factory=list)
    files_read: List[str] = field(default_factory=list)
    files_written: List[str] = field(default_factory=list)
    total_lines_read: int = 0
    total_chunks_retrieved: int = 0
    search_queries: List[str] = field(default_factory=list)

    def record_call(
        self,
        tool: str,
        args: Dict[str, Any],
        result: Optional[Dict[str, Any]] = None,
        duration_ms: int = 0,
    ) -> None:
        """Record a tool call and update stats."""
        call = ToolCall(tool=tool, args=args, result=result, duration_ms=duration_ms)
        self.calls.append(call)

        # Track files read
        if tool == "read_file" and result:
            file_path = args.get("file_path", "")
            if file_path and file_path not in self.files_read:
                self.files_read.append(file_path)
            self.total_lines_read += result.get("lines", 0)

        # Track batch reads
        elif tool == "read_files" and result:
            for f in result.get("files", []):
                path = f.get("path", "")
                if path and path not in self.files_read:
                    self.files_read.append(path)
                self.total_lines_read += f.get("lines", 0)

        # Track search_code results
        elif tool == "search_code" and result:
            query = args.get("query", "")
            if query:
                self.search_queries.append(query)
            self.total_chunks_retrieved += len(result.get("chunks", []))

        # Track files written
        elif tool in ("write_file", "edit_file") and result and result.get("success"):
            file_path = args.get("file_path", "")
            if file_path and file_path not in self.files_written:
                self.files_written.append(file_path)

    def show_progress(self, tool: str, args: Dict[str, Any]) -> None:
        """Show compact progress line for current tool."""
        call_num = len(self.calls) + 1

        if tool == "read_file":
            target = args.get("file_path", "")[-40:]
            self.console.print(f"  [dim]#{call_num} 📖 {target}[/dim]")
        elif tool == "read_files":
            count = len(args.get("file_paths", []))
            self.console.print(f"  [dim]#{call_num} 📖 Reading {count} files...[/dim]")
        elif tool == "search_code":
            query = args.get("query", "")[:30]
            self.console.print(f"  [dim]#{call_num} 🔎 Searching: \"{query}\"[/dim]")
        elif tool == "list_files":
            path = args.get("path", ".")
            self.console.print(f"  [dim]#{call_num} 📂 Listing {path}[/dim]")
        elif tool == "shell":
            cmd = args.get("command", "")[:40].replace("\n", " ")
            self.console.print(f"  [dim]#{call_num} 💻 {cmd}[/dim]")

    def show_summary(self) -> None:
        """Show summary of all tool calls at end of session."""
        if not self.calls:
            return

        # Count by tool type
        tool_counts: Dict[str, int] = {}
        for call in self.calls:
            tool_counts[call.tool] = tool_counts.get(call.tool, 0) + 1

        # Build summary table
        table = Table(title="Tool Call Summary", box=None, padding=(0, 1))
        table.add_column("Tool", style="cyan")
        table.add_column("Count", justify="right")
        table.add_column("Details", style="dim")

        for tool, count in sorted(tool_counts.items()):
            details = ""
            if tool == "read_file":
                details = f"{self.total_lines_read} lines"
            elif tool == "search_code":
                details = f"{self.total_chunks_retrieved} chunks"
            table.add_row(tool, str(count), details)

        self.console.print()
        self.console.print(table)

        # Files summary
        if self.files_read:
            self.console.print(f"\n[dim]Files read ({len(self.files_read)}):[/dim]")
            for f in self.files_read[:10]:
                self.console.print(f"  [dim]• {f}[/dim]")
            if len(self.files_read) > 10:
                self.console.print(f"  [dim]... and {len(self.files_read) - 10} more[/dim]")

        if self.files_written:
            self.console.print(f"\n[green]Files modified ({len(self.files_written)}):[/green]")
            for f in self.files_written:
                self.console.print(f"  [green]• {f}[/green]")


@dataclass
class PhaseStatus:
    """Status of a single phase in the plan."""
    name: str
    worker: str = ""
    goals: str = ""
    status: str = "pending"  # pending, running, completed, failed
    current_step: str = ""  # Current worker task being executed


@dataclass
class PhaseTracker:
    """
    Tracks execution progress through phases and worker steps.

    Provides a live-updating checklist view of:
    - PRD phases from orchestrator
    - Worker steps from architect
    - Tool calls within each step
    """
    console: Console
    phases: List[PhaseStatus] = field(default_factory=list)
    prd_title: str = ""
    prd_requirements: List[str] = field(default_factory=list)
    current_phase_index: int = 0
    current_worker: str = ""
    tool_count: int = 0
    project_name: str = ""  # For multi-project disambiguation

    def set_plan(self, plan: dict) -> None:
        """Initialize tracker with orchestrator's plan. Renders ONCE."""
        # Skip if plan already set (prevent duplicate renders)
        if self.phases:
            return

        prd = plan.get("prd", {})
        phases = plan.get("phases", [])

        self.prd_title = prd.get("title", "")
        self.prd_requirements = prd.get("requirements", [])

        self.phases = [
            PhaseStatus(
                name=p.get("name", f"Phase {i+1}"),
                worker=p.get("worker", ""),
                goals=p.get("goals", ""),  # Store full goals
            )
            for i, p in enumerate(phases)
        ]
        self.current_phase_index = 0
        self.render()

    def set_worker_tasks(self, tasks: list) -> None:
        """Set architect's decomposed tasks as phases."""
        self.phases = []
        for i, t in enumerate(tasks):
            if isinstance(t, dict):
                worker = t.get("worker", "coder")
                goals = t.get("goals", "")
                # For architect tasks, use worker name + brief goal as the name
                name = f"{worker}: {goals[:50]}..." if len(goals) > 50 else f"{worker}: {goals}" if goals else f"Step {i+1}"
            else:
                worker = "coder"
                goals = str(t)
                name = f"Step {i+1}: {goals[:50]}..." if len(goals) > 50 else f"Step {i+1}: {goals}"

            self.phases.append(PhaseStatus(
                name=name,
                worker=worker,
                goals="",  # Don't show goals separately for architect tasks
            ))
        self.current_phase_index = 0
        self.render()

    def start_phase(self, phase_name: str) -> None:
        """Mark a phase as running (with render)."""
        self.update_phase_status(phase_name, "running")
        self.render()

    def start_worker(self, worker: str, task: str = "") -> None:
        """Mark current phase's worker as active (with render)."""
        self.update_worker_status(worker, task, "running")
        self.render()

    def complete_worker(self, worker: str) -> None:
        """Mark worker complete and advance to next phase (with render)."""
        self.update_worker_status(worker, "", "completed")
        self.render()

    def update_phase_status(self, phase_name: str, status: str, phase_index: int = -1) -> None:
        """Update phase status WITHOUT rendering. Use for incremental updates."""
        if phase_index >= 0 and phase_index < len(self.phases):
            self.phases[phase_index].status = status
            self.current_phase_index = phase_index
        else:
            # Find matching phase by name or use current index
            for i, p in enumerate(self.phases):
                if p.name == phase_name or i == self.current_phase_index:
                    p.status = status
                    self.current_phase_index = i
                    break

    def update_worker_status(self, worker: str, task: str = "", status: str = "running") -> None:
        """Update worker status WITHOUT rendering. Use for incremental updates."""
        if status == "completed":
            if self.phases and self.current_phase_index < len(self.phases):
                self.phases[self.current_phase_index].status = "completed"
                self.phases[self.current_phase_index].current_step = ""
                self.current_phase_index += 1
            self.current_worker = ""
            self.tool_count = 0
        else:
            self.current_worker = worker
            self.tool_count = 0
            if self.phases and self.current_phase_index < len(self.phases):
                self.phases[self.current_phase_index].current_step = f"{worker}: {task[:40]}..." if task else worker
                self.phases[self.current_phase_index].status = "running"

    def increment_tool(self) -> None:
        """Track tool call within current step."""
        self.tool_count += 1

    def render(self) -> None:
        """Render the current checklist state."""
        if not self.phases:
            return

        # Clear previous output and render fresh
        lines = []

        # Project prefix for multi-project disambiguation
        project_prefix = ""
        if self.project_name:
            project_prefix = f"[dim magenta][{self.project_name}][/dim magenta] "

        # Title
        if self.prd_title:
            lines.append(f"{project_prefix}[bold blue]📋 {self.prd_title}[/bold blue]")
            lines.append("")

        # Phase checklist
        for i, phase in enumerate(self.phases):
            # Status icon
            if phase.status == "completed":
                icon = "[green]✓[/green]"
                style = "dim"
            elif phase.status == "running":
                icon = "[yellow]▶[/yellow]"
                style = "bold"
            elif phase.status == "failed":
                icon = "[red]✗[/red]"
                style = "red"
            else:
                icon = "[dim]○[/dim]"
                style = "dim"

            # Phase line - show full name for orchestrator phases (when PRD exists)
            worker_badge = f"[cyan]{phase.worker}[/cyan]" if phase.worker else ""

            # For orchestrator phases (has PRD title), show full name
            # For architect phases (no PRD), show shorter name
            if self.prd_title:
                name_display = phase.name  # Full name for orchestrator
            else:
                name_display = phase.name if len(phase.name) <= 40 else phase.name[:37] + "..."

            line = f"  {icon} [{style}]{name_display}[/{style}]"
            if worker_badge:
                line += f" {worker_badge}"

            lines.append(line)

            # Show goals for orchestrator phases (when PRD exists and goals present)
            if self.prd_title and phase.goals and phase.status != "completed":
                # Wrap goals at ~70 chars for readability
                goals_display = phase.goals[:120] + "..." if len(phase.goals) > 120 else phase.goals
                lines.append(f"      [dim italic]{goals_display}[/dim italic]")

            # Current step (if running)
            if phase.status == "running" and phase.current_step:
                tool_info = f" ({self.tool_count} tools)" if self.tool_count > 0 else ""
                lines.append(f"      [dim]→ {phase.current_step}{tool_info}[/dim]")

        # Progress summary - show current phase position, not just completed count
        completed = sum(1 for p in self.phases if p.status == "completed")
        running = sum(1 for p in self.phases if p.status == "running")
        total = len(self.phases)
        lines.append("")

        if running > 0:
            # Show which phase we're on: "Phase 2/3 (1 completed)"
            current_phase = completed + 1  # 1-indexed
            lines.append(f"  [dim]Phase {current_phase}/{total} ({completed} completed)[/dim]")
        else:
            # All done or not started
            lines.append(f"  [dim]Progress: {completed}/{total} phases[/dim]")

        # Print all lines
        self.console.print("\n".join(lines))


class OutputFormatter:
    """
    Unified output formatter for Tarang CLI.

    Provides consistent, rich terminal output for:
    - Tool execution previews and results
    - Approval requests with syntax highlighting
    - Diff displays for file changes
    - Shell command output
    - Search results

    Usage:
        formatter = OutputFormatter(console)
        formatter.show_tool_request("write_file", args, require_approval=True)
        formatter.show_tool_result("write_file", args, result)
    """

    # Language detection by file extension
    LANG_MAP = {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".tsx": "tsx",
        ".jsx": "jsx",
        ".json": "json",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".md": "markdown",
        ".html": "html",
        ".css": "css",
        ".scss": "scss",
        ".sh": "bash",
        ".bash": "bash",
        ".zsh": "bash",
        ".rs": "rust",
        ".go": "go",
        ".java": "java",
        ".c": "c",
        ".cpp": "cpp",
        ".h": "c",
        ".hpp": "cpp",
        ".rb": "ruby",
        ".php": "php",
        ".sql": "sql",
        ".toml": "toml",
        ".xml": "xml",
        ".vue": "vue",
        ".svelte": "svelte",
    }

    # Tool icons
    TOOL_ICONS = {
        "read_file": "📖",
        "write_file": "📝",
        "edit_file": "✏️",
        "delete_file": "🗑️",
        "shell": "💻",
        "list_files": "📂",
        "search_files": "🔍",
        "search_code": "🔎",
        "get_file_info": "ℹ️",
        "validate_file": "✅",
        "validate_build": "🔨",
    }

    # Tool colors
    TOOL_COLORS = {
        "read_file": "blue",
        "write_file": "green",
        "edit_file": "cyan",
        "delete_file": "red",
        "shell": "yellow",
        "list_files": "blue",
        "search_files": "magenta",
        "search_code": "magenta",
        "get_file_info": "blue",
        "validate_file": "green",
        "validate_build": "yellow",
    }

    def __init__(self, console: Optional[Console] = None, verbose: bool = False, compact: bool = True):
        """
        Initialize the formatter.

        Args:
            console: Rich Console instance. Created if not provided.
            verbose: Show detailed output for all operations.
            compact: Use compact single-line output for tools (default True).
        """
        self.console = console or Console()
        self.verbose = verbose
        self.compact = compact
        # Store pending tool requests for compact mode (to merge request + result)
        self._pending_tool: Optional[Dict[str, Any]] = None
        # Phase tracker for checklist display
        self.phase_tracker: Optional[PhaseTracker] = None
        # Tool call tracker for visibility
        self.tool_tracker: Optional[ToolCallTracker] = None
        # Project name for multi-project disambiguation
        self.project_name: str = ""

    def set_project_name(self, name: str) -> None:
        """Set project name for output prefixing."""
        self.project_name = name

    def _get_prefix(self) -> str:
        """Get project prefix for output lines."""
        if self.project_name:
            return f"[dim magenta][{self.project_name}][/dim magenta] "
        return ""

    def init_phase_tracker(self, project_name: str = "") -> PhaseTracker:
        """Initialize and return a phase tracker for this session."""
        self.phase_tracker = PhaseTracker(console=self.console, project_name=project_name)
        return self.phase_tracker

    def init_tool_tracker(self) -> ToolCallTracker:
        """Initialize and return a tool call tracker for this session."""
        self.tool_tracker = ToolCallTracker(console=self.console)
        return self.tool_tracker

    def _get_language(self, file_path: str) -> str:
        """Detect language from file extension."""
        _, ext = os.path.splitext(file_path)
        return self.LANG_MAP.get(ext.lower(), "text")

    def _get_icon(self, tool: str) -> str:
        """Get icon for tool."""
        return self.TOOL_ICONS.get(tool, "•")

    def _get_color(self, tool: str) -> str:
        """Get color for tool."""
        return self.TOOL_COLORS.get(tool, "white")

    # =========================================================================
    # Tool Progress Indicators
    # =========================================================================

    # Descriptive action messages for tools (max 10 chars for alignment)
    TOOL_ACTIONS = {
        "read_file": "Read",
        "read_files": "Batch",  # Batch read
        "write_file": "Write",
        "edit_file": "Edit",
        "delete_file": "Delete",
        "list_files": "List",
        "search_files": "Search",
        "search_code": "Index",
        "get_file_info": "Check",
        "shell": "Run",
        "validate_file": "Validate",
        "validate_build": "Build",
    }

    def show_tool_progress(self, tool: str, args: Dict[str, Any]) -> None:
        """
        Show tool execution in progress.

        In compact mode, we skip this and show the action in the result line instead.
        This avoids duplicate lines and keeps output clean.
        """
        # In compact mode, we integrate action text into result line
        # So no progress display needed - result will show "Read file.py (24 lines)"
        if self.compact:
            return

        # Non-compact mode shows full progress
        icon = self._get_icon(tool)
        action = self.TOOL_ACTIONS.get(tool, "Running")

        # Build target description
        if tool == "read_file":
            target = args.get("file_path", "")
            target = target if len(target) <= 40 else "..." + target[-37:]
        elif tool == "list_files":
            target = args.get("path", ".")
        elif tool in ("search_files", "search_code"):
            target = f"'{args.get('pattern', args.get('query', ''))[:25]}'"
        elif tool == "shell":
            cmd = args.get("command", "")[:35].replace("\n", " ")
            target = cmd
        elif tool in ("write_file", "edit_file", "delete_file", "get_file_info"):
            target = args.get("file_path", "")
            target = target if len(target) <= 40 else "..." + target[-37:]
        else:
            target = ""

        self.console.print(f"  [dim]{icon} {action} {target}...[/dim]")

    # =========================================================================
    # Tool Request Display (Before Execution)
    # =========================================================================

    def show_tool_request(
        self,
        tool: str,
        args: Dict[str, Any],
        require_approval: bool = False,
        description: str = "",
    ) -> None:
        """
        Display a tool request before execution.

        In compact mode, read-only tools are deferred to show_tool_result for single-line output.
        Write operations that require approval still show full previews.

        Args:
            tool: Tool name (e.g., "write_file", "shell")
            args: Tool arguments
            require_approval: Whether this tool needs user approval
            description: Optional description of what the tool will do
        """
        icon = self._get_icon(tool)
        color = self._get_color(tool)

        # In compact mode, defer read-only tools to show_tool_result
        if self.compact and tool in ("read_file", "list_files", "search_files", "search_code", "get_file_info"):
            self._pending_tool = {"tool": tool, "args": args, "description": description}
            return

        # Write operations always show full preview (need user to see what's changing)
        if tool == "write_file":
            self._show_write_file_request(args, description)
        elif tool == "edit_file":
            self._show_edit_file_request(args, description)
        elif tool == "delete_file":
            self._show_delete_file_request(args, description)
        elif tool == "shell":
            self._show_shell_request(args, description)
        elif tool == "read_file":
            file_path = args.get("file_path", "...")
            self.console.print(f"  [{color}]{icon} read_file:[/{color}] {file_path}")
        elif tool == "list_files":
            path = args.get("path", ".")
            pattern = args.get("pattern", "")
            display = f"{path}" + (f" ({pattern})" if pattern else "")
            self.console.print(f"  [{color}]{icon} list_files:[/{color}] {display}")
        elif tool == "search_files":
            pattern = args.get("pattern", "...")
            self.console.print(f"  [{color}]{icon} search_files:[/{color}] {pattern}")
        else:
            # Generic display
            self.console.print(f"  [{color}]{icon} {tool}[/{color}]")

    def _show_write_file_request(self, args: Dict[str, Any], description: str) -> None:
        """Display write_file request with syntax-highlighted preview."""
        file_path = args.get("file_path", "")
        content = args.get("content", "")
        language = self._get_language(file_path)
        lines = content.split("\n")

        self.console.print(f"[bold green]╭─ 📝 Create: {file_path}[/bold green]")
        if description:
            self.console.print(f"[bold green]│[/bold green]  [dim]{description}[/dim]")

        # Show syntax-highlighted preview (max 20 lines)
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
            for line in preview_lines[:10]:
                self.console.print(f"[bold green]│[/bold green]   [green]+ {line}[/green]")
            if len(lines) > 10:
                self.console.print(f"[bold green]│[/bold green]   [dim]... ({len(lines)} lines total)[/dim]")

        if len(lines) > 20:
            self.console.print(f"[bold green]╰─[/bold green] [dim]... and {len(lines) - 20} more lines[/dim]")
        else:
            self.console.print("[bold green]╰─[/bold green]")

    def _show_edit_file_request(self, args: Dict[str, Any], description: str) -> None:
        """Display edit_file request with diff preview."""
        file_path = args.get("file_path", "")
        search = args.get("search", "")
        replace = args.get("replace", "")

        search_lines = search.split("\n")
        replace_lines = replace.split("\n")

        self.console.print(f"[bold cyan]╭─ ✏️  Edit: {file_path}[/bold cyan]")
        if description:
            self.console.print(f"[bold cyan]│[/bold cyan]  [dim]{description}[/dim]")

        # Show removal (red)
        if search_lines:
            self.console.print("[bold cyan]│[/bold cyan]")
            self.console.print("[bold cyan]│[/bold cyan] [red]Remove:[/red]")
            for line in search_lines[:10]:
                self.console.print(f"[bold cyan]│[/bold cyan]   [red]- {line}[/red]")
            if len(search_lines) > 10:
                self.console.print(f"[bold cyan]│[/bold cyan]   [dim]... ({len(search_lines)} lines total)[/dim]")

        # Show addition (green)
        if replace_lines:
            self.console.print("[bold cyan]│[/bold cyan]")
            self.console.print("[bold cyan]│[/bold cyan] [green]Add:[/green]")
            for line in replace_lines[:10]:
                self.console.print(f"[bold cyan]│[/bold cyan]   [green]+ {line}[/green]")
            if len(replace_lines) > 10:
                self.console.print(f"[bold cyan]│[/bold cyan]   [dim]... ({len(replace_lines)} lines total)[/dim]")

        self.console.print("[bold cyan]╰─[/bold cyan]")

    def _show_delete_file_request(self, args: Dict[str, Any], description: str) -> None:
        """Display delete_file request with warning."""
        file_path = args.get("file_path", "")

        self.console.print(f"[bold red]╭─ 🗑️  Delete: {file_path}[/bold red]")
        if description:
            self.console.print(f"[bold red]│[/bold red]  [dim]{description}[/dim]")
        self.console.print("[bold red]╰─ This action cannot be undone![/bold red]")

    def _show_shell_request(self, args: Dict[str, Any], description: str) -> None:
        """Display shell command request with syntax highlighting."""
        command = args.get("command", "")
        cwd = args.get("cwd", "")
        timeout = args.get("timeout", 60)

        self.console.print(f"[bold yellow]╭─ 💻 Shell Command[/bold yellow]")
        if description:
            self.console.print(f"[bold yellow]│[/bold yellow]  [dim]{description}[/dim]")

        try:
            syntax = Syntax(command, "bash", theme="monokai")
            self.console.print(Panel(
                syntax,
                border_style="yellow",
                title="[yellow]$ Command[/yellow]",
            ))
        except Exception:
            self.console.print(f"[bold yellow]│[/bold yellow]  $ {command}")

        if cwd:
            self.console.print(f"[bold yellow]│[/bold yellow]  [dim]Directory: {cwd}[/dim]")
        self.console.print(f"[bold yellow]╰─[/bold yellow] [dim]Timeout: {timeout}s[/dim]")

    # =========================================================================
    # Tool Result Display (After Execution)
    # =========================================================================

    def show_tool_result(
        self,
        tool: str,
        args: Dict[str, Any],
        result: Dict[str, Any],
        duration_s: Optional[float] = None,
    ) -> None:
        """
        Display the result of a tool execution.

        In compact mode, shows a single-line summary combining request + result.

        Args:
            tool: Tool name
            args: Original tool arguments
            result: Tool execution result
            duration_s: Execution time in seconds (for right-aligned stats)
        """
        icon = self._get_icon(tool)
        color = self._get_color(tool)

        # Clear pending tool
        self._pending_tool = None

        # Stats for right side
        stats = f"[dim]{duration_s}s[/dim]" if duration_s is not None else ""

        if "error" in result:
            left = f"  [red]✗ {tool}: {result['error'][:50]}[/red]"
            if stats:
                self.console.print(f"{left:<{self.LINE_WIDTH}} {stats}")
            else:
                self.console.print(left)
            return

        # Compact mode: single-line output for read-only tools
        if self.compact and tool in ("read_file", "read_files", "list_files", "search_files", "search_code", "get_file_info"):
            self._show_compact_result(tool, args, result, duration_s)
            return

        if tool == "read_file":
            self._show_read_file_result(args, result)
        elif tool == "write_file":
            self._show_write_file_result(args, result, duration_s)
        elif tool == "edit_file":
            self._show_edit_file_result(args, result, duration_s)
        elif tool == "delete_file":
            self._show_delete_file_result(args, result)
        elif tool == "shell":
            self._show_shell_result(args, result, duration_s)
        elif tool == "list_files":
            self._show_list_files_result(args, result)
        elif tool == "search_files":
            self._show_search_files_result(args, result)
        else:
            # Generic success
            if result.get("success"):
                left = f"  [{color}]✓ {tool}: OK[/{color}]"
                if duration_s is not None:
                    self.console.print(f"{left:<{self.LINE_WIDTH}} [dim]{duration_s}s[/dim]")
                else:
                    self.console.print(left)
            else:
                self.console.print(f"  [dim]{tool}: completed[/dim]")

    # Width for action column alignment (longest: "Validate" = 8)
    ACTION_WIDTH = 8
    # Width for left part of line (for right-aligned stats)
    LINE_WIDTH = 55

    def _show_compact_result(
        self,
        tool: str,
        args: Dict[str, Any],
        result: Dict[str, Any],
        duration_s: Optional[float] = None,
    ) -> None:
        """Show compact single-line result for read-only tools with aligned columns."""
        icon = self._get_icon(tool)
        color = self._get_color(tool)
        action = self.TOOL_ACTIONS.get(tool, "Done")
        # Pad action to fixed width for alignment
        action_padded = action.ljust(self.ACTION_WIDTH)
        # Stats on the right (duration, future: tokens, etc.)
        stats = f"[dim]{duration_s}s[/dim]" if duration_s is not None else ""

        if tool == "read_file":
            file_path = args.get("file_path", "")
            lines = result.get("lines", 0)
            # Truncate long paths
            display_path = file_path if len(file_path) <= 35 else "..." + file_path[-32:]
            left = f"  [{color}]✓ {icon} {action_padded}[/{color}] {display_path} [dim]({lines} lines)[/dim]"

        elif tool == "read_files":
            # Batch read - show count and total lines
            file_paths = args.get("file_paths", [])
            successful = result.get("successful", 0)
            total_lines = result.get("total_lines", 0)
            left = f"  [{color}]✓ 📚 {action_padded}[/{color}] {successful} files [dim]({total_lines} lines)[/dim]"

        elif tool == "list_files":
            path = args.get("path", ".")
            if len(path) > 30:
                path = "..." + path[-27:]
            count = result.get("count", len(result.get("files", [])))
            left = f"  [{color}]✓ {icon} {action_padded}[/{color}] {path} [dim]({count} files)[/dim]"

        elif tool == "search_files":
            pattern = args.get("pattern", "")[:25]
            count = result.get("count", len(result.get("matches", [])))
            left = f"  [{color}]✓ {icon} {action_padded}[/{color}] '{pattern}' [dim]({count} matches)[/dim]"

        elif tool == "search_code":
            query = args.get("query", "")[:25]
            chunks = len(result.get("chunks", []))
            left = f"  [{color}]✓ {icon} {action_padded}[/{color}] '{query}' [dim]({chunks} chunks)[/dim]"

        elif tool == "get_file_info":
            file_path = args.get("file_path", "")
            exists = "exists" if result.get("exists") else "not found"
            display_path = file_path if len(file_path) <= 35 else "..." + file_path[-32:]
            left = f"  [{color}]✓ {icon} {action_padded}[/{color}] {display_path} [dim]({exists})[/dim]"

        else:
            action = self.TOOL_ACTIONS.get(tool, tool)
            action_padded = action.ljust(self.ACTION_WIDTH)
            left = f"  [{color}]✓ {icon} {action_padded}[/{color}]"

        # Print with stats right-aligned (add project prefix for multi-project disambiguation)
        prefix = self._get_prefix()
        if stats:
            # Use Rich's Text for proper alignment with markup
            self.console.print(f"{prefix}{left:<{self.LINE_WIDTH}} {stats}")
        else:
            self.console.print(f"{prefix}{left}")

    def _show_read_file_result(self, args: Dict[str, Any], result: Dict[str, Any]) -> None:
        """Display read_file result with line count."""
        file_path = args.get("file_path", "")
        content = result.get("content", "")
        lines = content.count("\n") + 1 if content else 0
        chars = len(content)

        self.console.print(f"  [blue]✓ read_file:[/blue] {file_path}")
        self.console.print(f"    [dim]Read {lines} lines ({chars:,} chars)[/dim]")

        # Show preview in verbose mode
        if self.verbose and content:
            preview_lines = content.split("\n")[:5]
            for line in preview_lines:
                truncated = line[:80] + "..." if len(line) > 80 else line
                self.console.print(f"    [dim]│ {truncated}[/dim]")
            if lines > 5:
                self.console.print(f"    [dim]│ ... ({lines - 5} more lines)[/dim]")

    def _show_write_file_result(self, args: Dict[str, Any], result: Dict[str, Any], duration_s: Optional[float] = None) -> None:
        """Display write_file result with summary."""
        file_path = args.get("file_path", "")
        content = args.get("content", "")
        lines = content.count("\n") + 1 if content else 0
        display_path = file_path if len(file_path) <= 40 else "..." + file_path[-37:]
        stats = f"[dim]{duration_s}s[/dim]" if duration_s is not None else ""

        if result.get("success"):
            if self.compact:
                left = f"  [green]✓ 📝[/green] {display_path} [dim]({lines} lines)[/dim]"
                if stats:
                    self.console.print(f"{left:<{self.LINE_WIDTH}} {stats}")
                else:
                    self.console.print(left)
            else:
                self.console.print(f"  [green]✓ write_file:[/green] {file_path}")
                self.console.print(f"    [dim]Created {lines} lines[/dim]")
        else:
            self.console.print(f"  [red]✗ write_file:[/red] {file_path} - FAILED")

    def _show_edit_file_result(self, args: Dict[str, Any], result: Dict[str, Any], duration_s: Optional[float] = None) -> None:
        """Display edit_file result with replacement count."""
        file_path = args.get("file_path", "")
        replacements = result.get("replacements", 1)
        display_path = file_path if len(file_path) <= 40 else "..." + file_path[-37:]
        stats = f"[dim]{duration_s}s[/dim]" if duration_s is not None else ""

        if result.get("success"):
            if self.compact:
                left = f"  [cyan]✓ ✏️[/cyan]  {display_path} [dim]({replacements} edit{'s' if replacements > 1 else ''})[/dim]"
                if stats:
                    self.console.print(f"{left:<{self.LINE_WIDTH}} {stats}")
                else:
                    self.console.print(left)
            else:
                self.console.print(f"  [cyan]✓ edit_file:[/cyan] {file_path}")
                self.console.print(f"    [dim]{replacements} replacement(s) made[/dim]")
        else:
            self.console.print(f"  [red]✗ edit_file:[/red] {file_path} - FAILED")

    def _show_delete_file_result(self, args: Dict[str, Any], result: Dict[str, Any]) -> None:
        """Display delete_file result."""
        file_path = args.get("file_path", "")
        display_path = file_path if len(file_path) <= 40 else "..." + file_path[-37:]

        if result.get("success"):
            if self.compact:
                self.console.print(f"  [red]✓ 🗑️[/red]  {display_path} [dim](deleted)[/dim]")
            else:
                self.console.print(f"  [red]✓ delete_file:[/red] {file_path} [dim](deleted)[/dim]")
        else:
            self.console.print(f"  [red]✗ delete_file:[/red] {file_path} - FAILED")

    def _show_shell_result(self, args: Dict[str, Any], result: Dict[str, Any], duration_s: Optional[float] = None) -> None:
        """Display shell command result with output."""
        command = args.get("command", "")
        exit_code = result.get("exit_code", -1)
        stdout = result.get("stdout", "")
        stderr = result.get("stderr", "")
        stats = f"[dim]{duration_s}s[/dim]" if duration_s is not None else ""

        # Compact command preview (first 35 chars to leave room for stats)
        cmd_preview = command[:35] + "..." if len(command) > 35 else command
        cmd_preview = cmd_preview.replace("\n", " ")

        # Status line
        if exit_code == 0:
            if self.compact:
                left = f"  [green]✓ 💻[/green] {cmd_preview} [dim](exit 0)[/dim]"
                if stats:
                    self.console.print(f"{left:<{self.LINE_WIDTH}} {stats}")
                else:
                    self.console.print(left)
            else:
                self.console.print(f"  [green]✓ shell:[/green] exit {exit_code}")
        else:
            if self.compact:
                left = f"  [yellow]⚠ 💻[/yellow] {cmd_preview} [dim](exit {exit_code})[/dim]"
                if stats:
                    self.console.print(f"{left:<{self.LINE_WIDTH}} {stats}")
                else:
                    self.console.print(left)
            else:
                self.console.print(f"  [yellow]⚠ shell:[/yellow] exit {exit_code}")

        # Show stdout (up to 15 lines, or 5 in compact mode)
        max_lines = 5 if self.compact else 15
        if stdout:
            stdout_lines = stdout.strip().split("\n")
            if self.compact and len(stdout_lines) <= 3:
                # Very short output - show inline
                for line in stdout_lines:
                    self.console.print(f"    [dim]{line[:80]}[/dim]")
            else:
                self.console.print(Panel(
                    "\n".join(stdout_lines[:max_lines]),
                    border_style="dim",
                    title="[dim]stdout[/dim]",
                    subtitle=f"[dim]{len(stdout_lines)} lines[/dim]" if len(stdout_lines) > max_lines else None,
                ))
                if len(stdout_lines) > max_lines:
                    self.console.print(f"    [dim]... ({len(stdout_lines) - max_lines} more lines)[/dim]")

        # Show stderr if present
        if stderr:
            stderr_lines = stderr.strip().split("\n")
            self.console.print(Panel(
                "\n".join(stderr_lines[:10]),
                border_style="red",
                title="[red]stderr[/red]",
            ))

    def _show_list_files_result(self, args: Dict[str, Any], result: Dict[str, Any]) -> None:
        """Display list_files result with file count."""
        files = result.get("files", [])
        path = args.get("path", ".")

        self.console.print(f"  [blue]✓ list_files:[/blue] {path}")
        self.console.print(f"    [dim]Found {len(files)} files[/dim]")

        # Show first few files in verbose mode
        if self.verbose and files:
            for f in files[:10]:
                self.console.print(f"    [dim]• {f}[/dim]")
            if len(files) > 10:
                self.console.print(f"    [dim]... and {len(files) - 10} more[/dim]")

    def _show_search_files_result(self, args: Dict[str, Any], result: Dict[str, Any]) -> None:
        """Display search_files result with matches."""
        pattern = args.get("pattern", "")
        matches = result.get("matches", [])
        total = result.get("total_matches", len(matches))

        self.console.print(f"  [magenta]✓ search_files:[/magenta] '{pattern}'")
        self.console.print(f"    [dim]Found {total} matches[/dim]")

        # Show matches in verbose mode
        if self.verbose and matches:
            for match in matches[:5]:
                file_path = match.get("file", "")
                line_num = match.get("line", 0)
                text = match.get("text", "")[:60]
                self.console.print(f"    [dim]{file_path}:{line_num}: {text}[/dim]")
            if len(matches) > 5:
                self.console.print(f"    [dim]... and {len(matches) - 5} more matches[/dim]")

    # =========================================================================
    # Approval UI
    # =========================================================================

    def show_approval_prompt(
        self,
        tool: str,
        args: Dict[str, Any],
        options: str = "Y/n/a(ll)/t(ool)/v(iew)",
    ) -> str:
        """
        Show approval prompt and get user response.

        Args:
            tool: Tool name
            args: Tool arguments
            options: Options to display

        Returns:
            User's response (lowercase, stripped)
        """
        self.console.print(f"  [yellow]Approve? [{options}]:[/yellow] ", end="")
        try:
            response = input().strip().lower()
            return response
        except (EOFError, KeyboardInterrupt):
            return "n"

    def show_approval_status(self, status: str, detail: str = "") -> None:
        """
        Show approval status message.

        Args:
            status: Status type ("approved", "approved_all", "approved_tool", "skipped", "cancelled")
            detail: Additional detail (e.g., tool name for approved_tool)
        """
        if status == "approved":
            self.console.print("  [green]✓ Approved[/green]")
        elif status == "approved_all":
            self.console.print("  [green]✓ Approved all for session[/green]")
        elif status == "approved_tool":
            self.console.print(f"  [green]✓ Approved all '{detail}' for session[/green]")
        elif status == "auto_approved":
            self.console.print("  [dim green]✓ Auto-approved[/dim green]")
        elif status == "skipped":
            self.console.print("  [yellow]⊘ Skipped by user[/yellow]")
        elif status == "cancelled":
            self.console.print("  [yellow]⊘ Cancelled[/yellow]")

    def show_view_content(self, tool: str, args: Dict[str, Any]) -> None:
        """
        Show full content when user requests to view before approval.

        Args:
            tool: Tool name
            args: Tool arguments
        """
        if tool == "write_file":
            content = args.get("content", "")
            file_path = args.get("file_path", "")
            language = self._get_language(file_path)

            try:
                syntax = Syntax(content, language, theme="monokai", line_numbers=True)
                self.console.print(Panel(
                    syntax,
                    title=f"[bold]{file_path}[/bold]",
                    border_style="blue",
                ))
            except Exception:
                self.console.print(f"\n--- Content for {file_path} ---")
                self.console.print(content)
                self.console.print("--- End ---\n")

        elif tool == "edit_file":
            file_path = args.get("file_path", "")
            search = args.get("search", "")
            replace = args.get("replace", "")

            content = Text()
            content.append("─── Search (to be replaced) ───\n", style="bold red")
            content.append(search + "\n", style="red")
            content.append("\n─── Replace (new content) ───\n", style="bold green")
            content.append(replace, style="green")

            self.console.print(Panel(
                content,
                title=f"[bold]{file_path}[/bold]",
                border_style="yellow",
            ))

        elif tool == "shell":
            command = args.get("command", "")
            try:
                syntax = Syntax(command, "bash", theme="monokai")
                self.console.print(Panel(syntax, title="[yellow]Command[/yellow]", border_style="yellow"))
            except Exception:
                self.console.print(f"\n  $ {command}\n")

    # =========================================================================
    # Status & Progress
    # =========================================================================

    def show_status(self, message: str, style: str = "dim") -> None:
        """Show a status message."""
        self.console.print(f"  [{style}]{message}[/{style}]")

    def show_phase(self, phase: str, message: str = "") -> None:
        """Show a phase transition."""
        phase_icons = {
            "explore": "🔍",
            "plan": "📋",
            "implement": "⚡",
            "generate": "✨",
            "review": "🔎",
            "complete": "✅",
        }
        icon = phase_icons.get(phase, "•")
        display = f"{icon} {phase.title()}"
        if message:
            display += f": {message}"
        self.console.print(f"[cyan]{display}[/cyan]")

    # =========================================================================
    # Orchestrator Phase & Task Tracking
    # =========================================================================

    def show_strategic_plan(self, plan: Dict[str, Any]) -> None:
        """
        Display the orchestrator's strategic plan with PRD and phases.

        Args:
            plan: Plan dict containing 'prd' and 'phases'
        """
        prd = plan.get("prd", {})
        phases = plan.get("phases", [])

        # PRD Header
        if prd:
            title = prd.get("title", "Project")
            self.console.print()
            self.console.print(f"[bold blue]╭─────────────────────────────────────────────────────╮[/bold blue]")
            self.console.print(f"[bold blue]│[/bold blue]  📋 [bold]{title}[/bold]")
            self.console.print(f"[bold blue]╰─────────────────────────────────────────────────────╯[/bold blue]")

            # Requirements
            requirements = prd.get("requirements", [])
            if requirements:
                self.console.print(f"  [dim]Requirements:[/dim]")
                for req in requirements[:5]:
                    self.console.print(f"    [dim]• {req[:60]}{'...' if len(req) > 60 else ''}[/dim]")

        # Phases overview
        if phases:
            self.console.print()
            self.console.print(f"[bold cyan]  📊 Execution Plan ({len(phases)} phases):[/bold cyan]")

            for i, phase in enumerate(phases, 1):
                name = phase.get("name", f"Phase {i}")
                worker = phase.get("worker", "architect")
                goals = phase.get("goals", "")[:50]

                # Phase status indicator
                status_icon = "○"  # pending
                color = "dim"

                self.console.print(f"    [{color}]{status_icon} {name}[/{color}]")
                if goals:
                    self.console.print(f"      [{color}]→ {worker}: {goals}{'...' if len(phase.get('goals', '')) > 50 else ''}[/{color}]")

            self.console.print()

    def show_phase_start(self, phase_name: str, phase_index: int = 0, total_phases: int = 0) -> None:
        """
        Display when a phase starts executing.

        Args:
            phase_name: Name of the phase
            phase_index: Current phase number (1-based)
            total_phases: Total number of phases
        """
        progress = f"[{phase_index}/{total_phases}]" if total_phases > 0 else ""
        self.console.print()
        self.console.print(f"[bold cyan]▶ {progress} {phase_name}[/bold cyan]")
        self.console.print(f"[cyan]{'─' * 50}[/cyan]")

    def show_worker_start(self, worker: str, task: str = "") -> None:
        """
        Display when a worker starts.

        Args:
            worker: Worker name (e.g., "architect", "explorer", "coder")
            task: Task description
        """
        worker_icons = {
            "orchestrator": "🎯",
            "architect": "📐",
            "explorer": "🔍",
            "coder": "💻",
        }
        icon = worker_icons.get(worker.lower(), "•")
        self.console.print(f"  [yellow]{icon} {worker}[/yellow]", end="")
        if task:
            # Truncate long tasks
            display_task = task[:60] + "..." if len(task) > 60 else task
            self.console.print(f" [dim]→ {display_task}[/dim]")
        else:
            self.console.print()

    def show_worker_done(self, worker: str, success: bool = True) -> None:
        """
        Display when a worker completes.

        Args:
            worker: Worker name
            success: Whether it completed successfully
        """
        if success:
            self.console.print(f"  [green]✓ {worker} done[/green]")
        else:
            self.console.print(f"  [red]✗ {worker} failed[/red]")

    def show_task_decomposition(self, tasks: list) -> None:
        """
        Display architect's task decomposition.

        Args:
            tasks: List of tasks from architect
        """
        if not tasks:
            return

        self.console.print()
        self.console.print(f"  [bold magenta]📋 Task Breakdown ({len(tasks)} tasks):[/bold magenta]")

        for i, task in enumerate(tasks, 1):
            if isinstance(task, dict):
                worker = task.get("worker", "coder")
                goals = task.get("goals", "")[:55]
                worker_icon = "🔍" if worker == "explorer" else "💻"
                self.console.print(f"    [dim]{i}. {worker_icon} {worker}:[/dim] {goals}{'...' if len(task.get('goals', '')) > 55 else ''}")
            else:
                self.console.print(f"    [dim]{i}. {str(task)[:60]}[/dim]")
        self.console.print()

    def show_delegation(self, from_agent: str, to_agent: str, task: str = "") -> None:
        """
        Display delegation between agents.

        Args:
            from_agent: Delegating agent
            to_agent: Target agent
            task: Task being delegated
        """
        self.console.print(f"  [dim]↳ {from_agent} → {to_agent}[/dim]")
        if task:
            # Show full task on separate line(s) for better readability
            # Wrap at 80 chars per line, max 3 lines
            max_line_len = 80
            max_lines = 3
            lines = []
            remaining = task
            while remaining and len(lines) < max_lines:
                if len(remaining) <= max_line_len:
                    lines.append(remaining)
                    remaining = ""
                else:
                    # Find break point (space near max_line_len)
                    break_at = remaining.rfind(" ", 0, max_line_len)
                    if break_at == -1:
                        break_at = max_line_len
                    lines.append(remaining[:break_at])
                    remaining = remaining[break_at:].lstrip()

            if remaining:
                lines[-1] = lines[-1][:max_line_len - 3] + "..."

            for line in lines:
                self.console.print(f"    [dim italic]{line}[/dim italic]")

    def show_thinking(self, message: str) -> None:
        """Show thinking/reasoning indicator."""
        self.console.print(f"  [dim cyan]💭 {message}[/dim cyan]")

    def show_error(self, message: str, recoverable: bool = True) -> None:
        """Show an error message."""
        style = "yellow" if recoverable else "red"
        icon = "⚠" if recoverable else "✗"
        self.console.print(f"[{style}]{icon} {message}[/{style}]")

    def show_success(self, message: str) -> None:
        """Show a success message."""
        self.console.print(f"[green]✓ {message}[/green]")

    def show_callback_status(self, success: bool, error: str = "") -> None:
        """Show callback status (for SSE flow). Silent in compact mode unless error."""
        if self.compact and success:
            # In compact mode, success is implied by the checkmark - no need to confirm
            return
        if success:
            self.console.print("  [dim green]↳ callback OK[/dim green]")
        else:
            self.console.print(f"  [red]↳ callback failed: {error}[/red]")
