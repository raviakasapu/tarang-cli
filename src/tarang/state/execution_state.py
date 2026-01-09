"""
Execution state management for long-running tasks.

Provides file-based checkpointing to support:
- Task resume after interruption
- Progress tracking across milestones/phases
- Retry counting per task/phase
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional
from enum import Enum


class ExecutionStatus(str, Enum):
    """Status of execution."""
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class ExecutionState:
    """State of a Tarang execution that can be persisted and resumed."""
    
    # Identity
    job_id: str
    instruction: str
    status: str = ExecutionStatus.PENDING.value
    
    # Progress tracking
    current_milestone_index: int = 0
    current_phase_index: int = 0
    current_task_index: int = 0
    
    # Completed work
    completed_milestones: List[str] = field(default_factory=list)
    completed_phases: Dict[str, List[str]] = field(default_factory=dict)
    completed_tasks: Dict[str, List[str]] = field(default_factory=dict)
    
    # Retry tracking - "milestone_1:phase_2:task_3" -> retry count
    retry_counts: Dict[str, int] = field(default_factory=dict)
    
    # Results storage
    milestone_results: Dict[str, Dict] = field(default_factory=dict)
    phase_results: Dict[str, Dict] = field(default_factory=dict)
    task_results: Dict[str, Dict] = field(default_factory=dict)
    
    # Timing
    started_at: float = 0.0
    deadline_at: float = 0.0  # started_at + max_duration
    last_checkpoint_at: float = 0.0
    last_activity_at: float = 0.0
    
    # PRD info (for complex builds)
    prd: Optional[Dict[str, Any]] = None
    milestones: List[Dict[str, Any]] = field(default_factory=list)
    
    # Error info
    last_error: Optional[str] = None
    error_count: int = 0

    # Context continuity fields (for follow-up instructions)
    project_context: Optional[Dict[str, Any]] = None  # Cached project info
    last_explorer_summary: Optional[str] = None  # Last exploration result
    active_files: List[str] = field(default_factory=list)  # Files being worked on
    tech_stack: Optional[Dict[str, str]] = None  # Detected tech stack
    last_instruction_result: Optional[str] = None  # Summary of last completed instruction

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExecutionState":
        """Create from dictionary, handling old/new formats for backward compat."""
        import dataclasses

        # Get valid field names from the dataclass
        valid_fields = {f.name for f in dataclasses.fields(cls)}

        # Filter to only known fields
        filtered_data = {k: v for k, v in data.items() if k in valid_fields}

        # Provide defaults for required fields if missing (old format compat)
        if "job_id" not in filtered_data:
            filtered_data["job_id"] = "legacy"

        if "instruction" not in filtered_data:
            filtered_data["instruction"] = data.get("instruction", "Unknown")

        # Handle old status values
        if "status" in filtered_data:
            status_val = filtered_data["status"]
            # Map old status values to new ones if needed
            if status_val == "error":
                filtered_data["status"] = ExecutionStatus.FAILED.value

        return cls(**filtered_data)
    
    def is_expired(self) -> bool:
        """Check if execution has exceeded deadline."""
        if self.deadline_at <= 0:
            return False
        return time.time() > self.deadline_at
    
    def get_progress_summary(self) -> str:
        """Get human-readable progress summary."""
        parts = []
        
        if self.milestones:
            total_milestones = len(self.milestones)
            completed_milestones = len(self.completed_milestones)
            parts.append(f"Milestone {completed_milestones}/{total_milestones}")
        
        if self.current_milestone_index < len(self.milestones):
            milestone = self.milestones[self.current_milestone_index]
            parts.append(f"Phase {self.current_phase_index + 1}")
            parts.append(f"Task {self.current_task_index + 1}")
        
        return " > ".join(parts) if parts else "Starting..."
    
    def increment_retry(self, key: str) -> int:
        """Increment retry count for a task/phase and return new count."""
        current = self.retry_counts.get(key, 0)
        self.retry_counts[key] = current + 1
        return self.retry_counts[key]
    
    def get_retry_count(self, key: str) -> int:
        """Get current retry count for a task/phase."""
        return self.retry_counts.get(key, 0)

    def get_continuity_context(self, max_chars: int = 1000) -> str:
        """
        Build context string from saved state for new instructions.

        This enables follow-up instructions to have awareness of previous work
        without needing to re-explore the project.

        Args:
            max_chars: Maximum characters for the context string

        Returns:
            Formatted context string from saved state
        """
        parts = []

        # Include tech stack if known
        if self.tech_stack:
            tech_items = [f"{k}: {v}" for k, v in list(self.tech_stack.items())[:5]]
            parts.append(f"Tech Stack: {', '.join(tech_items)}")

        # Include active files if working on something
        if self.active_files:
            files_str = ", ".join(self.active_files[:5])
            if len(self.active_files) > 5:
                files_str += f" (+{len(self.active_files) - 5} more)"
            parts.append(f"Active Files: {files_str}")

        # Include last explorer summary for project awareness
        if self.last_explorer_summary:
            summary = self.last_explorer_summary[:400]
            if len(self.last_explorer_summary) > 400:
                summary += "..."
            parts.append(f"Project Overview: {summary}")

        # Include last instruction result for continuity
        if self.last_instruction_result:
            result = self.last_instruction_result[:200]
            if len(self.last_instruction_result) > 200:
                result += "..."
            parts.append(f"Last Action: {result}")

        if not parts:
            return ""

        context = "\n".join(parts)
        if len(context) > max_chars:
            context = context[:max_chars - 3] + "..."
        return context

    def update_from_result(self, result: Dict[str, Any]) -> None:
        """
        Update context continuity fields from an execution result.

        Extracts relevant information from the result to maintain context
        for follow-up instructions.

        Args:
            result: The execution result dictionary
        """
        # Update last instruction result
        if "human_readable_summary" in result:
            self.last_instruction_result = result["human_readable_summary"]

        # Extract files modified/read
        for key in ["files_modified", "files_read", "files"]:
            if key in result:
                files = result[key]
                if isinstance(files, list):
                    # Prepend new files to active_files, keeping recent ones first
                    self.active_files = files[:10] + [
                        f for f in self.active_files if f not in files
                    ][:10]
                    break

        # Extract tech stack if available
        if "tech_stack" in result and isinstance(result["tech_stack"], dict):
            self.tech_stack = result["tech_stack"]

        # Extract exploration summary
        if "exploration_summary" in result:
            self.last_explorer_summary = result["exploration_summary"]
        elif "project_summary" in result:
            self.last_explorer_summary = result["project_summary"]


class ExecutionStateManager:
    """Manages execution state persistence to .tarang/state.json."""
    
    DEFAULT_CHECKPOINT_INTERVAL = 300  # 5 minutes
    DEFAULT_MAX_DURATION = 3600  # 1 hour
    
    def __init__(
        self,
        project_dir: str,
        checkpoint_interval: int = DEFAULT_CHECKPOINT_INTERVAL,
        max_duration: int = DEFAULT_MAX_DURATION,
    ):
        self.project_dir = Path(project_dir)
        self.state_dir = self.project_dir / ".tarang"
        self.state_file = self.state_dir / "state.json"
        self.checkpoint_interval = checkpoint_interval
        self.max_duration = max_duration
        
        # Ensure state directory exists
        self.state_dir.mkdir(parents=True, exist_ok=True)
    
    def create_state(
        self,
        job_id: str,
        instruction: str,
        prd: Optional[Dict[str, Any]] = None,
        milestones: Optional[List[Dict[str, Any]]] = None,
    ) -> ExecutionState:
        """Create a new execution state."""
        now = time.time()
        
        state = ExecutionState(
            job_id=job_id,
            instruction=instruction,
            status=ExecutionStatus.RUNNING.value,
            started_at=now,
            deadline_at=now + self.max_duration,
            last_checkpoint_at=now,
            last_activity_at=now,
            prd=prd,
            milestones=milestones or [],
        )
        
        self.save(state)
        return state
    
    def save(self, state: ExecutionState) -> None:
        """Save state to file."""
        state.last_checkpoint_at = time.time()
        state.last_activity_at = time.time()
        
        try:
            self.state_file.write_text(
                json.dumps(state.to_dict(), indent=2, default=str),
                encoding="utf-8"
            )
        except Exception as e:
            # Log but don't fail - state saving is best-effort
            print(f"Warning: Failed to save state: {e}")
    
    def load(self) -> Optional[ExecutionState]:
        """Load state from file if it exists."""
        if not self.state_file.exists():
            return None
        
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            return ExecutionState.from_dict(data)
        except Exception as e:
            print(f"Warning: Failed to load state: {e}")
            return None
    
    def should_checkpoint(self, state: ExecutionState) -> bool:
        """Check if checkpoint is due based on interval."""
        return time.time() - state.last_checkpoint_at >= self.checkpoint_interval
    
    def mark_completed(self, state: ExecutionState) -> None:
        """Mark execution as completed."""
        state.status = ExecutionStatus.COMPLETED.value
        self.save(state)
    
    def mark_failed(self, state: ExecutionState, error: str) -> None:
        """Mark execution as failed."""
        state.status = ExecutionStatus.FAILED.value
        state.last_error = error
        state.error_count += 1
        self.save(state)
    
    def mark_paused(self, state: ExecutionState) -> None:
        """Mark execution as paused (can be resumed)."""
        state.status = ExecutionStatus.PAUSED.value
        self.save(state)
    
    def clear(self) -> None:
        """Clear state file."""
        if self.state_file.exists():
            self.state_file.unlink()
    
    def can_resume(self) -> bool:
        """Check if there's a resumable state."""
        state = self.load()
        if not state:
            return False
        
        return state.status in [
            ExecutionStatus.RUNNING.value,
            ExecutionStatus.PAUSED.value,
        ]
    
    def get_resume_info(self) -> Optional[Dict[str, Any]]:
        """Get info about resumable state."""
        state = self.load()
        if not state:
            return None
        
        return {
            "job_id": state.job_id,
            "instruction": state.instruction[:100] + "..." if len(state.instruction) > 100 else state.instruction,
            "status": state.status,
            "progress": state.get_progress_summary(),
            "started_at": state.started_at,
            "last_activity": state.last_activity_at,
            "can_resume": self.can_resume(),
        }


def get_state_manager(project_dir: str = ".") -> ExecutionStateManager:
    """Get state manager for project."""
    return ExecutionStateManager(project_dir)
