"""Tests for GeminiTrigger."""

import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest

from ai_review.trigger.gemini import GeminiTrigger


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

        async def fake_exec(*args, **kwargs):
            captured_args.extend(args)
            proc = AsyncMock()
            proc.communicate = AsyncMock(
                return_value=(b'{"session_id":"123e4567-e89b-12d3-a456-426614174000","text":"ok"}', b"")
            )
            proc.returncode = 0
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
        assert "default" in captured_args
        assert "--allowed-tools" in captured_args
        tools_idx = captured_args.index("--allowed-tools")
        tools_val = captured_args[tools_idx + 1]
        assert "run_shell_command(arv)" in tools_val
        assert "run_shell_command(curl)" in tools_val
        assert "-r" not in captured_args
        assert trigger._sessions["gemini1"] == "123e4567-e89b-12d3-a456-426614174000"

    @pytest.mark.asyncio
    async def test_followup_uses_resume(self):
        trigger = GeminiTrigger()
        trigger._sessions["gemini1"] = "123e4567-e89b-12d3-a456-426614174000"
        captured_args = []

        async def fake_exec(*args, **kwargs):
            captured_args.extend(args)
            proc = AsyncMock()
            proc.communicate = AsyncMock(return_value=(b'{"text":"ok"}', b""))
            proc.returncode = 0
            return proc

        with patch("ai_review.trigger.gemini.asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await trigger.send_prompt("sess1", "gemini1", "follow up")

        assert result.success is True
        assert "-r" in captured_args
        assert "123e4567-e89b-12d3-a456-426614174000" in captured_args
        assert "--approval-mode" in captured_args
        assert "default" in captured_args
        assert "--allowed-tools" in captured_args
        tools_idx = captured_args.index("--allowed-tools")
        tools_val = captured_args[tools_idx + 1]
        assert "run_shell_command(arv)" in tools_val
        assert "run_shell_command(curl)" in tools_val
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

        async def fake_exec(*args, **kwargs):
            proc = AsyncMock()
            proc.communicate = AsyncMock(return_value=(b"", b"error occurred"))
            proc.returncode = 1
            return proc

        with patch("ai_review.trigger.gemini.asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await trigger.send_prompt("sess1", "gemini1", "test")

        assert result.success is False
        assert result.error == "error occurred"

    @pytest.mark.asyncio
    async def test_times_out_and_kills_process(self):
        trigger = GeminiTrigger(timeout_seconds=0.01)
        proc = AsyncMock()
        proc.returncode = 1
        proc.communicate = AsyncMock(side_effect=[asyncio.TimeoutError(), (b"", b"")])
        proc.kill = Mock()

        async def fake_exec(*args, **kwargs):
            return proc

        with patch("ai_review.trigger.gemini.asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await trigger.send_prompt("sess1", "gemini1", "test")

        assert result.success is False
        assert "timed out" in (result.error or "")
        proc.kill.assert_called_once()


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
