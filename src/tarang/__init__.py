"""
Tarang - AI Coding Agent (Thin Client).

A lightweight CLI that connects to the Tarang backend for AI-powered coding.
The CLI handles local operations (files, shell) while the backend handles
all reasoning and orchestration.

Architecture (v3.0):
- CLI: Sends context (skeleton, file contents) to backend
- Backend: Reasons about code, returns instructions
- CLI: Executes instructions locally (file edits, shell commands)

This protects the backend's reasoning IP while enabling local file access.

Usage:
    tarang login                      # Authenticate
    tarang config --openrouter-key    # Set API key
    tarang run "explain the project"  # Run instruction
    tarang                            # Interactive mode
"""

__version__ = "3.0.0"
__author__ = "Tarang Team"
