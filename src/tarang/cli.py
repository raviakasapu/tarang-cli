"""
Tarang CLI - Command line interface for the AI coding agent.

Usage:
    tarang run "create a hello world app"
    tarang run "explain how authentication works"
    tarang init my-project
    tarang resume
    vibe status
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import click

from tarang import __version__
from tarang.orchestrator import (
    execute_instruction,
    resume_execution,
    get_execution_status,
    check_framework,
)
from tarang.memory.project_memory import ProjectMemory
from tarang.tools.shell_tools import ProjectInitTool


@click.group()
@click.version_option(version=__version__, prog_name="Tarang")
def cli():
    """
    Tarang v2 - AI Coding Agent with ManagerAgent Architecture.

    An autonomous coding agent that can explore, plan, and build software projects.
    """
    pass


@cli.command()
@click.argument("instruction", required=False)
@click.option(
    "--project-dir", "-p",
    default=".",
    help="Project directory to operate in (default: current directory)",
)
@click.option(
    "--config", "-c",
    default="coder",
    help="Agent config to use (coder, explorer, orchestrator)",
)
@click.option(
    "--verbose", "-v",
    is_flag=True,
    help="Enable verbose output with agent thinking",
)
@click.option(
    "--once",
    is_flag=True,
    help="Run single instruction and exit (no interactive mode)",
)
def run(instruction: str, project_dir: str, config: str, verbose: bool, once: bool):
    """
    Start Tarang - AI coding assistant.

    Without instruction: starts interactive mode
    With instruction: runs it, then enters interactive mode (use --once to exit after)

    Examples:

        tarang run                              # Interactive mode
        tarang run "explain the project"        # Run then continue chatting
        tarang run "fix linter errors" --once   # Run and exit
    """
    # Check framework availability
    status = check_framework()
    if not status.get("available"):
        click.echo(f"Error: {status.get('error')}", err=True)
        click.echo("Install with: pip install agent-framework", err=True)
        sys.exit(1)

    if not status.get("configured"):
        click.echo(f"Error: {status.get('error')}", err=True)
        click.echo("Set your API key: export OPENROUTER_API_KEY=your_key", err=True)
        sys.exit(1)

    # Resolve project directory
    project_path = Path(project_dir).resolve()
    if not project_path.exists():
        click.echo(f"Error: Project directory not found: {project_dir}", err=True)
        sys.exit(1)

    click.echo(f"\n{'='*60}")
    click.echo(f"Tarang v{__version__}")
    click.echo(f"Project: {project_path}")
    click.echo(f"Config: {config}")
    click.echo(f"{'='*60}\n")

    conversation_history = []

    def run_instruction(instr: str) -> str:
        """Run a single instruction and return response."""
        # Build context from conversation history
        context_prefix = ""
        if conversation_history:
            context_prefix = "Previous conversation:\n"
            for turn in conversation_history[-3:]:
                context_prefix += f"User: {turn['user']}\n"
                context_prefix += f"Assistant: {turn['assistant'][:300]}...\n\n"
            context_prefix += "Current request:\n"

        full_instruction = context_prefix + instr

        result = asyncio.run(execute_instruction(
            instruction=full_instruction,
            project_dir=str(project_path),
            config_name=config,
            verbose=verbose,
            save_state=True,
        ))

        if "error" in result:
            click.echo(f"\nError: {result['error']}", err=True)
            return ""

        # Extract response - check multiple possible locations
        agent_result = result.get("result", {})
        response_text = ""

        if isinstance(agent_result, dict):
            # Try multiple keys for the response
            summary = (
                agent_result.get("human_readable_summary") or
                agent_result.get("final_answer") or
                agent_result.get("response") or
                agent_result.get("summary") or
                agent_result.get("message")
            )
            if not summary:
                # Check nested payload
                payload = agent_result.get("payload", {})
                if isinstance(payload, dict):
                    summary = (
                        payload.get("message") or
                        payload.get("response") or
                        payload.get("final_answer")
                    )
            if summary:
                response_text = summary
                click.echo(f"\n{'─'*60}")
                click.echo(summary)
                click.echo(f"{'─'*60}\n")
        elif isinstance(agent_result, str) and agent_result:
            response_text = agent_result
            click.echo(f"\n{'─'*60}")
            click.echo(agent_result)
            click.echo(f"{'─'*60}\n")

        return response_text

    try:
        # Run initial instruction if provided
        if instruction:
            response = run_instruction(instruction)
            conversation_history.append({
                "user": instruction,
                "assistant": response or "Task completed"
            })

            if once:
                click.echo("Done.")
                return

        # Interactive mode
        if not once:
            click.echo("Type your instructions (or 'exit' to quit):\n")

        while not once:
            try:
                user_input = click.prompt("You", prompt_suffix=" > ", default="", show_default=False)

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

                response = run_instruction(user_input)
                conversation_history.append({
                    "user": user_input,
                    "assistant": response or "Task completed"
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
        sys.exit(1)


@cli.command()
@click.option(
    "--project-dir", "-p",
    default=".",
    help="Project directory to resume in",
)
@click.option(
    "--verbose", "-v",
    is_flag=True,
    help="Enable verbose output",
)
def resume(project_dir: str, verbose: bool):
    """
    Resume a previous execution from saved state.

    If Tarang was interrupted or encountered an error, use this command
    to continue from where it left off.

    Examples:

        tarang resume              # Resume in current directory
        tarang resume -p ./myapp   # Resume in specific project
    """
    status = check_framework()
    if not status.get("available") or not status.get("configured"):
        click.echo(f"Error: {status.get('error')}", err=True)
        sys.exit(1)

    project_path = Path(project_dir).resolve()

    # Check for resumable state before starting
    state_info = get_execution_status(str(project_path))
    if state_info.get("status") == "no_execution":
        click.echo("No execution state found to resume.")
        click.echo("Run 'tarang run \"<instruction>\"' to start a new task.")
        sys.exit(0)

    if not state_info.get("can_resume"):
        click.echo(f"Cannot resume: execution status is '{state_info.get('status')}'")
        if state_info.get("status") == "completed":
            click.echo("Task already completed. Run 'tarang run' to start a new task.")
        else:
            click.echo("Run 'tarang reset' to clear state, then 'tarang run' to start fresh.")
        sys.exit(0)

    click.echo(f"\n{'='*60}")
    click.echo(f"Resuming Tarang execution")
    click.echo(f"Project: {project_path}")
    click.echo(f"{'='*60}")
    click.echo(f"\nInstruction: {state_info.get('instruction', 'Unknown')}")
    click.echo(f"Progress: {state_info.get('progress', 'Starting...')}")
    click.echo(f"{'─'*60}\n")

    try:
        result = asyncio.run(resume_execution(
            project_dir=str(project_path),
            verbose=verbose,
        ))

        if "error" in result:
            click.echo(f"\nError: {result['error']}", err=True)
            sys.exit(1)

        if "message" in result:
            click.echo(f"\n{result['message']}")

        # Show result summary if available
        agent_result = result.get("result", {})
        if isinstance(agent_result, dict):
            summary = (
                agent_result.get("human_readable_summary") or
                agent_result.get("final_answer") or
                agent_result.get("response")
            )
            if summary:
                click.echo(f"\n{'─'*60}")
                click.echo(summary)
                click.echo(f"{'─'*60}")

        click.echo(f"\n{'='*60}")
        click.echo("Execution completed")
        click.echo(f"{'='*60}\n")

    except KeyboardInterrupt:
        click.echo("\n\nInterrupted by user. State saved.")
        click.echo("Run 'tarang resume' to continue later.")
        sys.exit(130)
    except Exception as e:
        click.echo(f"\nError: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.argument("project_name")
@click.option(
    "--type", "-t",
    "project_type",
    default="python",
    type=click.Choice(["python", "node", "web", "generic"]),
    help="Project type (default: python)",
)
def init(project_name: str, project_type: str):
    """
    Initialize a new project with common structure.

    Creates a new directory with appropriate files based on project type.

    Examples:

        tarang init my-app

        tarang init my-api --type node

        tarang init website --type web
    """
    project_path = Path(project_name).resolve()

    if project_path.exists():
        click.echo(f"Error: Directory already exists: {project_name}", err=True)
        sys.exit(1)

    # Create the directory
    project_path.mkdir(parents=True)

    # Initialize with ProjectInitTool
    async def do_init():
        tool = ProjectInitTool(str(project_path))
        return await tool.execute(project_type=project_type, name=project_name)

    result = asyncio.run(do_init())

    if "error" in result:
        click.echo(f"Error: {result['error']}", err=True)
        sys.exit(1)

    click.echo(f"\nCreated {project_type} project: {project_name}")
    click.echo(f"\nDirectories created:")
    for d in result.get("directories_created", []):
        click.echo(f"  - {d}/")

    click.echo(f"\nFiles created:")
    for f in result.get("files_created", []):
        click.echo(f"  - {f}")

    click.echo(f"\nNext steps:")
    click.echo(f"  cd {project_name}")
    click.echo(f"  tarang run \"<your instruction>\"")


@cli.command()
@click.option(
    "--project-dir", "-p",
    default=".",
    help="Project directory to check",
)
def status(project_dir: str):
    """
    Show the status of Tarang in the current project.

    Displays saved state, progress, and whether execution can be resumed.
    """
    project_path = Path(project_dir).resolve()

    # Check for .tarang directory
    tarang_dir = project_path / ".tarang"
    if not tarang_dir.exists():
        click.echo(f"No Tarang state found in {project_path}")
        click.echo("Run 'tarang run \"<instruction>\"' to start.")
        return

    # Get execution status using new state manager
    state_info = get_execution_status(str(project_path))

    click.echo(f"\n{'='*60}")
    click.echo(f"Tarang Status")
    click.echo(f"Project: {project_path}")
    click.echo(f"{'='*60}\n")

    if state_info.get("status") == "no_execution":
        click.echo(state_info.get("message", "No execution state found."))
        return

    instruction = state_info.get("instruction", "Unknown")
    status_val = state_info.get("status", "unknown")
    progress = state_info.get("progress", "")
    can_resume = state_info.get("can_resume", False)

    click.echo(f"Instruction: {instruction}")
    click.echo(f"Status: {status_val}")

    if progress:
        click.echo(f"Progress: {progress}")

    if status_val == "failed":
        click.echo("\nExecution failed.")
        click.echo("Run 'tarang run \"<instruction>\"' to start fresh.")

    elif status_val in ("running", "paused"):
        click.echo("\nExecution was interrupted.")
        if can_resume:
            click.echo("Run 'tarang resume' to continue from checkpoint.")
        else:
            click.echo("State has expired. Run 'tarang run' to start fresh.")

    elif status_val == "completed":
        click.echo("\nExecution completed successfully.")

    # Show legacy memory info if available
    memory = ProjectMemory(str(project_path))
    phases = memory.get_completed_phases()
    if phases:
        click.echo(f"\nCompleted phases: {len(phases)}")
        for phase in phases:
            click.echo(f"  - {phase}")


@cli.command()
@click.option(
    "--project-dir", "-p",
    default=".",
    help="Project directory to reset",
)
@click.option(
    "--force", "-f",
    is_flag=True,
    help="Don't ask for confirmation",
)
def reset(project_dir: str, force: bool):
    """
    Reset Tarang execution state for this project.

    Clears execution state so you can start fresh without removing all project data.
    Use 'tarang clean' to remove all Tarang data including memory.
    """
    from tarang.state.execution_state import ExecutionStateManager

    project_path = Path(project_dir).resolve()
    state_manager = ExecutionStateManager(str(project_path))

    # Check if there's state to reset
    state = state_manager.load()
    if not state:
        click.echo("No execution state to reset.")
        return

    if not force:
        click.echo(f"Current state: {state.status}")
        click.echo(f"Instruction: {state.instruction[:80]}...")
        click.confirm("Reset execution state?", abort=True)

    state_manager.clear()
    click.echo("Execution state reset. Ready for a fresh start.")


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

    Removes the .tarang directory and all saved state.
    """
    project_path = Path(project_dir).resolve()
    tarang_dir = project_path / ".tarang"

    if not tarang_dir.exists():
        click.echo("No Tarang state to clean.")
        return

    if not force:
        click.confirm(
            f"This will remove all Tarang state from {project_path}. Continue?",
            abort=True,
        )

    import shutil
    shutil.rmtree(tarang_dir)
    click.echo("Tarang state cleaned.")


@cli.command()
@click.option(
    "--project-dir", "-p",
    default=".",
    help="Project directory to operate in",
)
@click.option(
    "--config", "-c",
    default="explorer",
    help="Agent config to use",
)
@click.option(
    "--verbose", "-v",
    is_flag=True,
    help="Enable verbose output",
)
def chat(project_dir: str, config: str, verbose: bool):
    """
    Start an interactive chat session with Tarang.

    Allows follow-up questions and multi-turn conversations.

    Example:
        tarang chat
        > explain the project structure
        > can we add a logo?
        > exit
    """
    from tarang.orchestrator import execute_instruction, check_framework
    from tarang.memory.project_memory import ProjectMemory

    # Check framework
    status = check_framework()
    if not status.get("available") or not status.get("configured"):
        click.echo(f"Error: {status.get('error')}", err=True)
        sys.exit(1)

    project_path = Path(project_dir).resolve()
    if not project_path.exists():
        click.echo(f"Error: Project directory not found: {project_dir}", err=True)
        sys.exit(1)

    click.echo(f"\n{'='*60}")
    click.echo(f"Tarang v{__version__} - Interactive Mode")
    click.echo(f"Project: {project_path}")
    click.echo(f"{'='*60}")
    click.echo("\nType your instructions. Commands:")
    click.echo("  exit, quit, q  - Exit chat")
    click.echo("  clear          - Clear conversation history")
    click.echo("  status         - Show project status")
    click.echo(f"{'─'*60}\n")

    # Track conversation history for context
    conversation_history = []
    memory = ProjectMemory(str(project_path))

    while True:
        try:
            # Get user input
            user_input = click.prompt("You", prompt_suffix=" > ", default="", show_default=False)

            if not user_input.strip():
                continue

            # Handle special commands
            cmd = user_input.strip().lower()
            if cmd in ("exit", "quit", "q"):
                click.echo("\nGoodbye!")
                break
            elif cmd == "clear":
                conversation_history = []
                click.echo("Conversation history cleared.\n")
                continue
            elif cmd == "status":
                state = memory.load_state()
                if state:
                    click.echo(f"Last instruction: {state.get('instruction', 'N/A')[:50]}...")
                    click.echo(f"Status: {state.get('status', 'unknown')}")
                else:
                    click.echo("No previous state found.")
                click.echo()
                continue

            # Build context from conversation history
            context_prefix = ""
            if conversation_history:
                context_prefix = "Previous conversation:\n"
                for turn in conversation_history[-3:]:  # Keep last 3 turns for context
                    context_prefix += f"User: {turn['user']}\n"
                    context_prefix += f"Assistant: {turn['assistant'][:200]}...\n\n"
                context_prefix += "Current request:\n"

            full_instruction = context_prefix + user_input

            # Run the instruction
            click.echo()  # Blank line before output
            result = asyncio.run(execute_instruction(
                instruction=full_instruction,
                project_dir=str(project_path),
                config_name=config,
                verbose=verbose,
                save_state=True,
            ))

            if "error" in result:
                click.echo(f"Error: {result['error']}\n", err=True)
                continue

            # Extract and display result - check multiple possible locations
            agent_result = result.get("result", {})
            response_text = ""
            if isinstance(agent_result, dict):
                summary = (
                    agent_result.get("human_readable_summary") or
                    agent_result.get("final_answer") or
                    agent_result.get("response") or
                    agent_result.get("summary") or
                    agent_result.get("message")
                )
                if not summary:
                    payload = agent_result.get("payload", {})
                    if isinstance(payload, dict):
                        summary = (
                            payload.get("message") or
                            payload.get("response") or
                            payload.get("final_answer")
                        )
                if summary:
                    response_text = summary
                    click.echo(f"\n{'─'*60}")
                    click.echo(summary)
                    click.echo(f"{'─'*60}\n")
            elif isinstance(agent_result, str) and agent_result:
                response_text = agent_result
                click.echo(f"\n{'─'*60}")
                click.echo(agent_result)
                click.echo(f"{'─'*60}\n")

            # Save to conversation history
            conversation_history.append({
                "user": user_input,
                "assistant": response_text or "Task completed"
            })

        except KeyboardInterrupt:
            click.echo("\n\nInterrupted. Type 'exit' to quit or continue chatting.\n")
            continue
        except EOFError:
            click.echo("\nGoodbye!")
            break
        except Exception as e:
            click.echo(f"Error: {e}\n", err=True)
            continue


@cli.command()
def check():
    """
    Check if Tarang is properly configured.

    Verifies that all dependencies are installed and environment is set up.
    """
    click.echo(f"\n{'='*60}")
    click.echo(f"Tarang Configuration Check")
    click.echo(f"{'='*60}\n")

    # Check version
    click.echo(f"Tarang version: {__version__}")

    # Check framework
    status = check_framework()

    if status.get("available"):
        click.echo("agent_framework: installed")
    else:
        click.echo("agent_framework: NOT INSTALLED")
        click.echo("  Install with: pip install agent-framework")

    if status.get("configured"):
        click.echo("OPENROUTER_API_KEY: configured")
    else:
        click.echo("OPENROUTER_API_KEY: NOT SET")
        click.echo("  Set with: export OPENROUTER_API_KEY=your_key")

    # Check config files
    from tarang.orchestrator import CONFIGS_DIR
    configs = [
        "orchestrator.yaml",
        "architect.yaml",
        "explorer.yaml",
        "coder.yaml",
        "planner.yaml",
        "worker_pool.yaml",
        "file_worker.yaml",
        "shell_worker.yaml",
    ]

    click.echo(f"\nConfig directory: {CONFIGS_DIR}")
    all_configs_present = True
    for config in configs:
        config_path = CONFIGS_DIR / config
        if config_path.exists():
            click.echo(f"  {config}: present")
        else:
            click.echo(f"  {config}: MISSING")
            all_configs_present = False

    # Summary
    click.echo(f"\n{'='*60}")
    if status.get("available") and status.get("configured") and all_configs_present:
        click.echo("All checks passed! Tarang is ready to use.")
    else:
        click.echo("Some checks failed. Please fix the issues above.")
    click.echo(f"{'='*60}\n")


def main():
    """Main entry point."""
    cli()


if __name__ == "__main__":
    main()
