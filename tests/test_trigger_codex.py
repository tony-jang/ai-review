"""Tests for CodexTrigger."""

from unittest.mock import AsyncMock, patch

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
    async def test_args_include_full_auto(self):
        """send_prompt passes --full-auto and prompt correctly."""
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
        assert "--full-auto" in captured_args
        assert "-c" in captured_args
        assert "sandbox_workspace_write.network_access=true" in captured_args
        assert "do review" in captured_args

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


class TestClose:
    @pytest.mark.asyncio
    async def test_close_is_noop(self):
        """close() does nothing (no MCP to unregister)."""
        trigger = CodexTrigger()
        # Should not raise
        await trigger.close()
