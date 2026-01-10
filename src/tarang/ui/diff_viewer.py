"""Diff viewer for previewing changes before applying."""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import Optional, List, Tuple

from rich.console import Console
from rich.syntax import Syntax
from rich.panel import Panel
from rich.text import Text


class DiffViewer:
    """View and manage diffs for file changes."""

    def __init__(self, console: Console):
        self.console = console

    def show_diff(
        self,
        file_path: str,
        original: str,
        modified: str,
        context_lines: int = 3,
    ):
        """Show a diff between original and modified content."""
        original_lines = original.splitlines(keepends=True)
        modified_lines = modified.splitlines(keepends=True)

        diff = difflib.unified_diff(
            original_lines,
            modified_lines,
            fromfile=f"a/{file_path}",
            tofile=f"b/{file_path}",
            n=context_lines,
        )

        diff_text = "".join(diff)
        if diff_text:
            syntax = Syntax(diff_text, "diff", theme="monokai", line_numbers=False)
            self.console.print(Panel(
                syntax,
                title=f"[bold]{file_path}[/bold]",
                border_style="yellow",
            ))
        else:
            self.console.print(f"[dim]No changes in {file_path}[/dim]")

    def show_new_file(self, file_path: str, content: str, max_lines: int = 20):
        """Show preview of a new file."""
        lines = content.split("\n")
        preview = "\n".join(lines[:max_lines])
        if len(lines) > max_lines:
            preview += f"\n... ({len(lines) - max_lines} more lines)"

        # Try to detect language from extension
        ext = Path(file_path).suffix.lstrip(".")
        lang_map = {
            "py": "python",
            "js": "javascript",
            "ts": "typescript",
            "tsx": "tsx",
            "jsx": "jsx",
            "json": "json",
            "yaml": "yaml",
            "yml": "yaml",
            "md": "markdown",
            "html": "html",
            "css": "css",
            "sh": "bash",
            "rs": "rust",
            "go": "go",
        }
        lang = lang_map.get(ext, "text")

        syntax = Syntax(preview, lang, theme="monokai", line_numbers=True)
        self.console.print(Panel(
            syntax,
            title=f"[bold green]+ {file_path}[/bold green] (new file)",
            border_style="green",
        ))

    def show_search_replace(
        self,
        file_path: str,
        search: str,
        replace: str,
        original_content: Optional[str] = None,
    ):
        """Show a search/replace preview."""
        content = Text()

        # Search section (what will be removed)
        content.append("───── Search (to be replaced) ─────\n", style="bold red")
        for line in search.split("\n")[:10]:
            content.append(f"- {line}\n", style="red")
        if len(search.split("\n")) > 10:
            content.append(f"... ({len(search.split(chr(10))) - 10} more lines)\n", style="dim")

        content.append("\n")

        # Replace section (what will be added)
        content.append("───── Replace (new content) ─────\n", style="bold green")
        for line in replace.split("\n")[:10]:
            content.append(f"+ {line}\n", style="green")
        if len(replace.split("\n")) > 10:
            content.append(f"... ({len(replace.split(chr(10))) - 10} more lines)\n", style="dim")

        self.console.print(Panel(
            content,
            title=f"[bold]{file_path}[/bold]",
            border_style="yellow",
        ))

    def create_inline_diff(
        self,
        original: str,
        modified: str,
    ) -> List[Tuple[str, str]]:
        """
        Create an inline diff showing changes line by line.

        Returns list of (line_content, style) tuples.
        """
        result = []
        matcher = difflib.SequenceMatcher(None, original.split("\n"), modified.split("\n"))

        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                for line in original.split("\n")[i1:i2]:
                    result.append((f"  {line}", "dim"))
            elif tag == "replace":
                for line in original.split("\n")[i1:i2]:
                    result.append((f"- {line}", "red"))
                for line in modified.split("\n")[j1:j2]:
                    result.append((f"+ {line}", "green"))
            elif tag == "delete":
                for line in original.split("\n")[i1:i2]:
                    result.append((f"- {line}", "red"))
            elif tag == "insert":
                for line in modified.split("\n")[j1:j2]:
                    result.append((f"+ {line}", "green"))

        return result
