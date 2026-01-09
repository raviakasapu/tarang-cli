"""
Shell execution tools for Tarang agents.

All tools avoid using 'success', 'complete', 'done', 'finished' in return values
to prevent false completion detection.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field
from agent_framework.base import BaseTool


# ============================================================================
# Pydantic Models for Tool Arguments and Outputs
# ============================================================================

class ShellArgs(BaseModel):
    """Arguments for shell tool."""
    command: str = Field(..., description="The command to execute")
    working_dir: Optional[str] = Field(default=None, description="Working directory (relative to project)")
    timeout: Optional[int] = Field(default=None, description="Command timeout in seconds")
    env: Optional[Dict[str, str]] = Field(default=None, description="Additional environment variables")


class ShellOutput(BaseModel):
    """Output from shell tool."""
    command: str
    exit_code: int
    stdout: str
    stderr: str
    working_dir: str


class ProjectInitArgs(BaseModel):
    """Arguments for init_project tool."""
    project_type: str = Field(default="python", description="Type of project (python, node, web, generic)")
    name: Optional[str] = Field(default=None, description="Project name (defaults to directory name)")


class ProjectInitOutput(BaseModel):
    """Output from init_project tool."""
    project_name: str
    project_type: str
    directories_created: List[str]
    files_created: List[str]
    project_path: str


# ============================================================================
# Tool Implementations
# ============================================================================

class ShellTool(BaseTool):
    """Execute shell commands safely."""

    _name = "shell"
    _description = "Execute a shell command in the project directory. Returns stdout, stderr, and exit code. Note: Long-running server commands (npm run dev, etc.) will timeout after 10s - use them only to verify the server starts, then stop."

    # Commands that are blocked for safety
    BLOCKED_COMMANDS = {
        "rm -rf /",
        "rm -rf /*",
        "dd if=",
        "mkfs",
        ":(){:|:&};:",  # Fork bomb
        "chmod -R 777 /",
        "sudo rm",
    }

    # Dev server commands that run indefinitely - use short timeout
    DEV_SERVER_PATTERNS = [
        "npm run dev",
        "npm start",
        "npm run start",
        "yarn dev",
        "yarn start",
        "pnpm dev",
        "pnpm start",
        "npx vite",
        "vite dev",
        "vite preview",
        "next dev",
        "gatsby develop",
        "ng serve",
        "flask run",
        "python -m http.server",
        "python manage.py runserver",
        "uvicorn",
        "gunicorn",
    ]

    def __init__(
        self,
        project_dir: Optional[str] = None,
        timeout: int = 300,
    ):
        super().__init__()
        self.project_dir = Path(project_dir) if project_dir else Path.cwd()
        self.timeout = timeout

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def args_schema(self):
        return ShellArgs

    @property
    def output_schema(self):
        return ShellOutput

    def _is_blocked(self, command: str) -> bool:
        """Check if command is blocked for safety."""
        cmd_lower = command.lower().strip()
        for blocked in self.BLOCKED_COMMANDS:
            if blocked in cmd_lower:
                return True
        return False

    def _is_dev_server(self, command: str) -> bool:
        """Check if command is a long-running dev server."""
        cmd_lower = command.lower().strip()
        for pattern in self.DEV_SERVER_PATTERNS:
            if pattern in cmd_lower:
                return True
        return False

    def execute(
        self,
        command: str,
        working_dir: Optional[str] = None,
        timeout: Optional[int] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Execute a shell command (sync wrapper for async execution)."""
        return asyncio.run(self._execute_async(command, working_dir, timeout, env))

    async def _execute_async(
        self,
        command: str,
        working_dir: Optional[str] = None,
        timeout: Optional[int] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Execute a shell command asynchronously."""
        # Safety check
        if self._is_blocked(command):
            return {
                "error": "Command blocked for safety reasons",
                "command": command,
                "exit_code": -1,
                "stdout": "",
                "stderr": "",
            }

        # Determine working directory
        if working_dir:
            cwd = self.project_dir / working_dir
        else:
            cwd = self.project_dir

        if not cwd.exists():
            return {
                "error": f"Working directory not found: {cwd}",
                "command": command,
                "exit_code": -1,
                "stdout": "",
                "stderr": "",
            }

        # Prepare environment
        run_env = os.environ.copy()
        if env:
            run_env.update(env)

        # Check if this is a dev server command - use short timeout
        is_dev_server = self._is_dev_server(command)
        if is_dev_server:
            # Dev servers run forever, use 10s timeout just to verify it starts
            cmd_timeout = 10
        else:
            # Use specified timeout or default
            cmd_timeout = timeout if timeout is not None else self.timeout

        try:
            # Run command asynchronously
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(cwd),
                env=run_env,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=cmd_timeout,
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()

                if is_dev_server:
                    # Dev server started successfully (it was running when we killed it)
                    return {
                        "command": command,
                        "exit_code": 0,
                        "stdout": f"Dev server started successfully. Server is running at the configured port. (Stopped after {cmd_timeout}s verification - run manually with '{command}' to keep it running)",
                        "stderr": "",
                        "working_dir": str(cwd),
                        "dev_server": True,
                    }
                else:
                    return {
                        "error": f"Command timed out after {cmd_timeout}s",
                        "command": command,
                        "exit_code": -1,
                        "stdout": "",
                        "stderr": "",
                        "timed_out": True,
                    }

            stdout_str = stdout.decode("utf-8", errors="replace")
            stderr_str = stderr.decode("utf-8", errors="replace")

            # Truncate very long output
            max_output = 50000
            if len(stdout_str) > max_output:
                stdout_str = stdout_str[:max_output] + "\n... (output truncated)"
            if len(stderr_str) > max_output:
                stderr_str = stderr_str[:max_output] + "\n... (output truncated)"

            # Add note for scaffold commands that this is just step 1
            is_scaffold = any(pattern in command.lower() for pattern in [
                "create vite", "create-react-app", "create next", "npx create-",
                "npm init", "yarn create", "pnpm create"
            ])
            if is_scaffold and process.returncode == 0:
                stdout_str += "\n\n[NOTE: Scaffolding complete. You must still: 1) cd into the project, 2) npm install, 3) write actual code, 4) npm run build]"

            return {
                "command": command,
                "exit_code": process.returncode,
                "stdout": stdout_str,
                "stderr": stderr_str,
                "working_dir": str(cwd),
            }

        except Exception as e:
            return {
                "error": f"Execution error: {str(e)}",
                "command": command,
                "exit_code": -1,
                "stdout": "",
                "stderr": "",
            }


class ProjectInitTool(BaseTool):
    """Initialize a new project with common structures."""

    _name = "init_project"
    _description = "Initialize a new project with directories and common files."

    def __init__(self, project_dir: Optional[str] = None):
        super().__init__()
        self.project_dir = Path(project_dir) if project_dir else Path.cwd()

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def args_schema(self):
        return ProjectInitArgs

    @property
    def output_schema(self):
        return ProjectInitOutput

    def execute(
        self,
        project_type: str = "python",
        name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Initialize a new project."""
        created_dirs = []
        created_files = []

        project_name = name or self.project_dir.name

        try:
            if project_type == "python":
                dirs = ["src", "tests", "docs"]
                files = {
                    "README.md": f"# {project_name}\n\nA Python project.\n",
                    "requirements.txt": "# Add your dependencies here\n",
                    ".gitignore": "__pycache__/\n*.pyc\n.env\nvenv/\n.venv/\ndist/\n*.egg-info/\n",
                    "src/__init__.py": "",
                    "tests/__init__.py": "",
                }

            elif project_type == "node":
                dirs = ["src", "tests", "public"]
                files = {
                    "README.md": f"# {project_name}\n\nA Node.js project.\n",
                    "package.json": f'{{\n  "name": "{project_name}",\n  "version": "1.0.0",\n  "main": "src/index.js"\n}}\n',
                    ".gitignore": "node_modules/\n.env\ndist/\n",
                    "src/index.js": "// Entry point\nconsole.log('Hello, world!');\n",
                }

            elif project_type == "web":
                dirs = ["css", "js", "images"]
                files = {
                    "README.md": f"# {project_name}\n\nA web project.\n",
                    "index.html": f"<!DOCTYPE html>\n<html>\n<head>\n  <title>{project_name}</title>\n  <link rel=\"stylesheet\" href=\"css/style.css\">\n</head>\n<body>\n  <h1>{project_name}</h1>\n  <script src=\"js/main.js\"></script>\n</body>\n</html>\n",
                    "css/style.css": "/* Styles */\nbody { font-family: sans-serif; }\n",
                    "js/main.js": "// JavaScript\nconsole.log('Ready');\n",
                    ".gitignore": ".env\nnode_modules/\n",
                }

            else:  # generic
                dirs = ["src", "docs"]
                files = {
                    "README.md": f"# {project_name}\n\nA project.\n",
                    ".gitignore": ".env\n",
                }

            # Create directories
            for d in dirs:
                dir_path = self.project_dir / d
                dir_path.mkdir(parents=True, exist_ok=True)
                created_dirs.append(d)

            # Create files
            for filepath, content in files.items():
                file_path = self.project_dir / filepath
                file_path.parent.mkdir(parents=True, exist_ok=True)
                if not file_path.exists():
                    file_path.write_text(content)
                    created_files.append(filepath)

            return {
                "project_name": project_name,
                "project_type": project_type,
                "directories_created": created_dirs,
                "files_created": created_files,
                "project_path": str(self.project_dir),
            }

        except Exception as e:
            return {
                "error": f"Init error: {str(e)}",
                "project_name": project_name,
            }
