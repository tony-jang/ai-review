"""Claude Code trigger engine — subprocess-based."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import TYPE_CHECKING
import uuid

from ai_review.trigger.base import TriggerEngine, TriggerResult

if TYPE_CHECKING:
    from ai_review.models import ModelConfig


class ClaudeCodeTrigger(TriggerEngine):
    """Trigger Claude Code via subprocess (claude -p)."""

    def __init__(self) -> None:
        self._close_wait_seconds = 2.0
        self._sessions: dict[str, str] = {}  # model_id -> cc session_id
        self._procs: set[asyncio.subprocess.Process] = set()

    async def create_session(self, model_id: str) -> str:
        """Create a CC session ID (each call is independent, no --resume)."""
        session_id = uuid.uuid4().hex[:12]
        self._sessions[model_id] = session_id
        return session_id

    async def send_prompt(
        self, client_session_id: str, model_id: str, prompt: str,
        *, model_config: ModelConfig | None = None,
    ) -> TriggerResult:
        """Run claude -p <prompt> with tool use enabled."""
        args = [
            "claude",
            "--print",
            "--output-format", "text",
            "--allowedTools", "Bash(curl:*) Read",
        ]
        if model_config and model_config.model_id:
            args.extend(["--model", model_config.model_id])
        args.extend(["-p", prompt])

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self._procs.add(proc)
            try:
                stdout, stderr = await proc.communicate()

                output = stdout.decode().strip()
                error = stderr.decode().strip()

                return TriggerResult(
                    success=proc.returncode == 0,
                    output=output,
                    error=error,
                    client_session_id=client_session_id,
                )
            finally:
                self._procs.discard(proc)
        except FileNotFoundError:
            return TriggerResult(
                success=False,
                error="claude CLI not found. Install Claude Code first.",
                client_session_id=client_session_id,
            )
        except Exception as e:
            return TriggerResult(
                success=False,
                error=str(e),
                client_session_id=client_session_id,
            )

    async def close(self) -> None:
        """Clean up sessions."""
        procs = list(self._procs)

        for proc in procs:
            if proc.returncode is not None:
                continue
            with suppress(ProcessLookupError):
                proc.terminate()

        for proc in procs:
            if proc.returncode is not None:
                continue
            with suppress(asyncio.TimeoutError, Exception):
                await asyncio.wait_for(proc.wait(), timeout=self._close_wait_seconds)

        for proc in procs:
            if proc.returncode is not None:
                continue
            with suppress(ProcessLookupError):
                proc.kill()

        for proc in procs:
            if proc.returncode is not None:
                continue
            with suppress(asyncio.TimeoutError, Exception):
                await asyncio.wait_for(proc.wait(), timeout=self._close_wait_seconds)

        self._procs.clear()
        self._sessions.clear()
