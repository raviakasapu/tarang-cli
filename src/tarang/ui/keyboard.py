"""
Keyboard handler for interactive controls during execution.

ESC   - Terminate current execution
SPACE - Pause and add extra instruction
"""

import sys
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Callable
import select

# Try to import platform-specific modules
try:
    import termios
    import tty
    HAS_TERMIOS = True
except ImportError:
    HAS_TERMIOS = False


class KeyAction(Enum):
    """Keyboard actions."""
    NONE = "none"
    CANCEL = "cancel"      # ESC pressed
    PAUSE = "pause"        # SPACE pressed


@dataclass
class KeyboardState:
    """Shared state for keyboard monitoring."""
    action: KeyAction = KeyAction.NONE
    extra_instruction: Optional[str] = None
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _running: bool = False
    _thread: Optional[threading.Thread] = None
    _original_settings: Optional[list] = None

    def reset(self):
        """Reset state for new execution."""
        with self._lock:
            self.action = KeyAction.NONE
            self.extra_instruction = None

    def set_cancel(self):
        """Set cancel action."""
        with self._lock:
            self.action = KeyAction.CANCEL

    def set_pause(self, instruction: str = None):
        """Set pause action with optional instruction."""
        with self._lock:
            self.action = KeyAction.PAUSE
            self.extra_instruction = instruction

    def get_action(self) -> KeyAction:
        """Get current action (thread-safe)."""
        with self._lock:
            return self.action

    def consume_action(self) -> KeyAction:
        """Get and clear action (thread-safe)."""
        with self._lock:
            action = self.action
            self.action = KeyAction.NONE
            return action


class KeyboardMonitor:
    """
    Monitor keyboard for ESC and SPACE during execution.

    Usage:
        monitor = KeyboardMonitor(console)
        monitor.start()

        while executing:
            action = monitor.state.consume_action()
            if action == KeyAction.CANCEL:
                break
            elif action == KeyAction.PAUSE:
                extra = monitor.prompt_extra_instruction()
                ...

        monitor.stop()
    """

    def __init__(self, console=None, on_status: Callable[[str], None] = None):
        """
        Initialize keyboard monitor.

        Args:
            console: Rich console for output (optional)
            on_status: Callback for status messages
        """
        self.console = console
        self.on_status = on_status or (lambda x: None)
        self.state = KeyboardState()
        self._stop_event = threading.Event()

    def start(self):
        """Start keyboard monitoring."""
        if not HAS_TERMIOS:
            # Windows or non-terminal - skip keyboard monitoring
            return

        self.state.reset()
        self._stop_event.clear()

        # Save terminal settings
        try:
            self.state._original_settings = termios.tcgetattr(sys.stdin)
        except Exception:
            return

        # Start monitor thread
        self.state._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.state._running = True
        self.state._thread.start()

    def stop(self):
        """Stop keyboard monitoring and restore terminal."""
        self.state._running = False
        self._stop_event.set()

        # Restore terminal settings
        if HAS_TERMIOS and self.state._original_settings:
            try:
                termios.tcsetattr(sys.stdin, termios.TCSADRAIN, self.state._original_settings)
            except Exception:
                pass

        # Wait for thread to finish
        if self.state._thread and self.state._thread.is_alive():
            self.state._thread.join(timeout=0.5)

    def _monitor_loop(self):
        """Background thread to monitor keyboard input."""
        if not HAS_TERMIOS:
            return

        try:
            # Set terminal to raw mode for single character input
            tty.setcbreak(sys.stdin.fileno())

            while self.state._running and not self._stop_event.is_set():
                # Check if input is available (with timeout)
                if select.select([sys.stdin], [], [], 0.1)[0]:
                    char = sys.stdin.read(1)

                    if char == '\x1b':  # ESC
                        self.state.set_cancel()
                        self.on_status("[yellow]ESC pressed - cancelling...[/yellow]")

                    elif char == ' ':  # SPACE
                        self.state.set_pause()
                        self.on_status("[cyan]SPACE pressed - pausing for instruction...[/cyan]")

        except Exception:
            pass
        finally:
            # Restore terminal
            if self.state._original_settings:
                try:
                    termios.tcsetattr(sys.stdin, termios.TCSADRAIN, self.state._original_settings)
                except Exception:
                    pass

    def prompt_extra_instruction(self) -> Optional[str]:
        """
        Prompt user for extra instruction after SPACE.

        Returns:
            Extra instruction string, or None if cancelled
        """
        # Temporarily stop monitoring to get clean input
        self.stop()

        try:
            if self.console:
                self.console.print("\n[bold cyan]Add instruction:[/bold cyan] ", end="")

            instruction = input().strip()
            return instruction if instruction else None

        except (KeyboardInterrupt, EOFError):
            return None
        finally:
            # Resume monitoring
            self.start()


def create_keyboard_hints() -> str:
    """Create keyboard hints string for display."""
    return "[dim]ESC=cancel  SPACE=add instruction[/dim]"
