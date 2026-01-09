"""
Project Skeleton Generator - Lightweight project context for backend.

Generates file tree and symbol information to send to the Orchestrator.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class SymbolDefinition:
    """A symbol (function, class, method) in the project."""
    name: str
    kind: str  # function, class, method, variable
    file: str
    line: int
    signature: Optional[str] = None


@dataclass
class ProjectSkeleton:
    """Lightweight project map for context."""
    file_tree: str
    symbols: List[SymbolDefinition] = field(default_factory=list)
    dependencies: Dict[str, List[str]] = field(default_factory=dict)
    total_files: int = 0
    total_lines: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API."""
        return {
            "file_tree": self.file_tree,
            "symbols": [
                {
                    "name": s.name,
                    "kind": s.kind,
                    "file": s.file,
                    "line": s.line,
                    "signature": s.signature,
                }
                for s in self.symbols[:100]  # Limit symbols
            ],
            "dependencies": dict(list(self.dependencies.items())[:50]),
            "total_files": self.total_files,
            "total_lines": self.total_lines,
        }


class SkeletonGenerator:
    """
    Generate lightweight project skeleton for backend context.

    Extracts file tree and symbol definitions without sending full code.
    """

    IGNORE_PATTERNS = [
        "node_modules", ".git", "__pycache__", ".venv",
        "venv", "dist", "build", ".next", "target",
        ".tarang", ".pytest_cache", ".mypy_cache",
        "*.pyc", "*.pyo", ".DS_Store",
    ]

    def __init__(self, project_root: Path):
        self.project_root = project_root

    def generate(self, max_depth: int = 4) -> ProjectSkeleton:
        """
        Generate project skeleton.

        Args:
            max_depth: Maximum directory depth for tree

        Returns:
            ProjectSkeleton with tree and symbols
        """
        file_tree = self._generate_tree(max_depth)
        symbols = self._extract_symbols()
        dependencies = self._analyze_dependencies()
        total_files, total_lines = self._count_stats()

        return ProjectSkeleton(
            file_tree=file_tree,
            symbols=symbols,
            dependencies=dependencies,
            total_files=total_files,
            total_lines=total_lines,
        )

    def _should_ignore(self, path: Path) -> bool:
        """Check if path should be ignored."""
        name = path.name
        for pattern in self.IGNORE_PATTERNS:
            if pattern.startswith("*"):
                if name.endswith(pattern[1:]):
                    return True
            elif pattern in str(path):
                return True
        return False

    def _generate_tree(self, max_depth: int) -> str:
        """Generate ASCII file tree."""
        lines = [f"{self.project_root.name}/"]

        def walk(path: Path, prefix: str = "", depth: int = 0):
            if depth > max_depth:
                return

            try:
                items = sorted(path.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
            except PermissionError:
                return

            # Filter ignored items
            items = [i for i in items if not self._should_ignore(i)]

            for i, item in enumerate(items[:30]):  # Limit items per directory
                is_last = i == len(items) - 1
                connector = "└── " if is_last else "├── "

                if item.is_dir():
                    lines.append(f"{prefix}{connector}{item.name}/")
                    extension = "    " if is_last else "│   "
                    walk(item, prefix + extension, depth + 1)
                else:
                    lines.append(f"{prefix}{connector}{item.name}")

        walk(self.project_root)
        return "\n".join(lines[:200])  # Limit total lines

    def _extract_symbols(self) -> List[SymbolDefinition]:
        """Extract symbols using ctags if available."""
        symbols = []

        # Try ctags first
        if self._has_ctags():
            symbols = self._extract_with_ctags()
            if symbols:
                return symbols

        # Fallback: Simple regex extraction for Python
        symbols = self._extract_python_symbols()
        return symbols

    def _has_ctags(self) -> bool:
        """Check if ctags is available."""
        try:
            subprocess.run(
                ["ctags", "--version"],
                capture_output=True,
                timeout=5
            )
            return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def _extract_with_ctags(self) -> List[SymbolDefinition]:
        """Extract symbols using universal-ctags."""
        symbols = []

        try:
            result = subprocess.run(
                [
                    "ctags", "-R", "--output-format=json",
                    "--languages=Python,JavaScript,TypeScript,Go,Rust",
                    "--exclude=node_modules", "--exclude=.git",
                    "--exclude=__pycache__", "--exclude=venv",
                    "-f", "-", str(self.project_root)
                ],
                capture_output=True,
                timeout=30
            )

            import json
            for line in result.stdout.decode().strip().split("\n"):
                if not line:
                    continue
                try:
                    tag = json.loads(line)
                    symbols.append(SymbolDefinition(
                        name=tag.get("name", ""),
                        kind=tag.get("kind", "unknown"),
                        file=tag.get("path", ""),
                        line=tag.get("line", 0),
                        signature=tag.get("signature"),
                    ))
                except json.JSONDecodeError:
                    continue

        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

        return symbols[:500]

    def _extract_python_symbols(self) -> List[SymbolDefinition]:
        """Fallback: Extract Python symbols with regex."""
        import re
        symbols = []

        func_pattern = re.compile(r'^(\s*)def\s+(\w+)\s*\(([^)]*)\)', re.MULTILINE)
        class_pattern = re.compile(r'^class\s+(\w+)', re.MULTILINE)

        for py_file in self.project_root.rglob("*.py"):
            if self._should_ignore(py_file):
                continue

            try:
                content = py_file.read_text(errors="replace")
                rel_path = str(py_file.relative_to(self.project_root))

                # Extract classes
                for match in class_pattern.finditer(content):
                    line_num = content[:match.start()].count("\n") + 1
                    symbols.append(SymbolDefinition(
                        name=match.group(1),
                        kind="class",
                        file=rel_path,
                        line=line_num,
                    ))

                # Extract functions
                for match in func_pattern.finditer(content):
                    indent = match.group(1)
                    name = match.group(2)
                    args = match.group(3)
                    line_num = content[:match.start()].count("\n") + 1

                    kind = "method" if indent else "function"
                    symbols.append(SymbolDefinition(
                        name=name,
                        kind=kind,
                        file=rel_path,
                        line=line_num,
                        signature=f"({args})",
                    ))

            except (IOError, UnicodeDecodeError):
                continue

        return symbols[:500]

    def _analyze_dependencies(self) -> Dict[str, List[str]]:
        """Build import dependency graph for Python files."""
        deps = {}

        for py_file in self.project_root.rglob("*.py"):
            if self._should_ignore(py_file):
                continue

            imports = []
            try:
                content = py_file.read_text(errors="replace")
                for line in content.split("\n")[:100]:  # Only scan first 100 lines
                    line = line.strip()
                    if line.startswith("import ") or line.startswith("from "):
                        imports.append(line)
            except (IOError, UnicodeDecodeError):
                continue

            if imports:
                rel_path = str(py_file.relative_to(self.project_root))
                deps[rel_path] = imports[:20]

        return deps

    def _count_stats(self) -> tuple:
        """Count total files and lines."""
        total_files = 0
        total_lines = 0

        for f in self.project_root.rglob("*"):
            if f.is_file() and not self._should_ignore(f):
                total_files += 1
                try:
                    total_lines += len(f.read_text(errors="replace").split("\n"))
                except (IOError, UnicodeDecodeError):
                    pass

        return total_files, total_lines
