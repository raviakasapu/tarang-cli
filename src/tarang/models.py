"""
Model configuration and selection for Tarang CLI.

Provides predefined model choices organized by:
- Provider (OpenRouter, Anthropic, Azure, OpenAI, Bedrock, Google)
- Role (Orchestrator/Thinking, Manager/Validation, Worker/Execution)

Usage:
    from tarang.models import ModelConfig, get_model_choices, select_models
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from rich.console import Console
from rich.table import Table
from rich.prompt import Prompt, Confirm
from rich.panel import Panel


class Provider(Enum):
    """Supported LLM providers."""
    OPENROUTER = "OpenRouterGateway"
    ANTHROPIC = "AnthropicGateway"
    OPENAI = "OpenAIGateway"
    AZURE = "AzureOpenAIGateway"
    BEDROCK = "BedrockGateway"
    GOOGLE = "GoogleAIGateway"


class ModelRole(Enum):
    """Model roles in the agent hierarchy."""
    ORCHESTRATOR = "orchestrator"  # Thinking/Reasoning - strategic planning
    MANAGER = "manager"            # Validation/Planning - task decomposition
    WORKER = "worker"              # Execution - code generation


@dataclass
class ModelChoice:
    """A model option with metadata."""
    id: str                    # Model ID for API
    name: str                  # Display name
    provider: Provider         # Which provider
    description: str           # Brief description
    recommended_for: List[ModelRole] = field(default_factory=list)
    context_window: int = 0    # Context size (0 = unknown)
    cost_tier: str = "medium"  # low, medium, high, premium


# =============================================================================
# Predefined Model Choices
# =============================================================================

OPENROUTER_MODELS = [
    # Anthropic via OpenRouter
    ModelChoice(
        id="anthropic/claude-sonnet-4-20250514",
        name="Claude Sonnet 4",
        provider=Provider.OPENROUTER,
        description="Latest Claude - excellent reasoning and coding",
        recommended_for=[ModelRole.ORCHESTRATOR, ModelRole.MANAGER],
        context_window=200000,
        cost_tier="high",
    ),
    ModelChoice(
        id="anthropic/claude-3.5-sonnet",
        name="Claude 3.5 Sonnet",
        provider=Provider.OPENROUTER,
        description="Fast, capable - great balance of speed/quality",
        recommended_for=[ModelRole.MANAGER, ModelRole.WORKER],
        context_window=200000,
        cost_tier="medium",
    ),
    ModelChoice(
        id="anthropic/claude-3.5-haiku",
        name="Claude 3.5 Haiku",
        provider=Provider.OPENROUTER,
        description="Fast and cheap - good for simple tasks",
        recommended_for=[ModelRole.WORKER],
        context_window=200000,
        cost_tier="low",
    ),
    # Google via OpenRouter
    ModelChoice(
        id="google/gemini-2.0-flash-001",
        name="Gemini 2.0 Flash",
        provider=Provider.OPENROUTER,
        description="Very fast, good for exploration and simple tasks",
        recommended_for=[ModelRole.WORKER, ModelRole.ORCHESTRATOR],
        context_window=1000000,
        cost_tier="low",
    ),
    ModelChoice(
        id="google/gemini-2.0-pro-exp-02-05",
        name="Gemini 2.0 Pro",
        provider=Provider.OPENROUTER,
        description="Strong reasoning, large context",
        recommended_for=[ModelRole.ORCHESTRATOR, ModelRole.MANAGER],
        context_window=2000000,
        cost_tier="medium",
    ),
    # OpenAI via OpenRouter
    ModelChoice(
        id="openai/gpt-4o",
        name="GPT-4o",
        provider=Provider.OPENROUTER,
        description="OpenAI's flagship - strong all-around",
        recommended_for=[ModelRole.ORCHESTRATOR, ModelRole.MANAGER],
        context_window=128000,
        cost_tier="high",
    ),
    ModelChoice(
        id="openai/gpt-4o-mini",
        name="GPT-4o Mini",
        provider=Provider.OPENROUTER,
        description="Faster, cheaper GPT-4o variant",
        recommended_for=[ModelRole.WORKER],
        context_window=128000,
        cost_tier="low",
    ),
    ModelChoice(
        id="openai/o1",
        name="OpenAI o1",
        provider=Provider.OPENROUTER,
        description="Advanced reasoning - best for complex planning",
        recommended_for=[ModelRole.ORCHESTRATOR],
        context_window=200000,
        cost_tier="premium",
    ),
    # DeepSeek via OpenRouter
    ModelChoice(
        id="deepseek/deepseek-chat-v3-0324",
        name="DeepSeek V3",
        provider=Provider.OPENROUTER,
        description="Strong coding model, very cost effective",
        recommended_for=[ModelRole.WORKER, ModelRole.MANAGER],
        context_window=64000,
        cost_tier="low",
    ),
    ModelChoice(
        id="deepseek/deepseek-r1",
        name="DeepSeek R1",
        provider=Provider.OPENROUTER,
        description="Reasoning model - good for complex tasks",
        recommended_for=[ModelRole.ORCHESTRATOR, ModelRole.MANAGER],
        context_window=64000,
        cost_tier="low",
    ),
    # Qwen via OpenRouter
    ModelChoice(
        id="qwen/qwen-2.5-coder-32b-instruct",
        name="Qwen 2.5 Coder 32B",
        provider=Provider.OPENROUTER,
        description="Specialized coding model",
        recommended_for=[ModelRole.WORKER],
        context_window=32000,
        cost_tier="low",
    ),
]

ANTHROPIC_MODELS = [
    ModelChoice(
        id="claude-sonnet-4-20250514",
        name="Claude Sonnet 4",
        provider=Provider.ANTHROPIC,
        description="Latest Claude - excellent reasoning and coding",
        recommended_for=[ModelRole.ORCHESTRATOR, ModelRole.MANAGER],
        context_window=200000,
        cost_tier="high",
    ),
    ModelChoice(
        id="claude-3-5-sonnet-20241022",
        name="Claude 3.5 Sonnet",
        provider=Provider.ANTHROPIC,
        description="Fast, capable - great balance",
        recommended_for=[ModelRole.MANAGER, ModelRole.WORKER],
        context_window=200000,
        cost_tier="medium",
    ),
    ModelChoice(
        id="claude-3-5-haiku-20241022",
        name="Claude 3.5 Haiku",
        provider=Provider.ANTHROPIC,
        description="Fast and cheap",
        recommended_for=[ModelRole.WORKER],
        context_window=200000,
        cost_tier="low",
    ),
    ModelChoice(
        id="claude-3-opus-20240229",
        name="Claude 3 Opus",
        provider=Provider.ANTHROPIC,
        description="Most capable, highest quality",
        recommended_for=[ModelRole.ORCHESTRATOR],
        context_window=200000,
        cost_tier="premium",
    ),
]

OPENAI_MODELS = [
    ModelChoice(
        id="gpt-4o",
        name="GPT-4o",
        provider=Provider.OPENAI,
        description="Flagship model - strong all-around",
        recommended_for=[ModelRole.ORCHESTRATOR, ModelRole.MANAGER],
        context_window=128000,
        cost_tier="high",
    ),
    ModelChoice(
        id="gpt-4o-mini",
        name="GPT-4o Mini",
        provider=Provider.OPENAI,
        description="Faster, cheaper variant",
        recommended_for=[ModelRole.WORKER],
        context_window=128000,
        cost_tier="low",
    ),
    ModelChoice(
        id="gpt-4-turbo",
        name="GPT-4 Turbo",
        provider=Provider.OPENAI,
        description="Previous flagship",
        recommended_for=[ModelRole.MANAGER],
        context_window=128000,
        cost_tier="high",
    ),
    ModelChoice(
        id="o1",
        name="o1",
        provider=Provider.OPENAI,
        description="Advanced reasoning model",
        recommended_for=[ModelRole.ORCHESTRATOR],
        context_window=200000,
        cost_tier="premium",
    ),
    ModelChoice(
        id="o1-mini",
        name="o1 Mini",
        provider=Provider.OPENAI,
        description="Faster reasoning model",
        recommended_for=[ModelRole.MANAGER],
        context_window=128000,
        cost_tier="high",
    ),
]

AZURE_MODELS = [
    # Azure uses deployment names, these are common defaults
    ModelChoice(
        id="gpt-4o",
        name="GPT-4o (deployment)",
        provider=Provider.AZURE,
        description="GPT-4o deployment",
        recommended_for=[ModelRole.ORCHESTRATOR, ModelRole.MANAGER],
        context_window=128000,
        cost_tier="high",
    ),
    ModelChoice(
        id="gpt-4o-mini",
        name="GPT-4o Mini (deployment)",
        provider=Provider.AZURE,
        description="GPT-4o Mini deployment",
        recommended_for=[ModelRole.WORKER],
        context_window=128000,
        cost_tier="low",
    ),
    ModelChoice(
        id="gpt-4",
        name="GPT-4 (deployment)",
        provider=Provider.AZURE,
        description="GPT-4 deployment",
        recommended_for=[ModelRole.MANAGER],
        context_window=128000,
        cost_tier="high",
    ),
]

BEDROCK_MODELS = [
    ModelChoice(
        id="anthropic.claude-3-5-sonnet-20241022-v2:0",
        name="Claude 3.5 Sonnet v2",
        provider=Provider.BEDROCK,
        description="Claude 3.5 Sonnet on Bedrock",
        recommended_for=[ModelRole.MANAGER, ModelRole.WORKER],
        context_window=200000,
        cost_tier="medium",
    ),
    ModelChoice(
        id="anthropic.claude-3-5-haiku-20241022-v1:0",
        name="Claude 3.5 Haiku",
        provider=Provider.BEDROCK,
        description="Fast Claude on Bedrock",
        recommended_for=[ModelRole.WORKER],
        context_window=200000,
        cost_tier="low",
    ),
    ModelChoice(
        id="anthropic.claude-3-opus-20240229-v1:0",
        name="Claude 3 Opus",
        provider=Provider.BEDROCK,
        description="Most capable Claude on Bedrock",
        recommended_for=[ModelRole.ORCHESTRATOR],
        context_window=200000,
        cost_tier="premium",
    ),
    ModelChoice(
        id="meta.llama3-1-70b-instruct-v1:0",
        name="Llama 3.1 70B",
        provider=Provider.BEDROCK,
        description="Meta's Llama 3.1 70B",
        recommended_for=[ModelRole.WORKER],
        context_window=128000,
        cost_tier="medium",
    ),
    ModelChoice(
        id="amazon.titan-text-premier-v1:0",
        name="Titan Text Premier",
        provider=Provider.BEDROCK,
        description="Amazon's Titan model",
        recommended_for=[ModelRole.WORKER],
        context_window=32000,
        cost_tier="low",
    ),
]

GOOGLE_MODELS = [
    ModelChoice(
        id="gemini-2.0-flash",
        name="Gemini 2.0 Flash",
        provider=Provider.GOOGLE,
        description="Fast, 1M context",
        recommended_for=[ModelRole.WORKER, ModelRole.ORCHESTRATOR],
        context_window=1000000,
        cost_tier="low",
    ),
    ModelChoice(
        id="gemini-2.0-pro",
        name="Gemini 2.0 Pro",
        provider=Provider.GOOGLE,
        description="Strong reasoning, 2M context",
        recommended_for=[ModelRole.ORCHESTRATOR, ModelRole.MANAGER],
        context_window=2000000,
        cost_tier="medium",
    ),
    ModelChoice(
        id="gemini-1.5-pro",
        name="Gemini 1.5 Pro",
        provider=Provider.GOOGLE,
        description="Proven stable model",
        recommended_for=[ModelRole.MANAGER],
        context_window=1000000,
        cost_tier="medium",
    ),
]

# Provider to models mapping
PROVIDER_MODELS: Dict[Provider, List[ModelChoice]] = {
    Provider.OPENROUTER: OPENROUTER_MODELS,
    Provider.ANTHROPIC: ANTHROPIC_MODELS,
    Provider.OPENAI: OPENAI_MODELS,
    Provider.AZURE: AZURE_MODELS,
    Provider.BEDROCK: BEDROCK_MODELS,
    Provider.GOOGLE: GOOGLE_MODELS,
}

# Provider display info
PROVIDER_INFO = {
    Provider.OPENROUTER: {
        "name": "OpenRouter",
        "description": "Access multiple providers via single API",
        "env_key": "OPENROUTER_API_KEY",
        "requires": ["OPENROUTER_API_KEY"],
    },
    Provider.ANTHROPIC: {
        "name": "Anthropic (Direct)",
        "description": "Direct access to Claude models",
        "env_key": "ANTHROPIC_API_KEY",
        "requires": ["ANTHROPIC_API_KEY"],
    },
    Provider.OPENAI: {
        "name": "OpenAI (Direct)",
        "description": "Direct access to GPT models",
        "env_key": "OPENAI_API_KEY",
        "requires": ["OPENAI_API_KEY"],
    },
    Provider.AZURE: {
        "name": "Azure OpenAI",
        "description": "OpenAI models via Azure",
        "env_key": "AZURE_OPENAI_API_KEY",
        "requires": ["AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT"],
    },
    Provider.BEDROCK: {
        "name": "AWS Bedrock",
        "description": "LLMs via AWS Bedrock",
        "env_key": "AWS_ACCESS_KEY_ID",
        "requires": ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"],
    },
    Provider.GOOGLE: {
        "name": "Google AI",
        "description": "Direct access to Gemini models",
        "env_key": "GOOGLE_API_KEY",
        "requires": ["GOOGLE_API_KEY"],
    },
}


@dataclass
class ModelConfig:
    """Current model configuration."""
    provider: Provider = Provider.OPENROUTER
    orchestrator_model: str = "google/gemini-2.0-flash-001"
    manager_model: str = "anthropic/claude-sonnet-4-20250514"
    worker_model: str = "google/gemini-2.0-flash-001"

    @classmethod
    def from_env(cls) -> "ModelConfig":
        """Load configuration from environment variables."""
        gateway = os.getenv("LLM_GATEWAY", "OpenRouterGateway")

        # Map gateway name to Provider enum
        provider_map = {p.value: p for p in Provider}
        provider = provider_map.get(gateway, Provider.OPENROUTER)

        return cls(
            provider=provider,
            orchestrator_model=os.getenv("ORCHESTRATOR_MODEL", "google/gemini-2.0-flash-001"),
            manager_model=os.getenv("MANAGER_MODEL", "anthropic/claude-sonnet-4-20250514"),
            worker_model=os.getenv("WORKER_MODEL", "google/gemini-2.0-flash-001"),
        )

    def to_env_lines(self) -> List[str]:
        """Generate .env file lines for this configuration."""
        return [
            f'LLM_GATEWAY="{self.provider.value}"',
            f'ORCHESTRATOR_MODEL="{self.orchestrator_model}"',
            f'MANAGER_MODEL="{self.manager_model}"',
            f'WORKER_MODEL="{self.worker_model}"',
        ]


def get_models_for_role(provider: Provider, role: ModelRole) -> List[ModelChoice]:
    """Get models recommended for a specific role."""
    all_models = PROVIDER_MODELS.get(provider, [])
    # Return models recommended for this role, plus all others
    recommended = [m for m in all_models if role in m.recommended_for]
    others = [m for m in all_models if role not in m.recommended_for]
    return recommended + others


def display_current_config(console: Console, config: ModelConfig) -> None:
    """Display current model configuration."""
    provider_info = PROVIDER_INFO[config.provider]

    table = Table(title="Current Model Configuration", show_header=True)
    table.add_column("Role", style="cyan")
    table.add_column("Model", style="green")
    table.add_column("Purpose", style="dim")

    table.add_row(
        "Provider",
        provider_info["name"],
        provider_info["description"],
    )
    table.add_row(
        "Orchestrator",
        config.orchestrator_model,
        "Thinking & strategic planning",
    )
    table.add_row(
        "Manager",
        config.manager_model,
        "Validation & task decomposition",
    )
    table.add_row(
        "Worker",
        config.worker_model,
        "Code execution & generation",
    )

    console.print(table)


def select_provider(console: Console, current: Provider) -> Optional[Provider]:
    """Interactive provider selection."""
    console.print("\n[bold]Select LLM Provider:[/bold]\n")

    providers = list(Provider)
    for i, p in enumerate(providers, 1):
        info = PROVIDER_INFO[p]
        marker = "[green]>[/green]" if p == current else " "
        # Check if configured
        configured = all(os.getenv(k) for k in info["requires"])
        status = "[green]configured[/green]" if configured else "[yellow]needs setup[/yellow]"
        console.print(f"  {marker} {i}. {info['name']:<20} {status}")
        console.print(f"       [dim]{info['description']}[/dim]")

    console.print(f"\n  0. Cancel")

    choice = Prompt.ask("\nChoice", default="0")

    try:
        idx = int(choice)
        if idx == 0:
            return None
        if 1 <= idx <= len(providers):
            return providers[idx - 1]
    except ValueError:
        pass

    return None


def select_model(
    console: Console,
    provider: Provider,
    role: ModelRole,
    current: str,
) -> Optional[str]:
    """Interactive model selection for a role."""
    role_names = {
        ModelRole.ORCHESTRATOR: ("Orchestrator", "Thinking & strategic planning"),
        ModelRole.MANAGER: ("Manager", "Validation & task decomposition"),
        ModelRole.WORKER: ("Worker", "Code execution & generation"),
    }
    role_name, role_desc = role_names[role]

    console.print(f"\n[bold]Select {role_name} Model:[/bold]")
    console.print(f"[dim]{role_desc}[/dim]\n")

    models = get_models_for_role(provider, role)

    if not models:
        console.print("[red]No models available for this provider[/red]")
        return None

    for i, m in enumerate(models, 1):
        marker = "[green]>[/green]" if m.id == current else " "
        recommended = "[cyan]*[/cyan]" if role in m.recommended_for else " "
        cost_color = {"low": "green", "medium": "yellow", "high": "red", "premium": "magenta"}
        cost = f"[{cost_color.get(m.cost_tier, 'white')}]{m.cost_tier}[/{cost_color.get(m.cost_tier, 'white')}]"

        console.print(f"  {marker}{recommended} {i}. {m.name:<25} {cost}")
        console.print(f"        [dim]{m.description}[/dim]")

    console.print(f"\n  [cyan]*[/cyan] = recommended for this role")
    console.print(f"  0. Cancel")
    console.print(f"  m. Enter model ID manually")

    choice = Prompt.ask("\nChoice", default="0")

    if choice.lower() == "m":
        return Prompt.ask("Enter model ID")

    try:
        idx = int(choice)
        if idx == 0:
            return None
        if 1 <= idx <= len(models):
            return models[idx - 1].id
    except ValueError:
        pass

    return None


def run_model_config(console: Console) -> Optional[ModelConfig]:
    """Run interactive model configuration wizard."""
    config = ModelConfig.from_env()

    console.print(Panel(
        "[bold]Model Configuration[/bold]\n\n"
        "Configure which models to use for each role:\n"
        "  [cyan]Orchestrator[/cyan] - Thinking & strategic planning\n"
        "  [cyan]Manager[/cyan]      - Validation & task decomposition\n"
        "  [cyan]Worker[/cyan]       - Code execution & generation",
        title="[bold blue]/model[/bold blue]",
    ))

    display_current_config(console, config)

    # Main menu
    while True:
        console.print("\n[bold]What would you like to change?[/bold]\n")
        console.print("  1. Provider")
        console.print("  2. Orchestrator Model (Thinking)")
        console.print("  3. Manager Model (Validation)")
        console.print("  4. Worker Model (Execution)")
        console.print("  5. Quick Setup (all three)")
        console.print()
        console.print("  s. Save & Apply")
        console.print("  0. Cancel")

        choice = Prompt.ask("\nChoice", default="0")

        if choice == "0":
            return None

        if choice.lower() == "s":
            return config

        if choice == "1":
            new_provider = select_provider(console, config.provider)
            if new_provider:
                config.provider = new_provider
                console.print(f"\n[green]Provider set to {PROVIDER_INFO[new_provider]['name']}[/green]")

        elif choice == "2":
            new_model = select_model(console, config.provider, ModelRole.ORCHESTRATOR, config.orchestrator_model)
            if new_model:
                config.orchestrator_model = new_model
                console.print(f"\n[green]Orchestrator model set to {new_model}[/green]")

        elif choice == "3":
            new_model = select_model(console, config.provider, ModelRole.MANAGER, config.manager_model)
            if new_model:
                config.manager_model = new_model
                console.print(f"\n[green]Manager model set to {new_model}[/green]")

        elif choice == "4":
            new_model = select_model(console, config.provider, ModelRole.WORKER, config.worker_model)
            if new_model:
                config.worker_model = new_model
                console.print(f"\n[green]Worker model set to {new_model}[/green]")

        elif choice == "5":
            # Quick setup - configure all three
            console.print("\n[bold]Quick Setup - Configure All Models[/bold]")

            new_orch = select_model(console, config.provider, ModelRole.ORCHESTRATOR, config.orchestrator_model)
            if new_orch:
                config.orchestrator_model = new_orch

            new_mgr = select_model(console, config.provider, ModelRole.MANAGER, config.manager_model)
            if new_mgr:
                config.manager_model = new_mgr

            new_worker = select_model(console, config.provider, ModelRole.WORKER, config.worker_model)
            if new_worker:
                config.worker_model = new_worker

            console.print("\n[green]All models configured![/green]")

        # Show updated config
        display_current_config(console, config)


def save_config_to_env(config: ModelConfig, env_path: Path) -> bool:
    """Save configuration to .env file."""
    try:
        # Read existing .env
        content = ""
        if env_path.exists():
            content = env_path.read_text()

        # Update or add each setting
        lines = content.split("\n")
        settings = {
            "LLM_GATEWAY": f'"{config.provider.value}"',
            "ORCHESTRATOR_MODEL": f'"{config.orchestrator_model}"',
            "MANAGER_MODEL": f'"{config.manager_model}"',
            "WORKER_MODEL": f'"{config.worker_model}"',
        }

        for key, value in settings.items():
            found = False
            for i, line in enumerate(lines):
                if line.startswith(f"{key}=") or line.startswith(f"{key} ="):
                    lines[i] = f"{key}={value}"
                    found = True
                    break
            if not found:
                lines.append(f"{key}={value}")

        # Write back
        env_path.write_text("\n".join(lines))
        return True

    except Exception:
        return False
