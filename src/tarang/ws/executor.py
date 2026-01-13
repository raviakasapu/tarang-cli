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

    def __init__(
        self,
        project_root: str,
        approval_callback: Optional[ApprovalCallback] = None,
    ):
        self.project_root = Path(project_root).resolve()
        self.approval_callback = approval_callback

        # Tool registry
        self._tools: Dict[str, Callable] = {
            "read_file": self._read_file,
            "write_file": self._write_file,
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
            return result
        except TypeError as e:
            return {"error": f"Invalid arguments for {tool}: {e}"}
        except Exception as e:
            logger.exception(f"Tool execution error: {tool}")
            return {"error": str(e)}

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

            return {
                "success": True,
                "file_path": str(path.relative_to(self.project_root)),
                "created": not existed,
                "lines_written": len(content.splitlines()),
                "bytes_written": len(content.encode("utf-8")),
            }

        except Exception as e:
            return {"error": f"Write error: {e}"}

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

            return {
                "success": True,
                "file_path": str(path.relative_to(self.project_root)),
                "replacements": replacements,
                "total_occurrences": count,
            }

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

                rel_path = str(item.relative_to(self.project_root))

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

            # Truncate long output
            max_output = 50000
            stdout_truncated = len(stdout) > max_output
            stderr_truncated = len(stderr) > max_output

            if stdout_truncated:
                stdout = stdout[:max_output] + "\n... (truncated)"
            if stderr_truncated:
                stderr = stderr[:max_output] + "\n... (truncated)"

            return {
                "success": result.returncode == 0,
                "exit_code": result.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "command": command,
                "cwd": str(work_dir.relative_to(self.project_root)),
            }

        except subprocess.TimeoutExpired:
            return {
                "error": f"Command timed out after {cmd_timeout}s",
                "command": command,
                "timeout": True,
            }
        except Exception as e:
            return {
                "error": f"Shell error: {e}",
                "command": command,
            }
