"""
Registry for Tarang components.

Registers tools, planners, gateways, etc. for use in YAML configs.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Type

import yaml

# Import framework components
from agent_framework.components.planners import (
    ReActPlanner,
    ChatPlanner,
    WorkerRouterPlanner,
    StrategicPlanner,
    StrategicDecomposerPlanner,
)
from agent_framework.components.memory import InMemoryMemory, SharedInMemoryMemory
from agent_framework.gateways.inference import OpenAIGateway

# Import Tarang tools
from tarang.tools.file_tools import (
    ListFilesTool,
    ReadFileTool,
    WriteFileTool,
    EditFileTool,
    SearchFilesTool,
)
from tarang.tools.shell_tools import ShellTool, ProjectInitTool
from tarang.tools.validation_tools import (
    ValidateFileTool,
    ValidateBuildTool,
    ValidateStructureTool,
)


# Config root for YAML-based component discovery
_CONFIG_ROOT = Path(__file__).resolve().parents[2] / "configs"


def _load_component_configs(category: str) -> Dict[str, Type]:
    """Load component configs from YAML files."""
    registry: Dict[str, Type] = {}
    directory = _CONFIG_ROOT / category

    if not directory.exists():
        return registry

    for path in sorted(directory.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            continue

        name = data.get("name")
        if not name:
            continue

        # For now, we just note the config exists
        # Actual class loading happens via explicit registry below
        pass

    return registry


# ============================================================================
# Explicit Tool Registry
# ============================================================================

TOOL_REGISTRY: Dict[str, Type] = {
    # Tarang file tools
    "ListFilesTool": ListFilesTool,
    "ReadFileTool": ReadFileTool,
    "WriteFileTool": WriteFileTool,
    "EditFileTool": EditFileTool,
    "SearchFilesTool": SearchFilesTool,
    # Tarang shell tools
    "ShellTool": ShellTool,
    "ProjectInitTool": ProjectInitTool,
    # Tarang validation tools
    "ValidateFileTool": ValidateFileTool,
    "ValidateBuildTool": ValidateBuildTool,
    "ValidateStructureTool": ValidateStructureTool,
    # Also register with full paths for YAML compatibility
    "tarang.tools.file_tools.ListFilesTool": ListFilesTool,
    "tarang.tools.file_tools.ReadFileTool": ReadFileTool,
    "tarang.tools.file_tools.WriteFileTool": WriteFileTool,
    "tarang.tools.file_tools.EditFileTool": EditFileTool,
    "tarang.tools.file_tools.SearchFilesTool": SearchFilesTool,
    "tarang.tools.shell_tools.ShellTool": ShellTool,
    "tarang.tools.shell_tools.ProjectInitTool": ProjectInitTool,
    "tarang.tools.validation_tools.ValidateFileTool": ValidateFileTool,
    "tarang.tools.validation_tools.ValidateBuildTool": ValidateBuildTool,
    "tarang.tools.validation_tools.ValidateStructureTool": ValidateStructureTool,
}


# ============================================================================
# Explicit Planner Registry
# ============================================================================

PLANNER_REGISTRY: Dict[str, Type] = {
    "ReActPlanner": ReActPlanner,
    "ChatPlanner": ChatPlanner,
    "WorkerRouterPlanner": WorkerRouterPlanner,
    "StrategicPlanner": StrategicPlanner,
    "StrategicDecomposerPlanner": StrategicDecomposerPlanner,
}


# ============================================================================
# Explicit Gateway Registry
# ============================================================================

GATEWAY_REGISTRY: Dict[str, Type] = {
    "OpenAIGateway": OpenAIGateway,
    # OpenRouter uses OpenAI-compatible gateway with different base_url
    "OpenRouterGateway": OpenAIGateway,
}


# ============================================================================
# Explicit Memory Registry
# ============================================================================

MEMORY_REGISTRY: Dict[str, Type] = {
    "InMemoryMemory": InMemoryMemory,
    "SharedInMemoryMemory": SharedInMemoryMemory,
}


# ============================================================================
# Policy Registry
# ============================================================================

from agent_framework.policies.default import (
    DefaultCompletionDetector,
    DefaultTerminationPolicy,
    DefaultLoopPreventionPolicy,
    DefaultHITLPolicy,
    DefaultCheckpointPolicy,
    DefaultFollowUpPolicy,
)
from agent_framework.policies.retry import (
    ExponentialBackoffRetryPolicy,
    SimpleRetryPolicy,
    NoRetryPolicy,
)
from agent_framework.policies.validation import (
    RuleBasedValidationPolicy,
    LLMValidationPolicy,
    HybridValidationPolicy,
)

POLICY_REGISTRY: Dict[str, Type] = {
    "DefaultCompletionDetector": DefaultCompletionDetector,
    "DefaultTerminationPolicy": DefaultTerminationPolicy,
    "DefaultLoopPreventionPolicy": DefaultLoopPreventionPolicy,
    "DefaultHITLPolicy": DefaultHITLPolicy,
    "DefaultCheckpointPolicy": DefaultCheckpointPolicy,
    "DefaultFollowUpPolicy": DefaultFollowUpPolicy,
    # Retry policies
    "ExponentialBackoffRetryPolicy": ExponentialBackoffRetryPolicy,
    "SimpleRetryPolicy": SimpleRetryPolicy,
    "NoRetryPolicy": NoRetryPolicy,
    # Validation policies
    "RuleBasedValidationPolicy": RuleBasedValidationPolicy,
    "LLMValidationPolicy": LLMValidationPolicy,
    "HybridValidationPolicy": HybridValidationPolicy,
}


# ============================================================================
# Subscriber Registry (empty for now)
# ============================================================================

SUBSCRIBER_REGISTRY: Dict[str, Type] = {}


# ============================================================================
# Prompt Registry (empty for now)
# ============================================================================

PROMPT_REGISTRY: Dict[str, Type] = {}


# ============================================================================
# Dynamic Registration Functions
# ============================================================================

def register_tool(name: str, tool_class: Type) -> None:
    """Register a tool class."""
    TOOL_REGISTRY[name] = tool_class


def register_planner(name: str, planner_class: Type) -> None:
    """Register a planner class."""
    PLANNER_REGISTRY[name] = planner_class


def register_gateway(name: str, gateway_class: Type) -> None:
    """Register an inference gateway class."""
    GATEWAY_REGISTRY[name] = gateway_class


def register_memory(name: str, memory_class: Type) -> None:
    """Register a memory class."""
    MEMORY_REGISTRY[name] = memory_class
