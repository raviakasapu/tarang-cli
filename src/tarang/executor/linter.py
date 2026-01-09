"""
Shadow Linter - Run linting after applying changes.

Auto-detects project type and runs appropriate linters.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class LintResult:
    """Result of linting."""
    success: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    tool: str = "none"


class ShadowLinter:
    """
    Run linting in the background after applying changes.

    Auto-detects project type and runs appropriate linter.
    """

    LINTER_CONFIGS: Dict[str, Dict] = {
        "python": {
            "detect": ["pyproject.toml", "setup.py", "requirements.txt"],
            "commands": [
                ["python", "-m", "py_compile", "{file}"],  # Syntax check
                ["ruff", "check", "{file}"],               # Fast linter
            ]
        },
        "javascript": {
            "detect": ["package.json"],
            "commands": [
                ["npx", "eslint", "{file}"],
            ]
        },
        "typescript": {
            "detect": ["tsconfig.json"],
            "commands": [
                ["npx", "tsc", "--noEmit"],
            ]
        },
        "rust": {
            "detect": ["Cargo.toml"],
            "commands": [
                ["cargo", "check"],
            ]
        },
        "go": {
            "detect": ["go.mod"],
            "commands": [
                ["go", "vet", "{file}"],
            ]
        },
    }

    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.project_type = self._detect_project_type()

    def lint_file(self, file_path: str) -> LintResult:
        """
        Run linters on a modified file.

        Args:
            file_path: Path to file (relative to project root)

        Returns:
            LintResult with errors/warnings
        """
        if not self.project_type:
            return LintResult(success=True, tool="none")

        config = self.LINTER_CONFIGS.get(self.project_type, {})
        commands = config.get("commands", [])

        errors = []
        warnings = []

        for cmd_template in commands:
            cmd = [
                part.replace("{file}", file_path)
                for part in cmd_template
            ]

            # Check if command exists
            if not shutil.which(cmd[0]):
                continue

            try:
                result = subprocess.run(
                    cmd,
                    cwd=self.project_root,
                    capture_output=True,
                    timeout=60
                )

                if result.returncode != 0:
                    output = result.stderr.decode() or result.stdout.decode()
                    errors.append(f"{cmd[0]}: {output}")

            except subprocess.TimeoutExpired:
                warnings.append(f"{cmd[0]} timed out")
            except FileNotFoundError:
                continue

        return LintResult(
            success=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            tool=self.project_type,
        )

    def lint_build(self) -> LintResult:
        """
        Run full project build/check.

        Returns:
            LintResult with build errors/warnings
        """
        build_commands = {
            "python": ["python", "-m", "py_compile"],
            "javascript": ["npm", "run", "build"],
            "typescript": ["npm", "run", "build"],
            "rust": ["cargo", "build"],
            "go": ["go", "build", "./..."],
        }

        if self.project_type not in build_commands:
            return LintResult(success=True, tool="none")

        cmd = build_commands[self.project_type]

        # Check if command exists
        if not shutil.which(cmd[0]):
            return LintResult(success=True, tool="none")

        try:
            result = subprocess.run(
                cmd,
                cwd=self.project_root,
                capture_output=True,
                timeout=300
            )

            errors = []
            if result.returncode != 0:
                errors = [result.stderr.decode() or result.stdout.decode()]

            return LintResult(
                success=result.returncode == 0,
                errors=errors,
                warnings=[],
                tool=cmd[0],
            )

        except subprocess.TimeoutExpired:
            return LintResult(
                success=False,
                errors=["Build timed out"],
                tool=cmd[0],
            )
        except Exception as e:
            return LintResult(
                success=False,
                errors=[str(e)],
                tool="build",
            )

    def _detect_project_type(self) -> Optional[str]:
        """Detect project type from marker files."""
        for project_type, config in self.LINTER_CONFIGS.items():
            for marker in config.get("detect", []):
                if (self.project_root / marker).exists():
                    return project_type
        return None
