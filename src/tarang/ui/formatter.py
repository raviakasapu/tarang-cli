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

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text
from rich.table import Table


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
        "get_file_info": "ℹ️",
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
        "get_file_info": "blue",
    }

    def __init__(self, console: Optional[Console] = None, verbose: bool = False):
        """
        Initialize the formatter.

        Args:
            console: Rich Console instance. Created if not provided.
            verbose: Show detailed output for all operations.
        """
        self.console = console or Console()
        self.verbose = verbose

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

        Args:
            tool: Tool name (e.g., "write_file", "shell")
            args: Tool arguments
            require_approval: Whether this tool needs user approval
            description: Optional description of what the tool will do
        """
        icon = self._get_icon(tool)
        color = self._get_color(tool)

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
    ) -> None:
        """
        Display the result of a tool execution.

        Args:
            tool: Tool name
            args: Original tool arguments
            result: Tool execution result
        """
        icon = self._get_icon(tool)
        color = self._get_color(tool)

        if "error" in result:
            self.console.print(f"  [red]✗ {tool} error: {result['error']}[/red]")
            return

        if tool == "read_file":
            self._show_read_file_result(args, result)
        elif tool == "write_file":
            self._show_write_file_result(args, result)
        elif tool == "edit_file":
            self._show_edit_file_result(args, result)
        elif tool == "delete_file":
            self._show_delete_file_result(args, result)
        elif tool == "shell":
            self._show_shell_result(args, result)
        elif tool == "list_files":
            self._show_list_files_result(args, result)
        elif tool == "search_files":
            self._show_search_files_result(args, result)
        else:
            # Generic success
            if result.get("success"):
                self.console.print(f"  [{color}]✓ {tool}: OK[/{color}]")
            else:
                self.console.print(f"  [dim]{tool}: completed[/dim]")

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

    def _show_write_file_result(self, args: Dict[str, Any], result: Dict[str, Any]) -> None:
        """Display write_file result with summary."""
        file_path = args.get("file_path", "")
        content = args.get("content", "")
        lines = content.count("\n") + 1 if content else 0

        if result.get("success"):
            self.console.print(f"  [green]✓ write_file:[/green] {file_path}")
            self.console.print(f"    [dim]Created {lines} lines[/dim]")
        else:
            self.console.print(f"  [red]✗ write_file:[/red] {file_path} - FAILED")

    def _show_edit_file_result(self, args: Dict[str, Any], result: Dict[str, Any]) -> None:
        """Display edit_file result with replacement count."""
        file_path = args.get("file_path", "")
        replacements = result.get("replacements", 1)

        if result.get("success"):
            self.console.print(f"  [cyan]✓ edit_file:[/cyan] {file_path}")
            self.console.print(f"    [dim]{replacements} replacement(s) made[/dim]")
        else:
            self.console.print(f"  [red]✗ edit_file:[/red] {file_path} - FAILED")

    def _show_delete_file_result(self, args: Dict[str, Any], result: Dict[str, Any]) -> None:
        """Display delete_file result."""
        file_path = args.get("file_path", "")

        if result.get("success"):
            self.console.print(f"  [red]✓ delete_file:[/red] {file_path} [dim](deleted)[/dim]")
        else:
            self.console.print(f"  [red]✗ delete_file:[/red] {file_path} - FAILED")

    def _show_shell_result(self, args: Dict[str, Any], result: Dict[str, Any]) -> None:
        """Display shell command result with output."""
        command = args.get("command", "")
        exit_code = result.get("exit_code", -1)
        stdout = result.get("stdout", "")
        stderr = result.get("stderr", "")

        # Status line
        if exit_code == 0:
            self.console.print(f"  [green]✓ shell:[/green] exit {exit_code}")
        else:
            self.console.print(f"  [yellow]⚠ shell:[/yellow] exit {exit_code}")

        # Show stdout (up to 15 lines)
        if stdout:
            stdout_lines = stdout.strip().split("\n")
            self.console.print(Panel(
                "\n".join(stdout_lines[:15]),
                border_style="dim",
                title="[dim]stdout[/dim]",
                subtitle=f"[dim]{len(stdout_lines)} lines[/dim]" if len(stdout_lines) > 15 else None,
            ))
            if len(stdout_lines) > 15:
                self.console.print(f"    [dim]... ({len(stdout_lines) - 15} more lines)[/dim]")

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
        """Show callback status (for SSE flow)."""
        if success:
            self.console.print("  [dim green]↳ callback OK[/dim green]")
        else:
            self.console.print(f"  [red]↳ callback failed: {error}[/red]")
