"""
SSE Stream Client with REST Callbacks - Industry-standard pattern.

This implements the SSE + REST callback pattern used by OpenAI, Anthropic, Cursor:
1. CLI sends POST /api/execute with instruction + initial context
2. Backend streams SSE events (status, tool_request, plan, change, etc.)
3. When backend needs a tool result, it sends tool_request and WAITS
4. CLI executes the tool locally
5. CLI sends POST /api/callback with the result
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
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, AsyncGenerator, Callable, Dict, Optional

import httpx
from rich.console import Console

from tarang.context_collector import ProjectContext
from tarang.context.retriever import create_retriever
from tarang.ui.formatter import OutputFormatter

logger = logging.getLogger(__name__)


class EventType(str, Enum):
    """SSE event types from backend."""
    STATUS = "status"
    SESSION_INFO = "session_info"  # Session/job/task IDs for tracking
    TOOL_REQUEST = "tool_request"  # Legacy name
    TOOL_CALL = "tool_call"  # New name (SSE Split Architecture)
    TOOL_DONE = "tool_done"
    THINKING = "thinking"  # Agent thinking/reasoning
    PLAN = "plan"  # Strategic plan from orchestrator (emitted ONCE)
    PHASE_UPDATE = "phase_update"  # Phase status change (no re-render)
    PHASE_SUMMARY = "phase_summary"  # Individual phase summary (display immediately)
    WORKER_UPDATE = "worker_update"  # Worker status change (no re-render)
    PHASE_START = "phase_start"  # Phase beginning (legacy)
    WORKER_START = "worker_start"  # Worker beginning (legacy)
    WORKER_DONE = "worker_done"  # Worker completed (legacy)
    DELEGATION = "delegation"  # Agent delegation
    CHANGE = "change"
    CONTENT = "content"
    ERROR = "error"
    COMPLETE = "complete"
    CANCELLED = "cancelled"
    # Pause/Resume events
    PAUSED = "paused"  # Task paused, waiting for resume
    RESUMED = "resumed"  # Task resumed
    PAUSE_INSTRUCTION = "pause_instruction"  # Instruction injected during pause


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

    # Auto-lint timeout (seconds)
    LINT_TIMEOUT = 30

    # File extension to lint command mapping
    LINT_COMMANDS = {
        ".js": "npx eslint --no-error-on-unmatched-pattern {file}",
        ".jsx": "npx eslint --no-error-on-unmatched-pattern {file}",
        ".ts": "npx eslint --no-error-on-unmatched-pattern {file}",
        ".tsx": "npx eslint --no-error-on-unmatched-pattern {file}",
        ".mjs": "npx eslint --no-error-on-unmatched-pattern {file}",
        ".py": "python -m py_compile {file}",
        ".go": "go vet {file}",
        ".rs": "rustfmt --check {file}",
    }

    # Project type detection
    PROJECT_MARKERS = {
        "package.json": "node",
        "pyproject.toml": "python",
        "requirements.txt": "python",
        "Cargo.toml": "rust",
        "go.mod": "go",
    }

    # =========================================================================
    # Smart Tool Output Handling
    # =========================================================================

    # Tool output profiles: different limits and filtering per tool/command type
    TOOL_OUTPUT_PROFILES = {
        "shell": {
            # Install commands - mostly noise
            "install": {
                "patterns": ["pip install", "npm install", "yarn add", "cargo add", "go get"],
                "success_limit": 500,
                "failure_limit": 2000,
                "noise_patterns": [
                    r"Collecting \S+",
                    r"Downloading \S+",
                    r"Installing collected",
                    r"Successfully installed",
                    r"━+",  # Progress bars
                    r"[\s]*\d+%[\s]*\|",  # Percentage bars
                    r"Using cached",
                    r"Requirement already satisfied",
                    r"added \d+ packages",
                    r"up to date",
                    r"npm WARN",
                ],
                "keep_patterns": ["error", "Error", "ERROR", "failed", "Failed", "FAILED", "warning:", "Warning:"],
            },
            # Run/execute commands - useful output
            "run": {
                "patterns": ["python ", "node ", "go run", "cargo run", "npm start", "npm run dev"],
                "success_limit": 4000,
                "failure_limit": 8000,
                "noise_patterns": [],
                "keep_patterns": [],  # Keep everything
            },
            # Test commands - summary important, verbose less so
            "test": {
                "patterns": ["pytest", "npm test", "cargo test", "go test", "jest", "vitest"],
                "success_limit": 2000,
                "failure_limit": 8000,
                "noise_patterns": [
                    r"^\.+$",  # Lines of dots (pytest progress)
                    r"^PASSED",
                    r"^\s*✓",  # Checkmarks
                ],
                "keep_patterns": ["FAILED", "FAIL", "Error", "error", "AssertionError", "Expected", "Actual"],
            },
            # Build commands
            "build": {
                "patterns": ["npm run build", "cargo build", "go build", "tsc", "webpack", "vite build"],
                "success_limit": 1000,
                "failure_limit": 6000,
                "noise_patterns": [
                    r"Compiling \S+",
                    r"Finished \S+ target",
                ],
                "keep_patterns": ["error", "Error", "ERROR", "warning", "Warning"],
            },
            # Default for other shell commands
            "default": {
                "patterns": [],
                "success_limit": 3000,
                "failure_limit": 6000,
                "noise_patterns": [],
                "keep_patterns": [],
            },
        },
        # File operation profiles
        "read_file": {
            "success_limit": 8000,
            "failure_limit": 500,
        },
        "write_file": {
            "success_limit": 300,  # Just confirmation
            "failure_limit": 1000,
        },
        "edit_file": {
            "success_limit": 300,
            "failure_limit": 1000,
        },
        "list_files": {
            "success_limit": 4000,
            "failure_limit": 500,
        },
        "search_files": {
            "success_limit": 4000,
            "failure_limit": 500,
        },
        "search_code": {
            "success_limit": 6000,
            "failure_limit": 500,
        },
    }

    def __init__(
        self,
        project_root: str,
        is_cancelled: Optional[Callable[[], bool]] = None,
        set_process: Optional[Callable[[subprocess.Popen], None]] = None,
        console: Optional["Console"] = None,
    ):
        self.project_root = Path(project_root).resolve()
        # Optional callbacks for shell interruption
        self._is_cancelled = is_cancelled or (lambda: False)
        self._set_process = set_process or (lambda p: None)
        # Console for live output (shell streaming)
        self._console = console
        # Cache detected project type
        self._project_type: Optional[str] = None

    def execute(self, tool: str, args: dict) -> dict:
        """Execute a tool and return the result."""
        try:
            # Read-only tools
            if tool == "list_files":
                result = self._list_files(args)
            elif tool == "read_file":
                result = self._read_file(args)
            elif tool == "read_files":
                result = self._read_files(args)  # Batch read - more efficient
            elif tool == "search_files":
                result = self._search_files(args)
            elif tool == "search_code":
                result = self._search_code(args)
            elif tool == "get_file_info":
                result = self._get_file_info(args)
            # Write tools (require approval - handled by caller)
            elif tool == "write_file":
                result = self._write_file(args)
            elif tool == "edit_file":
                result = self._edit_file(args)
            elif tool == "delete_file":
                result = self._delete_file(args)
            elif tool == "shell":
                result = self._shell(args)
            # Validation tools
            elif tool == "validate_file":
                result = self._validate_file(args)
            elif tool == "validate_build":
                result = self._validate_build(args)
            elif tool == "validate_structure":
                result = self._validate_structure(args)
            elif tool == "lint_check":
                result = self._lint_check(args)
            else:
                result = {"error": f"Unknown tool: {tool}"}

            # Tag output for all tools (shell already tagged internally)
            if "_output_meta" not in result:
                result = self._tag_tool_output(tool, result, args)
            return result
        except Exception as e:
            logger.exception(f"Tool execution error: {tool}")
            error_result = {"error": str(e)}
            return self._tag_tool_output(tool, error_result, args)

    # =========================================================================
    # Auto-lint helpers
    # =========================================================================

    def _detect_project_type(self) -> Optional[str]:
        """Detect project type based on config files."""
        if self._project_type is not None:
            return self._project_type

        for marker, proj_type in self.PROJECT_MARKERS.items():
            if (self.project_root / marker).exists():
                self._project_type = proj_type
                return proj_type

        return None

    def _get_lint_command(self, file_path: Path) -> Optional[str]:
        """Get appropriate lint command for file type."""
        ext = file_path.suffix.lower()
        cmd_template = self.LINT_COMMANDS.get(ext)

        if not cmd_template:
            return None

        # For node projects, check if eslint config exists
        if ext in (".js", ".jsx", ".ts", ".tsx", ".mjs"):
            project_type = self._detect_project_type()
            if project_type != "node":
                return None
            # Check for eslint config
            eslint_configs = [".eslintrc", ".eslintrc.js", ".eslintrc.json", ".eslintrc.yml", "eslint.config.js"]
            has_eslint = any((self.project_root / cfg).exists() for cfg in eslint_configs)
            if not has_eslint:
                return None

        return cmd_template.format(file=str(file_path))

    def _run_auto_lint(self, file_path: Path) -> Optional[Dict[str, Any]]:
        """Run auto-lint on a file after write/edit."""
        lint_cmd = self._get_lint_command(file_path)
        if not lint_cmd:
            return None

        try:
            result = subprocess.run(
                lint_cmd,
                shell=True,
                cwd=self.project_root,
                capture_output=True,
                timeout=self.LINT_TIMEOUT,
            )

            stdout = result.stdout.decode("utf-8", errors="replace").strip()
            stderr = result.stderr.decode("utf-8", errors="replace").strip()
            output = stdout or stderr

            # Truncate if too long
            if len(output) > 2000:
                output = output[:2000] + "\n... (truncated)"

            return {
                "lint_passed": result.returncode == 0,
                "lint_output": output if output else None,
                "lint_command": lint_cmd.split()[0],
            }

        except subprocess.TimeoutExpired:
            return {
                "lint_passed": False,
                "lint_output": "Lint timed out",
                "lint_command": lint_cmd.split()[0],
            }
        except Exception as e:
            logger.debug(f"Auto-lint failed: {e}")
            return None

    # =========================================================================
    # Smart Output Filtering
    # =========================================================================

    def _detect_shell_command_type(self, command: str) -> str:
        """Detect the type of shell command for smart filtering."""
        cmd_lower = command.lower()
        shell_profiles = self.TOOL_OUTPUT_PROFILES.get("shell", {})

        for cmd_type, profile in shell_profiles.items():
            if cmd_type == "default":
                continue
            patterns = profile.get("patterns", [])
            for pattern in patterns:
                if pattern.lower() in cmd_lower:
                    return cmd_type

        return "default"

    def _filter_shell_output(
        self,
        output: str,
        command: str,
        success: bool,
    ) -> Dict[str, Any]:
        """
        Smart filter shell output based on command type and success/failure.

        Returns dict with filtered output and metadata.
        """
        cmd_type = self._detect_shell_command_type(command)
        shell_profiles = self.TOOL_OUTPUT_PROFILES.get("shell", {})
        profile = shell_profiles.get(cmd_type, shell_profiles.get("default", {}))

        # Get limits based on success/failure
        limit = profile.get("success_limit", 3000) if success else profile.get("failure_limit", 6000)
        noise_patterns = profile.get("noise_patterns", [])
        keep_patterns = profile.get("keep_patterns", [])

        lines = output.splitlines()
        filtered_lines = []

        for line in lines:
            line_stripped = line.strip()
            if not line_stripped:
                continue

            # Check if line matches keep patterns (always keep)
            should_keep = False
            if keep_patterns:
                for pattern in keep_patterns:
                    if re.search(pattern, line, re.IGNORECASE):
                        should_keep = True
                        break

            # Check if line matches noise patterns (filter out)
            is_noise = False
            if noise_patterns and not should_keep:
                for pattern in noise_patterns:
                    if re.search(pattern, line):
                        is_noise = True
                        break

            if should_keep or not is_noise:
                filtered_lines.append(line)

        filtered_output = "\n".join(filtered_lines)

        # Apply length limit
        was_truncated = False
        if len(filtered_output) > limit:
            filtered_output = filtered_output[:limit]
            # Try to break at newline
            last_newline = filtered_output.rfind("\n")
            if last_newline > limit * 0.8:
                filtered_output = filtered_output[:last_newline]
            filtered_output += "\n... (truncated)"
            was_truncated = True

        return {
            "output": filtered_output,
            "command_type": cmd_type,
            "original_lines": len(lines),
            "filtered_lines": len(filtered_lines),
            "truncated": was_truncated,
            "limit_applied": limit,
        }

    def _tag_tool_output(
        self,
        tool: str,
        result: Dict[str, Any],
        args: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Tag tool output with metadata for smart handling downstream.

        Adds:
        - _output_meta: {tool, success, output_type, priority}
        """
        success = result.get("success", True) and "error" not in result

        # Determine output type and priority
        if tool == "shell":
            cmd = args.get("command", "")
            cmd_type = self._detect_shell_command_type(cmd)
            output_type = f"shell_{cmd_type}"
            # Priority: higher = more important to preserve
            # Failures are higher priority than successes
            # Run/test output more important than install
            priority_map = {"run": 80, "test": 70, "build": 60, "install": 30, "default": 50}
            base_priority = priority_map.get(cmd_type, 50)
            priority = base_priority + (20 if not success else 0)
        else:
            output_type = tool
            # File reads are high priority, write confirmations low
            priority_map = {
                "read_file": 80,
                "search_code": 75,
                "search_files": 70,
                "list_files": 60,
                "write_file": 30,
                "edit_file": 30,
            }
            base_priority = priority_map.get(tool, 50)
            priority = base_priority + (20 if not success else 0)

        result["_output_meta"] = {
            "tool": tool,
            "success": success,
            "output_type": output_type,
            "priority": priority,
        }

        return result

    def _list_files(self, args: dict) -> dict:
        """List files in directory."""
        path = args.get("path", ".")
        pattern = args.get("pattern")  # Glob pattern to filter files
        recursive = args.get("recursive", True)
        max_files = args.get("max_files", 500)

        # Handle absolute paths - resolve them directly
        path_obj = Path(path)
        if path_obj.is_absolute():
            target = path_obj.resolve()
        else:
            target = (self.project_root / path).resolve()

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
                    # Try relative to project_root first, then to target directory
                    try:
                        rel_path = str(full_path.relative_to(self.project_root))
                    except ValueError:
                        # Target is outside project_root, use relative to target
                        try:
                            rel_path = str(full_path.relative_to(target))
                        except ValueError:
                            continue
                    files.append(rel_path)

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
                    # Try relative to project_root first, then to target directory
                    try:
                        rel_path = str(item.relative_to(self.project_root))
                    except ValueError:
                        try:
                            rel_path = str(item.relative_to(target))
                        except ValueError:
                            continue
                    files.append(rel_path)

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

    def _read_files(self, args: dict) -> dict:
        """
        Read multiple files in a single batch operation.

        This is more efficient than calling read_file multiple times:
        - Single tool call instead of N calls
        - Reduces AI token overhead
        """
        file_paths = args.get("file_paths", [])

        if not file_paths:
            return {"error": "file_paths required"}

        if len(file_paths) > 10:
            return {"error": "Maximum 10 files per batch", "requested": len(file_paths)}

        results = []
        for file_path in file_paths:
            result = self._read_file({"file_path": file_path})
            results.append({
                "path": file_path,
                "content": result.get("content", ""),
                "lines": result.get("lines", 0),
                "error": result.get("error"),
            })

        # Summary stats
        successful = sum(1 for r in results if not r.get("error"))
        total_lines = sum(r.get("lines", 0) for r in results)

        return {
            "files": results,
            "count": len(results),
            "successful": successful,
            "total_lines": total_lines,
        }

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

    # Track background indexing state
    _indexing_in_progress = False
    _index_result = None

    def _search_code(self, args: dict) -> dict:
        """Search codebase using BM25 + Knowledge Graph + KB Docs retriever."""
        query = args.get("query", "")
        hops = args.get("hops", 1)
        max_chunks = args.get("max_chunks", 10)
        include_kb_docs = args.get("include_kb_docs", True)

        if not query:
            return {"error": "query required"}

        try:
            # Construct the correct index path (.tarang/index/)
            project_path = Path(self.project_root)
            index_path = project_path / ".tarang" / "index"
            retriever = create_retriever(index_path, project_root=project_path)
            if retriever is None:
                # Index not found - start background indexing
                return self._handle_missing_index(query)

            result = retriever.retrieve(
                query=query,
                hops=hops,
                max_chunks=max_chunks,
                include_kb_docs=include_kb_docs,
            )

            # Format chunks for response (result is a RetrievalResult dataclass)
            chunks = []
            for chunk in result.chunks:
                chunks.append({
                    "id": chunk.id,
                    "file": chunk.file,
                    "name": chunk.name,
                    "type": chunk.type,
                    "content": chunk.content[:2000] if chunk.content else "",  # Limit content size
                    "line_start": chunk.line_start,
                    "signature": chunk.signature or "",
                })

            # Format KB docs
            kb_docs = []
            for doc in result.kb_docs:
                kb_docs.append({
                    "id": doc.id,
                    "title": doc.title,
                    "summary": doc.summary,
                    "tags": doc.tags,
                })

            return {
                "success": True,
                "chunks": chunks,
                "signatures": result.signatures,
                "graph": result.graph_context,
                "kb_docs": kb_docs,
                "kb_context": result.kb_context,
                "indexed": True,
                "stats": result.stats,
            }
        except Exception as e:
            logger.exception("search_code error")
            return {"error": f"Search failed: {e}", "indexed": True}

    def _handle_missing_index(self, query: str) -> dict:
        """Handle missing index by building in background."""
        import threading
        from tarang.context import ProjectIndexer

        # Check if indexing already in progress
        if self._indexing_in_progress:
            return {
                "error": "Index is being built in background. Please use search_files or read_file for now.",
                "indexed": False,
                "indexing": True,
            }

        # Start background indexing
        def build_index():
            try:
                LocalToolExecutor._indexing_in_progress = True
                indexer = ProjectIndexer(self.project_root)
                result = indexer.build(force=False)
                LocalToolExecutor._index_result = result
                logger.info(f"Background indexing complete: {result.files_indexed} files, {result.chunks_created} chunks")
            except Exception as e:
                logger.error(f"Background indexing failed: {e}")
                LocalToolExecutor._index_result = {"error": str(e)}
            finally:
                LocalToolExecutor._indexing_in_progress = False

        thread = threading.Thread(target=build_index, daemon=True)
        thread.start()

        return {
            "error": "Index not found. Building index in background... Use search_files or read_file for now, then retry search_code.",
            "indexed": False,
            "indexing": True,
            "hint": f"Alternative: use search_files with pattern matching for '{query[:30]}'",
        }

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

            result = {
                "success": True,
                "file_path": file_path,
                "lines_written": lines_written,
                "created": created,
            }

            # Run auto-lint and merge results
            lint_result = self._run_auto_lint(target)
            if lint_result:
                result.update(lint_result)

            return result
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

            result = {
                "success": True,
                "file_path": file_path,
                "replacements": count,
            }

            # Run auto-lint and merge results
            lint_result = self._run_auto_lint(target)
            if lint_result:
                result.update(lint_result)

            return result
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
        """Execute a shell command with live output streaming and interruptibility."""
        command = args.get("command", "")
        cwd = args.get("cwd") or "."
        timeout = args.get("timeout", 60)
        stream_output = args.get("stream_output", True)  # Enable live streaming by default

        if not command:
            return {"error": "command required"}

        working_dir = self.project_root / cwd

        try:
            # Use Popen for interruptibility with line-buffered output
            process = subprocess.Popen(
                command,
                shell=True,
                cwd=working_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,  # Line buffered
            )

            # Register process for potential cancellation
            self._set_process(process)

            stdout_parts = []
            stderr_parts = []
            start_time = time.time()
            lines_printed = 0
            max_live_lines = 20  # Limit live output to prevent flooding

            # Use select for non-blocking reads (Unix) or polling (cross-platform)
            import select
            import sys

            # Set stdout/stderr to non-blocking if possible
            try:
                import fcntl
                for pipe in [process.stdout, process.stderr]:
                    if pipe:
                        fd = pipe.fileno()
                        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
                        fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
            except (ImportError, AttributeError):
                pass  # Windows doesn't support fcntl

            while True:
                # Check if cancelled
                if self._is_cancelled():
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                    cancel_result = {"error": "Cancelled by user", "exit_code": -1, "cancelled": True, "success": False}
                    return self._tag_tool_output("shell", cancel_result, {"command": command})

                # Check timeout
                elapsed = time.time() - start_time
                if elapsed > timeout:
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                    timeout_result = {"error": f"Command timed out after {timeout}s", "exit_code": -1, "success": False}
                    return self._tag_tool_output("shell", timeout_result, {"command": command})

                # Try to read available output (non-blocking)
                try:
                    # Use select with timeout on Unix
                    if hasattr(select, 'select') and sys.platform != 'win32':
                        readable, _, _ = select.select(
                            [process.stdout, process.stderr], [], [], 0.1
                        )
                        for pipe in readable:
                            line = pipe.readline()
                            if line:
                                if pipe == process.stdout:
                                    stdout_parts.append(line)
                                    # Stream to console if enabled
                                    if stream_output and self._console and lines_printed < max_live_lines:
                                        self._console.print(f"    [dim]{line.rstrip()}[/dim]")
                                        lines_printed += 1
                                else:
                                    stderr_parts.append(line)
                    else:
                        # Polling fallback for Windows
                        time.sleep(0.1)
                except Exception:
                    pass

                # Check if process finished
                retcode = process.poll()
                if retcode is not None:
                    # Process finished - read remaining output
                    remaining_stdout = process.stdout.read() if process.stdout else ""
                    remaining_stderr = process.stderr.read() if process.stderr else ""
                    if remaining_stdout:
                        stdout_parts.append(remaining_stdout)
                    if remaining_stderr:
                        stderr_parts.append(remaining_stderr)
                    break

            # Clear process reference
            self._set_process(None)

            stdout_full = "".join(stdout_parts)
            stderr_full = "".join(stderr_parts)
            success = retcode == 0

            # Show "..." if we truncated live output
            if stream_output and self._console and lines_printed >= max_live_lines:
                total_lines = stdout_full.count('\n')
                if total_lines > max_live_lines:
                    self._console.print(f"    [dim]... ({total_lines - max_live_lines} more lines)[/dim]")

            # Combine stdout and stderr for smart filtering
            combined_output = stdout_full
            if stderr_full:
                combined_output = f"{stdout_full}\n--- stderr ---\n{stderr_full}" if stdout_full else stderr_full

            # Apply smart filtering based on command type
            filter_result = self._filter_shell_output(combined_output, command, success)

            shell_result = {
                "success": success,
                "exit_code": retcode,
                "output": filter_result["output"],
                "command": command,
                "command_type": filter_result["command_type"],
            }

            # Add filtering metadata
            if filter_result["truncated"] or filter_result["original_lines"] != filter_result["filtered_lines"]:
                shell_result["_filter_info"] = {
                    "original_lines": filter_result["original_lines"],
                    "filtered_lines": filter_result["filtered_lines"],
                    "truncated": filter_result["truncated"],
                    "limit": filter_result["limit_applied"],
                }

            # Tag the output for downstream smart handling
            return self._tag_tool_output("shell", shell_result, {"command": command})

        except Exception as e:
            error_result = {"error": str(e), "exit_code": -1, "success": False}
            return self._tag_tool_output("shell", error_result, args)

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
        on_input_start: Optional[Callable[[], None]] = None,
        on_input_end: Optional[Callable[[], None]] = None,
    ):
        self.base_url = (base_url or self.DEFAULT_BASE_URL).rstrip("/")
        self.token = token
        self.openrouter_key = openrouter_key
        self.project_root = project_root or os.getcwd()
        self.timeout = timeout
        self.verbose = verbose
        self.current_task_id: Optional[str] = None

        # Callbacks for pausing keyboard monitor during prompts
        self._on_input_start = on_input_start or (lambda: None)
        self._on_input_end = on_input_end or (lambda: None)

        # Cancellation flag - checked by execute loop
        self._cancelled = False
        # Current shell process - can be interrupted
        self._shell_process: Optional[subprocess.Popen] = None

        # Rich output formatter for consistent display
        self.console = Console()
        self.formatter = OutputFormatter(self.console, verbose=verbose)

        # Tool call tracker for visibility (initialized per-session in execute())
        self._tool_tracker = None

        # Session-level approval settings
        self._approve_all = False  # Approve all operations for this session
        self._approved_tools: set = set()  # Approved tool types (e.g., "write_file", "edit_file")

        # Tool executor - can be overridden
        if on_tool_execute:
            self._execute_tool = on_tool_execute
        else:
            self._tool_executor = LocalToolExecutor(
                self.project_root,
                is_cancelled=lambda: self._cancelled,
                set_process=self._set_shell_process,
                console=self.console,  # Enable live shell output
            )
            self._execute_tool = self._tool_executor.execute

    def _set_shell_process(self, process: Optional[subprocess.Popen]):
        """Track current shell process for potential cancellation."""
        self._shell_process = process

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
        # Reset cancellation flag for new execution
        self._cancelled = False

        # Initialize tool tracker for this session
        self._tool_tracker = self.formatter.init_tool_tracker()

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

        url = f"{self.base_url}/api/execute"

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
                        # Check cancellation flag
                        if self._cancelled:
                            yield StreamEvent(
                                type=EventType.STATUS,
                                data={"message": "Cancelled", "cancelled": True},
                            )
                            return

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

        # Show progress indicator for read-only tools
        if not require_approval:
            # In verbose mode, show numbered tool calls via tracker
            if self.verbose and self._tool_tracker:
                self._tool_tracker.show_progress(tool, args)
            else:
                self.formatter.show_tool_progress(tool, args)

        # Show tool request with Rich formatting (full preview for write operations)
        self.formatter.show_tool_request(tool, args, require_approval, description)

        if require_approval:
            # Check if already approved for session or tool type
            if self._approve_all or tool in self._approved_tools:
                self.formatter.show_approval_status("auto_approved")
            else:
                # Pause keyboard monitor for clean input
                self._on_input_start()
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
                        callback_url = f"{self.base_url}/api/callback"
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
                finally:
                    # Resume keyboard monitor
                    self._on_input_end()

        # Track timing (after approval, measures execution + network round-trip)
        start_time = time.time()

        # Execute tool locally
        result = self._execute_tool(tool, args)

        # Calculate duration
        duration_ms = int((time.time() - start_time) * 1000)

        # Record in tool tracker for visibility
        if self._tool_tracker:
            self._tool_tracker.record_call(tool, args, result, duration_ms)

        # Send result via callback
        callback_url = f"{self.base_url}/api/callback"
        callback_body = {
            "task_id": self.current_task_id,
            "call_id": call_id,
            "result": result,
        }

        logger.info(f"[LOCAL] Sending callback to {callback_url} for task {self.current_task_id}")

        callback_ok = False
        try:
            resp = await client.post(
                callback_url,
                json=callback_body,
                headers={"Authorization": f"Bearer {self.token}"},
            )
            if resp.status_code != 200:
                logger.error(f"Callback failed: {resp.status_code} - {resp.text}")
            else:
                logger.info(f"[LOCAL] Callback sent successfully")
                callback_ok = True
        except Exception as e:
            logger.error(f"Callback error: {e}")

        # Calculate duration (from tool_call received to callback complete)
        duration_s = round(time.time() - start_time, 1)

        # Show result with Rich formatting (include full round-trip timing)
        self.formatter.show_tool_result(tool, args, result, duration_s)
        logger.info(f"[LOCAL] Tool result: {result.get('success', 'completed')} in {duration_s}s")

        if not callback_ok:
            self.formatter.show_callback_status(False, "callback failed")

    async def cancel(self) -> bool:
        """Cancel the current task immediately."""
        # Set cancellation flag first - this breaks the execute loop
        self._cancelled = True

        # Kill any running shell process
        if self._shell_process and self._shell_process.poll() is None:
            try:
                self._shell_process.terminate()
                self._shell_process.wait(timeout=2)
            except Exception:
                try:
                    self._shell_process.kill()
                except Exception:
                    pass
            self._shell_process = None

        # Notify backend
        if not self.current_task_id:
            return True

        url = f"{self.base_url}/api/cancel/{self.current_task_id}"

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                resp = await client.post(
                    url,
                    headers={"Authorization": f"Bearer {self.token}"},
                )
                return resp.status_code == 200
            except Exception as e:
                logger.error(f"Cancel error: {e}")
                return True  # Still return True since we set the flag

    async def pause(self) -> bool:
        """
        Pause the current task.

        The task will pause at the next checkpoint (before next LLM call or tool execution).
        Use resume() to continue, optionally with an instruction to inject.
        """
        if not self.current_task_id:
            return False

        url = f"{self.base_url}/api/pause/{self.current_task_id}"

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                resp = await client.post(
                    url,
                    headers={"Authorization": f"Bearer {self.token}"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("status") in ("paused", "already_paused")
                return False
            except Exception as e:
                logger.error(f"Pause error: {e}")
                return False

    async def resume(self, instruction: Optional[str] = None) -> bool:
        """
        Resume a paused task.

        Args:
            instruction: Optional instruction to inject (e.g., "skip tests", "use React instead")
                        The agent will see this instruction and can adjust its behavior.

        Returns:
            True if resumed successfully
        """
        if not self.current_task_id:
            return False

        url = f"{self.base_url}/api/resume/{self.current_task_id}"
        payload = {}
        if instruction:
            payload["instruction"] = instruction

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                resp = await client.post(
                    url,
                    json=payload if payload else None,
                    headers={"Authorization": f"Bearer {self.token}"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("status") == "resumed"
                return False
            except Exception as e:
                logger.error(f"Resume error: {e}")
                return False

    @property
    def is_paused(self) -> bool:
        """Check if the current task is paused (local state only)."""
        return getattr(self, "_paused", False)


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
