"""Rich console UI for Tarang CLI."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional, List, Dict, Any

from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown
from rich.syntax import Syntax
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.prompt import Prompt, Confirm
from rich.text import Text
from rich.rule import Rule
from rich.live import Live
from rich.layout import Layout

# Try to import prompt_toolkit for command history (up/down arrows)
try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import FileHistory, InMemoryHistory
    from prompt_toolkit.styles import Style as PTStyle
    HAS_PROMPT_TOOLKIT = True
except ImportError:
    HAS_PROMPT_TOOLKIT = False


class TarangConsole:
    """Rich console for Tarang CLI with Aider-like UI."""

    BANNER = """
[bold green]██████╗  ███████╗ ██╗   ██╗[/]
[bold green]██╔══██╗ ██╔════╝ ██║   ██║[/]
[bold green]██║  ██║ █████╗   ██║   ██║[/]
[bold green]██║  ██║ ██╔══╝   ╚██╗ ██╔╝[/]
[bold green]██████╔╝ ███████╗  ╚████╔╝ [/]
[bold green]╚═════╝  ╚══════╝   ╚═══╝  [/]

[bold cyan]████████╗  █████╗  ██████╗   █████╗  ███╗   ██╗  ██████╗ [/]
[bold cyan]╚══██╔══╝ ██╔══██╗ ██╔══██╗ ██╔══██╗ ████╗  ██║ ██╔════╝ [/]
[bold cyan]   ██║    ███████║ ██████╔╝ ███████║ ██╔██╗ ██║ ██║  ███╗[/]
[bold cyan]   ██║    ██╔══██║ ██╔══██╗ ██╔══██║ ██║╚██╗██║ ██║   ██║[/]
[bold cyan]   ██║    ██║  ██║ ██║  ██║ ██║  ██║ ██║ ╚████║ ╚██████╔╝[/]
[bold cyan]   ╚═╝    ╚═╝  ╚═╝ ╚═╝  ╚═╝ ╚═╝  ╚═╝ ╚═╝  ╚═══╝  ╚═════╝ [/]
"""

    def __init__(self, verbose: bool = False):
        self.console = Console()
        self.verbose = verbose
        self.project_path: Optional[Path] = None

        # Initialize command history for up/down arrow navigation
        self._prompt_session = None
        if HAS_PROMPT_TOOLKIT:
            # Store history in ~/.tarang/history
            history_path = Path.home() / ".tarang" / "history"
            history_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                history = FileHistory(str(history_path))
            except Exception:
                history = InMemoryHistory()

            # Style to match Rich prompt
            style = PTStyle.from_dict({
                'prompt': 'bold cyan',
            })
            self._prompt_session = PromptSession(
                history=history,
                style=style,
                enable_history_search=True,  # Ctrl+R for reverse search
            )

    def print_banner(self, version: str, project_path: Path):
        """Print the startup banner with project info."""
        self.project_path = project_path
        self.console.print(self.BANNER)

        # Project info bar
        git_info = self._get_git_info(project_path)
        info_text = f"[dim]v{version}[/] │ [bold]{project_path.name}[/]"
        if git_info:
            info_text += f" │ [yellow]{git_info}[/]"

        self.console.print(Panel(info_text, style="blue", padding=(0, 1)))

    def print_instructions(self):
        """Print usage instructions with matching colors."""
        self.console.print("[green]Type your instructions[/], or [cyan]/help[/] for commands")
        self.console.print("[bold]↑/↓[/][dim]=[/]history  [bold]ESC[/][green]=[/]cancel  [bold]SPACE[/][cyan]=[/]add instruction")
        self.console.print()

    def print_project_stats(self, total_files: int, total_lines: int):
        """Print project statistics."""
        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column(style="dim")
        table.add_column(style="bold")
        table.add_row("Files:", f"{total_files:,}")
        table.add_row("Lines:", f"{total_lines:,}")
        self.console.print(table)
        self.console.print()

    def print_help(self):
        """Print available commands."""
        help_text = """
[bold]Commands:[/]
  [cyan]/help[/]      Show this help message
  [cyan]/login[/]     Login to Tarang
  [cyan]/config[/]    Configure API key
  [cyan]/index[/]     Build code index for better context
  [cyan]/git[/]       Show git status
  [cyan]/files[/]     List tracked files
  [cyan]/add[/]       Add files to context
  [cyan]/drop[/]      Remove files from context
  [cyan]/clear[/]     Clear conversation history
  [cyan]/commit[/]    Commit pending changes
  [cyan]/diff[/]      Show uncommitted changes
  [cyan]/undo[/]      Undo last change
  [cyan]/exit[/]      Exit Tarang

[bold]Tips:[/]
  • Run [cyan]/index[/] to enable smart code retrieval
  • Type your request naturally: "add a login button"
  • Reference files: "fix the bug in src/main.py"
  • Ask questions: "explain how auth works"
"""
        self.console.print(Panel(help_text, title="[bold]Tarang Help[/]", border_style="blue"))

    def print_git_status(self, project_path: Path):
        """Print git status in a panel."""
        try:
            result = subprocess.run(
                ["git", "status", "--short"],
                cwd=project_path,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                status = result.stdout.strip() or "[dim]No changes[/]"
                self.console.print(Panel(status, title="[bold]Git Status[/]", border_style="yellow"))
            else:
                self.console.print("[dim]Not a git repository[/]")
        except Exception:
            self.console.print("[dim]Git not available[/]")

    def thinking(self, message: str = "Thinking..."):
        """Return a spinner context for thinking state."""
        return self.console.status(f"[bold cyan]{message}[/]", spinner="dots")

    def print_message(self, message: str, title: str = "Response"):
        """Print an AI response in a panel with markdown."""
        md = Markdown(message)
        self.console.print(Panel(md, title=f"[bold green]{title}[/]", border_style="green"))

    def print_error(self, error: str, recoverable: bool = True):
        """Print an error message."""
        style = "yellow" if recoverable else "red"
        icon = "⚠" if recoverable else "✗"
        self.console.print(f"[{style}]{icon} {error}[/{style}]")

    def print_success(self, message: str):
        """Print a success message."""
        self.console.print(f"[green]✓ {message}[/green]")

    def print_info(self, message: str):
        """Print an info message."""
        self.console.print(f"[blue]ℹ {message}[/blue]")

    def print_warning(self, message: str):
        """Print a warning message."""
        self.console.print(f"[yellow]⚠ {message}[/yellow]")

    def print_thought(self, thought: str):
        """Print AI thinking/reasoning."""
        if self.verbose:
            self.console.print(f"[dim italic]💭 {thought[:200]}...[/dim italic]")

    def prompt_input(self) -> str:
        """
        Get user input with styled prompt (sync version).

        Features:
        - Up/Down arrows: Navigate command history
        - Ctrl+R: Reverse search through history
        - History persisted to ~/.tarang/history
        """
        import asyncio

        try:
            if self._prompt_session:
                # Check if we're in an async context
                try:
                    loop = asyncio.get_running_loop()
                    # We're in async context - use nest_asyncio or run in executor
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor() as executor:
                        future = executor.submit(self._prompt_session.prompt, "You> ")
                        return future.result()
                except RuntimeError:
                    # No running loop - use sync version
                    return self._prompt_session.prompt("You> ")
            else:
                # Fallback to Rich prompt (no history)
                return Prompt.ask("[bold cyan]You[/]", console=self.console)
        except (KeyboardInterrupt, EOFError):
            return ""

    async def prompt_input_async(self) -> str:
        """
        Get user input with styled prompt (async version).

        Use this when calling from async context.
        """
        try:
            if self._prompt_session:
                return await self._prompt_session.prompt_async("You> ")
            else:
                # Fallback - run sync in executor
                import asyncio
                loop = asyncio.get_event_loop()
                return await loop.run_in_executor(
                    None,
                    lambda: Prompt.ask("[bold cyan]You[/]", console=self.console)
                )
        except (KeyboardInterrupt, EOFError):
            return ""

    def confirm(self, message: str, default: bool = True) -> bool:
        """Ask for confirmation."""
        return Confirm.ask(message, console=self.console, default=default)

    def print_edits_preview(self, edits: List[Dict[str, Any]]) -> bool:
        """
        Preview edits and ask for confirmation.

        Returns True if user accepts, False otherwise.
        """
        self.console.print()
        self.console.print(Rule("[bold]Proposed Changes[/]", style="yellow"))

        for edit in edits:
            file_path = edit.get("file", "unknown")
            description = edit.get("description", "")

            # Create edit panel
            content = Text()
            content.append(f"📄 {file_path}\n", style="bold")
            if description:
                content.append(f"   {description}", style="dim")

            self.console.print(content)

            # Show diff preview if available
            if edit.get("search") and edit.get("replace"):
                self._print_search_replace_diff(edit["search"], edit["replace"])
            elif edit.get("diff"):
                self._print_diff(edit["diff"])
            elif edit.get("content"):
                self.console.print(f"   [dim]New file: {len(edit['content'])} chars[/dim]")

            self.console.print()

        self.console.print(Rule(style="yellow"))
        return self.confirm("[yellow]Apply these changes?[/]", default=True)

    def _print_search_replace_diff(self, search: str, replace: str):
        """Print a search/replace diff."""
        lines = []
        for line in search.split("\n")[:5]:
            lines.append(f"[red]- {line}[/red]")
        if len(search.split("\n")) > 5:
            lines.append("[dim]...[/dim]")
        for line in replace.split("\n")[:5]:
            lines.append(f"[green]+ {line}[/green]")
        if len(replace.split("\n")) > 5:
            lines.append("[dim]...[/dim]")

        for line in lines:
            self.console.print(f"   {line}")

    def _print_diff(self, diff: str):
        """Print a unified diff with syntax highlighting."""
        syntax = Syntax(diff[:500], "diff", theme="monokai", line_numbers=False)
        self.console.print(syntax)

    def print_edit_result(self, file_path: str, success: bool, error: Optional[str] = None):
        """Print the result of applying an edit."""
        if success:
            self.console.print(f"  [green]✓[/green] {file_path}")
        else:
            self.console.print(f"  [red]✗[/red] {file_path}: {error}")

    def print_command_output(self, command: str, output: str, exit_code: int):
        """Print command execution output."""
        self.console.print(f"\n[bold]$ {command}[/bold]")
        if output:
            self.console.print(output[:500])
        if exit_code != 0:
            self.console.print(f"[yellow]Exit code: {exit_code}[/yellow]")

    def print_session_info(self, session_id: Optional[str], history_count: int):
        """Print session information."""
        table = Table(show_header=False, box=None)
        table.add_column(style="dim")
        table.add_column()
        table.add_row("Session:", session_id or "[dim]None[/dim]")
        table.add_row("History:", f"{history_count} messages")
        self.console.print(table)

    def print_goodbye(self):
        """Print goodbye message."""
        self.console.print("\n[bold cyan]👋 Goodbye![/bold cyan]\n")

    def _get_git_info(self, project_path: Path) -> Optional[str]:
        """Get current git branch and status."""
        try:
            # Get branch name
            result = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=project_path,
                capture_output=True,
                text=True,
                timeout=2,
            )
            if result.returncode != 0:
                return None

            branch = result.stdout.strip()

            # Get status count
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=project_path,
                capture_output=True,
                text=True,
                timeout=2,
            )
            changes = len([l for l in result.stdout.strip().split("\n") if l])

            if changes > 0:
                return f"⎇ {branch} ({changes} changed)"
            return f"⎇ {branch}"

        except Exception:
            return None

    def git_commit(self, project_path: Path, message: Optional[str] = None) -> bool:
        """Commit changes with auto-generated or custom message."""
        try:
            # Check for changes
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=project_path,
                capture_output=True,
                text=True,
            )

            if not result.stdout.strip():
                self.print_info("No changes to commit")
                return False

            # Show what will be committed
            self.print_git_status(project_path)

            if not message:
                message = Prompt.ask(
                    "[yellow]Commit message[/]",
                    default="Update via Tarang",
                    console=self.console,
                )

            if not self.confirm(f"Commit with message: '{message}'?"):
                return False

            # Stage and commit
            subprocess.run(["git", "add", "-A"], cwd=project_path, check=True)
            subprocess.run(
                ["git", "commit", "-m", message],
                cwd=project_path,
                check=True,
            )

            self.print_success("Changes committed")
            return True

        except subprocess.CalledProcessError as e:
            self.print_error(f"Git error: {e}")
            return False

    def git_diff(self, project_path: Path):
        """Show git diff."""
        try:
            result = subprocess.run(
                ["git", "diff", "--color=always"],
                cwd=project_path,
                capture_output=True,
                text=True,
            )
            if result.stdout:
                self.console.print(result.stdout)
            else:
                self.print_info("No unstaged changes")
        except Exception as e:
            self.print_error(f"Git error: {e}")
