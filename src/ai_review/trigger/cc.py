"""Claude Code trigger engine — subprocess-based."""

from __future__ import annotations

import asyncio
import uuid

from ai_review.trigger.base import TriggerEngine, TriggerResult


class ClaudeCodeTrigger(TriggerEngine):
    """Trigger Claude Code via subprocess (claude --print --resume)."""

    def __init__(self, mcp_server_url: str = "http://localhost:3000/mcp") -> None:
        self.mcp_server_url = mcp_server_url
        self._sessions: dict[str, str] = {}  # model_id -> cc session_id

    async def create_session(self, model_id: str) -> str:
        """Create a CC session by running an initial prompt."""
        session_id = uuid.uuid4().hex[:12]
        self._sessions[model_id] = session_id
        return session_id

    async def send_prompt(
        self, client_session_id: str, model_id: str, prompt: str
    ) -> TriggerResult:
        """Run claude --print --resume with the given prompt."""
        args = [
            "claude",
            "--print",
            "--output-format", "text",
            "--resume", client_session_id,
            "--",
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
        """No cleanup needed for subprocess-based trigger."""
        self._sessions.clear()
