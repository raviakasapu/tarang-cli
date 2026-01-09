"""
Terminal UI for Tarang - Claude Code style output.

Uses ⏺ for agent actions and ⎿ for results.
"""
from __future__ import annotations

import sys
from datetime import datetime
from typing import Any, Dict, Optional

try:
    from rich.console import Console
    from rich.markup import escape
    from rich.text import Text
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False


class TerminalUI:
    """
    Terminal-based UI for Tarang agents.

    Displays agent activity in Claude Code style:
    ⏺ Agent thinking/tool calls
    ⎿ Results
    """

    DISC = "⏺"
    HOOK = "⎿"

    # Agent colors for visual distinction
    AGENT_COLORS = {
        "VibeCodeOrchestrator": "bright_blue",
        "CodeExplorer": "bright_green",
        "TaskPlanner": "bright_yellow",
        "TaskArchitect": "bright_magenta",
        "WorkerPool": "bright_cyan",
        "FileWorker": "green",
        "ShellWorker": "yellow",
        # Fallback for unknown agents
        "default": "white",
    }

    # Tool colors
    TOOL_COLORS = {
        "list_files": "cyan",
        "read_file": "green",
        "write_file": "yellow",
        "edit_file": "magenta",
        "search_files": "blue",
        "shell": "red",
        "complete_task": "bright_green",
        "delegate": "bright_blue",
        "default": "white",
    }

    def __init__(
        self,
        colorize: bool = True,
        show_agent_name: bool = True,
        show_timestamps: bool = False,
        verbose: bool = False,
    ):
        """
        Initialize terminal UI.

        Args:
            colorize: Whether to use colors
            show_agent_name: Show agent name in output
            show_timestamps: Show timestamps
            verbose: Show detailed output
        """
        self.colorize = colorize and RICH_AVAILABLE
        self.show_agent_name = show_agent_name
        self.show_timestamps = show_timestamps
        self.verbose = verbose

        self.current_agent: Optional[str] = None
        self.iteration = 0
        self.start_time = datetime.now()

        if self.colorize:
            self.console = Console()
        else:
            self.console = None

    def _get_agent_color(self, agent_name: str) -> str:
        """Get color for an agent."""
        return self.AGENT_COLORS.get(agent_name, self.AGENT_COLORS["default"])

    def _get_tool_color(self, tool_name: str) -> str:
        """Get color for a tool."""
        return self.TOOL_COLORS.get(tool_name, self.TOOL_COLORS["default"])

    def _print(self, message: str, style: Optional[str] = None):
        """Print message to terminal."""
        if self.colorize and self.console:
            if style:
                self.console.print(message, style=style)
            else:
                self.console.print(message)
        else:
            print(message)

    def _format_timestamp(self) -> str:
        """Format current timestamp."""
        if self.show_timestamps:
            return f"[{datetime.now().strftime('%H:%M:%S')}] "
        return ""

    def _format_args(self, args: Dict[str, Any], max_len: int = 200, tool_name: str = "") -> str:
        """Format tool arguments for display.

        Args:
            args: Tool arguments dict
            max_len: Max length for total args string (default 200)
            tool_name: Tool name to customize formatting (e.g., show full shell commands)
        """
        if not args:
            return ""

        parts = []
        for key, value in args.items():
            if isinstance(value, str):
                # Show full command for shell tool, truncate others
                if tool_name == "shell" and key == "command":
                    # Show full command, just escape quotes
                    parts.append(f'{key}="{value}"')
                elif len(value) > 80:
                    value = value[:77] + "..."
                    parts.append(f'{key}="{value}"')
                else:
                    parts.append(f'{key}="{value}"')
            elif isinstance(value, bool):
                parts.append(f"{key}={str(value).lower()}")
            elif isinstance(value, (int, float)):
                parts.append(f"{key}={value}")
            elif isinstance(value, dict):
                parts.append(f"{key}={{...}}")
            elif isinstance(value, list):
                parts.append(f"{key}=[{len(value)} items]")
            else:
                parts.append(f"{key}={value}")

        result = ", ".join(parts)
        # Don't truncate shell commands
        if tool_name != "shell" and len(result) > max_len:
            result = result[:max_len - 3] + "..."
        return result

    def _summarize_result(self, result: Dict[str, Any]) -> str:
        """Summarize a tool result for display."""
        if not isinstance(result, dict):
            return str(result)[:100]

        # Handle errors
        if "error" in result:
            return f"Error: {result['error']}"

        # Handle different result types
        if "files" in result:
            count = result.get("count", len(result["files"]))
            truncated = " (truncated)" if result.get("truncated") else ""
            return f"Found {count} files{truncated}"

        if "content" in result:
            lines = result.get("lines", 0)
            truncated = " (truncated)" if result.get("truncated") else ""
            return f"Read {lines} lines{truncated}"

        if "lines_written" in result:
            return f"Wrote {result['lines_written']} lines to {result.get('file_path', 'file')}"

        if "replacements" in result:
            count = result['replacements']
            file_path = result.get('file_path', 'file')
            summary = f"Made {count} replacement(s) in {file_path}"
            # Show what was changed if available
            if result.get('old_text') and result.get('new_text'):
                old_preview = result['old_text'][:100].replace('\n', '\\n')
                new_preview = result['new_text'][:100].replace('\n', '\\n')
                summary += f"\n       - {old_preview}..."
                summary += f"\n       + {new_preview}..."
            return summary

        if "matches" in result:
            count = result.get("count", len(result["matches"]))
            return f"Found {count} matches"

        if "exit_code" in result:
            code = result["exit_code"]
            if code == 0:
                return "Command executed (exit 0)"
            else:
                return f"Command exited with code {code}"

        if "response" in result:
            response = result["response"]
            if len(response) > 100:
                return response[:97] + "..."
            return response

        # Generic summary
        keys = list(result.keys())[:3]
        return f"Result with keys: {', '.join(keys)}"

    # Event handlers for agent framework events

    def on_agent_start(self, agent_name: str, task: str):
        """Called when an agent starts."""
        self.current_agent = agent_name
        self.iteration = 0

        # Truncate long tasks
        task_display = task[:150] + "..." if len(task) > 150 else task

        timestamp = self._format_timestamp()
        agent_color = self._get_agent_color(agent_name)

        if self.colorize:
            self._print(f"\n{timestamp}{self.DISC} [{agent_name}] {task_display}", style=agent_color)
        else:
            self._print(f"\n{timestamp}{self.DISC} [{agent_name}] {task_display}")

    def on_agent_thinking(self, agent_name: str, thought: str):
        """Called when agent is thinking/planning."""
        if not self.verbose:
            return

        timestamp = self._format_timestamp()
        thought_display = thought[:200] + "..." if len(thought) > 200 else thought

        if self.colorize:
            self._print(f"{timestamp}  {self.HOOK} Thinking: {thought_display}", style="dim")
        else:
            self._print(f"{timestamp}  {self.HOOK} Thinking: {thought_display}")

    def on_llm_request(self, messages: list, agent_name: str = ""):
        """Called when LLM request is made (debug)."""
        if not self.verbose:
            return

        # Show last user message (the prompt)
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = str(msg.get("content", ""))[:100]
                self._print(f"  [PROMPT→] {content}...", style="cyan dim")
                break

    def on_llm_response(self, response: str, agent_name: str = ""):
        """Called when LLM response is received (debug)."""
        if not self.verbose:
            return

        response_preview = str(response)[:100]
        self._print(f"  [←RESPONSE] {response_preview}...", style="green dim")

    def on_tool_start(self, tool_name: str, args: Dict[str, Any]):
        """Called when a tool is about to be executed."""
        self.iteration += 1
        timestamp = self._format_timestamp()
        args_str = self._format_args(args, tool_name=tool_name)
        tool_color = self._get_tool_color(tool_name)

        display = f"{timestamp}{self.DISC} {tool_name}({args_str})"

        if self.colorize:
            self._print(f"\n{display}", style=tool_color)
        else:
            self._print(f"\n{display}")

    def on_tool_end(self, tool_name: str, result: Dict[str, Any]):
        """Called when a tool finishes execution."""
        timestamp = self._format_timestamp()
        summary = self._summarize_result(result)

        if self.colorize:
            self._print(f"{timestamp}  {self.HOOK} {summary}", style="dim")
        else:
            self._print(f"{timestamp}  {self.HOOK} {summary}")

        # In verbose mode, show more details for certain results
        if self.verbose and isinstance(result, dict):
            # Show file content preview
            if "content" in result and result.get("lines", 0) > 0:
                content = result["content"]
                if len(content) > 500:
                    self._print(f"       [Preview: {content[:500]}...]", style="dim")
            # Show shell output
            if "stdout" in result or "stderr" in result:
                stdout = result.get("stdout", "")[:300]
                stderr = result.get("stderr", "")[:300]
                if stdout:
                    self._print(f"       stdout: {stdout}", style="dim")
                if stderr:
                    self._print(f"       stderr: {stderr}", style="yellow dim")

    def on_agent_end(self, agent_name: str, result: Any):
        """Called when an agent completes."""
        timestamp = self._format_timestamp()

        if isinstance(result, dict):
            # Check multiple possible keys for the response
            response = (
                result.get("response") or
                result.get("final_answer") or
                result.get("summary") or
                result.get("result") or
                result.get("message")
            )
            if response:
                # Show final response
                if self.colorize:
                    self._print(f"\n{timestamp}{self.DISC} {response}", style="bright_white")
                else:
                    self._print(f"\n{timestamp}{self.DISC} {response}")
        elif isinstance(result, str) and result:
            # Handle string results
            if self.colorize:
                self._print(f"\n{timestamp}{self.DISC} {result}", style="bright_white")
            else:
                self._print(f"\n{timestamp}{self.DISC} {result}")

        self.current_agent = None

    def on_error(self, agent_name: str, error: str):
        """Called when an error occurs."""
        timestamp = self._format_timestamp()

        if self.colorize:
            self._print(f"\n{timestamp}{self.DISC} Error in {agent_name}: {error}", style="red bold")
        else:
            self._print(f"\n{timestamp}{self.DISC} Error in {agent_name}: {error}")

    def on_delegation(self, from_agent: str, to_agent: str, task: str):
        """Called when an agent delegates to another."""
        timestamp = self._format_timestamp()
        task_display = task[:100] + "..." if len(task) > 100 else task

        if self.colorize:
            self._print(
                f"\n{timestamp}{self.DISC} [{from_agent}] → [{to_agent}]: {task_display}",
                style="bright_blue"
            )
        else:
            self._print(f"\n{timestamp}{self.DISC} [{from_agent}] → [{to_agent}]: {task_display}")

    def on_phase_start(self, phase_name: str, phase_num: int, total_phases: int):
        """Called when a new phase starts."""
        timestamp = self._format_timestamp()

        if self.colorize:
            self._print(
                f"\n{timestamp}{'─' * 40}\n{self.DISC} Phase {phase_num}/{total_phases}: {phase_name}",
                style="bright_cyan bold"
            )
        else:
            self._print(f"\n{timestamp}{'─' * 40}\n{self.DISC} Phase {phase_num}/{total_phases}: {phase_name}")

    def on_phase_end(self, phase_name: str, result: str):
        """Called when a phase completes."""
        timestamp = self._format_timestamp()
        result_display = result[:100] + "..." if len(result) > 100 else result

        if self.colorize:
            self._print(f"{timestamp}  {self.HOOK} Phase result: {result_display}", style="dim cyan")
        else:
            self._print(f"{timestamp}  {self.HOOK} Phase result: {result_display}")

    # Framework event subscriber interface (async)

    def _extract_task(self, data: Dict[str, Any]) -> str:
        """Extract task description from event data."""
        task = data.get("task", "")
        if isinstance(task, dict):
            return task.get("description", "")
        return str(task) if task else ""

    def _extract_args(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Extract tool args from event data."""
        # Try direct args first, then check inside action dict
        args = data.get("args")
        if args is not None:
            return args
        action = data.get("action", {})
        if isinstance(action, dict):
            return action.get("args", {})
        return {}

    async def on_event(self, event_name: str, data: Dict[str, Any]) -> None:
        """
        Handle events from the agent framework (async interface).

        Maps framework events to UI methods.
        """
        event_handlers = {
            "agent_start": lambda d: self.on_agent_start(
                d.get("agent_name", "Unknown"),
                self._extract_task(d)
            ),
            "agent_end": lambda d: self.on_agent_end(
                d.get("agent_name", "Unknown"),
                d.get("result", {})
            ),
            "agent_thinking": lambda d: self.on_agent_thinking(
                d.get("agent_name", "Unknown"),
                d.get("thought", "")
            ),
            "tool_start": lambda d: self.on_tool_start(
                d.get("tool_name", "unknown"),
                d.get("tool_args", {})
            ),
            "tool_end": lambda d: self.on_tool_end(
                d.get("tool_name", "unknown"),
                d.get("result", {})
            ),
            "action_planned": lambda d: self.on_tool_start(
                d.get("tool_name", d.get("tool", "unknown")),
                self._extract_args(d)
            ),
            "action_executed": lambda d: self.on_tool_end(
                d.get("tool_name", d.get("tool", "unknown")),
                d.get("result", {})
            ),
            "worker_delegated": lambda d: self.on_delegation(
                d.get("from_agent", "Unknown"),
                d.get("to_agent", "Unknown"),
                self._extract_task(d)
            ),
            "phase_start": lambda d: self.on_phase_start(
                d.get("phase_name", "Unknown"),
                d.get("phase_num", 1),
                d.get("total_phases", 1)
            ),
            "phase_end": lambda d: self.on_phase_end(
                d.get("phase_name", "Unknown"),
                d.get("result", "")
            ),
            "error": lambda d: self.on_error(
                d.get("agent_name", "Unknown"),
                d.get("error", d.get("error_message", "Unknown error"))
            ),
            "llm_start": lambda d: self.on_llm_request(
                d.get("messages", []),
                d.get("agent_name", "")
            ),
            "llm_end": lambda d: self.on_llm_response(
                d.get("response", d.get("content", "")),
                d.get("agent_name", "")
            ),
            "planner_response": lambda d: self.on_llm_response(
                d.get("raw_response", d.get("response", "")),
                d.get("agent_name", "")
            )
        }

        handler = event_handlers.get(event_name)
        if handler:
            try:
                handler(data)
            except Exception as e:
                # Don't let UI errors crash the agent
                if self.verbose:
                    self._print(f"UI error handling {event_name}: {e}", style="red dim")
        elif self.verbose:
            # Log unhandled events in verbose mode for debugging
            data_preview = str(data)[:80] if data else ""
            self._print(f"  [event:{event_name}] {data_preview}", style="dim")

    # Sync alias for backwards compatibility
    def handle_event(self, event_name: str, data: Dict[str, Any]):
        """Sync wrapper for on_event."""
        import asyncio
        # Just call the handlers directly (they're sync internally)
        event_handlers = {
            "agent_start": lambda d: self.on_agent_start(d.get("agent_name", "Unknown"), d.get("task", "")),
            "tool_start": lambda d: self.on_tool_start(d.get("tool_name", "unknown"), d.get("tool_args", {})),
            "tool_end": lambda d: self.on_tool_end(d.get("tool_name", "unknown"), d.get("result", {})),
        }
        handler = event_handlers.get(event_name)
        if handler:
            handler(data)


# Convenience function for creating default UI
def create_terminal_ui(verbose: bool = False) -> TerminalUI:
    """Create a terminal UI with sensible defaults."""
    return TerminalUI(
        colorize=True,
        show_agent_name=True,
        show_timestamps=False,
        verbose=verbose,
    )
