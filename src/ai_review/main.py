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
    import os
    import threading

    from ai_review.server import create_app

    fastapi_app = create_app(port=port)

    typer.echo(f"Starting AI Review on http://localhost:{port}")

    log_config = uvicorn.config.LOGGING_CONFIG
    log_config["formatters"]["default"]["fmt"] = "%(asctime)s %(levelprefix)s %(message)s"
    log_config["formatters"]["default"]["datefmt"] = "%Y-%m-%d %H:%M:%S"
    log_config["formatters"]["access"]["fmt"] = '%(asctime)s %(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s'
    log_config["formatters"]["access"]["datefmt"] = "%Y-%m-%d %H:%M:%S"

    config = uvicorn.Config(
        fastapi_app,
        host="0.0.0.0",
        port=port,
        log_level="info",
        log_config=log_config,
        ws="websockets-sansio",
        timeout_graceful_shutdown=1,
    )
    server = uvicorn.Server(config)
    # Expose server on app state so the lifespan can install
    # loop-level signal handlers that survive signal.signal() overwrites.
    fastapi_app.state._uvicorn_server = server

    # Monkey-patch handle_exit so that on Ctrl+C:
    # 1) SSE clients are disconnected immediately (before uvicorn's drain)
    # 2) A 5-second force-exit timer starts (guarantees termination)
    _orig_handle_exit = server.handle_exit  # bound method
    _force_timer: threading.Timer | None = None

    def _handle_exit(sig: int, frame: object) -> None:
        nonlocal _force_timer
        # Disconnect all SSE clients so connections close quickly
        if hasattr(fastapi_app, "state") and hasattr(fastapi_app.state, "manager"):
            fastapi_app.state.manager.broker.disconnect_all()
        # Arm force-exit timer on first signal
        if _force_timer is None:
            _force_timer = threading.Timer(5.0, lambda: os._exit(1))
            _force_timer.daemon = True
            _force_timer.start()
        # Delegate to uvicorn's original handler (sets should_exit / force_exit)
        _orig_handle_exit(sig, frame)

    server.handle_exit = _handle_exit  # type: ignore[assignment]
    server.run()


if __name__ == "__main__":
    app()
