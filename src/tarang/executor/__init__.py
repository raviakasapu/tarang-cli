"""Tarang Executor - Local file and shell operations."""

from tarang.executor.diff_apply import DiffApplicator, DiffResult
from tarang.executor.linter import ShadowLinter, LintResult

__all__ = ["DiffApplicator", "DiffResult", "ShadowLinter", "LintResult"]
