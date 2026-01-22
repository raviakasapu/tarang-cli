"""
Tool Executor for Local Tool Execution.

Executes tools requested by the backend agent:
- File operations (read, write, edit, delete)
- Directory operations (list, create)
- Shell commands
- Search operations

All operations are executed locally on the user's machine.
"""
from __future__ import annotations

import asyncio
import fnmatch
import logging
import os
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# Type for approval callback
ApprovalCallback = Callable[[str, str, Dict[str, Any]], bool]


class ToolExecutor:
    """
    Executes tools locally for the hybrid architecture.

    Usage:
        executor = ToolExecutor(project_root="/path/to/project")
        result = await executor.execute("read_file", {"file_path": "src/main.py"})
    """

    # Maximum file size to read (10MB)
    MAX_FILE_SIZE = 10 * 1024 * 1024

    # Maximum lines to return for file reads
    MAX_LINES = 2000

    # Shell command timeout (seconds)
    SHELL_TIMEOUT = 60

    # Auto-lint timeout (seconds)
    LINT_TIMEOUT = 30

    # File extension to lint command mapping
    LINT_COMMANDS = {
        # JavaScript/TypeScript
        ".js": "npx eslint --no-error-on-unmatched-pattern {file}",
        ".jsx": "npx eslint --no-error-on-unmatched-pattern {file}",
        ".ts": "npx eslint --no-error-on-unmatched-pattern {file}",
        ".tsx": "npx eslint --no-error-on-unmatched-pattern {file}",
        ".mjs": "npx eslint --no-error-on-unmatched-pattern {file}",
        # Python
        ".py": "python -m py_compile {file}",
        # Go
        ".go": "go vet {file}",
        # Rust
        ".rs": "rustfmt --check {file}",
    }

    # Project type detection (config file -> project type)
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
        approval_callback: Optional[ApprovalCallback] = None,
    ):
        self.project_root = Path(project_root).resolve()
        self.approval_callback = approval_callback

        # Cache detected project type
        self._project_type: Optional[str] = None

        # Tool registry
        self._tools: Dict[str, Callable] = {
            "read_file": self._read_file,
            "write_file": self._write_file,
            "write_project": self._write_project,  # Multi-file write for greenfield
            "edit_file": self._edit_file,
            "delete_file": self._delete_file,
            "list_files": self._list_files,
            "search_files": self._search_files,
            "search_code": self._search_code,
            "get_file_info": self._get_file_info,
            "create_directory": self._create_directory,
            "shell": self._shell,
        }

    async def execute(
        self,
        tool: str,
        args: Dict[str, Any],
        require_approval: bool = False,
    ) -> Dict[str, Any]:
        """
        Execute a tool with the given arguments.

        Args:
            tool: Tool name
            args: Tool arguments
            require_approval: Whether to ask for user approval

        Returns:
            Tool result dictionary
        """
        if tool not in self._tools:
            return {"error": f"Unknown tool: {tool}"}

        # Check approval if required
        if require_approval and self.approval_callback:
            description = self._get_tool_description(tool, args)
            approved = self.approval_callback(tool, description, args)
            if not approved:
                return {"skipped": True, "message": "User rejected operation"}

        try:
            handler = self._tools[tool]
            result = await handler(**args)
            # Tag output for all tools (shell already tagged internally)
            if "_output_meta" not in result:
                result = self._tag_tool_output(tool, result, args)
            return result
        except TypeError as e:
            error_result = {"error": f"Invalid arguments for {tool}: {e}"}
            return self._tag_tool_output(tool, error_result, args)
        except Exception as e:
            logger.exception(f"Tool execution error: {tool}")
            error_result = {"error": str(e)}
            return self._tag_tool_output(tool, error_result, args)

    def _resolve_path(self, file_path: str) -> Path:
        """Resolve a path relative to project root."""
        path = Path(file_path)

        # If absolute and within project, use as-is
        if path.is_absolute():
            try:
                path.relative_to(self.project_root)
                return path
            except ValueError:
                # Outside project - treat as relative
                path = Path(file_path.lstrip("/"))

        # Resolve relative to project root
        resolved = (self.project_root / path).resolve()

        # Security check: ensure within project root
        try:
            resolved.relative_to(self.project_root)
        except ValueError:
            raise ValueError(f"Path escapes project root: {file_path}")

        return resolved

    def _get_tool_description(self, tool: str, args: Dict[str, Any]) -> str:
        """Get human-readable description of tool operation."""
        if tool == "read_file":
            return f"Read file: {args.get('file_path', '?')}"
        elif tool == "write_file":
            return f"Write file: {args.get('file_path', '?')}"
        elif tool == "edit_file":
            return f"Edit file: {args.get('file_path', '?')}"
        elif tool == "delete_file":
            return f"Delete file: {args.get('file_path', '?')}"
        elif tool == "shell":
            return f"Run command: {args.get('command', '?')}"
        elif tool == "list_files":
            return f"List files: {args.get('path', '.')}"
        elif tool == "search_files":
            return f"Search files: {args.get('pattern', '?')}"
        elif tool == "search_code":
            return f"Search code index: {args.get('query', '?')}"
        else:
            return f"{tool}: {args}"

    # =========================================================================
    # Auto-lint/validation helpers
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
                # Fallback to syntax check only
                return None

        return cmd_template.format(file=str(file_path))

    async def _run_auto_lint(self, file_path: Path) -> Optional[Dict[str, Any]]:
        """
        Run auto-lint on a file after write/edit.

        Returns lint result dict or None if no linter available.
        """
        lint_cmd = self._get_lint_command(file_path)
        if not lint_cmd:
            return None

        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(
                    lint_cmd,
                    shell=True,
                    cwd=self.project_root,
                    capture_output=True,
                    timeout=self.LINT_TIMEOUT,
                ),
            )

            stdout = result.stdout.decode("utf-8", errors="replace").strip()
            stderr = result.stderr.decode("utf-8", errors="replace").strip()

            # Combine output
            output = stdout or stderr

            # Truncate if too long
            if len(output) > 2000:
                output = output[:2000] + "\n... (truncated)"

            return {
                "lint_passed": result.returncode == 0,
                "lint_output": output if output else None,
                "lint_command": lint_cmd.split()[0],  # Just show tool name
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
        import re

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

    # Tool implementations

    async def _read_file(
        self,
        file_path: str,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
        max_lines: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Read file contents."""
        path = self._resolve_path(file_path)

        if not path.exists():
            return {"error": f"File not found: {file_path}"}

        if not path.is_file():
            return {"error": f"Not a file: {file_path}"}

        # Check file size
        size = path.stat().st_size
        if size > self.MAX_FILE_SIZE:
            return {
                "error": f"File too large: {size} bytes (max: {self.MAX_FILE_SIZE})",
                "size": size,
            }

        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            lines = content.splitlines()
            total_lines = len(lines)

            # Apply line range
            if start_line is not None or end_line is not None:
                start = (start_line or 1) - 1  # Convert to 0-based
                end = end_line or total_lines
                lines = lines[start:end]

            # Apply max lines
            max_l = max_lines or self.MAX_LINES
            truncated = len(lines) > max_l
            if truncated:
                lines = lines[:max_l]

            return {
                "content": "\n".join(lines),
                "total_lines": total_lines,
                "lines_returned": len(lines),
                "truncated": truncated,
                "file_path": str(path.relative_to(self.project_root)),
            }

        except UnicodeDecodeError:
            return {"error": f"Cannot read binary file: {file_path}"}
        except Exception as e:
            return {"error": f"Read error: {e}"}

    async def _write_file(
        self,
        file_path: str,
        content: str,
        create_directories: bool = True,
    ) -> Dict[str, Any]:
        """Write content to a file."""
        path = self._resolve_path(file_path)

        # Create parent directories if needed
        if create_directories:
            path.parent.mkdir(parents=True, exist_ok=True)

        try:
            # Check if file exists
            existed = path.exists()
            old_content = path.read_text() if existed else None

            # Write new content
            path.write_text(content, encoding="utf-8")

            result = {
                "success": True,
                "file_path": str(path.relative_to(self.project_root)),
                "created": not existed,
                "lines_written": len(content.splitlines()),
                "bytes_written": len(content.encode("utf-8")),
            }

            # Auto-lint the file
            lint_result = await self._run_auto_lint(path)
            if lint_result:
                result.update(lint_result)

            return result

        except Exception as e:
            return {"error": f"Write error: {e}"}

    async def _write_project(
        self,
        files: list,
        project_description: str = "",
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Write multiple files at once for greenfield project creation.

        Args:
            files: List of {path, content, description} dicts
            project_description: Overall project description

        Returns:
            Summary of files written
        """
        if not files:
            return {"error": "No files provided"}

        results = []
        errors = []
        total_lines = 0
        total_bytes = 0

        for file_info in files:
            if not isinstance(file_info, dict):
                errors.append(f"Invalid file entry: {file_info}")
                continue

            file_path = file_info.get("path", "")
            content = file_info.get("content", "")

            if not file_path:
                errors.append("File entry missing 'path'")
                continue

            path = self._resolve_path(file_path)

            try:
                # Create parent directories
                path.parent.mkdir(parents=True, exist_ok=True)

                # Check if file exists
                existed = path.exists()

                # Write content
                path.write_text(content, encoding="utf-8")

                lines = len(content.splitlines())
                bytes_written = len(content.encode("utf-8"))
                total_lines += lines
                total_bytes += bytes_written

                results.append({
                    "path": str(path.relative_to(self.project_root)),
                    "created": not existed,
                    "lines": lines,
                    "bytes": bytes_written,
                    "description": file_info.get("description", ""),
                })

            except Exception as e:
                errors.append(f"{file_path}: {e}")

        return {
            "success": len(results) > 0,
            "files_written": len(results),
            "files_failed": len(errors),
            "total_lines": total_lines,
            "total_bytes": total_bytes,
            "files": results,
            "errors": errors if errors else None,
            "project_description": project_description,
        }

    async def _edit_file(
        self,
        file_path: str,
        search: str,
        replace: str,
        all_occurrences: bool = False,
    ) -> Dict[str, Any]:
        """Edit file with search/replace."""
        path = self._resolve_path(file_path)

        if not path.exists():
            return {"error": f"File not found: {file_path}"}

        try:
            content = path.read_text(encoding="utf-8")

            # Count occurrences
            count = content.count(search)

            if count == 0:
                return {
                    "error": "Search string not found in file",
                    "file_path": str(path.relative_to(self.project_root)),
                }

            # Perform replacement
            if all_occurrences:
                new_content = content.replace(search, replace)
                replacements = count
            else:
                new_content = content.replace(search, replace, 1)
                replacements = 1

            path.write_text(new_content, encoding="utf-8")

            result = {
                "success": True,
                "file_path": str(path.relative_to(self.project_root)),
                "replacements": replacements,
                "total_occurrences": count,
            }

            # Auto-lint the file
            lint_result = await self._run_auto_lint(path)
            if lint_result:
                result.update(lint_result)

            return result

        except Exception as e:
            return {"error": f"Edit error: {e}"}

    async def _delete_file(self, file_path: str) -> Dict[str, Any]:
        """Delete a file."""
        path = self._resolve_path(file_path)

        if not path.exists():
            return {"error": f"File not found: {file_path}"}

        try:
            if path.is_file():
                path.unlink()
            else:
                return {"error": f"Not a file: {file_path}"}

            return {
                "success": True,
                "file_path": str(path.relative_to(self.project_root)),
                "deleted": True,
            }

        except Exception as e:
            return {"error": f"Delete error: {e}"}

    async def _list_files(
        self,
        path: str = ".",
        pattern: Optional[str] = None,
        recursive: bool = True,
        include_hidden: bool = False,
        max_files: int = 500,
    ) -> Dict[str, Any]:
        """List files in a directory."""
        # Handle absolute paths directly
        path_obj = Path(path)
        if path_obj.is_absolute():
            dir_path = path_obj.resolve()
        else:
            dir_path = self._resolve_path(path)

        if not dir_path.exists():
            return {"error": f"Directory not found: {path}"}

        if not dir_path.is_dir():
            return {"error": f"Not a directory: {path}"}

        try:
            files = []
            dirs = []

            if recursive:
                items = dir_path.rglob("*")
            else:
                items = dir_path.iterdir()

            for item in items:
                # Skip hidden files unless requested
                if not include_hidden and item.name.startswith("."):
                    continue

                # Apply pattern filter
                if pattern and not fnmatch.fnmatch(item.name, pattern):
                    continue

                # Try relative to project_root first, then to dir_path
                try:
                    rel_path = str(item.relative_to(self.project_root))
                except ValueError:
                    # Path is outside project_root, use relative to dir_path
                    try:
                        rel_path = str(item.relative_to(dir_path))
                    except ValueError:
                        continue

                if item.is_file():
                    files.append(rel_path)
                elif item.is_dir():
                    dirs.append(rel_path)

                # Limit results
                if len(files) + len(dirs) >= max_files:
                    break

            return {
                "files": sorted(files)[:max_files],
                "directories": sorted(dirs)[:50],
                "total_files": len(files),
                "total_directories": len(dirs),
                "truncated": len(files) >= max_files,
            }

        except Exception as e:
            return {"error": f"List error: {e}"}

    async def _search_files(
        self,
        pattern: str,
        path: str = ".",
        file_pattern: Optional[str] = None,
        max_results: int = 100,
        context_lines: int = 2,
    ) -> Dict[str, Any]:
        """Search for pattern in files."""
        import re

        dir_path = self._resolve_path(path)

        if not dir_path.exists():
            return {"error": f"Directory not found: {path}"}

        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error as e:
            return {"error": f"Invalid regex pattern: {e}"}

        matches = []
        files_searched = 0

        try:
            for file_path in dir_path.rglob("*"):
                if not file_path.is_file():
                    continue

                # Skip hidden and binary
                if file_path.name.startswith("."):
                    continue

                # Apply file pattern filter
                if file_pattern and not fnmatch.fnmatch(file_path.name, file_pattern):
                    continue

                # Skip large files
                if file_path.stat().st_size > 1024 * 1024:  # 1MB
                    continue

                files_searched += 1

                try:
                    content = file_path.read_text(encoding="utf-8", errors="ignore")
                    lines = content.splitlines()

                    for i, line in enumerate(lines):
                        if regex.search(line):
                            # Get context
                            start = max(0, i - context_lines)
                            end = min(len(lines), i + context_lines + 1)
                            context = lines[start:end]

                            matches.append({
                                "file": str(file_path.relative_to(self.project_root)),
                                "line": i + 1,
                                "content": line.strip(),
                                "context": context,
                            })

                            if len(matches) >= max_results:
                                break

                except (UnicodeDecodeError, PermissionError):
                    continue

                if len(matches) >= max_results:
                    break

            return {
                "matches": matches,
                "total_matches": len(matches),
                "files_searched": files_searched,
                "truncated": len(matches) >= max_results,
            }

        except Exception as e:
            return {"error": f"Search error: {e}"}

    async def _search_code(
        self,
        query: str,
        hops: int = 1,
        max_chunks: int = 10,
    ) -> Dict[str, Any]:
        """
        Search code using BM25 + Knowledge Graph.

        Uses the project's index created via /index command.
        Returns relevant code chunks with their relationships.
        """
        try:
            from tarang.context import get_retriever
        except ImportError:
            return {
                "error": "Context retrieval module not available. Run 'pip install tarang' to install.",
                "indexed": False,
            }

        # Get retriever for this project
        retriever = get_retriever(self.project_root)

        if retriever is None or not retriever.is_ready:
            return {
                "error": "Project not indexed. Run '/index' command first to build the code index.",
                "indexed": False,
                "hint": "The /index command creates a searchable index of your codebase using BM25 and a Symbol Knowledge Graph.",
            }

        try:
            # Execute search
            result = retriever.retrieve(
                query=query,
                hops=min(hops, 2),
                max_chunks=min(max_chunks, 20),
            )

            # Format response
            return {
                "success": True,
                "indexed": True,
                "query": query,
                "chunks": [
                    {
                        "id": c.id,
                        "file": c.file,
                        "type": c.type,
                        "name": c.name,
                        "signature": c.signature,
                        "content": c.content,
                        "line_start": c.line_start,
                        "line_end": c.line_end,
                    }
                    for c in result.chunks
                ],
                "signatures": result.signatures,
                "graph": result.graph_context,
                "stats": result.stats,
            }

        except Exception as e:
            logger.exception("search_code error")
            return {
                "error": f"Search failed: {e}",
                "indexed": True,
            }

    async def _get_file_info(self, file_path: str) -> Dict[str, Any]:
        """Get file metadata."""
        path = self._resolve_path(file_path)

        if not path.exists():
            return {"error": f"File not found: {file_path}"}

        try:
            stat = path.stat()

            return {
                "file_path": str(path.relative_to(self.project_root)),
                "exists": True,
                "is_file": path.is_file(),
                "is_directory": path.is_dir(),
                "size": stat.st_size,
                "modified": stat.st_mtime,
                "created": stat.st_ctime,
            }

        except Exception as e:
            return {"error": f"Info error: {e}"}

    async def _create_directory(
        self,
        path: str,
        parents: bool = True,
    ) -> Dict[str, Any]:
        """Create a directory."""
        dir_path = self._resolve_path(path)

        try:
            dir_path.mkdir(parents=parents, exist_ok=True)

            return {
                "success": True,
                "path": str(dir_path.relative_to(self.project_root)),
                "created": True,
            }

        except Exception as e:
            return {"error": f"Create directory error: {e}"}

    async def _shell(
        self,
        command: str,
        cwd: Optional[str] = None,
        timeout: Optional[int] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Execute a shell command."""
        # Resolve working directory
        if cwd:
            work_dir = self._resolve_path(cwd)
        else:
            work_dir = self.project_root

        if not work_dir.exists():
            return {"error": f"Working directory not found: {cwd}"}

        # Set timeout
        cmd_timeout = timeout or self.SHELL_TIMEOUT

        # Prepare environment
        cmd_env = os.environ.copy()
        if env:
            cmd_env.update(env)

        try:
            # Run command
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(
                    command,
                    shell=True,
                    cwd=work_dir,
                    capture_output=True,
                    timeout=cmd_timeout,
                    env=cmd_env,
                ),
            )

            stdout = result.stdout.decode("utf-8", errors="replace")
            stderr = result.stderr.decode("utf-8", errors="replace")
            success = result.returncode == 0

            # Combine stdout and stderr for smart filtering
            combined_output = stdout
            if stderr:
                combined_output = f"{stdout}\n--- stderr ---\n{stderr}" if stdout else stderr

            # Apply smart filtering based on command type
            filter_result = self._filter_shell_output(combined_output, command, success)

            shell_result = {
                "success": success,
                "exit_code": result.returncode,
                "output": filter_result["output"],
                "command": command,
                "command_type": filter_result["command_type"],
                "cwd": str(work_dir.relative_to(self.project_root)),
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

        except subprocess.TimeoutExpired:
            error_result = {
                "error": f"Command timed out after {cmd_timeout}s",
                "command": command,
                "timeout": True,
                "success": False,
            }
            return self._tag_tool_output("shell", error_result, {"command": command})
        except Exception as e:
            error_result = {
                "error": f"Shell error: {e}",
                "command": command,
                "success": False,
            }
            return self._tag_tool_output("shell", error_result, {"command": command})
