"""
File operation tools for Tarang agents.

All tools avoid using 'success', 'complete', 'done', 'finished' in return values
to prevent false completion detection.
"""
from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field
from agent_framework.base import BaseTool


# ============================================================================
# Pydantic Models for Tool Arguments and Outputs
# ============================================================================

class ListFilesArgs(BaseModel):
    """Arguments for list_files tool."""
    path: str = Field(default=".", description="Directory to list (relative to project)")
    pattern: str = Field(default="*", description="Glob pattern to filter files")
    recursive: bool = Field(default=False, description="Whether to list recursively")
    max_results: int = Field(default=100, description="Maximum number of results")


class ListFilesOutput(BaseModel):
    """Output from list_files tool."""
    files: List[str]
    count: int
    path: str
    truncated: bool


class ReadFileArgs(BaseModel):
    """Arguments for read_file tool."""
    file_path: str = Field(..., description="Path to file (relative to project)")
    encoding: str = Field(default="utf-8", description="File encoding")
    max_lines: int = Field(default=500, description="Maximum lines to read (use start_line/end_line for large files)")
    start_line: int = Field(default=1, description="Line number to start reading from (1-indexed)")
    end_line: Optional[int] = Field(default=None, description="Line number to stop reading at (inclusive). If None, reads max_lines from start_line.")


class ReadFileOutput(BaseModel):
    """Output from read_file tool."""
    content: str
    lines_read: int
    total_lines: int
    file_path: str
    truncated: bool
    start_line: int = 1
    end_line: int = 0
    hint: Optional[str] = None


class WriteFileArgs(BaseModel):
    """Arguments for write_file tool."""
    file_path: str = Field(..., description="Path to file (relative to project)")
    content: str = Field(..., description="Content to write")
    encoding: str = Field(default="utf-8", description="File encoding")


class WriteFileOutput(BaseModel):
    """Output from write_file tool."""
    file_path: str
    lines_written: int
    operation: str
    status: str


class EditFileArgs(BaseModel):
    """Arguments for edit_file tool."""
    file_path: str = Field(..., description="Path to file (relative to project)")
    old_text: str = Field(..., description="Text to find and replace")
    new_text: str = Field(..., description="Replacement text")
    encoding: str = Field(default="utf-8", description="File encoding")


class EditFileOutput(BaseModel):
    """Output from edit_file tool."""
    file_path: str
    replacements: int
    operation: str
    status: str


class SearchFilesArgs(BaseModel):
    """Arguments for search_files tool."""
    pattern: str = Field(..., description="Regex pattern to search for")
    path: str = Field(default=".", description="Directory to search in")
    file_pattern: str = Field(default="*", description="Glob pattern for files to search")
    max_results: int = Field(default=50, description="Maximum matches to return")
    case_sensitive: bool = Field(default=True, description="Whether search is case sensitive")


class SearchMatch(BaseModel):
    """A single search match."""
    file: str
    line: int
    content: str


class SearchFilesOutput(BaseModel):
    """Output from search_files tool."""
    matches: List[SearchMatch]
    count: int
    files_searched: int
    pattern: str
    truncated: bool


# ============================================================================
# Tool Implementations
# ============================================================================

class ListFilesTool(BaseTool):
    """List files and directories in a path."""

    _name = "list_files"
    _description = "List files and directories in a path. Use pattern for filtering (e.g., '*.py')."

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
        return ListFilesArgs

    @property
    def output_schema(self):
        return ListFilesOutput

    def execute(
        self,
        path: str = ".",
        pattern: str = "*",
        recursive: bool = False,
        max_results: int = 100,
    ) -> Dict[str, Any]:
        """List files in directory."""
        target = self.project_dir / path
        if not target.exists():
            return {"error": f"Directory not found: {path}", "files": [], "count": 0}

        if not target.is_dir():
            return {"error": f"Not a directory: {path}", "files": [], "count": 0}

        files = []
        try:
            if recursive:
                for root, dirs, filenames in os.walk(target):
                    # Skip hidden directories
                    dirs[:] = [d for d in dirs if not d.startswith('.')]
                    rel_root = Path(root).relative_to(self.project_dir)
                    for f in filenames:
                        if fnmatch.fnmatch(f, pattern):
                            files.append(str(rel_root / f))
                            if len(files) >= max_results:
                                break
                    if len(files) >= max_results:
                        break
            else:
                for item in target.iterdir():
                    if item.name.startswith('.'):
                        continue
                    if fnmatch.fnmatch(item.name, pattern):
                        rel_path = item.relative_to(self.project_dir)
                        suffix = "/" if item.is_dir() else ""
                        files.append(str(rel_path) + suffix)
                        if len(files) >= max_results:
                            break

            files.sort()
            return {
                "files": files,
                "count": len(files),
                "path": str(path),
                "truncated": len(files) >= max_results,
            }
        except PermissionError:
            return {"error": f"Permission denied: {path}", "files": [], "count": 0}


class ReadFileTool(BaseTool):
    """Read file contents."""

    _name = "read_file"
    _description = "Read the contents of a file. Returns content and line count."

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
        return ReadFileArgs

    @property
    def output_schema(self):
        return ReadFileOutput

    def execute(
        self,
        file_path: str,
        encoding: str = "utf-8",
        max_lines: int = 500,
        start_line: int = 1,
        end_line: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Read file contents with support for chunked reading.

        Args:
            file_path: Path to file
            encoding: File encoding
            max_lines: Max lines to read (default 500)
            start_line: Starting line number (1-indexed)
            end_line: Ending line number (inclusive). If None, reads max_lines from start.
        """
        target = self.project_dir / file_path
        if not target.exists():
            return {"error": f"File not found: {file_path}", "content": "", "lines_read": 0, "total_lines": 0}

        if not target.is_file():
            return {"error": f"Not a file: {file_path}", "content": "", "lines_read": 0, "total_lines": 0}

        try:
            with open(target, "r", encoding=encoding, errors="replace") as f:
                all_lines = f.readlines()

            total_lines = len(all_lines)

            # Calculate actual range
            start_idx = max(0, start_line - 1)  # Convert to 0-indexed

            if end_line is not None:
                end_idx = min(total_lines, end_line)
            else:
                end_idx = min(total_lines, start_idx + max_lines)

            # Extract requested lines
            selected_lines = all_lines[start_idx:end_idx]
            lines_read = len(selected_lines)

            # Check if truncated (more lines available after end_idx)
            truncated = end_idx < total_lines

            content = "".join(selected_lines)

            # Build helpful hint for large files
            hint = None
            if truncated:
                remaining = total_lines - end_idx
                hint = f"File has {remaining} more lines. Use start_line={end_idx + 1} to continue reading."
            elif start_idx > 0:
                hint = f"Reading lines {start_line}-{end_idx} of {total_lines} total."

            return {
                "content": content,
                "lines_read": lines_read,
                "total_lines": total_lines,
                "file_path": str(file_path),
                "truncated": truncated,
                "start_line": start_line,
                "end_line": end_idx,
                "hint": hint,
            }
        except Exception as e:
            return {"error": f"Read error: {e}", "content": "", "lines_read": 0, "total_lines": 0}


class WriteFileTool(BaseTool):
    """Write content to a file."""

    _name = "write_file"
    _description = "Write content to a file. Creates parent directories automatically."

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
        return WriteFileArgs

    @property
    def output_schema(self):
        return WriteFileOutput

    def execute(
        self,
        file_path: str,
        content: str,
        encoding: str = "utf-8",
    ) -> Dict[str, Any]:
        """Write content to file."""
        target = self.project_dir / file_path

        try:
            # Create parent directories
            target.parent.mkdir(parents=True, exist_ok=True)

            # Write content
            with open(target, "w", encoding=encoding) as f:
                f.write(content)

            lines_written = content.count("\n") + (1 if content and not content.endswith("\n") else 0)

            return {
                "file_path": str(file_path),
                "lines_written": lines_written,
                "operation": "write_file",
                "status": "written",
            }
        except Exception as e:
            return {"error": f"Write error: {e}", "file_path": str(file_path)}


class EditFileTool(BaseTool):
    """Edit a file by replacing text."""

    _name = "edit_file"
    _description = "Edit a file by replacing old_text with new_text. Use for precise modifications."

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
        return EditFileArgs

    @property
    def output_schema(self):
        return EditFileOutput

    def execute(
        self,
        file_path: str,
        old_text: str,
        new_text: str,
        encoding: str = "utf-8",
    ) -> Dict[str, Any]:
        """Edit file by text replacement."""
        target = self.project_dir / file_path

        if not target.exists():
            return {"error": f"File not found: {file_path}", "replacements": 0}

        try:
            with open(target, "r", encoding=encoding, errors="replace") as f:
                content = f.read()

            if old_text not in content:
                return {
                    "error": f"Text not found in file: {file_path}",
                    "replacements": 0,
                    "hint": "Make sure old_text matches exactly including whitespace",
                }

            # Count occurrences
            count = content.count(old_text)

            # Replace
            new_content = content.replace(old_text, new_text)

            with open(target, "w", encoding=encoding) as f:
                f.write(new_content)

            return {
                "file_path": str(file_path),
                "replacements": count,
                "old_text": old_text[:200] + "..." if len(old_text) > 200 else old_text,
                "new_text": new_text[:200] + "..." if len(new_text) > 200 else new_text,
                "operation": "edit_file",
                "status": "edited",
            }
        except Exception as e:
            return {"error": f"Edit error: {e}", "file_path": str(file_path)}


class SearchFilesTool(BaseTool):
    """Search for patterns in files."""

    _name = "search_files"
    _description = "Search for a regex pattern in files. Like grep."

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
        return SearchFilesArgs

    @property
    def output_schema(self):
        return SearchFilesOutput

    def execute(
        self,
        pattern: str,
        path: str = ".",
        file_pattern: str = "*",
        max_results: int = 50,
        case_sensitive: bool = True,
    ) -> Dict[str, Any]:
        """Search for pattern in files."""
        target = self.project_dir / path
        if not target.exists():
            return {"error": f"Path not found: {path}", "matches": [], "count": 0}

        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            regex = re.compile(pattern, flags)
        except re.error as e:
            return {"error": f"Invalid regex: {e}", "matches": [], "count": 0}

        matches = []
        files_searched = 0

        for root, dirs, files in os.walk(target):
            # Skip hidden directories
            dirs[:] = [d for d in dirs if not d.startswith('.')]

            for filename in files:
                if not fnmatch.fnmatch(filename, file_pattern):
                    continue
                if filename.startswith('.'):
                    continue

                file_path = Path(root) / filename
                files_searched += 1

                try:
                    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                        for line_num, line in enumerate(f, 1):
                            if regex.search(line):
                                rel_path = file_path.relative_to(self.project_dir)
                                matches.append({
                                    "file": str(rel_path),
                                    "line": line_num,
                                    "content": line.rstrip()[:200],
                                })
                                if len(matches) >= max_results:
                                    break
                except Exception:
                    continue

                if len(matches) >= max_results:
                    break
            if len(matches) >= max_results:
                break

        return {
            "matches": matches,
            "count": len(matches),
            "files_searched": files_searched,
            "pattern": pattern,
            "truncated": len(matches) >= max_results,
        }
