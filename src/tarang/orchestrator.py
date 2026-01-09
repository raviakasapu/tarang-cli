"""
Tarang Orchestrator - Build and run the main agent hierarchy.

This module creates agents from YAML configs using the deployment factory
and provides the main execution interface with state persistence.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from dotenv import load_dotenv

# Load environment variables
load_dotenv()


# Path to agent configs
CONFIGS_DIR = Path(__file__).parent.parent.parent / "configs" / "agents"


def check_framework() -> Dict[str, Any]:
    """Check if agent_framework is available and configured."""
    try:
        from agent_framework import Agent
        framework_available = True
    except ImportError:
        return {
            "available": False,
            "error": "agent_framework not installed",
        }

    # Check for API key
    api_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return {
            "available": True,
            "configured": False,
            "error": "OPENROUTER_API_KEY or OPENAI_API_KEY not set in environment",
        }

    return {
        "available": True,
        "configured": True,
    }


def build_orchestrator(
    project_dir: str,
    config_name: str = "orchestrator",
    verbose: bool = False,
) -> Any:
    """
    Build an agent from YAML configuration.

    Args:
        project_dir: The project directory to operate in
        config_name: Name of the config file (without .yaml)
        verbose: Enable verbose output

    Returns:
        The built agent

    Raises:
        ImportError: If agent_framework is not available
        FileNotFoundError: If config files are missing
    """
    from tarang.deployment.factory import AgentFactory

    # Map config names to paths
    config_paths = {
        "coder": CONFIGS_DIR / "coder.yaml",
        "orchestrator": CONFIGS_DIR / "orchestrator.yaml",
        "explorer": CONFIGS_DIR / "explorer.yaml",
        "planner": CONFIGS_DIR / "planner.yaml",
        "architect": CONFIGS_DIR / "architect.yaml",
        "worker_pool": CONFIGS_DIR / "worker_pool.yaml",
        "file_worker": CONFIGS_DIR / "file_worker.yaml",
        "shell_worker": CONFIGS_DIR / "shell_worker.yaml",
    }

    config_path = config_paths.get(config_name)
    if config_path is None:
        config_path = Path(config_name)

    if not config_path.exists():
        raise FileNotFoundError(
            f"Config not found at {config_path}. "
            "Make sure Tarang is properly installed."
        )

    # Build from YAML
    agent = AgentFactory.create_from_yaml(
        str(config_path),
        project_dir=project_dir,
        base_path=CONFIGS_DIR,
    )

    return agent


async def execute_instruction(
    instruction: str,
    project_dir: str = ".",
    config_name: str = "orchestrator",
    verbose: bool = False,
    save_state: bool = True,
    resume_state: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Execute a user instruction with state persistence.

    This is the main entry point for running Tarang tasks.

    Args:
        instruction: The user's instruction (query or build task)
        project_dir: The project directory to operate in
        config_name: Which agent config to use
        verbose: Enable verbose output
        save_state: Save execution state for resume
        resume_state: Optional ExecutionState to resume from

    Returns:
        Dict with execution result
    """
    from tarang.ui.terminal_ui import create_terminal_ui
    from tarang.state.execution_state import ExecutionStateManager, ExecutionStatus

    # Resolve project directory
    project_path = Path(project_dir).resolve()
    if not project_path.exists():
        return {
            "error": f"Project directory not found: {project_dir}",
            "instruction": instruction,
        }

    # Initialize state manager for checkpointing
    state_manager = ExecutionStateManager(str(project_path)) if save_state else None

    # Generate or use existing job ID
    job_id = str(uuid.uuid4())[:8]
    if resume_state:
        job_id = resume_state.job_id

    # Set job ID in environment for shared memory namespace
    os.environ["JOB_ID"] = job_id

    # Check for continuity context from previous completed execution
    continuity_context = ""
    if state_manager and not resume_state:
        previous_state = state_manager.load()
        if previous_state and previous_state.status == ExecutionStatus.COMPLETED.value:
            continuity_context = previous_state.get_continuity_context(max_chars=800)
            if continuity_context and verbose:
                print(f"[Context] Using continuity from previous execution")

    # Initialize state before try block so exception handler can reference it
    state = None

    try:
        # Build agent
        agent = build_orchestrator(
            str(project_path),
            config_name=config_name,
            verbose=verbose,
        )

        # Create progress handler
        ui = create_terminal_ui(verbose=verbose)

        # Create or resume execution state
        if state_manager:
            if resume_state:
                state = resume_state
                state.status = ExecutionStatus.RUNNING.value
                state_manager.save(state)
            else:
                state = state_manager.create_state(
                    job_id=job_id,
                    instruction=instruction,
                )
        else:
            state = None

        # Inject continuity context into instruction if available
        task_with_context = instruction
        if continuity_context:
            task_with_context = f"{instruction}\n\n## Project Context (from previous work):\n{continuity_context}"

        # Run the agent
        result = await agent.run(
            task=task_with_context,
            progress_handler=ui,
        )

        # Update state with result context for future continuity
        if state_manager and state:
            if isinstance(result, dict):
                state.update_from_result(result)
            state_manager.mark_completed(state)

        return {
            "instruction": instruction,
            "result": result,
            "project_dir": str(project_path),
            "job_id": job_id,
        }

    except KeyboardInterrupt:
        # User interrupted - save state for resume
        if state_manager and state:
            state_manager.mark_paused(state)

        return {
            "message": "Execution paused. Use 'tarang resume' to continue.",
            "instruction": instruction,
            "project_dir": str(project_path),
            "job_id": job_id,
            "can_resume": True,
        }

    except Exception as e:
        error_msg = str(e)

        # Mark failed
        if state_manager and state:
            state_manager.mark_failed(state, error_msg)

        return {
            "error": error_msg,
            "instruction": instruction,
            "project_dir": str(project_path),
            "job_id": job_id,
        }


async def resume_execution(
    project_dir: str = ".",
    verbose: bool = False,
) -> Dict[str, Any]:
    """
    Resume a previous execution from saved state.

    Args:
        project_dir: The project directory
        verbose: Enable verbose output

    Returns:
        Dict with resumed execution result
    """
    from tarang.state.execution_state import ExecutionStateManager, ExecutionStatus

    project_path = Path(project_dir).resolve()
    state_manager = ExecutionStateManager(str(project_path))

    # Check for resumable state
    if not state_manager.can_resume():
        return {
            "error": "No resumable execution found",
            "project_dir": str(project_path),
        }

    # Load state
    state = state_manager.load()
    if not state:
        return {
            "error": "Failed to load execution state",
            "project_dir": str(project_path),
        }

    # Check if expired
    if state.is_expired():
        return {
            "error": f"Execution expired (deadline exceeded). Started: {state.started_at}",
            "project_dir": str(project_path),
        }

    # Get resume info
    info = state_manager.get_resume_info()
    print(f"Resuming: {info['instruction']}")
    print(f"Progress: {info['progress']}")

    # Resume execution
    return await execute_instruction(
        instruction=state.instruction,
        project_dir=str(project_path),
        verbose=verbose,
        save_state=True,
        resume_state=state,
    )


def get_execution_status(project_dir: str = ".") -> Dict[str, Any]:
    """Get status of current/previous execution."""
    from tarang.state.execution_state import ExecutionStateManager

    project_path = Path(project_dir).resolve()
    state_manager = ExecutionStateManager(str(project_path))

    info = state_manager.get_resume_info()
    if not info:
        return {
            "status": "no_execution",
            "message": "No execution state found",
            "project_dir": str(project_path),
        }

    return {
        "status": info["status"],
        "instruction": info["instruction"],
        "progress": info["progress"],
        "can_resume": info["can_resume"],
        "project_dir": str(project_path),
    }


def run_sync(
    instruction: str,
    project_dir: str = ".",
    verbose: bool = False,
) -> Dict[str, Any]:
    """
    Synchronous wrapper for execute_instruction.

    For use in non-async contexts.
    """
    return asyncio.run(execute_instruction(
        instruction=instruction,
        project_dir=project_dir,
        verbose=verbose,
    ))
