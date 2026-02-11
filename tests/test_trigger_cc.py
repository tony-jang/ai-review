"""Tests for ClaudeCodeTrigger."""

from unittest.mock import AsyncMock, patch

import pytest

from ai_review.trigger.cc import ClaudeCodeTrigger


class TestCreateSession:
    @pytest.mark.asyncio
    async def test_returns_session_id(self):
        trigger = ClaudeCodeTrigger()
        sid = await trigger.create_session("opus")

        assert isinstance(sid, str)
        assert len(sid) == 12
        assert trigger._sessions["opus"] == sid

        await trigger.close()


class TestSendPrompt:
    @pytest.mark.asyncio
    async def test_args_no_mcp_config(self):
        """send_prompt does not pass --mcp-config flag."""
        trigger = ClaudeCodeTrigger()

        captured_args = []

        async def fake_exec(*args, **kwargs):
            captured_args.extend(args)
            proc = AsyncMock()
            proc.communicate = AsyncMock(return_value=(b"ok", b""))
            proc.returncode = 0
            return proc

        with patch("ai_review.trigger.cc.asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await trigger.send_prompt("sess1", "opus", "do review")

        assert result.success is True
        assert result.client_session_id == "sess1"

        # Verify args structure
        assert "claude" in captured_args
        assert "-p" in captured_args
        assert "do review" in captured_args
        assert "--print" in captured_args
        assert "--allowedTools" in captured_args
        assert "Bash(curl:*) Read" in captured_args
        # No --mcp-config
        assert "--mcp-config" not in captured_args
        # No --resume
        assert "--resume" not in captured_args

        await trigger.close()

    @pytest.mark.asyncio
    async def test_handles_file_not_found(self):
        """send_prompt returns graceful error when claude CLI is missing."""
        trigger = ClaudeCodeTrigger()

        async def raise_not_found(*args, **kwargs):
            raise FileNotFoundError("claude not found")

        with patch("ai_review.trigger.cc.asyncio.create_subprocess_exec", side_effect=raise_not_found):
            result = await trigger.send_prompt("sess1", "opus", "test")

        assert result.success is False
        assert "not found" in result.error
        assert result.client_session_id == "sess1"

        await trigger.close()

    @pytest.mark.asyncio
    async def test_handles_process_failure(self):
        """send_prompt handles non-zero exit code."""
        trigger = ClaudeCodeTrigger()

        async def fake_exec(*args, **kwargs):
            proc = AsyncMock()
            proc.communicate = AsyncMock(return_value=(b"", b"error occurred"))
            proc.returncode = 1
            return proc

        with patch("ai_review.trigger.cc.asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await trigger.send_prompt("sess1", "opus", "test")

        assert result.success is False
        assert result.error == "error occurred"

        await trigger.close()
