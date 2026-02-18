"""Tests for CodexTrigger."""

import asyncio
import json
from unittest.mock import AsyncMock, Mock, patch

import pytest

from ai_review.trigger.codex import CodexTrigger, _extract_codex_activity


# --- Helpers ---


class _FakeStdout:
    """Async iterator that yields bytes lines."""

    def __init__(self, lines: list[str]) -> None:
        self._lines = [line.encode() + b"\n" for line in lines]
        self._idx = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._idx >= len(self._lines):
            raise StopAsyncIteration
        line = self._lines[self._idx]
        self._idx += 1
        return line


class _FakeStderr:
    """Fake stderr with an async read() method."""

    def __init__(self, data: str = "") -> None:
        self._data = data.encode()

    async def read(self) -> bytes:
        return self._data


def _make_proc(stdout_lines: list[str], stderr: str = "", returncode: int = 0):
    """Create a fake process with JSONL stdout."""
    proc = AsyncMock()
    proc.stdout = _FakeStdout(stdout_lines)
    proc.stderr = _FakeStderr(stderr)
    proc.returncode = None
    proc.wait = AsyncMock(side_effect=lambda: setattr(proc, "returncode", returncode) or returncode)
    return proc


def _thread_started(thread_id: str = "019c6c7c-49e8-1234-5678-abcdef012345") -> str:
    return json.dumps({"type": "thread.started", "thread_id": thread_id})


def _item_started_cmd(command: str) -> str:
    return json.dumps({
        "type": "item.started",
        "item": {
            "id": "item_1",
            "type": "command_execution",
            "command": command,
            "aggregated_output": "",
            "exit_code": None,
            "status": "in_progress",
        },
    })


def _item_completed_msg(text: str = "review done") -> str:
    return json.dumps({
        "type": "item.completed",
        "item": {
            "id": "item_0",
            "type": "agent_message",
            "text": text,
        },
    })


def _turn_completed() -> str:
    return json.dumps({
        "type": "turn.completed",
        "usage": {"input_tokens": 100, "output_tokens": 50},
    })


# --- Tests ---


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
            return _make_proc([_item_completed_msg("review output")])

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
            return _make_proc([_item_completed_msg("ok")])

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
            return _make_proc([], stderr="error occurred", returncode=1)

        with patch("ai_review.trigger.codex.asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await trigger.send_prompt("sess1", "codex-model", "test")

        assert result.success is False
        assert result.error == "error occurred"

    @pytest.mark.asyncio
    async def test_times_out_and_kills_process(self):
        """send_prompt times out long-running codex process."""
        trigger = CodexTrigger(timeout_seconds=0.01)

        class _HangingStdout:
            def __aiter__(self):
                return self

            async def __anext__(self):
                await asyncio.sleep(10)
                raise StopAsyncIteration

        proc = AsyncMock()
        proc.stdout = _HangingStdout()
        proc.stderr = _FakeStderr()
        proc.returncode = None
        proc.wait = AsyncMock(side_effect=lambda: setattr(proc, "returncode", -9) or -9)
        proc.kill = Mock()

        async def fake_exec(*args, **kwargs):
            return proc

        with patch("ai_review.trigger.codex.asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await trigger.send_prompt("sess1", "codex-model", "test")

        assert result.success is False
        assert "timed out" in (result.error or "")
        proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_activity_callback_invoked(self):
        """on_activity is called for command_execution events in the stream."""
        trigger = CodexTrigger()

        activities = []
        trigger.on_activity = lambda action, target: activities.append((action, target))

        lines = [
            _thread_started(),
            _item_started_cmd('/bin/zsh -lc "rg TODO /src"'),
            _item_started_cmd('/bin/zsh -lc "cat /src/main.py"'),
            _item_completed_msg("done"),
        ]

        async def fake_exec(*args, **kwargs):
            return _make_proc(lines)

        with patch("ai_review.trigger.codex.asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await trigger.send_prompt("sess1", "codex-model", "review")

        assert result.success is True
        assert len(activities) == 2
        assert activities[0] == ("Grep", "grep:TODO")
        assert activities[1] == ("Read", "/src/main.py")

    @pytest.mark.asyncio
    async def test_thread_id_extracted(self):
        """thread.started event updates the session ID for resume."""
        trigger = CodexTrigger()

        thread_id = "019c6c7c-49e8-1234-5678-abcdef012345"
        lines = [
            _thread_started(thread_id),
            _item_completed_msg("done"),
        ]

        async def fake_exec(*args, **kwargs):
            return _make_proc(lines)

        with patch("ai_review.trigger.codex.asyncio.create_subprocess_exec", side_effect=fake_exec):
            await trigger.send_prompt("sess1", "codex-model", "review")

        assert trigger._sessions["codex-model"] == thread_id

    @pytest.mark.asyncio
    async def test_thread_id_not_overwritten_on_resume(self):
        """On resume, thread.started should not overwrite the existing session ID."""
        trigger = CodexTrigger()
        existing_id = "123e4567-e89b-12d3-a456-426614174000"
        trigger._sessions["codex-model"] = existing_id

        lines = [
            _thread_started("new-thread-id-should-be-ignored"),
            _item_completed_msg("done"),
        ]

        async def fake_exec(*args, **kwargs):
            return _make_proc(lines)

        with patch("ai_review.trigger.codex.asyncio.create_subprocess_exec", side_effect=fake_exec):
            await trigger.send_prompt("sess1", "codex-model", "follow up")

        assert trigger._sessions["codex-model"] == existing_id

    @pytest.mark.asyncio
    async def test_malformed_json_lines_skipped(self):
        """Malformed JSON lines are silently skipped."""
        trigger = CodexTrigger()

        lines = [
            "not json at all",
            "{broken json",
            _item_completed_msg("ok"),
        ]

        async def fake_exec(*args, **kwargs):
            return _make_proc(lines)

        with patch("ai_review.trigger.codex.asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await trigger.send_prompt("sess1", "codex-model", "test")

        assert result.success is True
        assert result.output == "ok"

    @pytest.mark.asyncio
    async def test_callback_exception_does_not_break_stream(self):
        """If on_activity raises, the stream continues processing."""
        trigger = CodexTrigger()

        call_count = 0

        def bad_callback(action, target):
            nonlocal call_count
            call_count += 1
            raise RuntimeError("callback boom")

        trigger.on_activity = bad_callback

        lines = [
            _item_started_cmd('/bin/zsh -lc "rg foo /src"'),
            _item_started_cmd('/bin/zsh -lc "cat /a.py"'),
            _item_completed_msg("done"),
        ]

        async def fake_exec(*args, **kwargs):
            return _make_proc(lines)

        with patch("ai_review.trigger.codex.asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await trigger.send_prompt("sess1", "codex-model", "review")

        assert result.success is True
        assert result.output == "done"
        assert call_count == 2  # Both callbacks were attempted


class TestExtractCodexActivity:
    def test_grep_rg(self):
        assert _extract_codex_activity('/bin/zsh -lc "rg TODO /src"') == ("Grep", "grep:TODO")

    def test_grep_grep(self):
        assert _extract_codex_activity('/bin/zsh -lc "grep -r pattern ."') == ("Grep", "grep:pattern")

    def test_read_cat(self):
        assert _extract_codex_activity('/bin/zsh -lc "cat /src/main.py"') == ("Read", "/src/main.py")

    def test_read_head(self):
        assert _extract_codex_activity('/bin/zsh -lc "head -20 /src/main.py"') == ("Read", "/src/main.py")

    def test_read_tail(self):
        assert _extract_codex_activity('/bin/zsh -lc "tail -n 5 /src/main.py"') == ("Read", "/src/main.py")

    def test_glob_find(self):
        assert _extract_codex_activity('/bin/zsh -lc "find /src -name *.py"') == ("Glob", "glob:/src")

    def test_glob_ls(self):
        assert _extract_codex_activity('/bin/zsh -lc "ls /src"') == ("Glob", "glob:/src")

    def test_bash_curl(self):
        result = _extract_codex_activity('/bin/zsh -lc "curl http://example.com"')
        assert result == ("Bash", "bash:curl http://example.com")

    def test_bash_default(self):
        result = _extract_codex_activity('/bin/zsh -lc "python test.py"')
        assert result == ("Bash", "bash:python test.py")

    def test_bash_truncated(self):
        long_cmd = "echo " + "x" * 200
        result = _extract_codex_activity(f'/bin/zsh -lc "{long_cmd}"')
        assert result == ("Bash", f"bash:{long_cmd[:80]}")

    def test_raw_command_without_shell_wrapper(self):
        result = _extract_codex_activity("rg pattern /src")
        assert result == ("Grep", "grep:pattern")

    def test_empty_command(self):
        assert _extract_codex_activity("") is None

    def test_rg_with_flags(self):
        result = _extract_codex_activity('/bin/zsh -lc "rg -n --type py TODO /src"')
        assert result == ("Grep", "grep:TODO")


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
