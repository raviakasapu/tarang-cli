"""
Tarang CLI - AI coding assistant with rich terminal UI.

The CLI handles local operations (files, shell, git) while the backend
handles all reasoning and orchestration.

Usage:
    tarang login                      # Authenticate with GitHub
    tarang config --openrouter-key    # Set API key
    tarang run "create a hello world" # Run instruction
    tarang                            # Interactive mode
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional, List, Dict, Any

import click

from tarang import __version__
from tarang.client import TarangAPIClient, TarangAuth, TarangResponse
from tarang.client.api_client import LocalContext
from tarang.context import SkeletonGenerator
from tarang.executor import DiffApplicator, ShadowLinter
from tarang.ui import TarangConsole, DiffViewer


# Global console instance
console: Optional[TarangConsole] = None


def get_console(verbose: bool = False) -> TarangConsole:
    """Get or create console instance."""
    global console
    if console is None:
        console = TarangConsole(verbose=verbose)
    return console


@click.group(invoke_without_command=True)
@click.version_option(version=__version__, prog_name="Tarang")
@click.pass_context
def cli(ctx):
    """
    Tarang - AI Coding Agent.

    A thin CLI that connects to the Tarang backend for AI-powered coding.

    Quick start:
        tarang login                   # Authenticate
        tarang config --openrouter-key YOUR_KEY
        tarang run "explain the project"
    """
    if ctx.invoked_subcommand is None:
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
@click.option("--project-dir", "-p", default=".", help="Project directory")
@click.option("--no-lint", is_flag=True, help="Skip linting after changes")
@click.option("--dry-run", is_flag=True, help="Preview changes without applying")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose output")
@click.option("--once", is_flag=True, help="Run single instruction and exit")
@click.option("--auto-commit", "-c", is_flag=True, help="Auto-commit after changes")
def run(
    instruction: str,
    project_dir: str,
    no_lint: bool,
    dry_run: bool,
    verbose: bool,
    once: bool,
    auto_commit: bool,
):
    """
    Run Tarang AI coding assistant.

    Without instruction: starts interactive mode
    With instruction: runs it and enters interactive mode (use --once to exit)

    Examples:
        tarang run                              # Interactive mode
        tarang run "explain the project"        # Run then continue
        tarang run "fix linter errors" --once   # Run and exit
        tarang run "add login" --dry-run        # Preview changes
    """
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

    # Initialize client
    creds = auth.load_credentials()
    client = TarangAPIClient(creds.get("backend_url"))
    client.token = creds.get("token")
    client.openrouter_key = creds.get("openrouter_key")

    # Show banner and project info
    ui.print_banner(__version__, project_path)

    with ui.thinking("Scanning project..."):
        skeleton_gen = SkeletonGenerator(project_path)
        skeleton = skeleton_gen.generate()

    ui.print_project_stats(skeleton.total_files, skeleton.total_lines)

    # Build context
    context = LocalContext(
        project_root=str(project_path),
        skeleton=skeleton.to_dict(),
    )

    # Initialize components
    diff_applicator = DiffApplicator(project_path)
    diff_viewer = DiffViewer(ui.console)
    linter = ShadowLinter(project_path)

    session_id = None
    conversation_history: List[Dict[str, str]] = []
    pending_changes: List[Dict[str, Any]] = []

    async def run_instruction(instr: str) -> str:
        """Run a single instruction and return response."""
        nonlocal session_id, context, pending_changes

        context.history = conversation_history[-6:]

        with ui.thinking():
            response = await client.execute(
                instruction=instr,
                context=context,
                session_id=session_id,
            )

        session_id = response.session_id

        # Handle response types
        if response.type == "error":
            ui.print_error(response.error or "Unknown error")
            return ""

        if verbose and response.thought_process:
            ui.print_thought(response.thought_process)

        # Handle edits
        if response.type == "edits" and response.edits:
            return await _handle_edits(
                response, ui, diff_applicator, diff_viewer, linter,
                client, project_path, no_lint, dry_run, auto_commit
            )

        # Handle commands
        if response.type == "command" and response.commands:
            return await _run_commands(response, project_path, ui)

        # Handle message
        if response.message:
            ui.print_message(response.message)
            return response.message

        return ""

    def handle_slash_command(cmd: str) -> bool:
        """Handle slash commands. Returns True if command was handled."""
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

        if cmd in ("/session", "/info"):
            ui.print_session_info(session_id, len(conversation_history))
            return True

        if cmd in ("/exit", "/quit", "/q"):
            ui.print_goodbye()
            sys.exit(0)

        return False

    try:
        # Run initial instruction if provided
        if instruction:
            response = asyncio.run(run_instruction(instruction))
            conversation_history.append({"role": "user", "content": instruction})
            conversation_history.append({"role": "assistant", "content": response or "Done"})

            if once:
                ui.print_success("Task completed")
                return

        # Interactive mode
        if not once:
            ui.console.print("[dim]Type your instructions, or /help for commands[/dim]\n")

        while not once:
            try:
                user_input = ui.prompt_input()

                if not user_input.strip():
                    continue

                # Handle slash commands
                if user_input.startswith("/"):
                    if handle_slash_command(user_input):
                        continue

                # Handle exit commands
                if user_input.lower() in ("exit", "quit", "q"):
                    ui.print_goodbye()
                    break

                # Run instruction
                response = asyncio.run(run_instruction(user_input))
                conversation_history.append({"role": "user", "content": user_input})
                conversation_history.append({"role": "assistant", "content": response or "Done"})

            except KeyboardInterrupt:
                ui.console.print()
                continue
            except EOFError:
                ui.print_goodbye()
                break

    except KeyboardInterrupt:
        ui.console.print("\n[yellow]Interrupted[/]")
        sys.exit(130)
    except Exception as e:
        ui.print_error(str(e), recoverable=False)
        if verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


async def _handle_edits(
    response: TarangResponse,
    ui: TarangConsole,
    diff_applicator: DiffApplicator,
    diff_viewer: DiffViewer,
    linter: ShadowLinter,
    client: TarangAPIClient,
    project_path: Path,
    no_lint: bool,
    dry_run: bool,
    auto_commit: bool,
) -> str:
    """Handle edits from backend response with preview."""
    edits = [
        {
            "file": e.file,
            "content": e.content,
            "search": e.search,
            "replace": e.replace,
            "diff": e.diff,
            "description": e.description,
        }
        for e in response.edits
    ]

    # Show preview and ask for confirmation
    if not ui.print_edits_preview(edits):
        ui.print_info("Changes cancelled")
        return "Changes cancelled by user"

    if dry_run:
        ui.print_info("Dry run - no changes applied")
        return "Dry run completed"

    # Apply edits
    results = []
    for edit in response.edits:
        if edit.content:
            result = diff_applicator.apply_content(edit.file, edit.content)
        elif edit.search and edit.replace:
            result = diff_applicator.apply_search_replace(edit.file, edit.search, edit.replace)
        elif edit.diff:
            result = diff_applicator.apply_diff(edit.file, edit.diff)
        else:
            continue

        results.append(result)
        ui.print_edit_result(edit.file, result.success, result.error)

    # Run linting
    lint_errors = []
    if not no_lint and results:
        ui.print_info("Verifying changes...")
        for result in results:
            if result.success:
                lint_result = linter.lint_file(result.path)
                if not lint_result.success:
                    lint_errors.extend(lint_result.errors)
                    ui.print_warning(f"Lint errors in {result.path}")

    # Report feedback
    success = len(lint_errors) == 0
    if response.session_id:
        await client.report_feedback(
            session_id=response.session_id,
            success=success,
            applied_edits=[r.path for r in results if r.success],
            lint_output="\n".join(lint_errors) if lint_errors else None,
        )

    # Auto-commit if enabled
    if auto_commit and success and any(r.success for r in results):
        ui.git_commit(project_path, f"Tarang: {response.edits[0].description[:50] if response.edits else 'update'}")

    applied = len([r for r in results if r.success])
    if lint_errors:
        return f"Applied {applied} edit(s) with {len(lint_errors)} lint error(s)"
    return f"Applied {applied} edit(s)"


async def _run_commands(response: TarangResponse, project_path: Path, ui: TarangConsole) -> str:
    """Run shell commands from backend response."""
    ui.console.print()

    for cmd in response.commands:
        if cmd.description:
            ui.print_info(cmd.description)

        try:
            result = subprocess.run(
                cmd.command,
                shell=True,
                cwd=project_path,
                capture_output=True,
                timeout=cmd.timeout,
            )

            output = result.stdout.decode() + result.stderr.decode()
            ui.print_command_output(cmd.command, output, result.returncode)

        except subprocess.TimeoutExpired:
            ui.print_warning(f"Command timed out: {cmd.command}")
        except Exception as e:
            ui.print_error(f"Command failed: {e}")

    return f"Ran {len(response.commands)} command(s)"


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
