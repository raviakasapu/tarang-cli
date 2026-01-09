"""
Validation tools for Tarang agents.

Provides rule-based validation for tasks, phases, and milestones:
- ValidateFileTool: Check file existence and content patterns
- ValidateBuildTool: Run build commands and verify success
- ValidateStructureTool: Verify project structure against expected layout
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field
from agent_framework.base import BaseTool


# ============================================================================
# Pydantic Models for Tool Arguments and Outputs
# ============================================================================

class ValidateFileArgs(BaseModel):
    """Arguments for validate_file tool."""
    path: str = Field(..., description="Path to file (relative to project)")
    must_exist: bool = Field(default=True, description="Whether file must exist")
    patterns: Optional[List[str]] = Field(
        default=None, 
        description="Content patterns that must be present in file"
    )
    min_lines: Optional[int] = Field(
        default=None,
        description="Minimum number of lines expected"
    )


class ValidateFileOutput(BaseModel):
    """Output from validate_file tool."""
    valid: bool
    path: str
    exists: bool
    issues: List[str]
    checks_passed: int
    checks_total: int


class ValidateBuildArgs(BaseModel):
    """Arguments for validate_build tool."""
    command: str = Field(
        default="npm run build",
        description="Build command to run"
    )
    timeout: int = Field(
        default=120,
        description="Timeout in seconds"
    )
    working_dir: Optional[str] = Field(
        default=None,
        description="Working directory for command (relative to project)"
    )
    success_patterns: Optional[List[str]] = Field(
        default=None,
        description="Patterns indicating success in output"
    )
    failure_patterns: Optional[List[str]] = Field(
        default=None,
        description="Patterns indicating failure in output"
    )


class ValidateBuildOutput(BaseModel):
    """Output from validate_build tool."""
    valid: bool
    exit_code: int
    command: str
    stdout: str
    stderr: str
    issues: List[str]
    execution_time_seconds: float


class ValidateStructureArgs(BaseModel):
    """Arguments for validate_structure tool."""
    expected_files: List[str] = Field(
        ...,
        description="List of files that should exist (relative to project)"
    )
    expected_dirs: Optional[List[str]] = Field(
        default=None,
        description="List of directories that should exist"
    )


class ValidateStructureOutput(BaseModel):
    """Output from validate_structure tool."""
    valid: bool
    missing_files: List[str]
    missing_dirs: List[str]
    found_files: List[str]
    found_dirs: List[str]
    issues: List[str]


# ============================================================================
# Tool Implementations
# ============================================================================

class ValidateFileTool(BaseTool):
    """Validates file existence and optionally checks content patterns."""

    _name = "validate_file"
    _description = (
        "Validate that a file exists and optionally contains expected patterns. "
        "Use after creating/modifying files to verify success."
    )

    def __init__(self, project_dir: str = "."):
        super().__init__()
        self.project_dir = Path(project_dir).resolve()

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def args_schema(self):
        return ValidateFileArgs

    @property
    def output_schema(self):
        return ValidateFileOutput
    
    def execute(
        self,
        path: str,
        must_exist: bool = True,
        patterns: Optional[List[str]] = None,
        min_lines: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Execute file validation."""
        issues = []
        checks_passed = 0
        checks_total = 0
        
        # Resolve path
        file_path = self.project_dir / path
        exists = file_path.exists()
        
        # Check existence
        checks_total += 1
        if must_exist and not exists:
            issues.append(f"File does not exist: {path}")
        elif exists:
            checks_passed += 1
        
        # Check content patterns (if file exists)
        if exists and patterns:
            try:
                content = file_path.read_text(encoding="utf-8")
                for pattern in patterns:
                    checks_total += 1
                    if pattern in content:
                        checks_passed += 1
                    else:
                        issues.append(f"Pattern not found: {pattern}")
            except Exception as e:
                issues.append(f"Failed to read file: {e}")
        
        # Check min lines (if file exists)
        if exists and min_lines is not None:
            checks_total += 1
            try:
                content = file_path.read_text(encoding="utf-8")
                line_count = len(content.splitlines())
                if line_count >= min_lines:
                    checks_passed += 1
                else:
                    issues.append(f"File has {line_count} lines, expected at least {min_lines}")
            except Exception as e:
                issues.append(f"Failed to count lines: {e}")
        
        return ValidateFileOutput(
            valid=len(issues) == 0,
            path=path,
            exists=exists,
            issues=issues,
            checks_passed=checks_passed,
            checks_total=checks_total,
        ).model_dump()


class ValidateBuildTool(BaseTool):
    """Runs build command and validates success."""

    _name = "validate_build"
    _description = (
        "Run a build command and validate it succeeds. "
        "Use to verify project compiles/builds correctly."
    )

    def __init__(self, project_dir: str = "."):
        super().__init__()
        self.project_dir = Path(project_dir).resolve()

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def args_schema(self):
        return ValidateBuildArgs

    @property
    def output_schema(self):
        return ValidateBuildOutput

    def execute(
        self,
        command: str = "npm run build",
        timeout: int = 120,
        working_dir: Optional[str] = None,
        success_patterns: Optional[List[str]] = None,
        failure_patterns: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Execute build validation."""
        import time
        
        issues = []
        
        # Resolve working directory
        cwd = self.project_dir
        if working_dir:
            cwd = self.project_dir / working_dir
        
        # Run command
        start_time = time.time()
        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            exit_code = result.returncode
            stdout = result.stdout
            stderr = result.stderr
        except subprocess.TimeoutExpired:
            return ValidateBuildOutput(
                valid=False,
                exit_code=-1,
                command=command,
                stdout="",
                stderr="",
                issues=[f"Command timed out after {timeout} seconds"],
                execution_time_seconds=timeout,
            ).model_dump()
        except Exception as e:
            return ValidateBuildOutput(
                valid=False,
                exit_code=-1,
                command=command,
                stdout="",
                stderr=str(e),
                issues=[f"Failed to execute command: {e}"],
                execution_time_seconds=time.time() - start_time,
            ).model_dump()
        
        execution_time = time.time() - start_time
        
        # Check exit code
        if exit_code != 0:
            issues.append(f"Command exited with code {exit_code}")
        
        # Check failure patterns
        combined_output = f"{stdout}\n{stderr}"
        if failure_patterns:
            for pattern in failure_patterns:
                if pattern.lower() in combined_output.lower():
                    issues.append(f"Failure pattern detected: {pattern}")
        
        # Check success patterns (if specified and no failures)
        if success_patterns and not issues:
            for pattern in success_patterns:
                if pattern.lower() not in combined_output.lower():
                    issues.append(f"Expected success pattern not found: {pattern}")
        
        # Truncate output for response
        max_output = 2000
        if len(stdout) > max_output:
            stdout = stdout[:max_output] + f"\n... (truncated {len(stdout) - max_output} chars)"
        if len(stderr) > max_output:
            stderr = stderr[:max_output] + f"\n... (truncated {len(stderr) - max_output} chars)"
        
        return ValidateBuildOutput(
            valid=len(issues) == 0,
            exit_code=exit_code,
            command=command,
            stdout=stdout,
            stderr=stderr,
            issues=issues,
            execution_time_seconds=execution_time,
        ).model_dump()


class ValidateStructureTool(BaseTool):
    """Validates project structure against expected layout."""

    _name = "validate_structure"
    _description = (
        "Validate that expected files and directories exist. "
        "Use to verify project structure after scaffolding."
    )

    def __init__(self, project_dir: str = "."):
        super().__init__()
        self.project_dir = Path(project_dir).resolve()

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def args_schema(self):
        return ValidateStructureArgs

    @property
    def output_schema(self):
        return ValidateStructureOutput

    def execute(
        self,
        expected_files: List[str],
        expected_dirs: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Execute structure validation."""
        missing_files = []
        missing_dirs = []
        found_files = []
        found_dirs = []
        issues = []
        
        # Check files
        for file_path in expected_files:
            full_path = self.project_dir / file_path
            if full_path.exists() and full_path.is_file():
                found_files.append(file_path)
            else:
                missing_files.append(file_path)
                issues.append(f"Missing file: {file_path}")
        
        # Check directories
        if expected_dirs:
            for dir_path in expected_dirs:
                full_path = self.project_dir / dir_path
                if full_path.exists() and full_path.is_dir():
                    found_dirs.append(dir_path)
                else:
                    missing_dirs.append(dir_path)
                    issues.append(f"Missing directory: {dir_path}")
        
        return ValidateStructureOutput(
            valid=len(issues) == 0,
            missing_files=missing_files,
            missing_dirs=missing_dirs,
            found_files=found_files,
            found_dirs=found_dirs,
            issues=issues,
        ).model_dump()
