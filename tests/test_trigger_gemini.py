"""Tests for GeminiTrigger."""

import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest

from ai_review.trigger.gemini import GeminiTrigger


def _make_async_lines(lines: list[bytes]):
    """Create an async iterator that yields lines."""
    async def _iter():
        for line in lines:
            yield line
    return _iter()


def _make_proc(*, stdout_lines: list[bytes], stderr_lines: list[bytes], returncode: int = 0):
    """Build a mock process with async-iterable stdout/stderr."""
    proc = AsyncMock()
    proc.stdout = _make_async_lines(stdout_lines)
    proc.stderr = _make_async_lines(stderr_lines)
    proc.returncode = returncode
    proc.wait = AsyncMock(return_value=returncode)
    proc.kill = Mock()
    proc.communicate = AsyncMock(return_value=(b"", b""))
    return proc


class TestCreateSession:
    @pytest.mark.asyncio
    async def test_returns_session_id(self):
        trigger = GeminiTrigger()
        sid = await trigger.create_session("gemini1")

        assert isinstance(sid, str)
        assert len(sid) == 12


class TestSendPrompt:
    @pytest.mark.asyncio
    async def test_first_prompt_uses_plain_mode(self):
        trigger = GeminiTrigger()
        captured_args = []

        proc = _make_proc(
            stdout_lines=[b'{"session_id":"123e4567-e89b-12d3-a456-426614174000","text":"ok"}\n'],
            stderr_lines=[],
        )

        async def fake_exec(*args, **kwargs):
            captured_args.extend(args)
            return proc

        with patch("ai_review.trigger.gemini.asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await trigger.send_prompt("sess1", "gemini1", "review me")

        assert result.success is True
        assert result.client_session_id == "sess1"
        assert "gemini" in captured_args
        assert "-p" in captured_args
        assert "review me" in captured_args
        assert "--output-format" in captured_args
        assert "json" in captured_args
        assert "--approval-mode" in captured_args
        assert "yolo" in captured_args
        assert "--allowed-tools" in captured_args
        tools_idx = captured_args.index("--allowed-tools")
        assert captured_args[tools_idx + 1] == "run_shell_command(arv)"
        assert captured_args[tools_idx + 2] == "--allowed-tools"
        assert captured_args[tools_idx + 3] == "run_shell_command(curl)"
        assert "-r" not in captured_args
        assert trigger._sessions["gemini1"] == "123e4567-e89b-12d3-a456-426614174000"

    @pytest.mark.asyncio
    async def test_followup_uses_resume(self):
        trigger = GeminiTrigger()
        trigger._sessions["gemini1"] = "123e4567-e89b-12d3-a456-426614174000"
        captured_args = []

        proc = _make_proc(
            stdout_lines=[b'{"text":"ok"}\n'],
            stderr_lines=[],
        )

        async def fake_exec(*args, **kwargs):
            captured_args.extend(args)
            return proc

        with patch("ai_review.trigger.gemini.asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await trigger.send_prompt("sess1", "gemini1", "follow up")

        assert result.success is True
        assert "-r" in captured_args
        assert "123e4567-e89b-12d3-a456-426614174000" in captured_args
        assert "--approval-mode" in captured_args
        assert "yolo" in captured_args
        assert "--allowed-tools" in captured_args
        tools_idx = captured_args.index("--allowed-tools")
        assert captured_args[tools_idx + 1] == "run_shell_command(arv)"
        assert captured_args[tools_idx + 2] == "--allowed-tools"
        assert captured_args[tools_idx + 3] == "run_shell_command(curl)"
        assert "follow up" in captured_args

    @pytest.mark.asyncio
    async def test_handles_file_not_found(self):
        trigger = GeminiTrigger()

        async def raise_not_found(*args, **kwargs):
            raise FileNotFoundError("gemini not found")

        with patch("ai_review.trigger.gemini.asyncio.create_subprocess_exec", side_effect=raise_not_found):
            result = await trigger.send_prompt("sess1", "gemini1", "test")

        assert result.success is False
        assert "not found" in result.error

    @pytest.mark.asyncio
    async def test_handles_process_failure(self):
        trigger = GeminiTrigger()

        proc = _make_proc(
            stdout_lines=[],
            stderr_lines=[b"error occurred\n"],
            returncode=1,
        )

        async def fake_exec(*args, **kwargs):
            return proc

        with patch("ai_review.trigger.gemini.asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await trigger.send_prompt("sess1", "gemini1", "test")

        assert result.success is False
        assert result.error == "error occurred"

    @pytest.mark.asyncio
    async def test_times_out_and_kills_process(self):
        trigger = GeminiTrigger(timeout_seconds=0.01)

        async def slow_stdout():
            await asyncio.sleep(10)
            yield b""

        async def slow_stderr():
            await asyncio.sleep(10)
            yield b""

        proc = AsyncMock()
        proc.stdout = slow_stdout()
        proc.stderr = slow_stderr()
        proc.returncode = 1
        proc.kill = Mock()
        proc.wait = AsyncMock(return_value=1)
        proc.communicate = AsyncMock(return_value=(b"", b""))

        async def fake_exec(*args, **kwargs):
            return proc

        with patch("ai_review.trigger.gemini.asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await trigger.send_prompt("sess1", "gemini1", "test")

        assert result.success is False
        assert "timed out" in (result.error or "")

    @pytest.mark.asyncio
    async def test_fatal_stderr_kills_immediately(self):
        """When a fatal pattern appears in stderr, the process is killed immediately."""
        trigger = GeminiTrigger()

        proc = _make_proc(
            stdout_lines=[],
            stderr_lines=[b"Error executing tool run_shell_command: Tool execution denied by policy.\n"],
            returncode=1,
        )

        async def fake_exec(*args, **kwargs):
            return proc

        with patch("ai_review.trigger.gemini.asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await trigger.send_prompt("sess1", "gemini1", "test")

        assert result.success is False
        assert "Tool execution denied by policy" in result.error
        proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_capacity_error_kills_after_grace_period(self):
        """Capacity error starts grace timer; process killed when timer expires."""
        trigger = GeminiTrigger(capacity_timeout_seconds=0.05)

        async def slow_stdout():
            await asyncio.sleep(10)
            yield b""

        proc = AsyncMock()
        proc.stdout = slow_stdout()
        proc.stderr = _make_async_lines([b"RESOURCE_EXHAUSTED: No capacity available for model\n"])
        proc.returncode = 1
        proc.wait = AsyncMock(return_value=1)
        proc.kill = Mock()
        proc.communicate = AsyncMock(return_value=(b"", b""))

        async def fake_exec(*args, **kwargs):
            return proc

        with patch("ai_review.trigger.gemini.asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await trigger.send_prompt("sess1", "gemini1", "test")

        assert result.success is False
        assert "capacity" in result.error.lower()
        proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_capacity_error_recovers_before_grace_period(self):
        """CLI recovers from capacity error before grace period expires — success."""
        trigger = GeminiTrigger(capacity_timeout_seconds=5.0)

        proc = _make_proc(
            stdout_lines=[b'{"text":"recovered ok"}\n'],
            stderr_lines=[b"RESOURCE_EXHAUSTED: No capacity available\n", b"Retrying...\n"],
            returncode=0,
        )

        async def fake_exec(*args, **kwargs):
            return proc

        with patch("ai_review.trigger.gemini.asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await trigger.send_prompt("sess1", "gemini1", "test")

        assert result.success is True
        assert result.output == '{"text":"recovered ok"}'


class TestClose:
    @pytest.mark.asyncio
    async def test_close_kills_stuck_process(self):
        trigger = GeminiTrigger()
        proc = AsyncMock()
        proc.returncode = None
        proc.wait = AsyncMock(side_effect=[asyncio.TimeoutError(), asyncio.TimeoutError()])
        proc.terminate = Mock()
        proc.kill = Mock()
        trigger._procs.add(proc)

        await trigger.close()

        proc.terminate.assert_called_once()
        proc.kill.assert_called_once()
