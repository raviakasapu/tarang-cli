"""
Tarang Authentication - CLI login and token management.

Handles OAuth flow via browser and secure token storage.
"""
from __future__ import annotations

import asyncio
import json
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse


CONFIG_DIR = Path.home() / ".tarang"
CONFIG_FILE = CONFIG_DIR / "config.json"


class TarangAuth:
    """
    Handles CLI authentication via browser OAuth flow.

    Stores credentials securely in ~/.tarang/config.json
    """

    def __init__(self, web_url: str = "https://devtarang.ai"):
        self.web_url = web_url
        self.token: Optional[str] = None

    def load_credentials(self) -> Optional[dict]:
        """Load saved credentials from config file."""
        if CONFIG_FILE.exists():
            try:
                return json.loads(CONFIG_FILE.read_text())
            except (json.JSONDecodeError, IOError):
                return None
        return None

    def save_credentials(self, **kwargs) -> None:
        """Save credentials to config file."""
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        config = self.load_credentials() or {}
        config.update(kwargs)
        CONFIG_FILE.write_text(json.dumps(config, indent=2))
        CONFIG_FILE.chmod(0o600)  # Secure permissions

    def get_token(self) -> Optional[str]:
        """Get saved auth token."""
        creds = self.load_credentials()
        return creds.get("token") if creds else None

    def get_openrouter_key(self) -> Optional[str]:
        """Get saved OpenRouter API key."""
        creds = self.load_credentials()
        return creds.get("openrouter_key") if creds else None

    def save_token(self, token: str) -> None:
        """Save auth token."""
        self.save_credentials(token=token)
        self.token = token

    def save_openrouter_key(self, key: str) -> None:
        """Save OpenRouter API key."""
        self.save_credentials(openrouter_key=key)

    def clear_credentials(self) -> None:
        """Clear all saved credentials."""
        if CONFIG_FILE.exists():
            CONFIG_FILE.unlink()

    async def login(self, callback_port: int = 54321) -> str:
        """
        Start OAuth flow via browser.

        Steps:
        1. Open browser to auth page
        2. Start local server to receive callback
        3. Store token securely

        Returns:
            The auth token
        """
        # Create callback server
        server = _CallbackServer(("localhost", callback_port))

        # Open browser to auth URL
        auth_url = f"{self.web_url}/auth/cli?callback=http://localhost:{callback_port}"
        print(f"Opening browser for authentication...")
        print(f"If browser doesn't open, visit: {auth_url}")
        webbrowser.open(auth_url)

        print("\nWaiting for authentication...")
        print("Please log in with GitHub in your browser.")

        # Wait for callback (timeout 5 min)
        try:
            token = await asyncio.wait_for(
                server.wait_for_token(),
                timeout=300
            )
        except asyncio.TimeoutError:
            raise TimeoutError("Authentication timed out. Please try again.")

        # Save and return token
        self.save_token(token)
        return token

    def is_authenticated(self) -> bool:
        """Check if user is authenticated."""
        return bool(self.get_token())

    def has_openrouter_key(self) -> bool:
        """Check if OpenRouter key is configured."""
        return bool(self.get_openrouter_key())


class _CallbackServer:
    """Local HTTP server to receive OAuth callback."""

    def __init__(self, address):
        self.token: Optional[str] = None
        self._received = asyncio.Event()
        self.server = HTTPServer(address, self._make_handler())
        self.server.timeout = 1  # Allow checking for cancellation

    def _make_handler(self):
        parent = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                query = parse_qs(urlparse(self.path).query)
                parent.token = query.get("token", [None])[0]

                self.send_response(200)
                self.send_header("Content-type", "text/html")
                self.end_headers()

                html = """
                <!DOCTYPE html>
                <html>
                <head>
                    <title>Tarang - Authentication Successful</title>
                    <style>
                        body { font-family: -apple-system, sans-serif; text-align: center; padding-top: 50px; }
                        h1 { color: #10B981; }
                    </style>
                </head>
                <body>
                    <h1>Authentication Successful!</h1>
                    <p>You can close this window and return to the terminal.</p>
                </body>
                </html>
                """
                self.wfile.write(html.encode())

                if parent.token:
                    print("\nReceived CLI callback, completing login...")
                    parent._received.set()
                else:
                    print("\nReceived CLI callback without token. Please retry login.")

            def log_message(self, *args):
                pass  # Suppress HTTP logs

        return Handler

    async def wait_for_token(self) -> str:
        """Wait for token from callback."""
        loop = asyncio.get_event_loop()

        while not self._received.is_set():
            # Handle one request (non-blocking)
            await loop.run_in_executor(None, self.server.handle_request)
            await asyncio.sleep(0.1)

        return self.token
