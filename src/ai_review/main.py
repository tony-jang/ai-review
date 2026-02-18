"""CLI entrypoint for ai-review."""

from __future__ import annotations

import typer
import uvicorn

app = typer.Typer(name="ai-review", help="Multi-model AI code review orchestrator", invoke_without_command=True)


@app.callback(invoke_without_command=True)
def start(
    port: int = typer.Option(3000, help="Server port"),
) -> None:
    """Start the AI Review server."""
    from ai_review.server import create_app

    fastapi_app = create_app(port=port)

    typer.echo(f"Starting AI Review on http://localhost:{port}")

    log_config = uvicorn.config.LOGGING_CONFIG
    log_config["formatters"]["default"]["fmt"] = "%(asctime)s %(levelprefix)s %(message)s"
    log_config["formatters"]["default"]["datefmt"] = "%Y-%m-%d %H:%M:%S"
    log_config["formatters"]["access"]["fmt"] = '%(asctime)s %(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s'
    log_config["formatters"]["access"]["datefmt"] = "%Y-%m-%d %H:%M:%S"

    uvicorn.run(
        fastapi_app, host="0.0.0.0", port=port,
        log_level="info", log_config=log_config,
        ws="websockets-sansio",
        timeout_graceful_shutdown=1,
    )


if __name__ == "__main__":
    app()
