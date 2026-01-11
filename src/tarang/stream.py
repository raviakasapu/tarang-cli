"""
SSE Stream Client with REST Callbacks - Industry-standard pattern.

This implements the SSE + REST callback pattern used by OpenAI, Anthropic, Cursor:
1. CLI sends POST /v3/execute with instruction + initial context
2. Backend streams SSE events (status, tool_request, plan, change, etc.)
3. When backend needs a tool result, it sends tool_request and WAITS
4. CLI executes the tool locally
5. CLI sends POST /v3/callback with the result
6. Backend continues the stream

Benefits:
- Serverless-friendly (Vercel, Cloudflare Workers)
- Simpler than WebSocket (unidirectional stream)
- Auto-reconnection via Last-Event-ID
- Easier debugging (curl-friendly)
"""
from __future__ import annotations

import fnmatch
import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, AsyncGenerator, Callable, Dict, Optional

import httpx
from rich.console import Console

from tarang.context_collector import ProjectContext
from tarang.ui.formatter import OutputFormatter

logger = logging.getLogger(__name__)


class EventType(str, Enum):
    """SSE event types from backend."""
    STATUS = "status"
    TOOL_REQUEST = "tool_request"  # Legacy name
    TOOL_CALL = "tool_call"  # New name (SSE Split Architecture)
    TOOL_DONE = "tool_done"
    THINKING = "thinking"  # New: agent thinking
    PLAN = "plan"
    CHANGE = "change"
    CONTENT = "content"
    ERROR = "error"
    COMPLETE = "complete"
    CANCELLED = "cancelled"


@dataclass
class StreamEvent:
    """An event from the SSE stream."""
    type: EventType
    data: Dict[str, Any]

    @classmethod
    def from_sse(cls, event: str, data: str) -> "StreamEvent":
        """Parse from SSE format."""
        try:
            event_type = EventType(event)
        except ValueError:
            event_type = EventType.ERROR

        try:
            parsed_data = json.loads(data)
        except json.JSONDecodeError:
            parsed_data = {"message": data}

        return cls(type=event_type, data=parsed_data)


@dataclass
class FileChange:
    """A file change from the stream."""
    type: str  # "create" or "edit"
    path: str
    content: Optional[str] = None
    search: Optional[str] = None
    replace: Optional[str] = None
    description: str = ""

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FileChange":
        return cls(
            type=data.get("type", ""),
            path=data.get("path", ""),
            content=data.get("content"),
            search=data.get("search"),
            replace=data.get("replace"),
            description=data.get("description", ""),
        )


class LocalToolExecutor:
    """
    Executes tools locally on the CLI side.

    Tools are aligned with backend's tool_provider.py TOOL_DEFINITIONS:
    - read_file, list_files, search_files, get_file_info (read-only)
    - write_file, edit_file, shell, delete_file (require approval)
    """

    # Files/directories to ignore
    IGNORE_PATTERNS = {
        ".git", ".svn", ".hg",
        "node_modules", "venv", ".venv", "env", ".env",
        "__pycache__", ".pytest_cache", ".mypy_cache",
        "vendor", "packages",
        "dist", "build", ".next", ".nuxt", "out",
        "target", "bin", "obj",
        ".idea", ".vscode", ".vs",
        ".tarang", ".tarang_backups",
        "*.pyc", "*.pyo", "*.so", "*.dylib",
        "*.egg-info", "*.egg",
        ".DS_Store", "Thumbs.db",
    }

    def __init__(self, project_root: str):
        self.project_root = Path(project_root).resolve()

    def execute(self, tool: str, args: dict) -> dict:
        """Execute a tool and return the result."""
        try:
            # Read-only tools
            if tool == "list_files":
                return self._list_files(args)
            elif tool == "read_file":
                return self._read_file(args)
            elif tool == "search_files":
                return self._search_files(args)
            elif tool == "get_file_info":
                return self._get_file_info(args)
            # Write tools (require approval - handled by caller)
            elif tool == "write_file":
                return self._write_file(args)
            elif tool == "edit_file":
                return self._edit_file(args)
            elif tool == "delete_file":
                return self._delete_file(args)
            elif tool == "shell":
                return self._shell(args)
            # Validation tools
            elif tool == "validate_file":
                return self._validate_file(args)
            elif tool == "validate_build":
                return self._validate_build(args)
            elif tool == "validate_structure":
                return self._validate_structure(args)
            elif tool == "lint_check":
                return self._lint_check(args)
            else:
                return {"error": f"Unknown tool: {tool}"}
        except Exception as e:
            logger.exception(f"Tool execution error: {tool}")
            return {"error": str(e)}

    def _list_files(self, args: dict) -> dict:
        """List files in directory."""
        path = args.get("path", ".")
        pattern = args.get("pattern")  # Glob pattern to filter files
        recursive = args.get("recursive", True)
        max_files = args.get("max_files", 500)

        target = self.project_root / path
        if not target.exists():
            return {"error": f"Path not found: {path}"}

        files = []
        if recursive:
            for root, dirs, filenames in os.walk(target):
                # Filter ignored directories
                dirs[:] = [d for d in dirs if not self._should_ignore(d)]

                for filename in filenames:
                    if self._should_ignore(filename):
                        continue
                    # Apply pattern filter if provided
                    if pattern and not fnmatch.fnmatch(filename, pattern):
                        continue

                    full_path = Path(root) / filename
                    try:
                        rel_path = str(full_path.relative_to(self.project_root))
                        files.append(rel_path)
                    except ValueError:
                        continue

                    if len(files) >= max_files:
                        break

                if len(files) >= max_files:
                    break
        else:
            for item in target.iterdir():
                if item.is_file() and not self._should_ignore(item.name):
                    # Apply pattern filter if provided
                    if pattern and not fnmatch.fnmatch(item.name, pattern):
                        continue
                    try:
                        rel_path = str(item.relative_to(self.project_root))
                        files.append(rel_path)
                    except ValueError:
                        continue

                    if len(files) >= max_files:
                        break

        return {"files": sorted(files), "count": len(files)}

    def _read_file(self, args: dict) -> dict:
        """Read file content."""
        file_path = args.get("file_path", "")
        max_lines = args.get("max_lines", 500)
        start_line = args.get("start_line")
        end_line = args.get("end_line")

        if not file_path:
            return {"error": "file_path required"}

        target = self.project_root / file_path
        if not target.exists():
            return {"error": f"File not found: {file_path}"}

        if not target.is_file():
            return {"error": f"Not a file: {file_path}"}

        # Check file size (max 100KB)
        try:
            size = target.stat().st_size
            if size > 100 * 1024:
                return {"error": f"File too large: {size} bytes"}
        except OSError as e:
            return {"error": str(e)}

        try:
            content = target.read_text(encoding="utf-8", errors="replace")
            lines = content.splitlines()
            total_lines = len(lines)

            # Apply line range if specified
            if start_line is not None or end_line is not None:
                start = (start_line or 1) - 1  # Convert to 0-based
                end = end_line or total_lines
                lines = lines[start:end]

            # Apply max lines limit
            truncated = len(lines) > max_lines
            if truncated:
                lines = lines[:max_lines]

            content = "\n".join(lines)
            if truncated:
                content += "\n... (truncated)"

            return {
                "content": content,
                "lines": len(lines),
                "total_lines": total_lines,
                "path": file_path,
                "truncated": truncated,
            }
        except Exception as e:
            return {"error": str(e)}

    def _search_files(self, args: dict) -> dict:
        """Search for pattern in files."""
        pattern = args.get("pattern", "")
        max_results = args.get("max_results", 100)
        search_path = args.get("path", ".")
        file_pattern = args.get("file_pattern")

        if not pattern:
            return {"error": "pattern required"}

        matches = []

        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error:
            # Treat as literal string
            regex = re.compile(re.escape(pattern), re.IGNORECASE)

        # Resolve search directory
        search_root = self.project_root / search_path
        if not search_root.exists():
            search_root = self.project_root

        for root, dirs, filenames in os.walk(search_root):
            dirs[:] = [d for d in dirs if not self._should_ignore(d)]

            for filename in filenames:
                if self._should_ignore(filename):
                    continue

                # Apply file pattern filter if specified
                if file_pattern and not fnmatch.fnmatch(filename, file_pattern):
                    continue

                full_path = Path(root) / filename

                # Only search text files
                ext = full_path.suffix.lower()
                if ext not in {".py", ".js", ".ts", ".jsx", ".tsx", ".json", ".yaml",
                               ".yml", ".md", ".txt", ".html", ".css", ".scss",
                               ".java", ".kt", ".go", ".rs", ".c", ".cpp", ".h",
                               ".rb", ".php", ".swift", ".sql", ".sh", ".toml"}:
                    continue

                try:
                    content = full_path.read_text(encoding="utf-8", errors="replace")
                    for i, line in enumerate(content.splitlines(), 1):
                        if regex.search(line):
                            try:
                                rel_path = str(full_path.relative_to(self.project_root))
                            except ValueError:
                                continue

                            matches.append({
                                "file": rel_path,
                                "line": i,
                                "content": line.strip()[:200],
                            })

                            if len(matches) >= max_results:
                                return {"matches": matches, "count": len(matches)}
                except Exception:
                    continue

        return {"matches": matches, "count": len(matches)}

    def _get_file_info(self, args: dict) -> dict:
        """Get metadata about a file."""
        file_path = args.get("file_path", "")

        if not file_path:
            return {"error": "file_path required"}

        target = self.project_root / file_path

        if not target.exists():
            return {"exists": False, "file_path": file_path}

        try:
            stat = target.stat()
            return {
                "exists": True,
                "file_path": file_path,
                "size": stat.st_size,
                "modified": stat.st_mtime,
                "is_directory": target.is_dir(),
                "is_file": target.is_file(),
            }
        except Exception as e:
            return {"error": str(e)}

    def _write_file(self, args: dict) -> dict:
        """Write content to a file."""
        file_path = args.get("file_path", "")
        content = args.get("content", "")

        if not file_path:
            return {"error": "file_path required"}

        target = self.project_root / file_path

        try:
            # Create parent directories
            target.parent.mkdir(parents=True, exist_ok=True)

            # Check if creating or updating
            created = not target.exists()

            # Write content
            target.write_text(content, encoding="utf-8")

            lines_written = content.count("\n") + (1 if content and not content.endswith("\n") else 0)

            return {
                "success": True,
                "file_path": file_path,
                "lines_written": lines_written,
                "created": created,
            }
        except Exception as e:
            return {"error": str(e), "success": False}

    def _edit_file(self, args: dict) -> dict:
        """Edit a file by replacing text."""
        file_path = args.get("file_path", "")
        search = args.get("search", "")
        replace = args.get("replace", "")

        if not file_path:
            return {"error": "file_path required"}
        if not search:
            return {"error": "search text required"}

        # Pre-flight validation: Reject no-op edits (search === replace)
        if search.strip() == replace.strip():
            return {
                "error": "STAGNATION ERROR: You attempted to replace text with identical text. "
                         "The file has NOT changed. This indicates a logic loop. "
                         "Please re-read the file to see its CURRENT state, "
                         "or provide your final_answer if the task is complete.",
                "success": False,
                "stagnation": True,
            }

        target = self.project_root / file_path

        if not target.exists():
            return {"error": f"File not found: {file_path}", "success": False}

        try:
            content = target.read_text(encoding="utf-8")

            if search not in content:
                return {
                    "error": f"Search text not found in {file_path}. "
                             "The file may have already been modified. "
                             "Use read_file to see the current content.",
                    "success": False,
                    "hint": "Make sure search text matches exactly including whitespace",
                }

            # Count occurrences and replace
            count = content.count(search)
            new_content = content.replace(search, replace)

            target.write_text(new_content, encoding="utf-8")

            return {
                "success": True,
                "file_path": file_path,
                "replacements": count,
            }
        except Exception as e:
            return {"error": str(e), "success": False}

    def _delete_file(self, args: dict) -> dict:
        """Delete a file."""
        file_path = args.get("file_path", "")

        if not file_path:
            return {"error": "file_path required"}

        target = self.project_root / file_path

        if not target.exists():
            return {"error": f"File not found: {file_path}", "success": False}

        try:
            if target.is_dir():
                import shutil
                shutil.rmtree(target)
            else:
                target.unlink()

            return {"success": True, "file_path": file_path}
        except Exception as e:
            return {"error": str(e), "success": False}

    def _shell(self, args: dict) -> dict:
        """Execute a shell command."""
        command = args.get("command", "")
        cwd = args.get("cwd") or "."
        timeout = args.get("timeout", 60)

        if not command:
            return {"error": "command required"}

        working_dir = self.project_root / cwd

        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=working_dir,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return {
                "exit_code": result.returncode,
                "stdout": result.stdout[:5000] if result.stdout else "",
                "stderr": result.stderr[:2000] if result.stderr else "",
            }
        except subprocess.TimeoutExpired:
            return {"error": f"Command timed out after {timeout}s", "exit_code": -1}
        except Exception as e:
            return {"error": str(e), "exit_code": -1}

    def _should_ignore(self, name: str) -> bool:
        """Check if file/directory should be ignored."""
        for pattern in self.IGNORE_PATTERNS:
            if fnmatch.fnmatch(name, pattern):
                return True
        return False

    # ========================================================================
    # Validation Tools
    # ========================================================================

    def _validate_file(self, args: dict) -> dict:
        """
        Validate that a file exists and contains expected patterns.

        Args:
            path: Path to file to validate
            patterns: List of patterns that should exist in the file
        """
        path = args.get("path", "")
        patterns = args.get("patterns", [])

        if not path:
            return {"error": "path required", "valid": False}

        target = self.project_root / path

        # Check file exists
        if not target.exists():
            return {
                "valid": False,
                "exists": False,
                "path": path,
                "message": f"File not found: {path}",
            }

        if not target.is_file():
            return {
                "valid": False,
                "exists": True,
                "is_file": False,
                "path": path,
                "message": f"Path is not a file: {path}",
            }

        # If no patterns, just confirm existence
        if not patterns:
            return {
                "valid": True,
                "exists": True,
                "path": path,
                "message": f"File exists: {path}",
            }

        # Check for patterns in content
        try:
            content = target.read_text(encoding="utf-8", errors="replace")
            found_patterns = []
            missing_patterns = []

            for pattern in patterns:
                if pattern in content:
                    found_patterns.append(pattern)
                else:
                    missing_patterns.append(pattern)

            valid = len(missing_patterns) == 0

            return {
                "valid": valid,
                "exists": True,
                "path": path,
                "found_patterns": found_patterns,
                "missing_patterns": missing_patterns,
                "message": "All patterns found" if valid else f"Missing patterns: {missing_patterns}",
            }
        except Exception as e:
            return {
                "valid": False,
                "exists": True,
                "path": path,
                "error": str(e),
            }

    def _validate_build(self, args: dict) -> dict:
        """
        Run a build/compile command and check for success.

        Args:
            command: Build command to run (e.g., "npm run build", "cargo build")
            timeout: Command timeout in seconds (default 120)
        """
        command = args.get("command", "")
        timeout = args.get("timeout", 120)

        if not command:
            return {"error": "command required", "valid": False}

        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=self.project_root,
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            success = result.returncode == 0

            return {
                "valid": success,
                "exit_code": result.returncode,
                "command": command,
                "stdout": result.stdout[:3000] if result.stdout else "",
                "stderr": result.stderr[:2000] if result.stderr else "",
                "message": "Build passed" if success else f"Build failed with exit code {result.returncode}",
            }
        except subprocess.TimeoutExpired:
            return {
                "valid": False,
                "exit_code": -1,
                "command": command,
                "message": f"Build timed out after {timeout}s",
            }
        except Exception as e:
            return {
                "valid": False,
                "exit_code": -1,
                "command": command,
                "error": str(e),
            }

    def _validate_structure(self, args: dict) -> dict:
        """
        Validate that expected files exist in the project.

        Args:
            expected_files: List of file paths that should exist
            base_path: Base directory to check from (default ".")
        """
        expected_files = args.get("expected_files", [])
        base_path = args.get("base_path", ".")

        if not expected_files:
            return {"error": "expected_files required", "valid": False}

        base = self.project_root / base_path

        found_files = []
        missing_files = []

        for file_path in expected_files:
            target = base / file_path
            if target.exists():
                found_files.append(file_path)
            else:
                missing_files.append(file_path)

        valid = len(missing_files) == 0

        return {
            "valid": valid,
            "found_files": found_files,
            "missing_files": missing_files,
            "total_expected": len(expected_files),
            "total_found": len(found_files),
            "message": "All expected files found" if valid else f"Missing files: {missing_files}",
        }

    def _lint_check(self, args: dict) -> dict:
        """
        Run a linter to check code quality.

        Args:
            command: Lint command (auto-detected if empty)
            file_path: Specific file to lint (optional)
        """
        command = args.get("command", "")
        file_path = args.get("file_path", "")

        # Auto-detect lint command based on project type
        if not command:
            command = self._detect_lint_command()
            if not command:
                return {
                    "valid": True,
                    "skipped": True,
                    "message": "No linter detected for this project type",
                }

        # Add specific file to command if provided
        if file_path:
            command = f"{command} {file_path}"

        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=self.project_root,
                capture_output=True,
                text=True,
                timeout=60,
            )

            # Most linters return 0 for clean code
            success = result.returncode == 0

            return {
                "valid": success,
                "exit_code": result.returncode,
                "command": command,
                "stdout": result.stdout[:3000] if result.stdout else "",
                "stderr": result.stderr[:2000] if result.stderr else "",
                "message": "Lint passed" if success else "Lint errors found",
            }
        except subprocess.TimeoutExpired:
            return {
                "valid": False,
                "exit_code": -1,
                "command": command,
                "message": "Lint command timed out",
            }
        except Exception as e:
            return {
                "valid": False,
                "exit_code": -1,
                "command": command,
                "error": str(e),
            }

    def _detect_lint_command(self) -> str:
        """Auto-detect the appropriate lint command for the project."""
        # Check for Node.js project
        package_json = self.project_root / "package.json"
        if package_json.exists():
            try:
                import json
                with open(package_json) as f:
                    pkg = json.load(f)
                scripts = pkg.get("scripts", {})
                if "lint" in scripts:
                    return "npm run lint"
                if "eslint" in scripts:
                    return "npm run eslint"
            except Exception:
                pass
            # Check for eslint config
            eslint_files = ["eslint.config.js", ".eslintrc", ".eslintrc.js", ".eslintrc.json"]
            for f in eslint_files:
                if (self.project_root / f).exists():
                    return "npx eslint ."

        # Check for Python project
        pyproject = self.project_root / "pyproject.toml"
        if pyproject.exists():
            # Check for ruff or flake8 in pyproject.toml
            try:
                content = pyproject.read_text()
                if "ruff" in content:
                    return "ruff check ."
                if "flake8" in content:
                    return "flake8 ."
            except Exception:
                pass

        # Check for Rust project
        if (self.project_root / "Cargo.toml").exists():
            return "cargo clippy"

        # Check for Go project
        if (self.project_root / "go.mod").exists():
            return "go vet ./..."

        return ""


class TarangStreamClient:
    """
    SSE + REST callback client for Tarang backend.

    Usage:
        client = TarangStreamClient(
            base_url="https://backend.example.com",
            token="...",
            openrouter_key="...",
            project_root="/path/to/project",
        )

        async for event in client.execute(instruction, context):
            if event.type == EventType.CHANGE:
                change = FileChange.from_dict(event.data)
                # Apply change locally
    """

    DEFAULT_BASE_URL = "https://tarang-backend-intl-web-app-production.up.railway.app"

    def __init__(
        self,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        openrouter_key: Optional[str] = None,
        project_root: Optional[str] = None,
        timeout: float = 300.0,  # 5 minutes for long operations
        on_tool_execute: Optional[Callable[[str, dict], dict]] = None,
        verbose: bool = False,
    ):
        self.base_url = (base_url or self.DEFAULT_BASE_URL).rstrip("/")
        self.token = token
        self.openrouter_key = openrouter_key
        self.project_root = project_root or os.getcwd()
        self.timeout = timeout
        self.verbose = verbose
        self.current_task_id: Optional[str] = None

        # Rich output formatter for consistent display
        self.console = Console()
        self.formatter = OutputFormatter(self.console, verbose=verbose)

        # Session-level approval settings
        self._approve_all = False  # Approve all operations for this session
        self._approved_tools: set = set()  # Approved tool types (e.g., "write_file", "edit_file")

        # Tool executor - can be overridden
        if on_tool_execute:
            self._execute_tool = on_tool_execute
        else:
            self._tool_executor = LocalToolExecutor(self.project_root)
            self._execute_tool = self._tool_executor.execute

    async def execute(
        self,
        instruction: str,
        context: ProjectContext,
        model: Optional[str] = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        """
        Execute instruction with SSE streaming and REST callbacks.

        Args:
            instruction: User instruction
            context: Project context collected locally
            model: Optional model override

        Yields:
            StreamEvent objects
        """
        if not self.token:
            yield StreamEvent(
                type=EventType.ERROR,
                data={"message": "Not authenticated. Run 'tarang login' first."},
            )
            return

        if not self.openrouter_key:
            yield StreamEvent(
                type=EventType.ERROR,
                data={"message": "OpenRouter key not set. Run 'tarang config --openrouter-key KEY'"},
            )
            return

        url = f"{self.base_url}/v2/v3/execute"

        headers = {
            "Authorization": f"Bearer {self.token}",
            "X-OpenRouter-Key": self.openrouter_key,
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
        }

        body = {
            "instruction": instruction,
            "context": context.to_dict(),
        }
        if model:
            body["model"] = model

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                async with client.stream(
                    "POST",
                    url,
                    headers=headers,
                    json=body,
                ) as response:
                    if response.status_code == 401:
                        yield StreamEvent(
                            type=EventType.ERROR,
                            data={"message": "Authentication failed. Run 'tarang login' again."},
                        )
                        return

                    if response.status_code != 200:
                        text = await response.aread()
                        yield StreamEvent(
                            type=EventType.ERROR,
                            data={"message": f"Request failed: {response.status_code} - {text.decode()}"},
                        )
                        return

                    # Get task ID from header
                    self.current_task_id = response.headers.get("X-Task-ID")

                    # Parse SSE stream
                    current_event = None
                    current_data = []

                    async for line in response.aiter_lines():
                        line = line.strip()

                        if not line:
                            # Empty line = end of event
                            if current_event and current_data:
                                data = "\n".join(current_data)
                                event = StreamEvent.from_sse(current_event, data)

                                # Handle tool requests (both legacy and new event names)
                                if event.type in (EventType.TOOL_REQUEST, EventType.TOOL_CALL):
                                    await self._handle_tool_request(client, event.data)
                                else:
                                    yield event

                            current_event = None
                            current_data = []
                            continue

                        if line.startswith("event:"):
                            current_event = line[6:].strip()
                        elif line.startswith("data:"):
                            current_data.append(line[5:].strip())

                    # Handle final event if no trailing newline
                    if current_event and current_data:
                        data = "\n".join(current_data)
                        event = StreamEvent.from_sse(current_event, data)
                        if event.type in (EventType.TOOL_REQUEST, EventType.TOOL_CALL):
                            await self._handle_tool_request(client, event.data)
                        else:
                            yield event

            except httpx.TimeoutException:
                yield StreamEvent(
                    type=EventType.ERROR,
                    data={"message": "Request timed out. Try a simpler instruction."},
                )
            except httpx.ConnectError as e:
                yield StreamEvent(
                    type=EventType.ERROR,
                    data={"message": f"Connection failed: {e}"},
                )
            except Exception as e:
                logger.exception("Stream error")
                yield StreamEvent(
                    type=EventType.ERROR,
                    data={"message": f"Stream error: {e}"},
                )

    async def _handle_tool_request(self, client: httpx.AsyncClient, data: dict) -> None:
        """Execute tool locally and send result via callback."""
        # Support both old (request_id) and new (call_id) formats
        call_id = data.get("call_id") or data.get("request_id", "")
        tool = data.get("tool", "")
        args = data.get("args", {})
        require_approval = data.get("require_approval", False)
        description = data.get("description", "")

        logger.info(f"[LOCAL] Executing tool: {tool} with args: {args} in {self.project_root}")

        # Show tool request with Rich formatting
        self.formatter.show_tool_request(tool, args, require_approval, description)

        if require_approval:
            # Check if already approved for session or tool type
            if self._approve_all or tool in self._approved_tools:
                self.formatter.show_approval_status("auto_approved")
            else:
                # Ask for user approval
                try:
                    response = self.formatter.show_approval_prompt(tool, args)

                    if response == 'v':
                        # Show full content/command
                        self.formatter.show_view_content(tool, args)
                        response = self.formatter.show_approval_prompt(tool, args, "Y/n/a(ll)/t(ool)")

                    if response == 'a':
                        # Approve all for this session
                        self._approve_all = True
                        self.formatter.show_approval_status("approved_all")
                    elif response == 't':
                        # Approve all of this tool type
                        self._approved_tools.add(tool)
                        self.formatter.show_approval_status("approved_tool", tool)
                    elif response == 'n':
                        result = {"skipped": True, "message": "User rejected operation"}
                        self.formatter.show_approval_status("skipped")
                        # Send skipped result
                        callback_url = f"{self.base_url}/v2/v3/callback"
                        callback_body = {
                            "task_id": self.current_task_id,
                            "call_id": call_id,
                            "result": result,
                        }
                        try:
                            await client.post(callback_url, json=callback_body, headers={"Authorization": f"Bearer {self.token}"})
                        except Exception:
                            pass
                        return
                except (EOFError, KeyboardInterrupt):
                    self.formatter.show_approval_status("cancelled")
                    return

        # Execute tool locally
        result = self._execute_tool(tool, args)

        # Show result with Rich formatting
        self.formatter.show_tool_result(tool, args, result)
        logger.info(f"[LOCAL] Tool result: {result.get('success', 'completed')}")

        # Send result via callback
        callback_url = f"{self.base_url}/v2/v3/callback"
        callback_body = {
            "task_id": self.current_task_id,
            "call_id": call_id,
            "result": result,
        }

        logger.info(f"[LOCAL] Sending callback to {callback_url} for task {self.current_task_id}")

        try:
            resp = await client.post(
                callback_url,
                json=callback_body,
                headers={"Authorization": f"Bearer {self.token}"},
            )
            if resp.status_code != 200:
                logger.error(f"Callback failed: {resp.status_code} - {resp.text}")
                self.formatter.show_callback_status(False, f"{resp.status_code}")
            else:
                logger.info(f"[LOCAL] Callback sent successfully")
                self.formatter.show_callback_status(True)
        except Exception as e:
            logger.error(f"Callback error: {e}")
            self.formatter.show_callback_status(False, str(e))

    async def cancel(self) -> bool:
        """Cancel the current task."""
        if not self.current_task_id:
            return False

        url = f"{self.base_url}/v2/v3/cancel/{self.current_task_id}"

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                resp = await client.post(
                    url,
                    headers={"Authorization": f"Bearer {self.token}"},
                )
                return resp.status_code == 200
            except Exception as e:
                logger.error(f"Cancel error: {e}")
                return False


# Backward compatibility alias
async def stream_execute(
    instruction: str,
    context: ProjectContext,
    token: str,
    openrouter_key: str,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
    project_root: Optional[str] = None,
) -> AsyncGenerator[StreamEvent, None]:
    """Convenience function for streaming execution."""
    client = TarangStreamClient(
        base_url=base_url,
        token=token,
        openrouter_key=openrouter_key,
        project_root=project_root,
    )
    async for event in client.execute(instruction, context, model):
        yield event
