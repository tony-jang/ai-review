"""Tests for OpenCodeTrigger."""

import asyncio
import json
from unittest.mock import AsyncMock, Mock, patch

import pytest

from ai_review.trigger.opencode import OpenCodeTrigger, _extract_opencode_activity


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
    """Create a fake process with nd-JSON stdout."""
    proc = AsyncMock()
    proc.stdout = _FakeStdout(stdout_lines)
    proc.stderr = _FakeStderr(stderr)
    proc.returncode = None
    proc.wait = AsyncMock(side_effect=lambda: setattr(proc, "returncode", returncode) or returncode)
    return proc


def _text_event(content: str, session_id: str = "") -> str:
    """Build a text event matching OpenCode's actual nd-JSON format.

    Real format: {"type":"text","timestamp":...,"sessionID":"ses_...","part":{"type":"text","text":"..."}}
    """
    event: dict = {"type": "text", "part": {"type": "text", "text": content}}
    if session_id:
        event["sessionID"] = session_id
    return json.dumps(event)


def _tool_event(name: str, input_data: dict, session_id: str = "") -> str:
    """Build a tool_use event matching OpenCode's actual nd-JSON format.

    Real format: {"type":"tool_use","sessionID":"ses_...","part":{"type":"tool","tool":"read","state":{"input":{...},"status":"completed"}}}
    Tool names are lowercase in OpenCode (e.g. "read", "bash", "grep").
    """
    event: dict = {
        "type": "tool_use",
        "part": {
            "type": "tool",
            "tool": name,
            "state": {"input": input_data, "status": "completed"},
        },
    }
    if session_id:
        event["sessionID"] = session_id
    return json.dumps(event)


def _step_start(session_id: str = "") -> str:
    event = {"type": "step_start"}
    if session_id:
        event["sessionID"] = session_id
    return json.dumps(event)


def _step_finish(session_id: str = "") -> str:
    event = {"type": "step_finish", "usage": {"input_tokens": 100, "output_tokens": 50}}
    if session_id:
        event["sessionID"] = session_id
    return json.dumps(event)


# --- Tests ---


class TestCreateSession:
    @pytest.mark.asyncio
    async def test_returns_session_id(self):
        trigger = OpenCodeTrigger()
        sid = await trigger.create_session("opencode-model")

        assert isinstance(sid, str)
        assert len(sid) == 12
        assert trigger._sessions["opencode-model"] == sid

        await trigger.close()


class TestIsOpencodeSessionId:
    def test_valid_session(self):
        assert OpenCodeTrigger._is_opencode_session_id("ses_65b3acf58ffeLSa4dfj1RVoPpW") is True

    def test_placeholder_hex(self):
        assert OpenCodeTrigger._is_opencode_session_id("abcdef123456") is False

    def test_empty(self):
        assert OpenCodeTrigger._is_opencode_session_id("") is False

    def test_none(self):
        assert OpenCodeTrigger._is_opencode_session_id(None) is False

    def test_no_prefix(self):
        assert OpenCodeTrigger._is_opencode_session_id("65b3acf58ffeLSa4dfj1RVoPpW") is False


class TestSendPrompt:
    @pytest.mark.asyncio
    async def test_first_prompt_basic_args(self):
        """First prompt uses opencode run --format json with prompt."""
        trigger = OpenCodeTrigger()
        captured_args = []

        async def fake_exec(*args, **kwargs):
            captured_args.extend(args)
            return _make_proc([_text_event("review output")])

        with patch("ai_review.trigger.opencode.asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await trigger.send_prompt("sess1", "oc-model", "do review")

        assert result.success is True
        assert result.output == "review output"
        assert result.client_session_id == "sess1"
        assert "opencode" in result.command
        assert "--format json" in result.command

        assert "opencode" in captured_args
        assert "run" in captured_args
        assert "--format" in captured_args
        fmt_idx = captured_args.index("--format")
        assert captured_args[fmt_idx + 1] == "json"
        assert "do review" in captured_args
        # No --session on first call
        assert "--session" not in captured_args

        await trigger.close()

    @pytest.mark.asyncio
    async def test_model_included_when_configured(self):
        """model_config provider/model_id is passed as --model provider/model."""
        trigger = OpenCodeTrigger()
        captured_args = []

        class FakeModelConfig:
            client_type = "opencode"
            provider = "anthropic"
            model_id = "claude-sonnet"

        async def fake_exec(*args, **kwargs):
            captured_args.extend(args)
            return _make_proc([_text_event("ok")])

        with patch("ai_review.trigger.opencode.asyncio.create_subprocess_exec", side_effect=fake_exec):
            await trigger.send_prompt("sess1", "oc-model", "review", model_config=FakeModelConfig())

        assert "--model" in captured_args
        model_idx = captured_args.index("--model")
        assert captured_args[model_idx + 1] == "anthropic/claude-sonnet"

        await trigger.close()

    @pytest.mark.asyncio
    async def test_model_without_provider(self):
        """model_config without provider passes model_id only."""
        trigger = OpenCodeTrigger()
        captured_args = []

        class FakeModelConfig:
            client_type = "opencode"
            provider = ""
            model_id = "claude-sonnet"

        async def fake_exec(*args, **kwargs):
            captured_args.extend(args)
            return _make_proc([_text_event("ok")])

        with patch("ai_review.trigger.opencode.asyncio.create_subprocess_exec", side_effect=fake_exec):
            await trigger.send_prompt("sess1", "oc-model", "review", model_config=FakeModelConfig())

        assert "--model" in captured_args
        model_idx = captured_args.index("--model")
        assert captured_args[model_idx + 1] == "claude-sonnet"

        await trigger.close()

    @pytest.mark.asyncio
    async def test_provider_matching_client_type_includes_prefix(self):
        """provider == client_type should still be prefixed (OpenCode parseModel splits on '/')."""
        trigger = OpenCodeTrigger()
        captured_args = []

        class FakeModelConfig:
            client_type = "opencode"
            provider = "opencode"
            model_id = "glm-4.7-free"

        async def fake_exec(*args, **kwargs):
            captured_args.extend(args)
            return _make_proc([_text_event("ok")])

        with patch("ai_review.trigger.opencode.asyncio.create_subprocess_exec", side_effect=fake_exec):
            await trigger.send_prompt("sess1", "oc-model", "review", model_config=FakeModelConfig())

        assert "--model" in captured_args
        model_idx = captured_args.index("--model")
        assert captured_args[model_idx + 1] == "opencode/glm-4.7-free"

        await trigger.close()

    @pytest.mark.asyncio
    async def test_resume_uses_session_flag(self):
        """When session is a real opencode ID, --session is included."""
        trigger = OpenCodeTrigger()
        real_session = "ses_65b3acf58ffeLSa4dfj1RVoPpW"
        trigger._sessions["oc-model"] = real_session
        captured_args = []

        async def fake_exec(*args, **kwargs):
            captured_args.extend(args)
            return _make_proc([_text_event("ok")])

        with patch("ai_review.trigger.opencode.asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await trigger.send_prompt("sess1", "oc-model", "follow up")

        assert result.success is True
        assert "--session" in captured_args
        idx = captured_args.index("--session")
        assert captured_args[idx + 1] == real_session
        # --model should NOT be present on resume
        assert "--model" not in captured_args
        assert "follow up" in captured_args

        await trigger.close()

    @pytest.mark.asyncio
    async def test_env_vars_injected(self):
        """env_vars are merged into subprocess environment."""
        trigger = OpenCodeTrigger()
        trigger.env_vars = {"ARV_BASE": "http://localhost:3000/api/sessions/s1", "ARV_KEY": "k1"}
        captured_env = {}

        async def fake_exec(*args, **kwargs):
            captured_env.update(kwargs.get("env", {}))
            return _make_proc([_text_event("ok")])

        with patch("ai_review.trigger.opencode.asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await trigger.send_prompt("sess1", "oc-model", "review")

        assert result.success is True
        assert captured_env.get("ARV_BASE") == "http://localhost:3000/api/sessions/s1"
        assert captured_env.get("ARV_KEY") == "k1"
        from ai_review.trigger.opencode import _ARV_DIR
        assert captured_env.get("PATH", "").startswith(_ARV_DIR)

        await trigger.close()

    @pytest.mark.asyncio
    async def test_handles_file_not_found(self):
        """send_prompt returns graceful error when opencode CLI is missing."""
        trigger = OpenCodeTrigger()

        async def raise_not_found(*args, **kwargs):
            raise FileNotFoundError("opencode not found")

        with patch("ai_review.trigger.opencode.asyncio.create_subprocess_exec", side_effect=raise_not_found):
            result = await trigger.send_prompt("sess1", "oc-model", "test")

        assert result.success is False
        assert "not found" in result.error
        assert result.client_session_id == "sess1"
        assert "opencode" in result.command

        await trigger.close()

    @pytest.mark.asyncio
    async def test_handles_process_failure(self):
        """send_prompt handles non-zero exit code."""
        trigger = OpenCodeTrigger()

        async def fake_exec(*args, **kwargs):
            return _make_proc([], stderr="error occurred", returncode=1)

        with patch("ai_review.trigger.opencode.asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await trigger.send_prompt("sess1", "oc-model", "test")

        assert result.success is False
        assert result.error == "error occurred"

        await trigger.close()

    @pytest.mark.asyncio
    async def test_times_out_and_kills_process(self):
        """send_prompt times out long-running opencode process."""
        trigger = OpenCodeTrigger(timeout_seconds=0.01)

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

        with patch("ai_review.trigger.opencode.asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await trigger.send_prompt("sess1", "oc-model", "test")

        assert result.success is False
        assert "timed out" in (result.error or "")
        proc.kill.assert_called_once()


class TestStreamParsing:
    @pytest.mark.asyncio
    async def test_text_events_accumulated(self):
        """Multiple text events are concatenated."""
        trigger = OpenCodeTrigger()

        lines = [
            _text_event("part1 "),
            _text_event("part2"),
        ]

        async def fake_exec(*args, **kwargs):
            return _make_proc(lines)

        with patch("ai_review.trigger.opencode.asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await trigger.send_prompt("sess1", "oc-model", "review")

        assert result.success is True
        assert result.output == "part1 part2"

        await trigger.close()

    @pytest.mark.asyncio
    async def test_session_id_extracted_from_first_event(self):
        """sessionID from first event is stored for resume."""
        trigger = OpenCodeTrigger()
        real_session = "ses_65b3acf58ffeLSa4dfj1RVoPpW"

        lines = [
            _step_start(session_id=real_session),
            _text_event("review done", session_id=real_session),
            _step_finish(session_id=real_session),
        ]

        async def fake_exec(*args, **kwargs):
            return _make_proc(lines)

        with patch("ai_review.trigger.opencode.asyncio.create_subprocess_exec", side_effect=fake_exec):
            await trigger.send_prompt("sess1", "oc-model", "review")

        assert trigger._sessions["oc-model"] == real_session

        await trigger.close()

    @pytest.mark.asyncio
    async def test_session_id_not_overwritten_on_resume(self):
        """On resume, sessionID from events should not overwrite existing session."""
        trigger = OpenCodeTrigger()
        real_session = "ses_65b3acf58ffeLSa4dfj1RVoPpW"
        trigger._sessions["oc-model"] = real_session

        different_session = "ses_99999999999xyzABCDEFGHIJK"
        lines = [
            _text_event("ok", session_id=different_session),
        ]

        async def fake_exec(*args, **kwargs):
            return _make_proc(lines)

        with patch("ai_review.trigger.opencode.asyncio.create_subprocess_exec", side_effect=fake_exec):
            await trigger.send_prompt("sess1", "oc-model", "follow up")

        # Original preserved because is_resume=True skips extraction
        assert trigger._sessions["oc-model"] == real_session

        await trigger.close()

    @pytest.mark.asyncio
    async def test_activity_callback_invoked(self):
        """on_activity is called for tool events in the stream."""
        trigger = OpenCodeTrigger()

        activities = []
        trigger.on_activity = lambda action, target: activities.append((action, target))

        lines = [
            _tool_event("read", {"filePath": "/src/main.py"}),
            _tool_event("grep", {"pattern": "TODO"}),
            _text_event("done"),
        ]

        async def fake_exec(*args, **kwargs):
            return _make_proc(lines)

        with patch("ai_review.trigger.opencode.asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await trigger.send_prompt("sess1", "oc-model", "review")

        assert result.success is True
        assert len(activities) == 2
        assert activities[0] == ("Read", "/src/main.py")
        assert activities[1] == ("Grep", "grep:TODO")

        await trigger.close()

    @pytest.mark.asyncio
    async def test_malformed_json_lines_skipped(self):
        """Malformed JSON lines are silently skipped."""
        trigger = OpenCodeTrigger()

        lines = [
            "not json at all",
            "{broken json",
            _text_event("ok"),
        ]

        async def fake_exec(*args, **kwargs):
            return _make_proc(lines)

        with patch("ai_review.trigger.opencode.asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await trigger.send_prompt("sess1", "oc-model", "test")

        assert result.success is True
        assert result.output == "ok"

        await trigger.close()

    @pytest.mark.asyncio
    async def test_callback_exception_does_not_break_stream(self):
        """If on_activity raises, the stream continues processing."""
        trigger = OpenCodeTrigger()

        call_count = 0

        def bad_callback(action, target):
            nonlocal call_count
            call_count += 1
            raise RuntimeError("callback boom")

        trigger.on_activity = bad_callback

        lines = [
            _tool_event("read", {"filePath": "/a.py"}),
            _tool_event("bash", {"command": "arv get index"}),
            _text_event("done"),
        ]

        async def fake_exec(*args, **kwargs):
            return _make_proc(lines)

        with patch("ai_review.trigger.opencode.asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await trigger.send_prompt("sess1", "oc-model", "review")

        assert result.success is True
        assert result.output == "done"
        assert call_count == 2  # Both callbacks were attempted

        await trigger.close()


class TestExtractOpencodeActivity:
    def test_read_camelcase(self):
        """OpenCode uses camelCase filePath."""
        assert _extract_opencode_activity("read", {"filePath": "/src/main.py"}) == ("Read", "/src/main.py")

    def test_read_snake_case_fallback(self):
        """Also support snake_case file_path as fallback."""
        assert _extract_opencode_activity("read", {"file_path": "/src/main.py"}) == ("Read", "/src/main.py")

    def test_read_case_insensitive(self):
        """Tool names are case-insensitive (Read or read both work)."""
        assert _extract_opencode_activity("Read", {"filePath": "/src/main.py"}) == ("Read", "/src/main.py")

    def test_grep(self):
        assert _extract_opencode_activity("grep", {"pattern": "TODO"}) == ("Grep", "grep:TODO")

    def test_glob(self):
        assert _extract_opencode_activity("glob", {"pattern": "**/*.py"}) == ("Glob", "glob:**/*.py")

    def test_bash(self):
        result = _extract_opencode_activity("bash", {"command": "curl http://example.com"})
        assert result == ("Bash", "bash:curl http://example.com")

    def test_arv_command(self):
        result = _extract_opencode_activity("bash", {"command": "arv get index"})
        assert result == ("arv_get_index", "")

    def test_bash_truncated(self):
        long_cmd = "x" * 200
        result = _extract_opencode_activity("bash", {"command": long_cmd})
        assert result == ("Bash", f"bash:{long_cmd[:80]}")

    def test_unknown_tool(self):
        assert _extract_opencode_activity("websearch", {"query": "test"}) is None

    def test_empty_input(self):
        assert _extract_opencode_activity("read", {}) == ("Read", "")


class TestClose:
    @pytest.mark.asyncio
    async def test_close_kills_stuck_process(self):
        trigger = OpenCodeTrigger()
        proc = AsyncMock()
        proc.returncode = None
        proc.wait = AsyncMock(side_effect=[asyncio.TimeoutError(), asyncio.TimeoutError()])
        proc.terminate = Mock()
        proc.kill = Mock()
        trigger._procs.add(proc)

        await trigger.close()

        proc.terminate.assert_called_once()
        proc.kill.assert_called_once()
