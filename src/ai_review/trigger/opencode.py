"""OpenCode trigger engine — subprocess-based with nd-JSON streaming."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shlex
import uuid
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING

from ai_review.trigger.base import TriggerEngine, TriggerResult
from ai_review.trigger.cc import _parse_arv_activity

_ARV_DIR = str(Path(__file__).resolve().parent.parent / "bin")

if TYPE_CHECKING:
    from ai_review.models import ModelConfig

logger = logging.getLogger(__name__)

_SESSION_ID_RE = re.compile(r"^ses_[A-Za-z0-9]+$")


def _extract_opencode_activity(tool_name: str, tool_input: dict) -> tuple[str, str] | None:
    """Map an OpenCode tool event to an (action, target) pair for the activity callback.

    OpenCode tool names are lowercase (e.g. "read", "bash", "grep", "glob").
    Input keys use camelCase (e.g. "filePath", "command", "pattern").
    """
    name = tool_name.lower()
    if name == "read":
        path = tool_input.get("filePath", tool_input.get("file_path", ""))
        return ("Read", path)
    if name == "grep":
        pattern = tool_input.get("pattern", "")
        return ("Grep", f"grep:{pattern}")
    if name == "glob":
        pattern = tool_input.get("pattern", "")
        return ("Glob", f"glob:{pattern}")
    if name == "bash":
        cmd = tool_input.get("command", "")
        # OpenCode may use full path (e.g. /…/bin/arv opinion …)
        first, _, rest = cmd.partition(" ")
        basename = first.rsplit("/", 1)[-1]
        if basename == "arv" and rest:
            return _parse_arv_activity(f"arv {rest}")
        if cmd.startswith("arv "):
            return _parse_arv_activity(cmd)
        return ("Bash", f"bash:{cmd[:80]}")
    return None


class OpenCodeTrigger(TriggerEngine):
    """Trigger OpenCode via subprocess (opencode run)."""

    def __init__(self, timeout_seconds: float = 600.0) -> None:
        self._close_wait_seconds = 2.0
        self._sessions: dict[str, str] = {}  # model_id -> opencode session id
        self._timeout_seconds = timeout_seconds
        self._procs: set[asyncio.subprocess.Process] = set()

    @staticmethod
    def _is_opencode_session_id(value: str) -> bool:
        """Return True if *value* looks like a real OpenCode session ID (ses_ + ULID)."""
        return bool(_SESSION_ID_RE.fullmatch(value or ""))

    async def create_session(self, model_id: str) -> str:
        """Create a placeholder session ID (replaced with real session ID after first prompt)."""
        sid = uuid.uuid4().hex[:12]
        self._sessions[model_id] = sid
        return sid

    async def send_prompt(
        self, client_session_id: str, model_id: str, prompt: str,
        *, model_config: ModelConfig | None = None,
    ) -> TriggerResult:
        """Run opencode run with nd-JSON output for real-time activity tracking."""
        session_id = self._sessions.get(model_id, "")
        is_resume = self._is_opencode_session_id(session_id)

        args = ["opencode", "run", "--format", "json"]

        if is_resume:
            args.extend(["--session", session_id])
        elif model_config and model_config.model_id:
            provider = model_config.provider or ""
            model_spec = model_config.model_id
            # OpenCode CLI expects provider/model format (parseModel splits on "/")
            if provider:
                args.extend(["--model", f"{provider}/{model_spec}"])
            else:
                args.extend(["--model", model_spec])

        args.append(prompt)
        command_str = shlex.join(args)

        try:
            env = dict(os.environ)
            env.update(self.env_vars)
            env["PATH"] = f"{_ARV_DIR}:{env.get('PATH', '')}"
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                start_new_session=True,
                limit=1024 * 1024,  # 1MB line buffer
            )
            self._procs.add(proc)
            try:
                result = await asyncio.wait_for(
                    self._read_stream(proc, client_session_id, model_id, is_resume),
                    timeout=self._timeout_seconds,
                )
                result.command = command_str
                return result
            except asyncio.TimeoutError:
                with suppress(ProcessLookupError):
                    proc.kill()
                with suppress(asyncio.TimeoutError, Exception):
                    await asyncio.wait_for(proc.wait(), timeout=self._close_wait_seconds)
                return TriggerResult(
                    success=False,
                    error=f"opencode CLI timed out after {int(self._timeout_seconds)}s",
                    client_session_id=client_session_id,
                    command=command_str,
                )
            finally:
                self._procs.discard(proc)
        except FileNotFoundError:
            return TriggerResult(
                success=False,
                error="opencode CLI not found. Install OpenCode first.",
                client_session_id=client_session_id,
                command=command_str,
            )
        except Exception as e:
            return TriggerResult(
                success=False,
                error=str(e),
                client_session_id=client_session_id,
                command=command_str,
            )

    async def _read_stream(
        self, proc: asyncio.subprocess.Process, client_session_id: str,
        model_id: str, is_resume: bool,
    ) -> TriggerResult:
        """Parse nd-JSON output line by line, invoking on_activity for tool events."""
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

            # Extract session ID from first event
            if not is_resume:
                extracted = event.get("sessionID", "")
                if extracted and model_id and self._is_opencode_session_id(extracted):
                    self._sessions[model_id] = extracted
                    is_resume = True  # stop extracting after first

            # text event → accumulate result (content lives in event.part.text)
            if event_type == "text":
                part = event.get("part", {})
                result_text += part.get("text", "") if isinstance(part, dict) else ""

            # tool_use event → fire activity callback
            # tool name: event.part.tool (lowercase), input: event.part.state.input
            if event_type == "tool_use":
                part = event.get("part", {})
                tool_name = part.get("tool", "")
                state = part.get("state", {})
                tool_input = state.get("input", {}) if isinstance(state, dict) else {}
                if tool_name and self.on_activity:
                    activity = _extract_opencode_activity(tool_name, tool_input)
                    if activity:
                        try:
                            self.on_activity(*activity)
                        except Exception:
                            pass  # Never break the stream for callback errors

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
