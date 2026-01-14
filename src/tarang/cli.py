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
from rich.prompt import Prompt

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

    # Check authentication - prompt to login if needed
    if not auth.is_authenticated():
        ui.console.print("[yellow]Not logged in.[/]")
        if ui.confirm("Login now?", default=True):
            try:
                asyncio.run(auth.login())
                ui.print_success("Login successful!")
            except Exception as e:
                ui.print_error(f"Login failed: {e}", recoverable=False)
                sys.exit(1)
        else:
            ui.print_info("Run [cyan]/login[/] when ready.")
            sys.exit(0)

    # Check OpenRouter key - prompt to set if needed
    if not auth.has_openrouter_key():
        ui.console.print("[yellow]OpenRouter API key not set.[/]")
        key = Prompt.ask("[cyan]Enter your OpenRouter API key[/]", password=True)
        if key and key.strip():
            auth.save_openrouter_key(key.strip())
            ui.print_success("API key saved!")
        else:
            ui.print_info("Run [cyan]tarang config --openrouter-key YOUR_KEY[/] to set later.")
            sys.exit(0)

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
                        instr = await ui.prompt_input_async()
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


async def _ensure_index(ui: TarangConsole, project_path: Path, verbose: bool) -> None:
    """
    Smart indexing strategy:
    - Small projects (<100 files): Auto-index silently
    - Large projects: Prompt user
    - Already indexed: Skip
    """
    from tarang.context import ProjectIndexer
    import os

    indexer = ProjectIndexer(project_path)

    # Check if already indexed
    if indexer.exists() and not indexer.is_stale():
        if verbose:
            stats = indexer.stats()
            ui.console.print(f"[dim]Index ready: {stats.get('chunks', 0)} chunks, {stats.get('symbols', 0)} symbols[/dim]")
        return

    # Count project files quickly (without full scan)
    file_count = 0
    SMALL_PROJECT_THRESHOLD = 100
    IGNORE_DIRS = {".git", "node_modules", "venv", ".venv", "__pycache__", "dist", "build", ".tarang"}

    for root, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
        file_count += len([f for f in files if not f.startswith(".")])
        if file_count > SMALL_PROJECT_THRESHOLD:
            break  # Large project, stop counting

    is_small = file_count <= SMALL_PROJECT_THRESHOLD

    if is_small:
        # Auto-index silently for small projects
        ui.console.print("[dim]Building code index...[/dim]")
        try:
            result = indexer.build(force=False)
            ui.console.print(f"[dim green]✓ Indexed {result.files_indexed} files ({result.chunks_created} chunks)[/dim green]")
        except Exception as e:
            ui.console.print(f"[dim yellow]Index build skipped: {e}[/dim yellow]")
    else:
        # Prompt for large projects
        ui.console.print(f"[yellow]Project has {file_count}+ files and no code index.[/yellow]")
        if ui.confirm("Build code index for smarter context? (takes ~30s)", default=True):
            ui.console.print("[dim]Building code index...[/dim]")
            try:
                result = indexer.build(force=False)
                ui.console.print(f"[green]✓ Indexed {result.files_indexed} files ({result.chunks_created} chunks, {result.duration_ms}ms)[/green]")
            except Exception as e:
                ui.print_error(f"Index build failed: {e}")
        else:
            ui.console.print("[dim]Skipped. Run /index manually when ready.[/dim]")


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
    2. Send POST /api/execute with instruction + context
    3. Backend streams SSE events (status, tool_request, plan, change, etc.)
    4. When tool_request received, execute tool locally and POST /api/callback
    5. Backend continues streaming after receiving callback
    6. Apply file changes locally when complete

    Keyboard controls:
    - ESC: Cancel current execution
    - SPACE: Pause and add extra instruction
    """
    from tarang.context_collector import collect_context, ProjectContext
    from tarang.context import get_retriever, ProjectIndexer
    from tarang.stream import TarangStreamClient, EventType, FileChange
    from tarang.ui.keyboard import KeyboardMonitor, KeyAction, create_keyboard_hints

    # =========================================================================
    # Smart Indexing on Session Start
    # =========================================================================
    await _ensure_index(ui, project_path, verbose)

    # Create keyboard monitor first (needed for callbacks)
    keyboard = KeyboardMonitor(
        console=ui.console,
        on_status=lambda msg: ui.console.print(msg)
    )

    # Create stream client with keyboard callbacks for clean prompts
    client = TarangStreamClient(
        base_url=creds.get("backend_url"),
        token=creds.get("token"),
        openrouter_key=creds.get("openrouter_key"),
        project_root=str(project_path),
        verbose=verbose,
        on_input_start=keyboard.stop,   # Pause keyboard monitor
        on_input_end=keyboard.start,    # Resume keyboard monitor
    )

    # Debug: Show backend URL
    if verbose:
        ui.console.print(f"[dim]Backend: {client.base_url}[/dim]")

    # Print instructions with matching colors
    ui.print_instructions()

    while True:
        # Get instruction from user
        if not instruction:
            try:
                instruction = await ui.prompt_input_async()
                if not instruction.strip():
                    continue

                # Handle slash commands
                if instruction.startswith("/"):
                    if await _handle_slash_command(ui, instruction, project_path):
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

        # Try indexed retrieval first (BM25 + KG)
        retriever = get_retriever(project_path)
        if retriever and retriever.is_ready:
            # Use smart retrieval
            result = retriever.retrieve(instruction, hops=1, max_chunks=10)
            context = ProjectContext(
                cwd=str(project_path),
                files=[],  # Will be populated below
                relevant_files=[],  # Not used with indexed retrieval
            )
            # Attach indexed context to be sent to backend
            context._indexed_context = result.to_context_dict()
            if verbose:
                stats = result.stats
                ui.console.print(f"[dim]Retrieved {stats.get('total_chunks', 0)} chunks, {stats.get('expanded_symbols', 0)} connected symbols[/dim]")
        else:
            # Fall back to old context collection
            context = collect_context(str(project_path), instruction)
            if verbose:
                ui.console.print(f"[dim]Found {len(context.files)} files, {len(context.relevant_files)} relevant[/dim]")
                ui.console.print("[dim]Tip: Run /index for smarter context retrieval[/dim]")

        # Stream execution with tool callbacks
        ui.console.print()
        changes_to_apply = []
        current_phase = None
        extra_instructions = []  # Queue of extra instructions from SPACE

        # Start keyboard monitoring
        keyboard.start()

        try:
            async for event in client.execute(instruction, context):
                # Check for keyboard actions
                action = keyboard.state.consume_action()

                if action == KeyAction.CANCEL:
                    ui.console.print("\n[yellow]⏹ Cancelling...[/yellow]")
                    await client.cancel()
                    break

                elif action == KeyAction.PAUSE:
                    # Stop monitoring temporarily for clean input
                    keyboard.stop()
                    ui.console.print("\n[bold cyan]━━━ Paused ━━━[/bold cyan]")
                    try:
                        extra = input("[cyan]Add instruction:[/cyan] ").strip()
                        if extra:
                            extra_instructions.append(extra)
                            ui.console.print(f"[green]✓ Queued:[/green] {extra[:50]}...")
                    except (KeyboardInterrupt, EOFError):
                        pass
                    ui.console.print("[bold cyan]━━━ Resuming ━━━[/bold cyan]\n")
                    keyboard.start()

                if event.type == EventType.STATUS:
                    msg = event.data.get("message", "Working...")
                    phase = event.data.get("phase", "")
                    worker = event.data.get("worker", "")
                    delegation = event.data.get("delegation", "")
                    task = event.data.get("task", "")

                    # Worker start/done events
                    if worker:
                        if "completed" in msg.lower() or "done" in msg.lower():
                            client.formatter.show_worker_done(worker, success=True)
                        else:
                            client.formatter.show_worker_start(worker, task)
                    # Delegation events
                    elif delegation:
                        client.formatter.show_delegation("agent", delegation, task)
                    # Phase transitions
                    elif phase and phase != current_phase:
                        current_phase = phase
                        client.formatter.show_phase_start(phase)
                    elif verbose:
                        ui.console.print(f"[dim]{msg}[/dim]")

                elif event.type == EventType.THINKING:
                    # Agent thinking/reasoning
                    msg = event.data.get("message", "Thinking...")

                    # Skip "Using..." tool messages - the tool result will show instead
                    if "Using " in msg and any(tool in msg for tool in ("read_file", "list_files", "search_files", "search_code", "get_file_info", "write_file", "edit_file", "shell")):
                        continue

                    # Extract worker name if present (e.g., "[explorer] Analyzing structure...")
                    if msg.startswith("[") and "]" in msg:
                        worker_end = msg.index("]")
                        worker_name = msg[1:worker_end]
                        action = msg[worker_end + 2:]

                        # Skip tool-related messages (handled by tool output)
                        if action.strip().startswith("Using "):
                            continue

                        if verbose:
                            ui.console.print(f"  [dim cyan]💭 {worker_name}: {action}[/dim cyan]")
                        else:
                            # Show actual thinking, skip generic "Step N" style messages
                            if action and not action.startswith("Step "):
                                ui.console.print(f"  [dim]💭 {action[:60]}{'...' if len(action) > 60 else ''}[/dim]")
                    else:
                        if verbose:
                            ui.console.print(f"  [dim cyan]💭 {msg}[/dim cyan]")

                elif event.type == EventType.TOOL_DONE:
                    # Tool execution completed (already handled internally)
                    tool = event.data.get("tool", "")
                    if verbose:
                        ui.console.print(f"  [dim]  ✓ {tool}[/dim]")

                elif event.type == EventType.PLAN:
                    # Strategic plan from orchestrator
                    plan = event.data.get("plan", event.data)
                    phases = event.data.get("phases", [])

                    # Use new formatter if we have phases
                    if phases or plan.get("prd"):
                        client.formatter.show_strategic_plan(plan)
                    else:
                        # Legacy format
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

                    # Also show task decomposition if phases have tasks
                    if phases:
                        client.formatter.show_task_decomposition(phases)

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
                    content = _extract_content(event.data)
                    ui.print_message(content, title="Answer")

                elif event.type == EventType.ERROR:
                    msg = event.data.get("message", "Unknown error")
                    ui.print_error(msg)

                elif event.type == EventType.COMPLETE:
                    if verbose:
                        ui.console.print("[dim]✓ Complete[/dim]")

            # Apply changes - stop keyboard monitor for clean prompts
            keyboard.stop()

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
            extra_instructions.clear()  # Clear queue on cancel

        finally:
            # Always stop keyboard monitoring
            keyboard.stop()

        # Process queued extra instructions or reset
        if extra_instructions:
            instruction = extra_instructions.pop(0)
            ui.console.print(f"[cyan]→ Next queued:[/cyan] {instruction[:60]}...")
        else:
            instruction = None


async def _handle_slash_command(ui: TarangConsole, cmd: str, project_path: Path) -> bool:
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

    if cmd == "/login":
        from tarang.client import TarangAuth
        auth = TarangAuth()
        if auth.is_authenticated():
            ui.print_info("Already logged in.")
            if not ui.confirm("Login again?", default=False):
                return True
        ui.print_info("Starting authentication...")
        try:
            await auth.login()
            ui.print_success("Login successful!")
        except Exception as e:
            ui.print_error(f"Login failed: {e}")
        return True

    if cmd == "/config":
        from tarang.client import TarangAuth
        from tarang.stream import TarangStreamClient
        auth = TarangAuth()
        creds = auth.load_credentials() or {}

        # Show current status
        ui.console.print("\n[bold]Configuration[/]")
        token_status = "[green]✓[/]" if creds.get("token") else "[red]✗[/]"
        key_status = "[green]✓[/]" if creds.get("openrouter_key") else "[red]✗[/]"
        custom_backend = creds.get("backend_url")
        backend_display = custom_backend or "[dim](default)[/dim]"
        ui.console.print(f"  Login:      {token_status}")
        ui.console.print(f"  API Key:    {key_status}")
        ui.console.print(f"  Backend:    {backend_display}")

        # Prompt for OpenRouter key
        ui.console.print()
        current_key = "(keep current)" if creds.get("openrouter_key") else ""
        key = Prompt.ask("[cyan]OpenRouter API key[/]", default=current_key, password=True)
        if key and key != "(keep current)":
            auth.save_openrouter_key(key.strip())
            ui.print_success("API key saved!")

        # Prompt for backend URL
        ui.console.print("[dim]Leave empty or type 'default' to use default backend[/dim]")
        current_display = custom_backend or "(default)"
        backend = Prompt.ask("[cyan]Backend URL[/]", default=current_display)
        if backend in ("", "(default)", "default"):
            if custom_backend:
                # Reset to default - remove from config
                auth.save_credentials(backend_url=None)
                ui.print_success("Backend reset to default")
        elif backend != current_display:
            auth.save_credentials(backend_url=backend.strip().rstrip("/"))
            ui.print_success(f"Backend set to: {backend}")

        return True

    if cmd.startswith("/index"):
        # Parse flags
        force = "--force" in cmd or "-f" in cmd
        show_stats = "--stats" in cmd or "-s" in cmd

        from tarang.context import ProjectIndexer

        indexer = ProjectIndexer(project_path)

        if show_stats:
            stats = indexer.stats()
            if not stats.get("indexed"):
                ui.console.print("[yellow]Project not indexed.[/] Run [cyan]/index[/] to build index.")
            else:
                ui.console.print("\n[bold]Index Statistics[/]")
                ui.console.print(f"  Files:      {stats['files']}")
                ui.console.print(f"  Chunks:     {stats['chunks']}")
                ui.console.print(f"  Symbols:    {stats['symbols']}")
                ui.console.print(f"  Edges:      {stats['edges']}")
                if stats.get("chunk_types"):
                    ui.console.print(f"  Types:      {stats['chunk_types']}")
            return True

        # Build or update index
        ui.console.print("[dim]Indexing project...[/dim]")

        try:
            result = indexer.build(force=force)

            ui.console.print(f"  [green]✓[/] Scanned: {result.files_scanned} files")
            ui.console.print(f"  [green]✓[/] Indexed: {result.files_indexed} files")
            ui.console.print(f"  [green]✓[/] Chunks:  {result.chunks_created}")
            ui.console.print(f"  [green]✓[/] Symbols: {result.symbols_created}")
            ui.console.print(f"  [green]✓[/] Edges:   {result.edges_created}")
            ui.console.print(f"  [dim]Duration: {result.duration_ms}ms[/dim]")

            if result.errors:
                ui.console.print(f"\n[yellow]Warnings ({len(result.errors)}):[/]")
                for err in result.errors[:5]:
                    ui.console.print(f"  [dim]{err}[/dim]")
                if len(result.errors) > 5:
                    ui.console.print(f"  [dim]... and {len(result.errors) - 5} more[/dim]")

            ui.console.print("\n[green]Index built![/] Stored in [cyan].tarang/index/[/]")

        except Exception as e:
            ui.print_error(f"Indexing failed: {e}")

        return True

    if cmd in ("/exit", "/quit", "/q"):
        if ui.confirm("Exit Tarang?", default=True):
            ui.print_goodbye()
            sys.exit(0)
        return True

    return False


def _extract_content(data) -> str:
    """
    Extract human-readable content from event data.

    Handles various formats:
    - Dict with human_readable_summary
    - Dict with text field
    - Dict with payload.message
    - String that looks like a dict
    - Plain string
    """
    import ast
    import json

    # If it's a string, try to parse it as dict
    if isinstance(data, str):
        # Try JSON first
        try:
            data = json.loads(data)
        except (json.JSONDecodeError, ValueError):
            # Try Python literal (handles single quotes)
            try:
                data = ast.literal_eval(data)
            except (ValueError, SyntaxError):
                # It's just a plain string
                return data

    # Now data should be a dict
    if isinstance(data, dict):
        # Priority order for extraction
        if "human_readable_summary" in data:
            return data["human_readable_summary"]
        if "text" in data:
            # text might itself be a nested structure
            return _extract_content(data["text"])
        if "payload" in data and isinstance(data["payload"], dict):
            if "message" in data["payload"]:
                return data["payload"]["message"]
        if "message" in data:
            return data["message"]
        if "content" in data:
            return data["content"]
        # Fallback - return as formatted string
        return str(data)

    return str(data)


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
