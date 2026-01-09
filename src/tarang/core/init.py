"""Tarang project initialization."""

import json
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

from tarang.core.config import save_config, get_config_path, TARANG_HOME

console = Console()


async def run_init(force: bool = False, verbose: bool = False):
    """Run the interactive initialization process.

    Args:
        force: Overwrite existing configuration
        verbose: Enable verbose output
    """
    console.print("\n[bold cyan]Tarang Project Setup[/bold cyan]\n")

    # Check for existing config
    config_path = get_config_path()
    if config_path.exists() and not force:
        if not Confirm.ask(
            "[yellow]Configuration already exists.[/yellow] Overwrite?",
            default=False,
        ):
            console.print("[dim]Setup cancelled.[/dim]")
            return

    config = {}

    # Step 1: API Key setup
    console.print(Panel(
        "[bold]Step 1: API Key Configuration[/bold]\n\n"
        "Tarang uses OpenRouter for LLM access (BYOK model).\n"
        "Get your API key at: [link]https://openrouter.ai/keys[/link]",
        border_style="cyan",
    ))

    openrouter_key = Prompt.ask(
        "Enter your OpenRouter API key",
        password=True,
    )

    if openrouter_key:
        config["openrouter_key"] = openrouter_key
        console.print("[green]API key saved.[/green]\n")

    # Step 2: Model preferences
    console.print(Panel(
        "[bold]Step 2: Model Preferences[/bold]\n\n"
        "Choose your preferred models for different tasks.",
        border_style="cyan",
    ))

    reasoning_model = Prompt.ask(
        "Reasoning model (for planning)",
        default="anthropic/claude-3.5-sonnet",
    )
    config["reasoning_model"] = reasoning_model

    coding_model = Prompt.ask(
        "Coding model (for code generation)",
        default="deepseek/deepseek-coder",
    )
    config["coding_model"] = coding_model

    # Step 3: Project-specific settings
    console.print(Panel(
        "[bold]Step 3: Project Configuration[/bold]\n\n"
        "Optional: Configure project-specific settings.",
        border_style="cyan",
    ))

    # Check if we're in a project directory
    cwd = Path.cwd()
    project_config = {}

    if Confirm.ask("Configure project-specific settings?", default=True):
        # Test directory
        test_dirs = _detect_directories(cwd, ["tests", "test", "spec", "__tests__"])
        if test_dirs:
            console.print(f"[dim]Detected test directories: {', '.join(test_dirs)}[/dim]")

        test_dir = Prompt.ask(
            "Test directory",
            default=test_dirs[0] if test_dirs else "tests",
        )
        project_config["test_dir"] = test_dir

        # Docs directory
        doc_dirs = _detect_directories(cwd, ["docs", "documentation", "doc"])
        if doc_dirs:
            console.print(f"[dim]Detected doc directories: {', '.join(doc_dirs)}[/dim]")

        docs_dir = Prompt.ask(
            "Documentation directory",
            default=doc_dirs[0] if doc_dirs else "docs",
        )
        project_config["docs_dir"] = docs_dir

        # Source directory
        src_dirs = _detect_directories(cwd, ["src", "lib", "app"])
        if src_dirs:
            console.print(f"[dim]Detected source directories: {', '.join(src_dirs)}[/dim]")

        src_dir = Prompt.ask(
            "Source directory",
            default=src_dirs[0] if src_dirs else "src",
        )
        project_config["src_dir"] = src_dir

        # Language detection
        language = _detect_language(cwd)
        if language:
            console.print(f"[dim]Detected primary language: {language}[/dim]")

        lang = Prompt.ask(
            "Primary language",
            default=language or "python",
        )
        project_config["language"] = lang

        # Linting command
        lint_cmd = _detect_lint_command(cwd, lang)
        if lint_cmd:
            console.print(f"[dim]Detected lint command: {lint_cmd}[/dim]")

        lint = Prompt.ask(
            "Lint command (for verification)",
            default=lint_cmd or "echo 'No linter configured'",
        )
        project_config["lint_command"] = lint

    # Save configurations
    console.print("\n[dim]Saving configuration...[/dim]")

    # Save global config
    save_config(config, project_level=False)

    # Save project config if we have project-specific settings
    if project_config:
        project_config["name"] = cwd.name
        save_config(project_config, project_level=True)

    console.print(Panel(
        "[bold green]Setup complete![/bold green]\n\n"
        f"Global config: [dim]{TARANG_HOME / 'config.json'}[/dim]\n"
        f"Project config: [dim]{cwd / '.tarang' / 'project.json'}[/dim]\n\n"
        "Run [bold]tarang[/bold] to start a session.",
        border_style="green",
    ))


def _detect_directories(root: Path, candidates: list[str]) -> list[str]:
    """Detect existing directories from a list of candidates."""
    found = []
    for candidate in candidates:
        if (root / candidate).is_dir():
            found.append(candidate)
    return found


def _detect_language(root: Path) -> str | None:
    """Detect the primary language of the project."""
    indicators = {
        "python": ["pyproject.toml", "setup.py", "requirements.txt", "Pipfile"],
        "typescript": ["tsconfig.json"],
        "javascript": ["package.json"],
        "rust": ["Cargo.toml"],
        "go": ["go.mod"],
        "ruby": ["Gemfile"],
        "java": ["pom.xml", "build.gradle"],
    }

    for lang, files in indicators.items():
        for f in files:
            if (root / f).exists():
                return lang
    return None


def _detect_lint_command(root: Path, language: str) -> str | None:
    """Detect the appropriate lint command for the project."""
    commands = {
        "python": "ruff check .",
        "typescript": "npx tsc --noEmit",
        "javascript": "npm run lint",
        "rust": "cargo check",
        "go": "go vet ./...",
    }

    # Check for specific config files
    if (root / "ruff.toml").exists() or (root / "pyproject.toml").exists():
        return "ruff check ."
    if (root / ".eslintrc.js").exists() or (root / ".eslintrc.json").exists():
        return "npx eslint ."
    if (root / "biome.json").exists():
        return "npx biome check ."

    return commands.get(language)
