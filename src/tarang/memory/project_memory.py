"""
Project Memory - State persistence for Tarang projects.

Stores execution state in .tarang directory for resume capability.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


class ProjectMemory:
    """
    Persist project state to disk for resume capability.

    State is stored in .tarang/ directory within the project:
    .tarang/
    ├── state.json          # Main execution state
    ├── phases/             # Completed phase results
    │   ├── phase_1.json
    │   └── phase_2.json
    ├── tasks.json          # Task list for worker pool
    └── history.json        # Execution history
    """

    STATE_FILE = "state.json"
    TASKS_FILE = "tasks.json"
    HISTORY_FILE = "history.json"
    PHASES_DIR = "phases"

    def __init__(self, project_dir: str):
        """
        Initialize project memory.

        Args:
            project_dir: The project directory
        """
        self.project_dir = Path(project_dir)
        self.state_dir = self.project_dir / ".tarang"

    def _ensure_dirs(self):
        """Ensure state directories exist."""
        self.state_dir.mkdir(exist_ok=True)
        (self.state_dir / self.PHASES_DIR).mkdir(exist_ok=True)

    def _read_json(self, filename: str) -> Optional[Dict[str, Any]]:
        """Read JSON file from state directory."""
        filepath = self.state_dir / filename
        if not filepath.exists():
            return None
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return None

    def _write_json(self, filename: str, data: Dict[str, Any]):
        """Write JSON file to state directory."""
        self._ensure_dirs()
        filepath = self.state_dir / filename
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)

    # Main state methods

    def save_state(self, state: Dict[str, Any]):
        """
        Save main execution state.

        Args:
            state: State dict with instruction, status, result, etc.
        """
        state["updated_at"] = datetime.now().isoformat()
        self._write_json(self.STATE_FILE, state)

    def load_state(self) -> Optional[Dict[str, Any]]:
        """
        Load main execution state.

        Returns:
            State dict or None if not found
        """
        return self._read_json(self.STATE_FILE)

    def clear_state(self):
        """Clear all state (for clean start)."""
        if self.state_dir.exists():
            import shutil
            shutil.rmtree(self.state_dir)

    # Phase methods

    def save_phase(self, phase_name: str, result: Dict[str, Any]):
        """
        Save completed phase result.

        Args:
            phase_name: Name/identifier of the phase
            result: Phase result dict
        """
        self._ensure_dirs()
        phase_file = self.state_dir / self.PHASES_DIR / f"{phase_name}.json"
        result["completed_at"] = datetime.now().isoformat()
        with open(phase_file, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, default=str)

    def load_phase(self, phase_name: str) -> Optional[Dict[str, Any]]:
        """
        Load a phase result.

        Args:
            phase_name: Name/identifier of the phase

        Returns:
            Phase result dict or None
        """
        phase_file = self.state_dir / self.PHASES_DIR / f"{phase_name}.json"
        if not phase_file.exists():
            return None
        try:
            with open(phase_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return None

    def get_completed_phases(self) -> List[str]:
        """
        Get list of completed phase names.

        Returns:
            List of phase names
        """
        phases_dir = self.state_dir / self.PHASES_DIR
        if not phases_dir.exists():
            return []
        return [f.stem for f in phases_dir.glob("*.json")]

    # Task list methods

    def save_task_list(self, tasks: List[Dict[str, Any]]):
        """
        Save task list for worker pool.

        Args:
            tasks: List of task dicts
        """
        self._write_json(self.TASKS_FILE, {"tasks": tasks})

    def get_task_list(self) -> List[Dict[str, Any]]:
        """
        Get current task list.

        Returns:
            List of task dicts
        """
        data = self._read_json(self.TASKS_FILE)
        if data:
            return data.get("tasks", [])
        return []

    def update_task(self, task_id: int, updates: Dict[str, Any]):
        """
        Update a specific task.

        Args:
            task_id: Task ID to update
            updates: Dict of updates to apply
        """
        tasks = self.get_task_list()
        for task in tasks:
            if task.get("id") == task_id:
                task.update(updates)
                task["updated_at"] = datetime.now().isoformat()
                break
        self.save_task_list(tasks)

    def mark_task_complete(self, task_id: int, result: Optional[Dict[str, Any]] = None):
        """
        Mark a task as complete.

        Args:
            task_id: Task ID to mark complete
            result: Optional result dict
        """
        updates = {"status": "completed"}
        if result:
            updates["result"] = result
        self.update_task(task_id, updates)

    def get_pending_tasks(self) -> List[Dict[str, Any]]:
        """
        Get tasks that are not completed.

        Returns:
            List of pending task dicts
        """
        tasks = self.get_task_list()
        return [t for t in tasks if t.get("status") != "completed"]

    # History methods

    def add_to_history(self, entry: Dict[str, Any]):
        """
        Add an entry to execution history.

        Args:
            entry: History entry dict
        """
        history = self._read_json(self.HISTORY_FILE) or {"entries": []}
        entry["timestamp"] = datetime.now().isoformat()
        history["entries"].append(entry)

        # Keep last 100 entries
        history["entries"] = history["entries"][-100:]
        self._write_json(self.HISTORY_FILE, history)

    def get_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        """
        Get recent history entries.

        Args:
            limit: Maximum entries to return

        Returns:
            List of history entries (newest first)
        """
        history = self._read_json(self.HISTORY_FILE) or {"entries": []}
        entries = history.get("entries", [])
        return list(reversed(entries[-limit:]))

    # Convenience methods

    def is_initialized(self) -> bool:
        """Check if project has Tarang state."""
        return self.state_dir.exists()

    def get_status(self) -> str:
        """Get current execution status."""
        state = self.load_state()
        if not state:
            return "not_started"
        return state.get("status", "unknown")

    def get_summary(self) -> Dict[str, Any]:
        """
        Get summary of project state.

        Returns:
            Summary dict
        """
        state = self.load_state()
        phases = self.get_completed_phases()
        tasks = self.get_task_list()
        pending = self.get_pending_tasks()

        return {
            "initialized": self.is_initialized(),
            "status": self.get_status(),
            "instruction": state.get("instruction") if state else None,
            "phases_completed": len(phases),
            "tasks_total": len(tasks),
            "tasks_pending": len(pending),
            "last_updated": state.get("updated_at") if state else None,
        }


# Context manager for auto-save
class MemoryContext:
    """
    Context manager for automatic state saving.

    Usage:
        with MemoryContext(project_dir) as memory:
            memory.save_state({"status": "running"})
            # ... do work ...
            # State auto-saved on exit
    """

    def __init__(self, project_dir: str):
        self.memory = ProjectMemory(project_dir)
        self._state: Dict[str, Any] = {}

    def __enter__(self) -> "MemoryContext":
        self._state = self.memory.load_state() or {}
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self._state["status"] = "error"
            self._state["error"] = str(exc_val)
        self.memory.save_state(self._state)
        return False

    def update(self, updates: Dict[str, Any]):
        """Update state (will be saved on exit)."""
        self._state.update(updates)

    @property
    def state(self) -> Dict[str, Any]:
        """Get current state."""
        return self._state
