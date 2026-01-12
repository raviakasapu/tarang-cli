"""
Tarang - AI Coding Agent.

Just type your instructions. The orchestrator handles everything:
- Simple queries (explanations, questions)
- Complex tasks (multi-step implementations)
- Long-running jobs with phases and milestones

Architecture:
- Backend: Runs agents with reasoning/planning (protected IP)
- CLI: Executes tools locally via WebSocket (filesystem access)
- WebSocket: Bidirectional real-time communication

Usage:
    tarang login                        # Authenticate
    tarang config --openrouter-key KEY  # Set API key
    tarang "explain the project"        # Run instruction
    tarang "add user authentication"    # Build features
    tarang                              # Interactive mode
"""

__version__ = "3.5.9"
__author__ = "Tarang Team"
