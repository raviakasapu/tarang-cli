"""
Context Collector - Gathers local project context for LLM.

This module scans the project and collects relevant files based on:
1. Project structure (file list)
2. Instruction keywords (relevant files)
3. Recently modified files

The context is sent to the backend with the instruction,
enabling the LLM to make informed decisions without
bidirectional communication.
"""
from __future__ import annotations

import fnmatch
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Set, TYPE_CHECKING

if TYPE_CHECKING:
    from rich.console import Console

# Type alias for progress callback: (phase: str, current: int, total: int) -> None
ProgressCallback = Callable[[str, int, int], None]


@dataclass
class FileContent:
    """A file with its content."""
    path: str
    content: str
    lines: int = 0


@dataclass
class ProjectContext:
    """Context about the project."""
    cwd: str
    files: List[str] = field(default_factory=list)
    relevant_files: List[FileContent] = field(default_factory=list)
    _indexed_context: Optional[dict] = field(default=None, repr=False)
    _folder_tree: Optional[str] = field(default=None, repr=False)

    def to_dict(self) -> dict:
        """Convert to dictionary for API."""
        result = {
            "cwd": self.cwd,
            "files": self.files,
            "relevant_files": [
                {"path": f.path, "content": f.content, "lines": f.lines}
                for f in self.relevant_files
            ],
        }

        # Include folder tree for project structure understanding
        if self._folder_tree:
            result["folder_tree"] = self._folder_tree

        # Include indexed context if available (BM25 + KG retrieval)
        if self._indexed_context:
            result["indexed"] = self._indexed_context

        return result


class ContextCollector:
    """
    Collects project context for LLM processing.

    Usage:
        collector = ContextCollector("/path/to/project")
        context = collector.collect("add authentication")
    """

    # Files/directories to ignore
    IGNORE_PATTERNS = {
        # Version control
        ".git", ".svn", ".hg",
        # Dependencies
        "node_modules", "venv", ".venv", "env", ".env",
        "__pycache__", ".pytest_cache", ".mypy_cache",
        "vendor", "packages",
        # Build outputs
        "dist", "build", ".next", ".nuxt", "out",
        "target", "bin", "obj",
        # IDE
        ".idea", ".vscode", ".vs",
        # Misc
        ".tarang", ".tarang_backups",
        "*.pyc", "*.pyo", "*.so", "*.dylib",
        "*.egg-info", "*.egg",
        ".DS_Store", "Thumbs.db",
    }

    # File extensions to read
    CODE_EXTENSIONS = {
        ".py", ".js", ".ts", ".jsx", ".tsx",
        ".java", ".kt", ".scala",
        ".go", ".rs", ".c", ".cpp", ".h", ".hpp",
        ".rb", ".php", ".swift", ".m",
        ".html", ".css", ".scss", ".sass", ".less",
        ".json", ".yaml", ".yml", ".toml",
        ".md", ".txt", ".rst",
        ".sql", ".sh", ".bash", ".zsh",
        ".vue", ".svelte",
        ".xml", ".gradle",
    }

    # Max file size to read (100KB)
    MAX_FILE_SIZE = 100 * 1024

    # Max files to list
    MAX_FILES = 500

    # Max relevant files to include
    MAX_RELEVANT_FILES = 15

    # Max content per file
    MAX_CONTENT_LINES = 300

    # Config file extensions to auto-include from root (reveals project type)
    CONFIG_EXTENSIONS = {
        ".json", ".toml", ".yaml", ".yml", ".lock",
        ".config.js", ".config.ts",
    }

    # Config filenames (no extension or special names)
    CONFIG_NAMES = {
        "Dockerfile", "Makefile", "Gemfile", "Procfile",
        "requirements.txt", "setup.py", "setup.cfg",
        ".gitignore", ".env.example",
    }

    # Skip these even if they match (too large or not useful)
    SKIP_CONFIG_FILES = {
        "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
        "poetry.lock", "Cargo.lock", "composer.lock",
    }

    def __init__(self, project_root: str, on_progress: Optional[ProgressCallback] = None):
        self.project_root = Path(project_root).resolve()
        self.on_progress = on_progress

    def _emit_progress(self, phase: str, current: int, total: int) -> None:
        """Emit progress update if callback is set."""
        if self.on_progress:
            self.on_progress(phase, current, total)

    def collect(self, instruction: str) -> ProjectContext:
        """
        Collect project context based on instruction.

        Args:
            instruction: User instruction to inform file selection

        Returns:
            ProjectContext with file list and relevant file contents
        """
        # Phase 1: Scan files
        self._emit_progress("Scanning files", 0, 4)
        all_files = self._scan_files()

        # Phase 2: Build folder tree
        self._emit_progress("Building tree", 1, 4)
        folder_tree = self._build_folder_tree(all_files)

        # Phase 3: Collect identity files
        self._emit_progress("Reading configs", 2, 4)
        identity_files = self._collect_identity_files()

        # Phase 4: Find relevant files
        self._emit_progress("Finding relevant", 3, 4)
        relevant_paths = self._find_relevant_files(instruction, all_files)

        # For small projects, include all files if we didn't find specific matches
        if len(all_files) <= 10 and len(relevant_paths) < 3:
            # Small project - read all code files
            relevant_paths = all_files

        # Combine: identity files first, then instruction-relevant files
        # (avoiding duplicates)
        identity_paths = {f.path for f in identity_files}
        additional_files = []
        for i, path in enumerate(relevant_paths):
            if path not in identity_paths:
                content = self._read_file(path)
                if content:
                    additional_files.append(content)
                    if len(identity_files) + len(additional_files) >= self.MAX_RELEVANT_FILES:
                        break

        relevant_files = identity_files + additional_files

        # Done
        self._emit_progress("Complete", 4, 4)

        context = ProjectContext(
            cwd=str(self.project_root),
            files=all_files[:self.MAX_FILES],
            relevant_files=relevant_files,
        )

        # Add folder tree as metadata
        context._folder_tree = folder_tree

        return context

    def _collect_identity_files(self) -> List[FileContent]:
        """
        Collect root-level config files that reveal project type.

        Reads actual files in root directory (not a hardcoded list).
        Skips .md files, lock files, and other non-config files.
        """
        identity_files = []

        # Scan root directory only (not recursive)
        try:
            for item in self.project_root.iterdir():
                if not item.is_file():
                    continue

                filename = item.name

                # Skip ignored files
                if self._should_ignore(filename):
                    continue

                # Skip lock files and other large files
                if filename in self.SKIP_CONFIG_FILES:
                    continue

                # Skip markdown files (user specified: non .md)
                if filename.endswith(".md"):
                    continue

                # Include if it's a config file (by extension or name)
                is_config = (
                    item.suffix.lower() in self.CONFIG_EXTENSIONS
                    or filename in self.CONFIG_NAMES
                    or filename.startswith(".")  # dotfiles like .eslintrc
                )

                if is_config:
                    content = self._read_file(filename)
                    if content:
                        identity_files.append(content)

        except OSError:
            pass

        return identity_files

    def _build_folder_tree(self, files: List[str], max_depth: int = 3) -> str:
        """
        Build a folder structure tree string.

        This helps LLM understand project layout without calling list_files.
        """
        # Build directory structure
        dirs: Set[str] = set()
        root_files: List[str] = []

        for f in files:
            parts = Path(f).parts
            if len(parts) == 1:
                root_files.append(f)
            else:
                # Add all parent directories up to max_depth
                for i in range(1, min(len(parts), max_depth + 1)):
                    dirs.add("/".join(parts[:i]))

        # Build tree string
        lines = ["."]

        # Root files first
        for f in sorted(root_files)[:10]:
            lines.append(f"├── {f}")
        if len(root_files) > 10:
            lines.append(f"├── ... ({len(root_files) - 10} more files)")

        # Then directories
        sorted_dirs = sorted(dirs)
        for d in sorted_dirs[:20]:
            depth = d.count("/")
            indent = "│   " * depth
            name = d.split("/")[-1]
            lines.append(f"{indent}├── {name}/")

        if len(sorted_dirs) > 20:
            lines.append(f"... ({len(sorted_dirs) - 20} more directories)")

        return "\n".join(lines)

    def _scan_files(self) -> List[str]:
        """Scan project for all files."""
        files = []

        for root, dirs, filenames in os.walk(self.project_root):
            # Filter directories
            dirs[:] = [d for d in dirs if not self._should_ignore(d)]

            for filename in filenames:
                if self._should_ignore(filename):
                    continue

                full_path = Path(root) / filename
                try:
                    rel_path = str(full_path.relative_to(self.project_root))
                    files.append(rel_path)
                except ValueError:
                    continue

                if len(files) >= self.MAX_FILES:
                    break

            if len(files) >= self.MAX_FILES:
                break

        return sorted(files)

    def _should_ignore(self, name: str) -> bool:
        """Check if file/directory should be ignored."""
        for pattern in self.IGNORE_PATTERNS:
            if fnmatch.fnmatch(name, pattern):
                return True
        return False

    def _find_relevant_files(
        self,
        instruction: str,
        all_files: List[str],
    ) -> List[str]:
        """Find files relevant to the instruction."""
        relevant: Set[str] = set()

        # Extract keywords from instruction
        keywords = self._extract_keywords(instruction)

        # Score files by relevance
        scored_files = []
        for file_path in all_files:
            score = self._score_file(file_path, keywords, instruction)
            if score > 0:
                scored_files.append((file_path, score))

        # Sort by score and return top files
        scored_files.sort(key=lambda x: x[1], reverse=True)
        return [f[0] for f in scored_files[:self.MAX_RELEVANT_FILES]]

    def _extract_keywords(self, instruction: str) -> List[str]:
        """Extract keywords from instruction."""
        # Remove common words
        stopwords = {
            "the", "a", "an", "is", "are", "was", "were", "be", "been",
            "have", "has", "had", "do", "does", "did", "will", "would",
            "could", "should", "may", "might", "must", "can", "need",
            "to", "of", "in", "for", "on", "with", "at", "by", "from",
            "as", "into", "through", "during", "before", "after",
            "and", "but", "if", "or", "because", "until", "while",
            "this", "that", "these", "those", "i", "me", "my", "we",
            "you", "your", "it", "its", "they", "them", "their",
            "what", "which", "who", "how", "where", "when", "why",
            "add", "create", "make", "build", "implement", "write",
            "fix", "update", "change", "modify", "remove", "delete",
            "please", "help", "want", "like", "using", "use",
        }

        # Split and filter
        words = re.findall(r'\b\w+\b', instruction.lower())
        keywords = [w for w in words if w not in stopwords and len(w) > 2]

        return keywords

    def _score_file(
        self,
        file_path: str,
        keywords: List[str],
        instruction: str,
    ) -> int:
        """Score a file's relevance."""
        score = 0
        file_lower = file_path.lower()
        filename = Path(file_path).name.lower()
        stem = Path(file_path).stem.lower()

        # Check if file is explicitly mentioned
        if filename in instruction.lower() or stem in instruction.lower():
            score += 100

        # Check file path for keywords
        for keyword in keywords:
            if keyword in file_lower:
                score += 10
            if keyword in filename:
                score += 20
            if keyword == stem:
                score += 50

        # Boost common entry points
        entry_points = ["main", "app", "index", "server", "cli", "__init__"]
        if stem in entry_points:
            score += 5

        # Boost by extension relevance
        ext = Path(file_path).suffix.lower()
        if ext in {".py", ".js", ".ts", ".tsx", ".jsx"}:
            score += 2
        if ext in {".json", ".yaml", ".yml", ".toml"}:
            score += 1

        return score

    def _read_file(self, rel_path: str) -> Optional[FileContent]:
        """Read file content."""
        full_path = self.project_root / rel_path

        # Check if readable
        if not full_path.exists() or not full_path.is_file():
            return None

        # Check extension
        if full_path.suffix.lower() not in self.CODE_EXTENSIONS:
            return None

        # Check size
        try:
            size = full_path.stat().st_size
            if size > self.MAX_FILE_SIZE:
                return None
        except OSError:
            return None

        # Read content
        try:
            content = full_path.read_text(encoding="utf-8", errors="replace")
            lines = content.splitlines()

            # Truncate if too long
            if len(lines) > self.MAX_CONTENT_LINES:
                lines = lines[:self.MAX_CONTENT_LINES]
                content = "\n".join(lines) + "\n... (truncated)"

            return FileContent(
                path=rel_path,
                content=content,
                lines=len(lines),
            )

        except Exception:
            return None


def collect_context(
    project_root: str,
    instruction: str,
    on_progress: Optional[ProgressCallback] = None,
) -> ProjectContext:
    """
    Convenience function to collect context.

    Args:
        project_root: Path to project
        instruction: User instruction
        on_progress: Optional callback for progress updates

    Returns:
        ProjectContext
    """
    collector = ContextCollector(project_root, on_progress=on_progress)
    return collector.collect(instruction)


def collect_context_with_progress(
    project_root: str,
    instruction: str,
    console: "Console",
) -> ProjectContext:
    """
    Collect context with Rich progress bar display.

    Args:
        project_root: Path to project
        instruction: User instruction
        console: Rich Console instance

    Returns:
        ProjectContext
    """
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn

    with Progress(
        SpinnerColumn(),
        TextColumn("[cyan]{task.description}[/cyan]"),
        BarColumn(bar_width=20),
        TextColumn("[dim]{task.fields[status]}[/dim]"),
        console=console,
        transient=True,  # Remove progress bar when done
    ) as progress:
        task = progress.add_task("Collecting context", total=4, status="Starting...")

        def on_progress(phase: str, current: int, total: int):
            progress.update(task, completed=current, status=phase)

        collector = ContextCollector(project_root, on_progress=on_progress)
        return collector.collect(instruction)
