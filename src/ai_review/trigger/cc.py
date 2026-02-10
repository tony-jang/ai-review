"""Claude Code trigger engine — subprocess-based."""

from __future__ import annotations

import asyncio
import json
import tempfile
import uuid
from pathlib import Path

from ai_review.trigger.base import TriggerEngine, TriggerResult


class ClaudeCodeTrigger(TriggerEngine):
    """Trigger Claude Code via subprocess (claude -p with --mcp-config)."""

    def __init__(self, mcp_server_url: str = "http://localhost:3000/mcp") -> None:
        self.mcp_server_url = mcp_server_url
        self._sessions: dict[str, str] = {}  # model_id -> cc session_id
        self._mcp_config_path: Path | None = None
        self._tmp_dir: tempfile.TemporaryDirectory | None = None

    def _ensure_mcp_config(self) -> Path:
        """Create a temp MCP config file pointing to the ai-review server."""
        if self._mcp_config_path and self._mcp_config_path.exists():
            return self._mcp_config_path

        self._tmp_dir = tempfile.TemporaryDirectory(prefix="ai-review-")
        config_path = Path(self._tmp_dir.name) / "mcp.json"
        config = {
            "mcpServers": {
                "ai-review": {
                    "url": self.mcp_server_url,
                }
            }
        }
        config_path.write_text(json.dumps(config))
        self._mcp_config_path = config_path
        return config_path

    async def create_session(self, model_id: str) -> str:
        """Create a CC session ID (each call is independent, no --resume)."""
        session_id = uuid.uuid4().hex[:12]
        self._sessions[model_id] = session_id
        return session_id

    async def send_prompt(
        self, client_session_id: str, model_id: str, prompt: str
    ) -> TriggerResult:
        """Run claude --print -p <prompt> --mcp-config <path>."""
        mcp_config = self._ensure_mcp_config()

        args = [
            "claude",
            "--print",
            "--output-format", "text",
            "--mcp-config", str(mcp_config),
            "-p", prompt,
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
        """Clean up temp files and sessions."""
        self._sessions.clear()
        if self._tmp_dir:
            self._tmp_dir.cleanup()
            self._tmp_dir = None
            self._mcp_config_path = None
