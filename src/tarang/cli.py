"""
Tarang CLI - AI coding assistant with hybrid WebSocket architecture.

Just type your instructions. The orchestrator handles everything:
- Simple queries (explanations, questions)
- Complex tasks (multi-step implementations)
- Long-running jobs with phases and milestones

Usage:
    tarang login                        # Authenticate with GitHub
    tarang config --openrouter-key KEY  # Set API key
    tarang "explain the project"        # Run instruction
    tarang                              # Interactive mode
"""
from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path
from typing import Optional, List, Dict

import click

from tarang import __version__
from tarang.client import TarangAPIClient, TarangAuth
from tarang.ui import TarangConsole


# Global console instance
console: Optional[TarangConsole] = None


def get_console(verbose: bool = False) -> TarangConsole:
    """Get or create console instance."""
    global console
    if console is None:
        console = TarangConsole(verbose=verbose)
    return console


@click.group(invoke_without_command=True)
@click.option("--project-dir", "-p", default=".", help="Project directory")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose output")
@click.option("--yes", "-y", is_flag=True, help="Auto-approve all operations")
@click.version_option(version=__version__, prog_name="Tarang")
@click.pass_context
def cli(ctx, project_dir: str, verbose: bool, yes: bool):
    """
    Tarang - AI Coding Agent.

    Just type your instructions. The orchestrator handles everything:
    - Simple queries (explanations, questions)
    - Complex tasks (multi-step implementations)
    - Long-running jobs with phases and milestones

    Quick start:
        tarang login                        # Authenticate
        tarang config --openrouter-key KEY  # Set API key
        tarang run "explain the project"    # Run instruction
        tarang                              # Interactive mode

    Examples:
        tarang run "add user authentication"
        tarang run "fix the login bug"
        tarang run "refactor the API" -y    # Auto-approve changes
    """
    if ctx.invoked_subcommand is None:
        # Store options in context for the run function
        ctx.ensure_object(dict)
        ctx.obj["instruction"] = None
        ctx.obj["project_dir"] = project_dir
        ctx.obj["verbose"] = verbose
        ctx.obj["auto_approve"] = yes
        ctx.invoke(run)


@cli.command()
def login():
    """
    Authenticate with Tarang via GitHub.

    Opens a browser window for OAuth authentication.
    Your token is stored securely in ~/.tarang/config.json
    """
    ui = get_console()
    auth = TarangAuth()

    if auth.is_authenticated():
        ui.print_info("Already logged in.")
        if not ui.confirm("Login again?", default=False):
            return

    ui.print_info("Starting authentication...")

    try:
        asyncio.run(auth.login())
        ui.print_success("Login successful!")
        ui.print_info("Credentials saved to ~/.tarang/config.json")

        if not auth.has_openrouter_key():
            ui.console.print("\n[yellow]Next step:[/] Set your OpenRouter API key:")
            ui.console.print("  [cyan]tarang config --openrouter-key YOUR_KEY[/]")

    except TimeoutError:
        ui.print_error("Authentication timed out. Please try again.", recoverable=False)
        sys.exit(1)
    except Exception as e:
        ui.print_error(f"Authentication failed: {e}", recoverable=False)
        sys.exit(1)


@cli.command()
@click.option("--openrouter-key", "-k", help="Set your OpenRouter API key")
@click.option("--backend-url", "-u", help="Set custom backend URL")
@click.option("--show", is_flag=True, help="Show current configuration")
def config(openrouter_key: str, backend_url: str, show: bool):
    """
    Configure Tarang settings.

    Set your OpenRouter API key for LLM access:
        tarang config --openrouter-key sk-or-...

    View current config:
        tarang config --show
    """
    ui = get_console()
    auth = TarangAuth()

    if show:
        creds = auth.load_credentials() or {}
        ui.console.print("\n[bold]Tarang Configuration[/] (~/.tarang/config.json)")
        ui.console.print("─" * 50)

        token_status = "[green]✓ configured[/]" if creds.get("token") else "[red]✗ not set[/]"
        key_status = "[green]✓ configured[/]" if creds.get("openrouter_key") else "[red]✗ not set[/]"

        ui.console.print(f"Token:         {token_status}")
        ui.console.print(f"OpenRouter:    {key_status}")
        if creds.get("backend_url"):
            ui.console.print(f"Backend URL:   {creds.get('backend_url')}")
        ui.console.print()
        return

    if openrouter_key:
        if not openrouter_key.startswith("sk-or-"):
            ui.print_warning("OpenRouter keys usually start with 'sk-or-'")

        auth.save_openrouter_key(openrouter_key)
        ui.print_success("OpenRouter API key saved.")

    if backend_url:
        auth.save_credentials(backend_url=backend_url)
        ui.print_success(f"Backend URL set to: {backend_url}")

    if not openrouter_key and not backend_url:
        ui.print_info("No configuration changes made. Use --help to see options.")


@cli.command()
@click.argument("instruction", required=False)
@click.option("--project-dir", "-p", default=None, help="Project directory")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose output")
@click.option("--yes", "-y", is_flag=True, help="Auto-approve all operations")
@click.pass_context
def run(ctx, instruction: str, project_dir: str, verbose: bool, yes: bool):
    """
    Run an instruction or start interactive mode.

    Examples:
        tarang run "explain the project"
        tarang run "add authentication" -y
        tarang run                           # Interactive mode
    """
    # Get options from parent context or use provided ones
    obj = ctx.obj or {}
    instruction = instruction or obj.get("instruction")
    project_dir = project_dir or obj.get("project_dir", ".")
    verbose = verbose or obj.get("verbose", False)
    auto_approve = yes or obj.get("auto_approve", False)

    ui = get_console(verbose)
    auth = TarangAuth()

    # Check authentication
    if not auth.is_authenticated():
        ui.print_error("Not logged in. Run 'tarang login' first.", recoverable=False)
        sys.exit(1)

    if not auth.has_openrouter_key():
        ui.print_error("OpenRouter key not set.", recoverable=False)
        ui.console.print("Run: [cyan]tarang config --openrouter-key YOUR_KEY[/]")
        sys.exit(1)

    # Resolve project directory
    project_path = Path(project_dir).resolve()
    if not project_path.exists():
        ui.print_error(f"Project directory not found: {project_dir}", recoverable=False)
        sys.exit(1)

    # Show banner
    ui.print_banner(__version__, project_path)

    # Load credentials
    creds = auth.load_credentials()

    # Run the SSE stream session (simpler than WebSocket)
    asyncio.run(_run_stream_session(
        ui=ui,
        creds=creds,
        project_path=project_path,
        instruction=instruction,
        verbose=verbose,
        auto_approve=auto_approve,
    ))


async def _run_hybrid_session(
    ui: TarangConsole,
    creds: dict,
    project_path: Path,
    instruction: Optional[str],
    verbose: bool,
    auto_approve: bool,
):
    """Run the hybrid WebSocket session."""
    import signal
    from tarang.ws import TarangWSClient, ToolExecutor, MessageHandlers

    # Track if we're in the middle of execution
    is_executing = False
    cancelled = False

    # Create WebSocket client
    ws_client = TarangWSClient(
        base_url=creds.get("backend_url"),
        token=creds.get("token"),
        openrouter_key=creds.get("openrouter_key"),
    )

    # Create tool executor
    executor = ToolExecutor(project_root=str(project_path))

    # Create approval callback
    def on_approval(tool: str, description: str, args: dict) -> bool:
        if auto_approve:
            ui.console.print(f"  [dim]Auto-approved[/dim]")
            return True
        return ui.confirm(f"Apply?", default=True)

    # Create message handlers
    handlers = MessageHandlers(
        console=ui.console,
        executor=executor,
        on_approval=on_approval,
        verbose=verbose,
        auto_approve=auto_approve,
    )

    conversation_history: List[Dict[str, str]] = []

    def handle_slash_command(cmd: str) -> bool:
        """Handle slash commands."""
        cmd = cmd.lower().strip()

        if cmd in ("/help", "/h", "/?"):
            ui.print_help()
            return True

        if cmd in ("/git", "/status"):
            ui.print_git_status(project_path)
            return True

        if cmd in ("/commit", "/c"):
            ui.git_commit(project_path)
            return True

        if cmd in ("/diff", "/d"):
            ui.git_diff(project_path)
            return True

        if cmd == "/clear":
            conversation_history.clear()
            ui.print_success("Conversation history cleared")
            return True

        if cmd in ("/exit", "/quit", "/q"):
            ui.print_goodbye()
            sys.exit(0)

        return False

    async def send_cancel():
        """Send cancel message to backend."""
        nonlocal cancelled
        if not cancelled:
            cancelled = True
            try:
                await ws_client.cancel()
                ui.console.print("\n[yellow]⏹ Cancelling...[/yellow]")
            except Exception:
                pass

    try:
        async with ws_client:
            if verbose:
                ui.console.print(f"[dim]Session: {ws_client.session_id}[/dim]")

            ui.console.print("[dim]Type your instructions, or /help for commands[/dim]")
            ui.console.print("[dim]Press Ctrl+C during execution to cancel[/dim]\n")

            # Run initial instruction if provided
            instr = instruction
            while True:
                cancelled = False

                if not instr:
                    # Get instruction from user
                    try:
                        instr = ui.prompt_input()
                        if not instr.strip():
                            continue

                        # Handle slash commands
                        if instr.startswith("/"):
                            if handle_slash_command(instr):
                                instr = None
                                continue

                        # Handle exit
                        if instr.lower() in ("exit", "quit", "q"):
                            ui.print_goodbye()
                            break

                    except (KeyboardInterrupt, EOFError):
                        ui.print_goodbye()
                        break

                # Execute instruction via WebSocket
                ui.console.print()
                is_executing = True

                try:
                    async for event in ws_client.execute(instr, str(project_path)):
                        if cancelled:
                            ui.console.print("[yellow]Execution cancelled[/yellow]")
                            break

                        should_continue = await handlers.handle(event, ws_client)
                        if not should_continue:
                            break

                except KeyboardInterrupt:
                    # Ctrl+C during execution
                    await send_cancel()
                    ui.console.print()

                    # Show what was completed
                    summary = handlers.get_summary()
                    if summary.get("files_changed"):
                        ui.console.print("[dim]Files changed before cancellation:[/dim]")
                        for f in summary["files_changed"]:
                            ui.console.print(f"  [dim]- {f}[/dim]")

                finally:
                    is_executing = False

                # Track conversation (even if cancelled)
                summary = handlers.get_summary()
                if instr:
                    conversation_history.append({"role": "user", "content": instr})
                    status = "Cancelled" if cancelled else "Done"
                    conversation_history.append({"role": "assistant", "content": status})

                # Reset for next instruction
                instr = None
                handlers.state = type(handlers.state)()

    except ConnectionError as e:
        ui.print_error(f"Connection failed: {e}")
        ui.console.print("[dim]Make sure the backend is running.[/dim]")
        sys.exit(1)
    except KeyboardInterrupt:
        if is_executing:
            ui.console.print("\n[yellow]⏹ Cancelled[/yellow]")
        else:
            ui.console.print()
            ui.print_goodbye()
        sys.exit(130)
    except Exception as e:
        ui.print_error(str(e), recoverable=False)
        if verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


async def _run_stream_session(
    ui: TarangConsole,
    creds: dict,
    project_path: Path,
    instruction: Optional[str],
    verbose: bool,
    auto_approve: bool,
):
    """
    Run the SSE + REST callback session.

    Flow:
    1. Collect local context (file list, relevant files)
    2. Send POST /v3/execute with instruction + context
    3. Backend streams SSE events (status, tool_request, plan, change, etc.)
    4. When tool_request received, execute tool locally and POST /v3/callback
    5. Backend continues streaming after receiving callback
    6. Apply file changes locally when complete
    """
    from tarang.context_collector import collect_context
    from tarang.stream import TarangStreamClient, EventType, FileChange

    # Create stream client with project root for local tool execution
    client = TarangStreamClient(
        base_url=creds.get("backend_url"),
        token=creds.get("token"),
        openrouter_key=creds.get("openrouter_key"),
        project_root=str(project_path),
        verbose=verbose,
    )

    ui.console.print("[dim]Type your instructions, or /help for commands[/dim]")
    ui.console.print("[dim]Press Ctrl+C to cancel[/dim]\n")

    while True:
        # Get instruction from user
        if not instruction:
            try:
                instruction = ui.prompt_input()
                if not instruction.strip():
                    continue

                # Handle slash commands
                if instruction.startswith("/"):
                    if _handle_slash_command(ui, instruction, project_path):
                        instruction = None
                        continue

                # Handle exit
                if instruction.lower() in ("exit", "quit", "q"):
                    ui.print_goodbye()
                    break

            except (KeyboardInterrupt, EOFError):
                ui.print_goodbye()
                break

        # Collect local context (for initial context in request)
        ui.console.print("[dim]Collecting context...[/dim]")
        context = collect_context(str(project_path), instruction)
        if verbose:
            ui.console.print(f"[dim]Found {len(context.files)} files, {len(context.relevant_files)} relevant[/dim]")

        # Stream execution with tool callbacks
        ui.console.print()
        changes_to_apply = []
        current_phase = None

        try:
            async for event in client.execute(instruction, context):
                if event.type == EventType.STATUS:
                    msg = event.data.get("message", "Working...")
                    phase = event.data.get("phase", "")

                    # Show phase transitions
                    if phase and phase != current_phase:
                        current_phase = phase
                        phase_icons = {"explore": "🔍", "plan": "📋", "implement": "⚡", "generate": "✨"}
                        icon = phase_icons.get(phase, "•")
                        ui.console.print(f"[cyan]{icon} {phase.title()}[/cyan]")

                    if verbose:
                        ui.console.print(f"[dim]{msg}[/dim]")

                elif event.type == EventType.THINKING:
                    # Agent thinking/reasoning
                    msg = event.data.get("message", "Thinking...")
                    iteration = event.data.get("iteration", 0)
                    if verbose:
                        ui.console.print(f"[dim cyan]💭 {msg}[/dim cyan]")
                    else:
                        # Show minimal thinking indicator
                        ui.console.print(f"[dim]• {msg}[/dim]")

                elif event.type == EventType.TOOL_DONE:
                    # Tool execution completed (already handled internally)
                    tool = event.data.get("tool", "")
                    if verbose:
                        ui.console.print(f"[dim]  ✓ {tool}[/dim]")

                elif event.type == EventType.PLAN:
                    desc = event.data.get("description", "")
                    steps = event.data.get("steps", [])
                    files = event.data.get("files", [])

                    if desc:
                        ui.console.print(f"\n[bold]Plan:[/bold] {desc}")
                    if steps:
                        ui.console.print("[dim]Steps:[/dim]")
                        for i, step in enumerate(steps[:5], 1):
                            ui.console.print(f"  {i}. {step}")
                    if files:
                        ui.console.print("[dim]Files to modify:[/dim]")
                        for f in files[:10]:
                            ui.console.print(f"  • {f}")

                elif event.type == EventType.CHANGE:
                    change = FileChange.from_dict(event.data)
                    changes_to_apply.append(change)

                    # Show change preview
                    icon = "📝" if change.type == "edit" else "📄"
                    ui.console.print(f"\n[bold yellow]{icon} {change.type.title()}: {change.path}[/bold yellow]")
                    if change.description:
                        ui.console.print(f"[dim]{change.description}[/dim]")

                    if change.type == "create" and change.content:
                        # Show preview of new file
                        lines = change.content.splitlines()[:15]
                        preview = "\n".join(lines)
                        if len(change.content.splitlines()) > 15:
                            preview += "\n... (truncated)"
                        ui.console.print(f"[dim]```\n{preview}\n```[/dim]")

                    elif change.type == "edit" and change.search and change.replace:
                        # Show diff preview
                        search_preview = change.search[:100] + "..." if len(change.search) > 100 else change.search
                        replace_preview = change.replace[:100] + "..." if len(change.replace) > 100 else change.replace
                        ui.console.print(f"[red]- {search_preview}[/red]")
                        ui.console.print(f"[green]+ {replace_preview}[/green]")

                elif event.type == EventType.CONTENT:
                    # Text response (for queries)
                    content = event.data.get("text", "")
                    ui.print_message(content, title="Answer")

                elif event.type == EventType.ERROR:
                    msg = event.data.get("message", "Unknown error")
                    ui.print_error(msg)

                elif event.type == EventType.COMPLETE:
                    if verbose:
                        ui.console.print("[dim]✓ Complete[/dim]")

            # Apply changes
            if changes_to_apply:
                ui.console.print(f"\n[bold]Ready to apply {len(changes_to_apply)} change(s)[/bold]")

                for change in changes_to_apply:
                    if not auto_approve:
                        if not ui.confirm(f"Apply {change.type} to {change.path}?", default=True):
                            ui.console.print(f"[dim]Skipped: {change.path}[/dim]")
                            continue

                    # Apply the change
                    success = _apply_change(project_path, change, ui)
                    if success:
                        ui.console.print(f"[green]✓[/green] Applied: {change.path}")
                    else:
                        ui.console.print(f"[red]✗[/red] Failed: {change.path}")

                ui.console.print("\n[green]Done![/green]\n")
            else:
                ui.console.print()

        except KeyboardInterrupt:
            ui.console.print("\n[yellow]Cancelling...[/yellow]")
            await client.cancel()
            ui.console.print("[yellow]Cancelled[/yellow]")

        # Reset for next instruction
        instruction = None


def _handle_slash_command(ui: TarangConsole, cmd: str, project_path: Path) -> bool:
    """Handle slash commands. Returns True if handled."""
    cmd = cmd.lower().strip()

    if cmd in ("/help", "/h", "/?"):
        ui.print_help()
        return True

    if cmd in ("/git", "/status"):
        ui.print_git_status(project_path)
        return True

    if cmd in ("/commit", "/c"):
        ui.git_commit(project_path)
        return True

    if cmd in ("/diff", "/d"):
        ui.git_diff(project_path)
        return True

    if cmd == "/clear":
        ui.console.print("[green]Ready for new instructions[/green]")
        return True

    if cmd in ("/exit", "/quit", "/q"):
        ui.print_goodbye()
        sys.exit(0)

    return False


def _apply_change(project_path: Path, change, ui: TarangConsole) -> bool:
    """Apply a file change locally."""
    from tarang.stream import FileChange

    file_path = project_path / change.path

    try:
        if change.type == "create":
            # Create parent directories
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(change.content or "", encoding="utf-8")
            return True

        elif change.type == "edit":
            if not file_path.exists():
                ui.console.print(f"[red]File not found: {change.path}[/red]")
                return False

            content = file_path.read_text(encoding="utf-8")

            if change.search and change.search not in content:
                ui.console.print(f"[red]Search text not found in {change.path}[/red]")
                return False

            new_content = content.replace(change.search, change.replace or "", 1)
            file_path.write_text(new_content, encoding="utf-8")
            return True

        elif change.type == "delete":
            if file_path.exists():
                file_path.unlink()
            return True

        return False

    except Exception as e:
        ui.console.print(f"[red]Error applying change: {e}[/red]")
        return False


@cli.command()
@click.argument("query", required=True)
def ask(query: str):
    """Quick question without code generation."""
    ui = get_console()
    auth = TarangAuth()

    if not auth.has_openrouter_key():
        ui.print_error("OpenRouter key not set.")
        ui.console.print("Run: [cyan]tarang config --openrouter-key YOUR_KEY[/]")
        sys.exit(1)

    creds = auth.load_credentials()
    client = TarangAPIClient(creds.get("backend_url"))
    client.openrouter_key = creds.get("openrouter_key")

    try:
        with ui.thinking("Thinking..."):
            answer = asyncio.run(client.quick_ask(query))
        ui.print_message(answer, title="Answer")
    except Exception as e:
        ui.print_error(str(e))
        sys.exit(1)


@cli.command()
def status():
    """Show Tarang status and configuration."""
    ui = get_console()
    auth = TarangAuth()
    creds = auth.load_credentials() or {}

    ui.console.print(f"\n[bold cyan]Tarang[/] v{__version__}")
    ui.console.print("─" * 40)

    # Auth status
    if auth.is_authenticated():
        ui.console.print("[green]✓[/] Authentication: Logged in")
    else:
        ui.console.print("[red]✗[/] Authentication: Not logged in")
        ui.console.print("  Run: [cyan]tarang login[/]")

    # OpenRouter key
    if auth.has_openrouter_key():
        key = creds.get("openrouter_key", "")
        ui.console.print(f"[green]✓[/] OpenRouter Key: {key[:12]}...")
    else:
        ui.console.print("[red]✗[/] OpenRouter Key: Not set")
        ui.console.print("  Run: [cyan]tarang config --openrouter-key YOUR_KEY[/]")

    # Backend URL
    backend_url = creds.get("backend_url", TarangAPIClient.DEFAULT_BASE_URL)
    ui.console.print(f"[dim]Backend:[/] {backend_url}")

    # Test connectivity
    ui.console.print()
    with ui.thinking("Testing connection..."):
        try:
            import httpx
            response = httpx.get(f"{backend_url}/health", timeout=5)
            if response.status_code == 200:
                ui.print_success("Backend connected")
            else:
                ui.print_warning(f"Backend status: {response.status_code}")
        except Exception as e:
            ui.print_error(f"Cannot connect: {e}")

    ui.console.print()


@cli.command()
@click.option("--project-dir", "-p", default=".", help="Project directory")
@click.option("--force", "-f", is_flag=True, help="Don't ask for confirmation")
def clean(project_dir: str, force: bool):
    """Clean Tarang state from the project."""
    ui = get_console()
    project_path = Path(project_dir).resolve()
    tarang_dir = project_path / ".tarang"
    backup_dir = project_path / ".tarang_backups"

    if not tarang_dir.exists() and not backup_dir.exists():
        ui.print_info("No Tarang state to clean.")
        return

    if not force and not ui.confirm(f"Remove Tarang state from {project_path}?"):
        return

    if tarang_dir.exists():
        shutil.rmtree(tarang_dir)
        ui.print_success("Removed .tarang directory")

    if backup_dir.exists():
        shutil.rmtree(backup_dir)
        ui.print_success("Removed .tarang_backups directory")


@cli.command()
def logout():
    """Log out and clear saved credentials."""
    ui = get_console()
    auth = TarangAuth()

    if not auth.is_authenticated():
        ui.print_info("Not logged in.")
        return

    if ui.confirm("Clear all saved credentials?"):
        auth.clear_credentials()
        ui.print_success("Logged out. Credentials cleared.")


def main():
    """Main entry point."""
    cli()


if __name__ == "__main__":
    main()
