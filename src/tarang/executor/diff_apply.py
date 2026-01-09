"""
Diff Applicator - Apply edits from backend to local files.

Supports unified diffs, search/replace, and full content replacement.
"""
from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class DiffResult:
    """Result of applying a diff."""
    success: bool
    path: str
    error: Optional[str] = None
    backup_path: Optional[str] = None


class DiffApplicator:
    """
    Apply edits from backend to local files.

    Supports:
    - Unified diffs (via patch command)
    - Search/replace edits
    - Full content replacement

    Includes backup/rollback for safety.
    """

    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.backup_dir = project_root / ".tarang_backups"

    def apply_diff(self, path: str, diff: str) -> DiffResult:
        """
        Apply a unified diff to a file.

        Args:
            path: File path relative to project root
            diff: Unified diff content

        Returns:
            DiffResult with success/error info
        """
        file_path = self.project_root / path

        # Create backup first
        backup_path = self._create_backup(file_path)

        try:
            # Try using patch command
            result = subprocess.run(
                ["patch", "-u", str(file_path)],
                input=diff.encode(),
                capture_output=True,
                timeout=30
            )

            if result.returncode != 0:
                # Restore from backup
                self._restore_backup(file_path, backup_path)
                return DiffResult(
                    success=False,
                    path=path,
                    error=result.stderr.decode() or "Patch failed",
                )

            return DiffResult(
                success=True,
                path=path,
                backup_path=str(backup_path) if backup_path else None,
            )

        except FileNotFoundError:
            # patch command not available, restore and fail
            self._restore_backup(file_path, backup_path)
            return DiffResult(
                success=False,
                path=path,
                error="patch command not available",
            )
        except subprocess.TimeoutExpired:
            self._restore_backup(file_path, backup_path)
            return DiffResult(
                success=False,
                path=path,
                error="Patch timed out",
            )

    def apply_search_replace(
        self,
        path: str,
        search: str,
        replace: str,
    ) -> DiffResult:
        """
        Apply a search/replace edit.

        Args:
            path: File path relative to project root
            search: Text to find
            replace: Text to replace with

        Returns:
            DiffResult with success/error info
        """
        file_path = self.project_root / path

        if not file_path.exists():
            return DiffResult(
                success=False,
                path=path,
                error=f"File not found: {path}",
            )

        try:
            content = file_path.read_text()

            if search not in content:
                return DiffResult(
                    success=False,
                    path=path,
                    error=f"Search text not found in {path}",
                )

            # Create backup
            backup_path = self._create_backup(file_path)

            # Apply replacement
            new_content = content.replace(search, replace, 1)
            file_path.write_text(new_content)

            return DiffResult(
                success=True,
                path=path,
                backup_path=str(backup_path) if backup_path else None,
            )

        except Exception as e:
            return DiffResult(
                success=False,
                path=path,
                error=str(e),
            )

    def apply_content(self, path: str, content: str) -> DiffResult:
        """
        Write full content to a file.

        Args:
            path: File path relative to project root
            content: Full file content

        Returns:
            DiffResult with success/error info
        """
        file_path = self.project_root / path

        try:
            # Create backup if file exists
            backup_path = self._create_backup(file_path) if file_path.exists() else None

            # Ensure parent directory exists
            file_path.parent.mkdir(parents=True, exist_ok=True)

            # Write content
            file_path.write_text(content)

            return DiffResult(
                success=True,
                path=path,
                backup_path=str(backup_path) if backup_path else None,
            )

        except Exception as e:
            return DiffResult(
                success=False,
                path=path,
                error=str(e),
            )

    def rollback(self, result: DiffResult) -> bool:
        """
        Rollback a change using backup.

        Args:
            result: DiffResult with backup_path

        Returns:
            True if rollback succeeded
        """
        if not result.backup_path:
            return False

        return self._restore_backup(
            self.project_root / result.path,
            Path(result.backup_path)
        )

    def cleanup_backups(self, max_age_hours: int = 24) -> int:
        """
        Clean up old backup files.

        Args:
            max_age_hours: Maximum age of backups to keep

        Returns:
            Number of files cleaned up
        """
        if not self.backup_dir.exists():
            return 0

        cleaned = 0
        cutoff = time.time() - (max_age_hours * 3600)

        for backup_file in self.backup_dir.glob("*.bak"):
            if backup_file.stat().st_mtime < cutoff:
                backup_file.unlink()
                cleaned += 1

        return cleaned

    def _create_backup(self, file_path: Path) -> Optional[Path]:
        """Create a backup of a file."""
        if not file_path.exists():
            return None

        self.backup_dir.mkdir(exist_ok=True)
        timestamp = int(time.time() * 1000)
        backup_path = self.backup_dir / f"{file_path.name}.{timestamp}.bak"
        shutil.copy2(file_path, backup_path)
        return backup_path

    def _restore_backup(self, file_path: Path, backup_path: Optional[Path]) -> bool:
        """Restore a file from backup."""
        if backup_path and backup_path.exists():
            shutil.copy2(backup_path, file_path)
            return True
        return False
