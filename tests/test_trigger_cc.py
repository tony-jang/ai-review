"""Tests for ClaudeCodeTrigger."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from ai_review.trigger.cc import ClaudeCodeTrigger


class TestMcpConfig:
    def test_ensure_mcp_config_creates_file(self):
        trigger = ClaudeCodeTrigger(mcp_server_url="http://localhost:9000/mcp")
        path = trigger._ensure_mcp_config()

        assert path.exists()
        config = json.loads(path.read_text())
        assert config["mcpServers"]["ai-review"]["url"] == "http://localhost:9000/mcp"

        trigger._tmp_dir.cleanup()

    def test_ensure_mcp_config_idempotent(self):
        trigger = ClaudeCodeTrigger(mcp_server_url="http://localhost:9000/mcp")
        path1 = trigger._ensure_mcp_config()
        path2 = trigger._ensure_mcp_config()

        assert path1 == path2

        trigger._tmp_dir.cleanup()

    @pytest.mark.asyncio
    async def test_close_cleans_up(self):
        trigger = ClaudeCodeTrigger(mcp_server_url="http://localhost:9000/mcp")
        trigger._ensure_mcp_config()

        await trigger.close()

        assert trigger._mcp_config_path is None
        assert trigger._tmp_dir is None


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
    async def test_args_include_mcp_config(self):
        """send_prompt passes --mcp-config and -p flags correctly."""
        trigger = ClaudeCodeTrigger(mcp_server_url="http://localhost:9000/mcp")

        # Mock create_subprocess_exec to capture the args
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
        assert "--mcp-config" in captured_args
        assert "-p" in captured_args
        assert "do review" in captured_args
        # No --resume
        assert "--resume" not in captured_args

        await trigger.close()

    @pytest.mark.asyncio
    async def test_handles_file_not_found(self):
        """send_prompt returns graceful error when claude CLI is missing."""
        trigger = ClaudeCodeTrigger(mcp_server_url="http://localhost:9000/mcp")

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
        trigger = ClaudeCodeTrigger(mcp_server_url="http://localhost:9000/mcp")

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
