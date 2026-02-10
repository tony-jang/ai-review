"""CLI entrypoint for ai-review."""

from __future__ import annotations

import typer
import uvicorn

app = typer.Typer(name="ai-review", help="Multi-model AI code review orchestrator", invoke_without_command=True)


@app.callback(invoke_without_command=True)
def start(
    port: int = typer.Option(3000, help="Server port"),
    base: str = typer.Option("main", help="Base branch for diff"),
    repo: str = typer.Option(".", help="Repository path"),
) -> None:
    """Start the AI Review server."""
    from pathlib import Path

    repo_path = str(Path(repo).resolve())

    from ai_review.server import create_app

    fastapi_app = create_app(repo_path=repo_path)

    typer.echo(f"Starting AI Review on http://localhost:{port}")
    typer.echo(f"  Repo: {repo_path}")
    typer.echo(f"  Base: {base}")
    typer.echo(f"  MCP:  http://localhost:{port}/mcp")

    uvicorn.run(fastapi_app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    app()
