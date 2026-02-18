"""Claude Code trigger engine — subprocess-based."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING
import uuid

from ai_review.trigger.base import TriggerEngine, TriggerResult

_ARV_DIR = str(Path(__file__).resolve().parent.parent / "bin")

if TYPE_CHECKING:
    from ai_review.models import ModelConfig

logger = logging.getLogger(__name__)


def _parse_arv_activity(cmd: str) -> tuple[str, str]:
    """Parse arv subcommand into a specific (action, target) pair.

    Examples:
        "arv get file /src/main.py" → ("arv_get_file", "/src/main.py")
        "arv report -n title -s high ..." → ("arv_report", "-n title -s high ...")
        "arv opinion iss-1 -a fix_required" → ("arv_opinion", "iss-1 -a fix_required")
    """
    parts = cmd.split()
    # parts[0] == "arv"
    if len(parts) < 2:
        return ("arv", cmd)
    sub = parts[1]
    if sub == "get" and len(parts) >= 3:
        resource = parts[2]
        rest = " ".join(parts[3:]) if len(parts) > 3 else ""
        return (f"arv_get_{resource}", rest)
    rest = " ".join(parts[2:]) if len(parts) > 2 else ""
    return (f"arv_{sub}", rest)


def _extract_activity(tool_name: str, tool_input: dict) -> tuple[str, str] | None:
    """Map a tool_use event to an (action, target) pair for the activity callback."""
    if tool_name == "Read":
        path = tool_input.get("file_path", "")
        return ("Read", path)
    if tool_name == "Grep":
        pattern = tool_input.get("pattern", "")
        return ("Grep", f"grep:{pattern}")
    if tool_name == "Glob":
        pattern = tool_input.get("pattern", "")
        return ("Glob", f"glob:{pattern}")
    if tool_name == "Bash":
        cmd = tool_input.get("command", "")
        if cmd.startswith("arv "):
            return _parse_arv_activity(cmd)
        return ("Bash", f"bash:{cmd[:80]}")
    return None


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
        """Run claude -p <prompt> with stream-json output for real-time activity tracking."""
        args = [
            "claude",
            "--print",
            "--output-format", "stream-json",
            "--verbose",
            "--allowedTools", "Bash(arv:*) Write Read Grep Glob",
        ]
        if model_config and model_config.model_id:
            args.extend(["--model", model_config.model_id])
        args.extend(["-p", prompt])

        try:
            env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
            env.update(self.env_vars)
            env["PATH"] = f"{_ARV_DIR}:{env.get('PATH', '')}"
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                limit=1024 * 1024,  # 1MB line buffer for large stream-json events
            )
            self._procs.add(proc)
            try:
                return await self._read_stream(proc, client_session_id)
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

    async def _read_stream(
        self, proc: asyncio.subprocess.Process, client_session_id: str,
    ) -> TriggerResult:
        """Parse stream-json output line by line, invoking on_activity for tool_use events."""
        stderr_task = asyncio.create_task(self._drain_stderr(proc))
        result_text = ""

        async for raw_line in proc.stdout:
            line = raw_line.decode().strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue

            event_type = event.get("type")

            # Extract tool_use from assistant messages
            if event_type == "assistant":
                for block in event.get("message", {}).get("content", []):
                    if block.get("type") == "tool_use":
                        tool_name = block.get("name", "")
                        tool_input = block.get("input", {})
                        activity = _extract_activity(tool_name, tool_input)
                        if activity and self.on_activity:
                            try:
                                self.on_activity(*activity)
                            except Exception:
                                pass  # Never break the stream for callback errors

            # Capture result text
            if event_type == "result":
                result_text = event.get("result", "")

        await proc.wait()
        stderr_output = await stderr_task

        return TriggerResult(
            success=proc.returncode == 0,
            output=result_text.strip() if isinstance(result_text, str) else str(result_text),
            error=stderr_output.strip(),
            client_session_id=client_session_id,
        )

    @staticmethod
    async def _drain_stderr(proc: asyncio.subprocess.Process) -> str:
        """Read stderr in the background to prevent pipe buffer deadlock."""
        data = await proc.stderr.read()
        return data.decode() if data else ""

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
