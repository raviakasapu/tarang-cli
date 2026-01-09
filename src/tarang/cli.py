"""Tarang CLI - Main entry point."""

import asyncio
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from tarang.core.config import load_config, get_config_path
from tarang.core.session import TarangSession

console = Console()


def print_banner():
    """Print the Tarang welcome banner."""
    banner = """
[bold cyan]  ████████╗ █████╗ ██████╗  █████╗ ███╗   ██╗ ██████╗ [/bold cyan]
[bold cyan]  ╚══██╔══╝██╔══██╗██╔══██╗██╔══██╗████╗  ██║██╔════╝ [/bold cyan]
[bold cyan]     ██║   ███████║██████╔╝███████║██╔██╗ ██║██║  ███╗[/bold cyan]
[bold cyan]     ██║   ██╔══██║██╔══██╗██╔══██║██║╚██╗██║██║   ██║[/bold cyan]
[bold cyan]     ██║   ██║  ██║██║  ██║██║  ██║██║ ╚████║╚██████╔╝[/bold cyan]
[bold cyan]     ╚═╝   ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═══╝ ╚═════╝ [/bold cyan]

[dim]AI-powered coding assistant | devtarang.ai[/dim]
"""
    console.print(banner)


@click.group(invoke_without_command=True)
@click.version_option(package_name="tarang")
@click.option("--no-lint", is_flag=True, help="Skip shadow linting verification")
@click.option("--dry-run", is_flag=True, help="Show changes without applying them")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose output")
@click.pass_context
def cli(ctx: click.Context, no_lint: bool, dry_run: bool, verbose: bool):
    """Tarang - AI-powered coding assistant.

    Run 'tarang' to start an interactive session.
    Run 'tarang init' to set up a new project.
    Run 'tarang login' to authenticate with devtarang.ai.
    """
    ctx.ensure_object(dict)
    ctx.obj["no_lint"] = no_lint
    ctx.obj["dry_run"] = dry_run
    ctx.obj["verbose"] = verbose

    if ctx.invoked_subcommand is None:
        asyncio.run(_start_session(no_lint, dry_run, verbose))


async def _start_session(no_lint: bool, dry_run: bool, verbose: bool):
    """Start an interactive Tarang session."""
    print_banner()

    # Check for configuration
    config_path = get_config_path()
    if not config_path.exists():
        console.print(
            "\n[yellow]No configuration found.[/yellow] "
            "Run [bold]tarang init[/bold] to set up your project.\n"
        )
        return

    config = load_config()

    # Check for API key
    if not config.get("openrouter_key") and not config.get("api_key"):
        console.print(
            "\n[yellow]No API key configured.[/yellow] "
            "Run [bold]tarang init[/bold] to set up your API key.\n"
        )
        return

    console.print(
        Panel(
            "[dim]Type your request and press Enter. Type 'exit' or 'quit' to end the session.[/dim]",
            title="Session Started",
            border_style="cyan",
        )
    )

    session = TarangSession(
        config=config,
        no_lint=no_lint,
        dry_run=dry_run,
        verbose=verbose,
    )

    try:
        while True:
            try:
                user_input = Prompt.ask("\n[bold cyan]>[/bold cyan]")

                if user_input.lower() in ("exit", "quit", "q"):
                    console.print("\n[dim]Session ended. Happy coding![/dim]\n")
                    break

                if not user_input.strip():
                    continue

                await session.process_request(user_input)

            except KeyboardInterrupt:
                console.print("\n\n[dim]Session interrupted. Happy coding![/dim]\n")
                break

    finally:
        await session.cleanup()


@cli.command()
@click.option("--force", "-f", is_flag=True, help="Overwrite existing configuration")
@click.pass_context
def init(ctx: click.Context, force: bool):
    """Initialize Tarang for the current project."""
    from tarang.core.init import run_init
    asyncio.run(run_init(force=force, verbose=ctx.obj.get("verbose", False)))


@cli.command()
@click.pass_context
def login(ctx: click.Context):
    """Authenticate with devtarang.ai."""
    from tarang.core.auth import run_login
    asyncio.run(run_login(verbose=ctx.obj.get("verbose", False)))


@cli.command()
@click.pass_context
def status(ctx: click.Context):
    """Show current project and session status."""
    from tarang.core.status import show_status
    show_status(verbose=ctx.obj.get("verbose", False))


@cli.command()
@click.argument("query")
@click.pass_context
def ask(ctx: click.Context, query: str):
    """Ask a quick question without starting a full session."""
    from tarang.core.quick import quick_ask
    asyncio.run(quick_ask(query, verbose=ctx.obj.get("verbose", False)))


def main():
    """Main entry point."""
    cli(obj={})


if __name__ == "__main__":
    main()
