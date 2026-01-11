"""
WebSocket module for hybrid agent architecture.

This module provides:
- TarangWSClient: WebSocket client for bidirectional communication
- ToolExecutor: Local tool execution (file ops, shell)
- MessageHandlers: Handle different message types from backend
"""

from tarang.ws.client import TarangWSClient
from tarang.ws.executor import ToolExecutor
from tarang.ws.handlers import MessageHandlers

__all__ = ["TarangWSClient", "ToolExecutor", "MessageHandlers"]
