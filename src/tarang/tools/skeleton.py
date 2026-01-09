"""Project skeleton generator for context extraction."""

import subprocess
from pathlib import Path
from typing import Any

# Default patterns to ignore
IGNORE_PATTERNS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".next",
    "coverage",
    ".coverage",
    "*.pyc",
    "*.pyo",
    ".DS_Store",
    "*.egg-info",
}

# File extensions to include for symbol extraction
CODE_EXTENSIONS = {
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".rs",
    ".go",
    ".java",
    ".rb",
    ".php",
    ".c",
    ".cpp",
    ".h",
    ".hpp",
}


class SkeletonGenerator:
    """Generate a project skeleton for LLM context."""

    def __init__(self, root: Path):
        """Initialize the skeleton generator.

        Args:
            root: Project root directory
        """
        self.root = root
        self._gitignore_patterns: set[str] = set()
        self._load_gitignore()

    def _load_gitignore(self):
        """Load patterns from .gitignore if present."""
        gitignore = self.root / ".gitignore"
        if gitignore.exists():
            with open(gitignore) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        self._gitignore_patterns.add(line.rstrip("/"))

    def _should_ignore(self, path: Path) -> bool:
        """Check if a path should be ignored."""
        name = path.name

        # Check default patterns
        if name in IGNORE_PATTERNS:
            return True

        # Check gitignore patterns
        for pattern in self._gitignore_patterns:
            if name == pattern or path.match(pattern):
                return True

        return False

    async def generate(self) -> dict[str, Any]:
        """Generate the project skeleton.

        Returns:
            Dictionary containing file tree and symbols
        """
        skeleton = {
            "root": str(self.root),
            "tree": self._build_tree(self.root),
            "symbols": await self._extract_symbols(),
        }
        return skeleton

    def _build_tree(self, path: Path, prefix: str = "") -> list[dict[str, Any]]:
        """Build a file tree structure.

        Args:
            path: Current directory path
            prefix: Prefix for nested items

        Returns:
            List of file/directory entries
        """
        entries = []

        try:
            items = sorted(path.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
        except PermissionError:
            return entries

        for item in items:
            if self._should_ignore(item):
                continue

            entry = {
                "name": item.name,
                "type": "directory" if item.is_dir() else "file",
            }

            if item.is_dir():
                children = self._build_tree(item)
                if children:  # Only include non-empty directories
                    entry["children"] = children
                    entries.append(entry)
            else:
                # Add file size for reference
                try:
                    entry["size"] = item.stat().st_size
                except OSError:
                    entry["size"] = 0
                entries.append(entry)

        return entries

    async def _extract_symbols(self) -> dict[str, list[str]]:
        """Extract symbols (functions, classes) from code files.

        Returns:
            Dictionary mapping file paths to symbol lists
        """
        symbols: dict[str, list[str]] = {}

        # Try ctags first (faster and more reliable)
        if self._has_ctags():
            symbols = await self._extract_with_ctags()
        else:
            # Fallback to basic regex extraction
            symbols = await self._extract_with_regex()

        return symbols

    def _has_ctags(self) -> bool:
        """Check if ctags is available."""
        try:
            result = subprocess.run(
                ["ctags", "--version"],
                capture_output=True,
                timeout=5,
            )
            return result.returncode == 0
        except (subprocess.SubprocessError, FileNotFoundError):
            return False

    async def _extract_with_ctags(self) -> dict[str, list[str]]:
        """Extract symbols using ctags."""
        symbols: dict[str, list[str]] = {}

        try:
            result = subprocess.run(
                [
                    "ctags",
                    "-R",
                    "--output-format=json",
                    "--fields=+n",
                    "--exclude=.git",
                    "--exclude=node_modules",
                    "--exclude=__pycache__",
                    "--exclude=.venv",
                    str(self.root),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode == 0:
                import json
                for line in result.stdout.strip().split("\n"):
                    if line:
                        try:
                            tag = json.loads(line)
                            path = tag.get("path", "")
                            name = tag.get("name", "")
                            kind = tag.get("kind", "")

                            if path and name:
                                rel_path = str(Path(path).relative_to(self.root))
                                if rel_path not in symbols:
                                    symbols[rel_path] = []

                                symbol_str = f"{kind}: {name}" if kind else name
                                symbols[rel_path].append(symbol_str)
                        except json.JSONDecodeError:
                            continue

        except (subprocess.SubprocessError, subprocess.TimeoutExpired):
            pass

        return symbols

    async def _extract_with_regex(self) -> dict[str, list[str]]:
        """Extract symbols using basic regex patterns."""
        import re

        symbols: dict[str, list[str]] = {}

        patterns = {
            ".py": [
                (r"^class\s+(\w+)", "class"),
                (r"^def\s+(\w+)", "function"),
                (r"^async\s+def\s+(\w+)", "async function"),
            ],
            ".js": [
                (r"^class\s+(\w+)", "class"),
                (r"^function\s+(\w+)", "function"),
                (r"^const\s+(\w+)\s*=\s*(?:async\s+)?\(", "function"),
                (r"^export\s+(?:default\s+)?(?:class|function)\s+(\w+)", "export"),
            ],
            ".ts": [
                (r"^class\s+(\w+)", "class"),
                (r"^interface\s+(\w+)", "interface"),
                (r"^type\s+(\w+)", "type"),
                (r"^function\s+(\w+)", "function"),
                (r"^export\s+(?:default\s+)?(?:class|function|interface|type)\s+(\w+)", "export"),
            ],
        }

        for file_path in self.root.rglob("*"):
            if self._should_ignore(file_path):
                continue

            if not file_path.is_file():
                continue

            ext = file_path.suffix
            if ext not in patterns:
                continue

            try:
                content = file_path.read_text(errors="ignore")
                rel_path = str(file_path.relative_to(self.root))
                file_symbols = []

                for pattern, kind in patterns[ext]:
                    for match in re.finditer(pattern, content, re.MULTILINE):
                        name = match.group(1)
                        file_symbols.append(f"{kind}: {name}")

                if file_symbols:
                    symbols[rel_path] = file_symbols

            except (OSError, UnicodeDecodeError):
                continue

        return symbols
