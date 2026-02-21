"""Codex CLI trigger engine — subprocess-based with JSONL streaming."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shlex
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING
import uuid

from ai_review.trigger.base import TriggerEngine, TriggerResult
from ai_review.trigger.cc import _parse_arv_activity

_ARV_DIR = str(Path(__file__).resolve().parent.parent / "bin")

if TYPE_CHECKING:
    from ai_review.models import ModelConfig

logger = logging.getLogger(__name__)


def _extract_codex_activity(command: str) -> tuple[str, str] | None:
    """Parse a Codex command_execution command string into (action, target).

    Codex wraps commands as ``/bin/zsh -lc "..."``; we extract the inner
    command and map it to the same action labels used by ClaudeCodeTrigger.
    """
    # Unwrap shell wrapper: /bin/zsh -lc "inner command"
    inner = command
    try:
        parts = shlex.split(command)
        if len(parts) >= 3 and parts[-2] == "-lc":
            inner = parts[-1]
    except ValueError:
        pass  # malformed quoting — use raw command

    # Match known tool patterns
    tokens = inner.split()
    if not tokens:
        return None
    first = tokens[0].rsplit("/", 1)[-1]  # basename

    if first in ("cat", "head", "tail"):
        path = tokens[-1] if len(tokens) > 1 else ""
        return ("Read", path)
    if first in ("rg", "grep"):
        # Best-effort pattern extraction: first non-flag positional argument,
        # skipping values consumed by known option flags
        _VALUE_FLAGS = {
            "-t", "-g", "-m", "-e", "-f", "-A", "-B", "-C",
            "--type", "--glob", "--max-count", "--regexp", "--file",
            "--after-context", "--before-context", "--context",
        }
        pattern = ""
        skip_next = False
        for t in tokens[1:]:
            if skip_next:
                skip_next = False
                continue
            if t in _VALUE_FLAGS:
                skip_next = True
                continue
            if t.startswith("-"):
                continue
            pattern = t
            break
        return ("Grep", f"grep:{pattern}")
    if first in ("find", "ls"):
        path = tokens[1] if len(tokens) > 1 else "."
        return ("Glob", f"glob:{path}")
    if first == "arv":
        return _parse_arv_activity(inner)
    # Default: treat as Bash
    return ("Bash", f"bash:{inner[:80]}")


class CodexTrigger(TriggerEngine):
    """Trigger OpenAI Codex CLI via subprocess and resume existing sessions."""

    def __init__(self, timeout_seconds: float = 600.0) -> None:
        self._close_wait_seconds = 2.0
        self._sessions: dict[str, str] = {}  # model_id -> codex session id
        self._timeout_seconds = timeout_seconds
        self._procs: set[asyncio.subprocess.Process] = set()

    async def create_session(self, model_id: str) -> str:
        """Create a local placeholder session id."""
        sid = uuid.uuid4().hex[:12]
        self._sessions.setdefault(model_id, sid)
        return sid

    async def send_prompt(
        self, client_session_id: str, model_id: str, prompt: str,
        *, model_config: ModelConfig | None = None,
    ) -> TriggerResult:
        """Run codex exec for first message, then codex exec resume for follow-ups."""
        session_id = self._sessions.get(model_id, "")
        is_resume = self._looks_like_uuid(session_id)

        if is_resume:
            args = [
                "codex", "exec",
                "--skip-git-repo-check",
                "resume", session_id,
                "--full-auto",
                "-c", "sandbox_workspace_write.network_access=true",
                prompt,
            ]
        else:
            args = [
                "codex", "exec",
                "--skip-git-repo-check",
                "--full-auto",
                "--json",
                "-c", "sandbox_workspace_write.network_access=true",
            ]
            if model_config and model_config.model_id:
                args.extend(["--model", model_config.model_id])
            args.append(prompt)

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
                return await asyncio.wait_for(
                    self._read_stream(proc, client_session_id, model_id, is_resume),
                    timeout=self._timeout_seconds,
                )
            except asyncio.TimeoutError:
                with suppress(ProcessLookupError):
                    proc.kill()
                with suppress(asyncio.TimeoutError, Exception):
                    await asyncio.wait_for(proc.wait(), timeout=self._close_wait_seconds)
                return TriggerResult(
                    success=False,
                    error=f"codex CLI timed out after {int(self._timeout_seconds)}s",
                    client_session_id=client_session_id,
                )
            finally:
                self._procs.discard(proc)
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

    async def _read_stream(
        self,
        proc: asyncio.subprocess.Process,
        client_session_id: str,
        model_id: str,
        is_resume: bool,
    ) -> TriggerResult:
        """Parse JSONL output line by line, invoking on_activity for command_execution events."""
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
            item = event.get("item", {})
            item_type = item.get("type")

            # Extract thread_id from thread.started (first prompt only)
            if event_type == "thread.started" and not is_resume:
                thread_id = event.get("thread_id", "")
                if thread_id:
                    self._sessions[model_id] = thread_id

            # command_execution started → fire activity callback
            if event_type == "item.started" and item_type == "command_execution":
                command = item.get("command", "")
                if command and self.on_activity:
                    activity = _extract_codex_activity(command)
                    if activity:
                        try:
                            self.on_activity(*activity)
                        except Exception:
                            pass  # Never break the stream for callback errors

            # Capture last agent_message as result text
            if event_type == "item.completed" and item_type == "agent_message":
                result_text = item.get("text", "")

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

    @staticmethod
    def _looks_like_uuid(value: str) -> bool:
        return bool(re.fullmatch(r"[0-9a-fA-F-]{32,36}", value or ""))
