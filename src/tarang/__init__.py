"""
Tarang - AI Coding Agent (Thin Client).

A lightweight CLI that connects to the Tarang backend for AI-powered coding.
The CLI handles local operations (files, shell) while the backend handles
all reasoning and orchestration.

Usage:
    tarang login                      # Authenticate
    tarang config --openrouter-key    # Set API key
    tarang run "explain the project"  # Run instruction
    tarang                            # Interactive mode
"""

__version__ = "2.0.1"
__author__ = "Tarang Team"
