"""
Tarang CLI - Thin client for the AI coding agent.

The CLI handles local operations (files, shell) while the backend
handles all reasoning and orchestration.

Usage:
    tarang login                      # Authenticate with GitHub
    tarang config --openrouter-key    # Set API key
    tarang run "create a hello world" # Run instruction
    tarang                             # Interactive mode
"""
from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path

import click

from tarang import __version__
from tarang.client import TarangAPIClient, TarangAuth, TarangResponse

# ASCII Art Banner
DEV_BANNER = [
    "                         ██████╗          ███████╗         ██╗   ██╗",
    "                         ██╔══██╗         ██╔════╝         ██║   ██║",
    "                         ██║  ██║         █████╗           ██║   ██║",
    "                         ██║  ██║         ██╔══╝           ╚██╗ ██╔╝",
    "                         ██████╔╝         ███████╗          ╚████╔╝ ",
    "                         ╚═════╝          ╚══════╝           ╚═══╝  ",
]

TARANG_BANNER = [
    "████████╗         █████╗         ██████╗          █████╗         ███╗   ██╗        ██████╗ ",
    "╚══██╔══╝        ██╔══██╗        ██╔══██╗        ██╔══██╗        ████╗  ██║       ██╔════╝ ",
    "   ██║           ███████║        ██████╔╝        ███████║        ██╔██╗ ██║       ██║  ███╗",
    "   ██║           ██╔══██║        ██╔══██╗        ██╔══██║        ██║╚██╗██║       ██║   ██║",
    "   ██║           ██║  ██║        ██║  ██║        ██║  ██║        ██║ ╚████║       ╚██████╔╝",
    "   ╚═╝           ╚═╝  ╚═╝        ╚═╝  ╚═╝        ╚═╝  ╚═╝        ╚═╝  ╚═══╝        ╚═════╝ ",
]


def print_banner():
    """Print the Tarang ASCII art banner."""
    click.echo()
    for line in DEV_BANNER:
        click.echo(click.style(line, fg="green", bold=True))
    click.echo()
    for line in TARANG_BANNER:
        click.echo(click.style(line, fg="cyan", bold=True))


from tarang.client.api_client import LocalContext
from tarang.context import SkeletonGenerator
from tarang.executor import DiffApplicator, ShadowLinter


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
    # If no subcommand, start interactive session
    if ctx.invoked_subcommand is None:
        ctx.invoke(run)


@cli.command()
def login():
    """
    Authenticate with Tarang via GitHub.

    Opens a browser window for OAuth authentication.
    Your token is stored securely in ~/.tarang/config.json
    """
    auth = TarangAuth()

    if auth.is_authenticated():
        click.echo("Already logged in.")
        if not click.confirm("Login again?"):
            return

    click.echo("Starting authentication...")

    try:
        asyncio.run(auth.login())
        click.echo("\nLogin successful!")
        click.echo("Your credentials are saved in ~/.tarang/config.json")

        if not auth.has_openrouter_key():
            click.echo("\nNext step: Set your OpenRouter API key:")
            click.echo("  tarang config --openrouter-key YOUR_KEY")

    except TimeoutError:
        click.echo("\nAuthentication timed out. Please try again.", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"\nAuthentication failed: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.option(
    "--openrouter-key", "-k",
    help="Set your OpenRouter API key",
)
@click.option(
    "--backend-url", "-u",
    help="Set custom backend URL (default: https://api.devtarang.ai)",
)
@click.option(
    "--show",
    is_flag=True,
    help="Show current configuration",
)
def config(openrouter_key: str, backend_url: str, show: bool):
    """
    Configure Tarang settings.

    Set your OpenRouter API key for LLM access:
        tarang config --openrouter-key sk-or-...

    View current config:
        tarang config --show
    """
    auth = TarangAuth()

    if show:
        creds = auth.load_credentials() or {}
        click.echo(f"\nTarang Configuration (~/.tarang/config.json)")
        click.echo(f"{'='*50}")
        click.echo(f"Token: {'configured' if creds.get('token') else 'not set'}")
        click.echo(f"OpenRouter Key: {'configured' if creds.get('openrouter_key') else 'not set'}")
        if creds.get("backend_url"):
            click.echo(f"Backend URL: {creds.get('backend_url')}")
        click.echo()
        return

    if openrouter_key:
        if not openrouter_key.startswith("sk-or-"):
            click.echo("Warning: OpenRouter keys usually start with 'sk-or-'", err=True)

        auth.save_openrouter_key(openrouter_key)
        click.echo("OpenRouter API key saved.")

    if backend_url:
        auth.save_credentials(backend_url=backend_url)
        click.echo(f"Backend URL set to: {backend_url}")

    if not openrouter_key and not backend_url:
        click.echo("No configuration changes made.")
        click.echo("Use --help to see available options.")


@cli.command()
@click.argument("instruction", required=False)
@click.option(
    "--project-dir", "-p",
    default=".",
    help="Project directory (default: current directory)",
)
@click.option(
    "--no-lint",
    is_flag=True,
    help="Skip shadow linting after applying changes",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show changes without applying them",
)
@click.option(
    "--verbose", "-v",
    is_flag=True,
    help="Enable verbose output",
)
@click.option(
    "--once",
    is_flag=True,
    help="Run single instruction and exit (no interactive mode)",
)
def run(
    instruction: str,
    project_dir: str,
    no_lint: bool,
    dry_run: bool,
    verbose: bool,
    once: bool,
):
    """
    Run Tarang AI coding assistant.

    Without instruction: starts interactive mode
    With instruction: runs it and enters interactive mode (use --once to exit)

    Examples:
        tarang run                              # Interactive mode
        tarang run "explain the project"        # Run then continue chatting
        tarang run "fix linter errors" --once   # Run and exit
        tarang run "add login" --dry-run        # Preview changes
    """
    # Check authentication
    auth = TarangAuth()

    if not auth.is_authenticated():
        click.echo("Not logged in. Run 'tarang login' first.", err=True)
        sys.exit(1)

    if not auth.has_openrouter_key():
        click.echo("OpenRouter key not set.", err=True)
        click.echo("Run: tarang config --openrouter-key YOUR_KEY", err=True)
        sys.exit(1)

    # Resolve project directory
    project_path = Path(project_dir).resolve()
    if not project_path.exists():
        click.echo(f"Error: Project directory not found: {project_dir}", err=True)
        sys.exit(1)

    # Initialize components
    creds = auth.load_credentials()
    client = TarangAPIClient(creds.get("backend_url"))
    client.token = creds.get("token")
    client.openrouter_key = creds.get("openrouter_key")

    # Show banner
    print_banner()
    click.echo()
    click.echo(f"v{__version__} | Project: {project_path}")

    if verbose:
        click.echo("\nScanning project...")

    skeleton_gen = SkeletonGenerator(project_path)
    skeleton = skeleton_gen.generate()

    click.echo(f"Files: {skeleton.total_files} | Lines: {skeleton.total_lines}\n")

    # Build context
    context = LocalContext(
        project_root=str(project_path),
        skeleton=skeleton.to_dict(),
    )

    # Initialize executor
    diff_applicator = DiffApplicator(project_path)
    linter = ShadowLinter(project_path)

    session_id = None
    conversation_history = []

    async def run_instruction(instr: str) -> str:
        """Run a single instruction and return response."""
        nonlocal session_id, context

        # Add conversation history to context
        context.history = conversation_history[-6:]  # Last 3 exchanges

        click.echo("Thinking...")

        response = await client.execute(
            instruction=instr,
            context=context,
            session_id=session_id,
        )

        session_id = response.session_id

        # Handle response types
        if response.type == "error":
            click.echo(f"\nError: {response.error}", err=True)
            return ""

        # Show thought process if verbose
        if verbose and response.thought_process:
            click.echo(f"\n[Thinking] {response.thought_process[:200]}...")

        # Handle edits
        if response.type == "edits" and response.edits:
            return await _apply_edits(
                response,
                diff_applicator,
                linter,
                client,
                no_lint,
                dry_run,
            )

        # Handle commands
        if response.type == "command" and response.commands:
            return await _run_commands(response, project_path)

        # Handle message
        if response.message:
            click.echo(f"\n{'─'*60}")
            click.echo(response.message)
            click.echo(f"{'─'*60}\n")
            return response.message

        return ""

    try:
        # Run initial instruction if provided
        if instruction:
            response = asyncio.run(run_instruction(instruction))
            conversation_history.append({
                "role": "user",
                "content": instruction,
            })
            conversation_history.append({
                "role": "assistant",
                "content": response or "Task completed",
            })

            if once:
                click.echo("Done.")
                return

        # Interactive mode
        if not once:
            click.echo("Type your instructions (or 'exit' to quit):\n")

        while not once:
            try:
                user_input = click.prompt(
                    "You",
                    prompt_suffix=" > ",
                    default="",
                    show_default=False,
                )

                if not user_input.strip():
                    continue

                cmd = user_input.strip().lower()
                if cmd in ("exit", "quit", "q"):
                    click.echo("\nGoodbye!")
                    break
                elif cmd == "clear":
                    conversation_history.clear()
                    click.echo("History cleared.\n")
                    continue
                elif cmd == "status":
                    click.echo(f"Session: {session_id or 'None'}")
                    click.echo(f"History: {len(conversation_history)} messages\n")
                    continue

                response = asyncio.run(run_instruction(user_input))
                conversation_history.append({
                    "role": "user",
                    "content": user_input,
                })
                conversation_history.append({
                    "role": "assistant",
                    "content": response or "Task completed",
                })

            except KeyboardInterrupt:
                click.echo("\n")
                continue
            except EOFError:
                click.echo("\nGoodbye!")
                break

    except KeyboardInterrupt:
        click.echo("\n\nInterrupted by user", err=True)
        sys.exit(130)
    except Exception as e:
        click.echo(f"\nError: {e}", err=True)
        if verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


async def _apply_edits(
    response: TarangResponse,
    diff_applicator: DiffApplicator,
    linter: ShadowLinter,
    client: TarangAPIClient,
    no_lint: bool,
    dry_run: bool,
) -> str:
    """Apply edits from backend response."""
    click.echo(f"\n{'─'*60}")
    click.echo(f"Applying {len(response.edits)} edit(s):")

    results = []
    for edit in response.edits:
        click.echo(f"  • {edit.file}: {edit.description}")

        if dry_run:
            if edit.content:
                click.echo(f"    [DRY-RUN] Would write {len(edit.content)} chars")
            elif edit.search and edit.replace:
                click.echo(f"    [DRY-RUN] Would replace: {edit.search[:50]}...")
            elif edit.diff:
                click.echo(f"    [DRY-RUN] Would apply diff")
            continue

        # Apply the edit
        if edit.content:
            result = diff_applicator.apply_content(edit.file, edit.content)
        elif edit.search and edit.replace:
            result = diff_applicator.apply_search_replace(
                edit.file, edit.search, edit.replace
            )
        elif edit.diff:
            result = diff_applicator.apply_diff(edit.file, edit.diff)
        else:
            click.echo(f"    [SKIP] No content to apply")
            continue

        results.append(result)

        if result.success:
            click.echo(f"    ✓ Applied")
        else:
            click.echo(f"    ✗ Failed: {result.error}")

    if dry_run:
        click.echo(f"{'─'*60}")
        click.echo("[DRY-RUN] No changes made.\n")
        return "Dry run completed"

    # Run linting
    lint_errors = []
    if not no_lint and results:
        click.echo("\nVerifying changes...")

        for result in results:
            if result.success:
                lint_result = linter.lint_file(result.path)
                if not lint_result.success:
                    lint_errors.extend(lint_result.errors)
                    click.echo(f"  ✗ Lint errors in {result.path}")

    # Report feedback to backend
    success = len(lint_errors) == 0
    if response.session_id:
        await client.report_feedback(
            session_id=response.session_id,
            success=success,
            applied_edits=[r.path for r in results if r.success],
            lint_output="\n".join(lint_errors) if lint_errors else None,
        )

    click.echo(f"{'─'*60}\n")

    if lint_errors:
        return f"Applied with {len(lint_errors)} lint error(s)"
    return f"Applied {len([r for r in results if r.success])} edit(s)"


async def _run_commands(response: TarangResponse, project_path: Path) -> str:
    """Run shell commands from backend response."""
    import subprocess

    click.echo(f"\n{'─'*60}")
    click.echo(f"Running {len(response.commands)} command(s):")

    for cmd in response.commands:
        click.echo(f"\n$ {cmd.command}")

        if cmd.description:
            click.echo(f"  ({cmd.description})")

        try:
            result = subprocess.run(
                cmd.command,
                shell=True,
                cwd=project_path,
                capture_output=True,
                timeout=cmd.timeout,
            )

            if result.stdout:
                click.echo(result.stdout.decode()[:500])
            if result.stderr:
                click.echo(result.stderr.decode()[:500], err=True)

            if result.returncode != 0:
                click.echo(f"  [Exit code: {result.returncode}]")

        except subprocess.TimeoutExpired:
            click.echo("  [Timed out]", err=True)
        except Exception as e:
            click.echo(f"  [Error: {e}]", err=True)

    click.echo(f"{'─'*60}\n")
    return f"Ran {len(response.commands)} command(s)"


@cli.command()
@click.argument("query", required=True)
def ask(query: str):
    """
    Quick question (no code generation).

    For fast answers about coding concepts without project context.

    Example:
        tarang ask "what is a closure in Python?"
    """
    auth = TarangAuth()

    if not auth.has_openrouter_key():
        click.echo("OpenRouter key not set.", err=True)
        click.echo("Run: tarang config --openrouter-key YOUR_KEY", err=True)
        sys.exit(1)

    creds = auth.load_credentials()
    client = TarangAPIClient(creds.get("backend_url"))
    client.openrouter_key = creds.get("openrouter_key")

    try:
        answer = asyncio.run(client.quick_ask(query))
        click.echo(f"\n{answer}\n")
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@cli.command()
def status():
    """
    Show Tarang status and configuration.

    Displays authentication status and connectivity.
    """
    auth = TarangAuth()
    creds = auth.load_credentials() or {}

    print_banner()
    click.echo()
    click.echo(f"v{__version__} Status")
    click.echo(f"{'='*40}\n")

    # Auth status
    if auth.is_authenticated():
        click.echo("Authentication: ✓ Logged in")
    else:
        click.echo("Authentication: ✗ Not logged in")
        click.echo("  Run: tarang login")

    # OpenRouter key
    if auth.has_openrouter_key():
        key = creds.get("openrouter_key", "")
        click.echo(f"OpenRouter Key: ✓ Configured ({key[:8]}...)")
    else:
        click.echo("OpenRouter Key: ✗ Not set")
        click.echo("  Run: tarang config --openrouter-key YOUR_KEY")

    # Backend URL
    backend_url = creds.get("backend_url", TarangAPIClient.DEFAULT_BASE_URL)
    click.echo(f"Backend URL: {backend_url}")

    # Test connectivity
    click.echo("\nTesting connection...")
    try:
        import httpx
        response = httpx.get(f"{backend_url}/health", timeout=5)
        if response.status_code == 200:
            click.echo("Backend: ✓ Connected")
        else:
            click.echo(f"Backend: ⚠ Status {response.status_code}")
    except Exception as e:
        click.echo(f"Backend: ✗ Cannot connect ({e})")

    click.echo()


@cli.command()
@click.option(
    "--project-dir", "-p",
    default=".",
    help="Project directory to clean",
)
@click.option(
    "--force", "-f",
    is_flag=True,
    help="Don't ask for confirmation",
)
def clean(project_dir: str, force: bool):
    """
    Clean Tarang state from the project.

    Removes the .tarang directory and backup files.
    """
    project_path = Path(project_dir).resolve()
    tarang_dir = project_path / ".tarang"
    backup_dir = project_path / ".tarang_backups"

    if not tarang_dir.exists() and not backup_dir.exists():
        click.echo("No Tarang state to clean.")
        return

    if not force:
        click.confirm(
            f"Remove Tarang state from {project_path}?",
            abort=True,
        )

    if tarang_dir.exists():
        shutil.rmtree(tarang_dir)
        click.echo("Removed .tarang directory")

    if backup_dir.exists():
        shutil.rmtree(backup_dir)
        click.echo("Removed .tarang_backups directory")

    click.echo("Done.")


@cli.command()
def logout():
    """
    Log out and clear saved credentials.
    """
    auth = TarangAuth()

    if not auth.is_authenticated():
        click.echo("Not logged in.")
        return

    if click.confirm("Clear all saved credentials?"):
        auth.clear_credentials()
        click.echo("Logged out. Credentials cleared.")


def main():
    """Main entry point."""
    cli()


if __name__ == "__main__":
    main()
