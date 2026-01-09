"""Tarang status display."""

from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from tarang.core.config import load_config, get_config_path, get_project_config_path

console = Console()


def show_status(verbose: bool = False):
    """Display current Tarang status.

    Args:
        verbose: Show additional details
    """
    console.print("\n[bold cyan]Tarang Status[/bold cyan]\n")

    # Configuration status
    config_path = get_config_path()
    project_config_path = get_project_config_path()
    config = load_config()

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Key", style="dim")
    table.add_column("Value")

    # Global config
    if config_path.exists():
        table.add_row("Global config", f"[green]✓[/green] {config_path}")
    else:
        table.add_row("Global config", "[yellow]Not configured[/yellow]")

    # Project config
    if project_config_path:
        table.add_row("Project config", f"[green]✓[/green] {project_config_path}")
    else:
        table.add_row("Project config", "[dim]None[/dim]")

    # API key status
    if config.get("openrouter_key"):
        key = config["openrouter_key"]
        masked = f"{key[:8]}...{key[-4:]}" if len(key) > 12 else "***"
        table.add_row("OpenRouter key", f"[green]✓[/green] {masked}")
    else:
        table.add_row("OpenRouter key", "[yellow]Not set[/yellow]")

    # Auth status
    if config.get("api_key"):
        table.add_row("Tarang auth", "[green]✓[/green] Authenticated")
    else:
        table.add_row("Tarang auth", "[dim]Not logged in[/dim]")

    # Model preferences
    table.add_row("", "")  # Spacer
    table.add_row("Reasoning model", config.get("reasoning_model", "[dim]default[/dim]"))
    table.add_row("Coding model", config.get("coding_model", "[dim]default[/dim]"))

    console.print(table)

    # Project details
    if project_config_path:
        console.print("\n[bold]Project Settings[/bold]\n")

        project_table = Table(show_header=False, box=None, padding=(0, 2))
        project_table.add_column("Key", style="dim")
        project_table.add_column("Value")

        project = config.get("project", {})
        if not project:
            # Load project config directly
            import json
            with open(project_config_path) as f:
                project = json.load(f)

        project_table.add_row("Name", project.get("name", Path.cwd().name))
        project_table.add_row("Language", project.get("language", "[dim]auto[/dim]"))
        project_table.add_row("Source dir", project.get("src_dir", "[dim]./[/dim]"))
        project_table.add_row("Test dir", project.get("test_dir", "[dim]tests/[/dim]"))
        project_table.add_row("Docs dir", project.get("docs_dir", "[dim]docs/[/dim]"))
        project_table.add_row("Lint command", project.get("lint_command", "[dim]none[/dim]"))

        console.print(project_table)

    console.print()
