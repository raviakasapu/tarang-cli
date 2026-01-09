"""Apply unified diffs to files with backup and rollback support."""

import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class ApplyResult:
    """Result of applying a diff."""

    success: bool
    file_path: str
    message: str
    backup_path: str | None = None


class DiffApplicator:
    """Apply code edits with backup and rollback capabilities."""

    def __init__(self, dry_run: bool = False, backup_dir: str = ".tarang/backups"):
        """Initialize the diff applicator.

        Args:
            dry_run: If True, don't actually apply changes
            backup_dir: Directory for backup files
        """
        self.dry_run = dry_run
        self.backup_dir = Path(backup_dir)
        self._applied_changes: list[tuple[Path, Path]] = []  # (original, backup)

    async def apply(self, edit: dict[str, Any]) -> bool:
        """Apply a single edit instruction.

        Args:
            edit: Edit instruction with file path and diff/content

        Returns:
            True if successful
        """
        file_path = Path(edit.get("file", ""))

        if not file_path:
            return False

        # Handle different edit types
        if "diff" in edit:
            return await self._apply_diff(file_path, edit["diff"])
        elif "content" in edit:
            return await self._apply_content(file_path, edit["content"])
        elif "search_replace" in edit:
            return await self._apply_search_replace(
                file_path,
                edit["search_replace"].get("search", ""),
                edit["search_replace"].get("replace", ""),
            )
        else:
            return False

    async def _apply_diff(self, file_path: Path, diff: str) -> bool:
        """Apply a unified diff to a file.

        Args:
            file_path: Path to the file
            diff: Unified diff content

        Returns:
            True if successful
        """
        if self.dry_run:
            return True

        # Create backup
        backup_path = await self._create_backup(file_path)

        try:
            # Write diff to temp file
            diff_file = Path("/tmp/tarang_diff.patch")
            diff_file.write_text(diff)

            # Apply with patch command
            result = subprocess.run(
                ["patch", "-p0", "--no-backup-if-mismatch", str(file_path)],
                input=diff,
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode == 0:
                self._applied_changes.append((file_path, backup_path))
                return True
            else:
                # Restore from backup on failure
                if backup_path and backup_path.exists():
                    shutil.copy2(backup_path, file_path)
                return False

        except (subprocess.SubprocessError, OSError) as e:
            # Restore from backup on error
            if backup_path and backup_path.exists():
                shutil.copy2(backup_path, file_path)
            return False

    async def _apply_content(self, file_path: Path, content: str) -> bool:
        """Replace entire file content.

        Args:
            file_path: Path to the file
            content: New file content

        Returns:
            True if successful
        """
        if self.dry_run:
            return True

        # Create backup if file exists
        backup_path = None
        if file_path.exists():
            backup_path = await self._create_backup(file_path)

        try:
            # Ensure parent directory exists
            file_path.parent.mkdir(parents=True, exist_ok=True)

            # Write new content
            file_path.write_text(content)

            if backup_path:
                self._applied_changes.append((file_path, backup_path))

            return True

        except OSError:
            # Restore from backup on error
            if backup_path and backup_path.exists():
                shutil.copy2(backup_path, file_path)
            return False

    async def _apply_search_replace(
        self, file_path: Path, search: str, replace: str
    ) -> bool:
        """Apply search and replace to a file.

        Args:
            file_path: Path to the file
            search: Text to search for
            replace: Replacement text

        Returns:
            True if successful
        """
        if not file_path.exists():
            return False

        if self.dry_run:
            return True

        backup_path = await self._create_backup(file_path)

        try:
            content = file_path.read_text()

            if search not in content:
                return False

            new_content = content.replace(search, replace, 1)
            file_path.write_text(new_content)

            self._applied_changes.append((file_path, backup_path))
            return True

        except OSError:
            if backup_path and backup_path.exists():
                shutil.copy2(backup_path, file_path)
            return False

    async def _create_backup(self, file_path: Path) -> Path | None:
        """Create a backup of a file.

        Args:
            file_path: Path to the file to backup

        Returns:
            Path to the backup file, or None if file doesn't exist
        """
        if not file_path.exists():
            return None

        # Create backup directory
        self.backup_dir.mkdir(parents=True, exist_ok=True)

        # Generate backup filename with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"{file_path.name}.{timestamp}.bak"
        backup_path = self.backup_dir / backup_name

        shutil.copy2(file_path, backup_path)
        return backup_path

    async def rollback(self) -> int:
        """Rollback all applied changes.

        Returns:
            Number of files rolled back
        """
        rolled_back = 0

        for original, backup in reversed(self._applied_changes):
            if backup and backup.exists():
                try:
                    shutil.copy2(backup, original)
                    rolled_back += 1
                except OSError:
                    continue

        self._applied_changes.clear()
        return rolled_back

    def cleanup_backups(self, max_age_hours: int = 24):
        """Clean up old backup files.

        Args:
            max_age_hours: Maximum age of backups to keep
        """
        if not self.backup_dir.exists():
            return

        cutoff = datetime.now().timestamp() - (max_age_hours * 3600)

        for backup in self.backup_dir.glob("*.bak"):
            try:
                if backup.stat().st_mtime < cutoff:
                    backup.unlink()
            except OSError:
                continue
