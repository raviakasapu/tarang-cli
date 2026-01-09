"""Shadow linting for code verification before applying changes."""

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tarang.core.config import load_config


@dataclass
class LintResult:
    """Result of a lint check."""

    passed: bool
    message: str
    errors: list[str] | None = None
    warnings: list[str] | None = None


# Default linters by language/extension
DEFAULT_LINTERS: dict[str, list[str]] = {
    ".py": ["ruff", "check", "--quiet"],
    ".js": ["npx", "eslint", "--quiet"],
    ".ts": ["npx", "tsc", "--noEmit"],
    ".tsx": ["npx", "tsc", "--noEmit"],
    ".jsx": ["npx", "eslint", "--quiet"],
    ".rs": ["cargo", "check", "--quiet"],
    ".go": ["go", "vet"],
}


class ShadowLinter:
    """Run linting checks on code before applying changes."""

    def __init__(self, root: Path):
        """Initialize the shadow linter.

        Args:
            root: Project root directory
        """
        self.root = root
        self._config = load_config()
        self._custom_command = self._config.get("lint_command")

    async def check(self, file_path: str | Path) -> LintResult:
        """Run lint check on a file.

        Args:
            file_path: Path to the file to check

        Returns:
            LintResult with pass/fail and any messages
        """
        path = Path(file_path)

        if not path.exists():
            return LintResult(
                passed=False,
                message=f"File not found: {file_path}",
            )

        # Use custom command if configured
        if self._custom_command:
            return await self._run_custom_lint(path)

        # Use default linter based on extension
        ext = path.suffix
        if ext in DEFAULT_LINTERS:
            return await self._run_default_lint(path, ext)

        # No linter available - pass by default
        return LintResult(
            passed=True,
            message="No linter configured for this file type",
        )

    async def _run_custom_lint(self, file_path: Path) -> LintResult:
        """Run custom lint command.

        Args:
            file_path: Path to the file

        Returns:
            LintResult
        """
        command = self._custom_command.replace("{file}", str(file_path))

        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=60,
                cwd=self.root,
            )

            if result.returncode == 0:
                return LintResult(
                    passed=True,
                    message="Lint check passed",
                )
            else:
                return LintResult(
                    passed=False,
                    message="Lint check failed",
                    errors=result.stdout.strip().split("\n") if result.stdout else None,
                    warnings=result.stderr.strip().split("\n") if result.stderr else None,
                )

        except subprocess.TimeoutExpired:
            return LintResult(
                passed=False,
                message="Lint check timed out",
            )
        except subprocess.SubprocessError as e:
            return LintResult(
                passed=False,
                message=f"Lint check error: {e}",
            )

    async def _run_default_lint(self, file_path: Path, ext: str) -> LintResult:
        """Run default linter for file extension.

        Args:
            file_path: Path to the file
            ext: File extension

        Returns:
            LintResult
        """
        command = DEFAULT_LINTERS[ext] + [str(file_path)]

        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=60,
                cwd=self.root,
            )

            if result.returncode == 0:
                return LintResult(
                    passed=True,
                    message="Lint check passed",
                )
            else:
                return LintResult(
                    passed=False,
                    message="Lint check failed",
                    errors=self._parse_lint_output(result.stdout + result.stderr),
                )

        except FileNotFoundError:
            # Linter not installed - pass by default
            return LintResult(
                passed=True,
                message=f"Linter not found for {ext}, skipping",
            )
        except subprocess.TimeoutExpired:
            return LintResult(
                passed=False,
                message="Lint check timed out",
            )
        except subprocess.SubprocessError as e:
            return LintResult(
                passed=False,
                message=f"Lint check error: {e}",
            )

    def _parse_lint_output(self, output: str) -> list[str]:
        """Parse lint output into a list of errors.

        Args:
            output: Raw lint output

        Returns:
            List of error messages
        """
        if not output:
            return []

        lines = output.strip().split("\n")
        # Filter out empty lines and summary lines
        errors = [
            line.strip()
            for line in lines
            if line.strip() and not line.startswith("Found")
        ]
        return errors[:10]  # Limit to first 10 errors

    async def check_all(self, file_paths: list[str | Path]) -> dict[str, LintResult]:
        """Check multiple files.

        Args:
            file_paths: List of file paths to check

        Returns:
            Dictionary mapping file paths to their LintResults
        """
        results = {}
        for path in file_paths:
            results[str(path)] = await self.check(path)
        return results
