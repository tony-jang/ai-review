"""Gemini CLI trigger engine — subprocess-based with resume support."""

from __future__ import annotations

import asyncio
import json
import os
import logging
import re
import shlex
import uuid
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)

from ai_review.trigger.base import TriggerEngine, TriggerResult

_ARV_DIR = str(Path(__file__).resolve().parent.parent / "bin")

if TYPE_CHECKING:
    from ai_review.models import ModelConfig


_FATAL_PATTERNS = re.compile(
    r"|".join([
        r"Tool execution denied by policy",
        r"Cannot use both a positional prompt and the --prompt",
    ]),
)

_CAPACITY_PATTERNS = re.compile(
    r"|".join([
        r"RESOURCE_EXHAUSTED",
        r"No capacity available",
        r"exhausted your capacity",
    ]),
)


class GeminiTrigger(TriggerEngine):
    """Trigger Gemini CLI via subprocess and resume existing sessions."""

    def __init__(
        self, timeout_seconds: float = 600.0, capacity_timeout_seconds: float = 90.0,
    ) -> None:
        self._close_wait_seconds = 2.0
        self._sessions: dict[str, str] = {}  # model_id -> gemini session id
        self._timeout_seconds = timeout_seconds
        self._capacity_timeout_seconds = capacity_timeout_seconds
        self._procs: set[asyncio.subprocess.Process] = set()

    async def create_session(self, model_id: str) -> str:
        """Create a local placeholder session id."""
        return uuid.uuid4().hex[:12]

    async def send_prompt(
        self, client_session_id: str, model_id: str, prompt: str,
        *, model_config: ModelConfig | None = None,
    ) -> TriggerResult:
        """Run gemini prompt, resuming with -r when a session id is known."""
        base_args = [
            "gemini",
            "--approval-mode",
            "yolo",
            "--allowed-tools", "run_shell_command(arv)",
            "--allowed-tools", "run_shell_command(curl)",
        ]
        if model_config and model_config.model_id:
            base_args.extend(["--model", model_config.model_id])
        resume_session = self._sessions.get(model_id, "")
        if resume_session:
            args = [
                *base_args,
                "-r",
                resume_session,
                "-p",
                prompt,
                "--output-format",
                "json",
            ]
        else:
            args = [
                *base_args,
                "-p",
                prompt,
                "--output-format",
                "json",
            ]

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
            )
            self._procs.add(proc)
            try:
                try:
                    result = await asyncio.wait_for(
                        self._read_with_early_fail(proc, client_session_id),
                        timeout=self._timeout_seconds,
                    )
                except asyncio.TimeoutError:
                    with suppress(ProcessLookupError):
                        proc.kill()
                    with suppress(asyncio.TimeoutError, Exception):
                        await asyncio.wait_for(
                            proc.communicate(),
                            timeout=self._close_wait_seconds,
                        )
                    return TriggerResult(
                        success=False,
                        error=f"gemini CLI timed out after {int(self._timeout_seconds)}s",
                        client_session_id=client_session_id,
                        command=command_str,
                    )

                if result.success and not resume_session:
                    extracted = self._extract_session_id(result.output or "")
                    if extracted:
                        self._sessions[model_id] = extracted

                result.command = command_str
                return result
            finally:
                self._procs.discard(proc)
        except FileNotFoundError:
            return TriggerResult(
                success=False,
                error="gemini CLI not found. Install Gemini CLI first.",
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

    async def _read_with_early_fail(
        self, proc: asyncio.subprocess.Process, client_session_id: str,
    ) -> TriggerResult:
        """Read stdout/stderr concurrently; kill immediately on fatal stderr patterns."""
        stdout_chunks: list[bytes] = []
        stderr_chunks: list[bytes] = []
        fatal_error: str | None = None
        capacity_timer: asyncio.Task[None] | None = None

        async def _capacity_grace() -> None:
            """Wait for grace period, then kill the process."""
            nonlocal fatal_error
            await asyncio.sleep(self._capacity_timeout_seconds)
            fatal_error = "capacity error not recovered within grace period"
            logger.warning("gemini: capacity grace period expired, killing process")
            with suppress(ProcessLookupError):
                proc.kill()

        async def _drain_stdout() -> None:
            assert proc.stdout is not None
            async for chunk in proc.stdout:
                stdout_chunks.append(chunk)

        async def _drain_stderr() -> None:
            nonlocal fatal_error, capacity_timer
            assert proc.stderr is not None
            async for line in proc.stderr:
                stderr_chunks.append(line)
                text = line.decode(errors="replace")
                if _FATAL_PATTERNS.search(text):
                    fatal_error = text.strip()
                    logger.warning("gemini: fatal error detected, killing process: %s", fatal_error)
                    with suppress(ProcessLookupError):
                        proc.kill()
                    return
                if _CAPACITY_PATTERNS.search(text) and capacity_timer is None:
                    logger.warning("gemini: capacity error detected, starting grace period (%ss): %s",
                                   self._capacity_timeout_seconds, text.strip())
                    capacity_timer = asyncio.create_task(_capacity_grace())

        stdout_task = asyncio.create_task(_drain_stdout())
        stderr_task = asyncio.create_task(_drain_stderr())

        await stderr_task
        if fatal_error:
            if capacity_timer is not None:
                capacity_timer.cancel()
                with suppress(asyncio.CancelledError):
                    await capacity_timer
            stdout_task.cancel()
            with suppress(asyncio.CancelledError):
                await stdout_task
            await proc.wait()
            return TriggerResult(
                success=False,
                error=fatal_error,
                client_session_id=client_session_id,
            )

        await stdout_task
        if capacity_timer is not None:
            capacity_timer.cancel()
            with suppress(asyncio.CancelledError):
                await capacity_timer
        await proc.wait()

        output = b"".join(stdout_chunks).decode().strip()
        error = b"".join(stderr_chunks).decode().strip()
        return TriggerResult(
            success=proc.returncode == 0,
            output=output,
            error=error,
            client_session_id=client_session_id,
        )

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
    def _extract_session_id(output: str) -> str:
        """Best-effort extraction from JSON output or free text."""
        try:
            obj = json.loads(output)
            sid = GeminiTrigger._find_session_id_in_json(obj)
            if sid:
                return sid
        except Exception:
            pass

        # Fallback: UUID-like token in output
        m = re.search(r"\b([0-9a-fA-F]{8}-[0-9a-fA-F-]{27})\b", output or "")
        if m:
            return m.group(1)
        return ""

    @staticmethod
    def _find_session_id_in_json(obj: Any) -> str:
        if isinstance(obj, dict):
            for k in ("session_id", "sessionId", "session", "id"):
                v = obj.get(k)
                if isinstance(v, str) and re.fullmatch(r"[0-9a-fA-F-]{8,64}", v):
                    return v
            for v in obj.values():
                sid = GeminiTrigger._find_session_id_in_json(v)
                if sid:
                    return sid
        elif isinstance(obj, list):
            for v in obj:
                sid = GeminiTrigger._find_session_id_in_json(v)
                if sid:
                    return sid
        return ""
