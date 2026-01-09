"""Tarang authentication via devtarang.ai."""

import asyncio
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse
import threading

from rich.console import Console
from rich.panel import Panel

from tarang.core.config import save_config, load_config

console = Console()

# Local callback server settings
CALLBACK_PORT = 54321
CALLBACK_PATH = "/callback"


class CallbackHandler(BaseHTTPRequestHandler):
    """Handle OAuth callback from devtarang.ai."""

    token: str | None = None

    def do_GET(self):
        """Handle GET request with token."""
        parsed = urlparse(self.path)

        if parsed.path == CALLBACK_PATH:
            query = parse_qs(parsed.query)

            if "token" in query:
                CallbackHandler.token = query["token"][0]
                self._send_success_response()
            elif "error" in query:
                self._send_error_response(query.get("error", ["Unknown error"])[0])
            else:
                self._send_error_response("No token received")
        else:
            self.send_response(404)
            self.end_headers()

    def _send_success_response(self):
        """Send success HTML response."""
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()

        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Tarang - Login Successful</title>
            <style>
                body {
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    height: 100vh;
                    margin: 0;
                    background: linear-gradient(135deg, #0a0a0a 0%, #1a1a2e 100%);
                    color: #fff;
                }
                .container {
                    text-align: center;
                    padding: 2rem;
                }
                h1 { color: #00d4ff; margin-bottom: 1rem; }
                p { color: #888; }
                .checkmark {
                    font-size: 4rem;
                    margin-bottom: 1rem;
                }
            </style>
        </head>
        <body>
            <div class="container">
                <div class="checkmark">✓</div>
                <h1>Login Successful!</h1>
                <p>You can close this window and return to your terminal.</p>
            </div>
        </body>
        </html>
        """
        self.wfile.write(html.encode())

    def _send_error_response(self, error: str):
        """Send error HTML response."""
        self.send_response(400)
        self.send_header("Content-Type", "text/html")
        self.end_headers()

        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Tarang - Login Failed</title>
            <style>
                body {{
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    height: 100vh;
                    margin: 0;
                    background: #0a0a0a;
                    color: #fff;
                }}
                .container {{ text-align: center; padding: 2rem; }}
                h1 {{ color: #ff4444; }}
                p {{ color: #888; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>Login Failed</h1>
                <p>{error}</p>
                <p>Please try again from your terminal.</p>
            </div>
        </body>
        </html>
        """
        self.wfile.write(html.encode())

    def log_message(self, format, *args):
        """Suppress HTTP server logs."""
        pass


async def run_login(verbose: bool = False):
    """Run the login flow via browser OAuth.

    Args:
        verbose: Enable verbose output
    """
    console.print("\n[bold cyan]Tarang Login[/bold cyan]\n")

    # Start local callback server
    server = HTTPServer(("localhost", CALLBACK_PORT), CallbackHandler)
    server_thread = threading.Thread(target=server.handle_request)
    server_thread.start()

    # Build auth URL
    callback_url = f"http://localhost:{CALLBACK_PORT}{CALLBACK_PATH}"
    auth_url = f"https://devtarang.ai/auth/cli?callback={callback_url}"

    console.print(
        Panel(
            "Opening browser for authentication...\n\n"
            f"If browser doesn't open, visit:\n[link]{auth_url}[/link]",
            border_style="cyan",
        )
    )

    # Open browser
    webbrowser.open(auth_url)

    # Wait for callback
    console.print("[dim]Waiting for authentication...[/dim]")
    server_thread.join(timeout=120)

    # Check result
    if CallbackHandler.token:
        # Save token to config
        config = load_config()
        config["api_key"] = CallbackHandler.token
        save_config(config)

        console.print(
            Panel(
                "[bold green]Login successful![/bold green]\n\n"
                "You can now use Tarang. Run [bold]tarang[/bold] to start.",
                border_style="green",
            )
        )
    else:
        console.print(
            "\n[red]Login failed or timed out.[/red]\n"
            "Please try again with [bold]tarang login[/bold]"
        )

    server.server_close()
