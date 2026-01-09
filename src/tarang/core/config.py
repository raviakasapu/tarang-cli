"""Configuration management for Tarang."""

import json
import os
from pathlib import Path
from typing import Any

# Global config directory
TARANG_HOME = Path.home() / ".tarang"
GLOBAL_CONFIG_FILE = TARANG_HOME / "config.json"

# Project-level config
PROJECT_CONFIG_DIR = ".tarang"
PROJECT_CONFIG_FILE = "project.json"


def get_config_path() -> Path:
    """Get the path to the global config file."""
    return GLOBAL_CONFIG_FILE


def get_project_config_path() -> Path | None:
    """Get the path to the project-level config file, if it exists."""
    cwd = Path.cwd()
    project_config = cwd / PROJECT_CONFIG_DIR / PROJECT_CONFIG_FILE
    if project_config.exists():
        return project_config
    return None


def ensure_config_dir():
    """Ensure the global config directory exists."""
    TARANG_HOME.mkdir(parents=True, exist_ok=True)


def load_config() -> dict[str, Any]:
    """Load configuration from global and project-level configs.

    Project-level config takes precedence over global config.
    """
    config: dict[str, Any] = {}

    # Load global config
    if GLOBAL_CONFIG_FILE.exists():
        with open(GLOBAL_CONFIG_FILE) as f:
            config = json.load(f)

    # Merge project-level config
    project_config_path = get_project_config_path()
    if project_config_path:
        with open(project_config_path) as f:
            project_config = json.load(f)
            config = {**config, **project_config}

    # Also check environment variables
    if os.environ.get("OPENROUTER_API_KEY"):
        config["openrouter_key"] = os.environ["OPENROUTER_API_KEY"]

    if os.environ.get("TARANG_API_KEY"):
        config["api_key"] = os.environ["TARANG_API_KEY"]

    return config


def save_config(config: dict[str, Any], project_level: bool = False):
    """Save configuration.

    Args:
        config: Configuration dictionary to save
        project_level: If True, save to project-level config; otherwise global
    """
    if project_level:
        project_dir = Path.cwd() / PROJECT_CONFIG_DIR
        project_dir.mkdir(parents=True, exist_ok=True)
        config_path = project_dir / PROJECT_CONFIG_FILE
    else:
        ensure_config_dir()
        config_path = GLOBAL_CONFIG_FILE

    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)


def get_api_base_url() -> str:
    """Get the API base URL for the Tarang backend."""
    return os.environ.get("TARANG_API_URL", "https://api.devtarang.ai")
