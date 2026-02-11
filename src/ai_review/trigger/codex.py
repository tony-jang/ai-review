"""Codex CLI trigger engine — subprocess-based."""

from __future__ import annotations

import asyncio
import uuid

from ai_review.trigger.base import TriggerEngine, TriggerResult


class CodexTrigger(TriggerEngine):
    """Trigger OpenAI Codex CLI via subprocess (codex exec --full-auto)."""

    def __init__(self) -> None:
        pass

    async def create_session(self, model_id: str) -> str:
        """Create a session ID (each codex exec call is independent)."""
        return uuid.uuid4().hex[:12]

    async def send_prompt(
        self, client_session_id: str, model_id: str, prompt: str
    ) -> TriggerResult:
        """Run codex exec --full-auto <prompt>."""
        args = [
            "codex", "exec",
            "--full-auto",
            "-c", "sandbox_workspace_write.network_access=true",
            prompt,
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()

            output = stdout.decode().strip()
            error = stderr.decode().strip()

            return TriggerResult(
                success=proc.returncode == 0,
                output=output,
                error=error,
                client_session_id=client_session_id,
            )
        except FileNotFoundError:
            return TriggerResult(
                success=False,
                error="codex CLI not found. Install Codex CLI first.",
                client_session_id=client_session_id,
            )
        except Exception as e:
            return TriggerResult(
                success=False,
                error=str(e),
                client_session_id=client_session_id,
            )

    async def close(self) -> None:
        pass
