"""Tarang quick ask - single question mode."""

import httpx
from rich.console import Console
from rich.markdown import Markdown

from tarang.core.config import load_config, get_api_base_url

console = Console()


async def quick_ask(query: str, verbose: bool = False):
    """Ask a quick question without starting a full session.

    Args:
        query: The question to ask
        verbose: Enable verbose output
    """
    config = load_config()

    if not config.get("openrouter_key") and not config.get("api_key"):
        console.print(
            "[yellow]No API key configured.[/yellow] "
            "Run [bold]tarang init[/bold] first."
        )
        return

    headers = {"Content-Type": "application/json"}
    if api_key := config.get("api_key"):
        headers["Authorization"] = f"Bearer {api_key}"
    if openrouter_key := config.get("openrouter_key"):
        headers["X-OpenRouter-Key"] = openrouter_key

    console.print(f"\n[dim]Asking: {query}[/dim]\n")

    async with httpx.AsyncClient(
        base_url=get_api_base_url(),
        timeout=60.0,
        headers=headers,
    ) as client:
        try:
            response = await client.post(
                "/v2/quick",
                json={"query": query},
            )
            response.raise_for_status()
            data = response.json()

            if "answer" in data:
                md = Markdown(data["answer"])
                console.print(md)
            elif "error" in data:
                console.print(f"[red]Error:[/red] {data['error']}")

        except httpx.HTTPError as e:
            console.print(f"[red]Request failed:[/red] {e}")
