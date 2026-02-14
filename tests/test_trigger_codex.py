"""Tests for CodexTrigger."""

import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest

from ai_review.trigger.codex import CodexTrigger


class TestCreateSession:
    @pytest.mark.asyncio
    async def test_returns_session_id(self):
        trigger = CodexTrigger()
        sid = await trigger.create_session("codex-model")

        assert isinstance(sid, str)
        assert len(sid) == 12


class TestSendPrompt:
    @pytest.mark.asyncio
    async def test_first_prompt_uses_exec_json(self):
        """First prompt uses plain exec with json output."""
        trigger = CodexTrigger()

        captured_args = []

        async def fake_exec(*args, **kwargs):
            captured_args.extend(args)
            proc = AsyncMock()
            proc.communicate = AsyncMock(return_value=(b"review output", b""))
            proc.returncode = 0
            return proc

        with patch("ai_review.trigger.codex.asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await trigger.send_prompt("sess1", "codex-model", "do review")

        assert result.success is True
        assert result.output == "review output"
        assert result.client_session_id == "sess1"

        assert "codex" in captured_args
        assert "exec" in captured_args
        assert "--skip-git-repo-check" in captured_args
        assert "--json" in captured_args
        assert "--full-auto" in captured_args
        assert "-c" in captured_args
        assert "sandbox_workspace_write.network_access=true" in captured_args
        assert "do review" in captured_args

    @pytest.mark.asyncio
    async def test_followup_uses_resume_with_existing_session(self):
        trigger = CodexTrigger()
        trigger._sessions["codex-model"] = "123e4567-e89b-12d3-a456-426614174000"

        captured_args = []

        async def fake_exec(*args, **kwargs):
            captured_args.extend(args)
            proc = AsyncMock()
            proc.communicate = AsyncMock(return_value=(b"ok", b""))
            proc.returncode = 0
            return proc

        with patch("ai_review.trigger.codex.asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await trigger.send_prompt("sess1", "codex-model", "follow up")

        assert result.success is True
        assert "resume" in captured_args
        assert "123e4567-e89b-12d3-a456-426614174000" in captured_args
        assert "follow up" in captured_args

    @pytest.mark.asyncio
    async def test_handles_file_not_found(self):
        """send_prompt returns graceful error when codex CLI is missing."""
        trigger = CodexTrigger()

        async def raise_not_found(*args, **kwargs):
            raise FileNotFoundError("codex not found")

        with patch("ai_review.trigger.codex.asyncio.create_subprocess_exec", side_effect=raise_not_found):
            result = await trigger.send_prompt("sess1", "codex-model", "test")

        assert result.success is False
        assert "not found" in result.error
        assert result.client_session_id == "sess1"

    @pytest.mark.asyncio
    async def test_handles_process_failure(self):
        """send_prompt handles non-zero exit code."""
        trigger = CodexTrigger()

        async def fake_exec(*args, **kwargs):
            proc = AsyncMock()
            proc.communicate = AsyncMock(return_value=(b"", b"error occurred"))
            proc.returncode = 1
            return proc

        with patch("ai_review.trigger.codex.asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await trigger.send_prompt("sess1", "codex-model", "test")

        assert result.success is False
        assert result.error == "error occurred"

    @pytest.mark.asyncio
    async def test_times_out_and_kills_process(self):
        """send_prompt times out long-running codex process."""
        trigger = CodexTrigger(timeout_seconds=0.01)
        proc = AsyncMock()
        proc.returncode = 1
        proc.communicate = AsyncMock(side_effect=[asyncio.TimeoutError(), (b"", b"")])
        proc.kill = Mock()

        async def fake_exec(*args, **kwargs):
            return proc

        with patch("ai_review.trigger.codex.asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await trigger.send_prompt("sess1", "codex-model", "test")

        assert result.success is False
        assert "timed out" in (result.error or "")
        proc.kill.assert_called_once()


class TestClose:
    @pytest.mark.asyncio
    async def test_close_kills_stuck_process(self):
        trigger = CodexTrigger()
        proc = AsyncMock()
        proc.returncode = None
        proc.wait = AsyncMock(side_effect=[asyncio.TimeoutError(), asyncio.TimeoutError()])
        proc.terminate = Mock()
        proc.kill = Mock()
        trigger._procs.add(proc)

        await trigger.close()

        proc.terminate.assert_called_once()
        proc.kill.assert_called_once()
