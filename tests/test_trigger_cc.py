"""Tests for ClaudeCodeTrigger."""

import asyncio
import json
from unittest.mock import AsyncMock, Mock, patch

import pytest

from ai_review.trigger.cc import ClaudeCodeTrigger, _extract_activity, _parse_arv_activity


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
    """Create a fake process with stream-json stdout."""
    proc = AsyncMock()
    proc.stdout = _FakeStdout(stdout_lines)
    proc.stderr = _FakeStderr(stderr)
    proc.returncode = None
    proc.wait = AsyncMock(side_effect=lambda: setattr(proc, "returncode", returncode) or returncode)
    # Ensure returncode is set after stdout is consumed (simulated by wait)
    return proc


def _result_line(text: str = "review done", session_id: str = "") -> str:
    event = {"type": "result", "result": text}
    if session_id:
        event["session_id"] = session_id
    return json.dumps(event)


def _assistant_tool_use(name: str, input_data: dict) -> str:
    return json.dumps({
        "type": "assistant",
        "message": {
            "content": [
                {"type": "tool_use", "name": name, "input": input_data}
            ]
        }
    })


# --- Tests ---


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
    async def test_args_stream_json(self):
        """send_prompt uses stream-json, verbose, and allows Grep/Glob."""
        trigger = ClaudeCodeTrigger()
        captured_args = []

        async def fake_exec(*args, **kwargs):
            captured_args.extend(args)
            return _make_proc([_result_line("ok")])

        with patch("ai_review.trigger.cc.asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await trigger.send_prompt("sess1", "opus", "do review")

        assert result.success is True
        assert result.output == "ok"
        assert result.client_session_id == "sess1"

        # Verify args
        assert "claude" in captured_args
        assert "-p" in captured_args
        assert "do review" in captured_args
        assert "--print" in captured_args
        assert "--output-format" in captured_args
        idx = captured_args.index("--output-format")
        assert captured_args[idx + 1] == "stream-json"
        assert "--verbose" in captured_args
        assert "--allowedTools" in captured_args
        tools_idx = captured_args.index("--allowedTools")
        tools_val = captured_args[tools_idx + 1]
        assert "Bash(arv *)" in tools_val
        assert "Write" in tools_val
        assert "Grep" in tools_val
        assert "Glob" in tools_val
        assert "Read" in tools_val
        # No --mcp-config, no --resume
        assert "--mcp-config" not in captured_args
        assert "--resume" not in captured_args

        await trigger.close()

    @pytest.mark.asyncio
    async def test_env_vars_injected(self):
        """env_vars are merged into subprocess environment."""
        trigger = ClaudeCodeTrigger()
        trigger.env_vars = {"ARV_BASE": "http://localhost:3000/api/sessions/s1", "ARV_KEY": "k1", "ARV_MODEL": "opus"}
        captured_env = {}

        async def fake_exec(*args, **kwargs):
            captured_env.update(kwargs.get("env", {}))
            return _make_proc([_result_line("ok")])

        with patch("ai_review.trigger.cc.asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await trigger.send_prompt("sess1", "opus", "review")

        assert result.success is True
        assert captured_env.get("ARV_BASE") == "http://localhost:3000/api/sessions/s1"
        assert captured_env.get("ARV_KEY") == "k1"
        assert captured_env.get("ARV_MODEL") == "opus"
        # PATH should include arv bin directory
        from ai_review.trigger.cc import _ARV_DIR
        assert captured_env.get("PATH", "").startswith(_ARV_DIR)

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
            return _make_proc([], stderr="error occurred", returncode=1)

        with patch("ai_review.trigger.cc.asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await trigger.send_prompt("sess1", "opus", "test")

        assert result.success is False
        assert result.error == "error occurred"

        await trigger.close()

    @pytest.mark.asyncio
    async def test_activity_callback_invoked(self):
        """on_activity is called for tool_use events in the stream."""
        trigger = ClaudeCodeTrigger()

        activities = []
        trigger.on_activity = lambda action, target: activities.append((action, target))

        lines = [
            _assistant_tool_use("Read", {"file_path": "/src/main.py"}),
            _assistant_tool_use("Grep", {"pattern": "TODO"}),
            _result_line("done"),
        ]

        async def fake_exec(*args, **kwargs):
            return _make_proc(lines)

        with patch("ai_review.trigger.cc.asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await trigger.send_prompt("sess1", "opus", "review")

        assert result.success is True
        assert len(activities) == 2
        assert activities[0] == ("Read", "/src/main.py")
        assert activities[1] == ("Grep", "grep:TODO")

        await trigger.close()

    @pytest.mark.asyncio
    async def test_malformed_json_lines_skipped(self):
        """Malformed JSON lines are silently skipped."""
        trigger = ClaudeCodeTrigger()

        lines = [
            "not json at all",
            "{broken json",
            _result_line("ok"),
        ]

        async def fake_exec(*args, **kwargs):
            return _make_proc(lines)

        with patch("ai_review.trigger.cc.asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await trigger.send_prompt("sess1", "opus", "test")

        assert result.success is True
        assert result.output == "ok"

        await trigger.close()

    @pytest.mark.asyncio
    async def test_callback_exception_does_not_break_stream(self):
        """If on_activity raises, the stream continues processing."""
        trigger = ClaudeCodeTrigger()

        call_count = 0

        def bad_callback(action, target):
            nonlocal call_count
            call_count += 1
            raise RuntimeError("callback boom")

        trigger.on_activity = bad_callback

        lines = [
            _assistant_tool_use("Read", {"file_path": "/a.py"}),
            _assistant_tool_use("Grep", {"pattern": "foo"}),
            _result_line("done"),
        ]

        async def fake_exec(*args, **kwargs):
            return _make_proc(lines)

        with patch("ai_review.trigger.cc.asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await trigger.send_prompt("sess1", "opus", "review")

        assert result.success is True
        assert result.output == "done"
        assert call_count == 2  # Both callbacks were attempted

        await trigger.close()


class TestExtractActivity:
    def test_read(self):
        assert _extract_activity("Read", {"file_path": "/src/main.py"}) == ("Read", "/src/main.py")

    def test_grep(self):
        assert _extract_activity("Grep", {"pattern": "TODO"}) == ("Grep", "grep:TODO")

    def test_glob(self):
        assert _extract_activity("Glob", {"pattern": "**/*.py"}) == ("Glob", "glob:**/*.py")

    def test_bash(self):
        result = _extract_activity("Bash", {"command": "curl http://example.com"})
        assert result == ("Bash", "bash:curl http://example.com")

    def test_arv_command(self):
        result = _extract_activity("Bash", {"command": "arv get index"})
        assert result == ("arv_get_index", "")

    def test_bash_truncated(self):
        long_cmd = "x" * 200
        result = _extract_activity("Bash", {"command": long_cmd})
        assert result == ("Bash", f"bash:{long_cmd[:80]}")

    def test_unknown_tool(self):
        assert _extract_activity("WebSearch", {"query": "test"}) is None

    def test_empty_input(self):
        assert _extract_activity("Read", {}) == ("Read", "")


class TestParseArvActivity:
    # --- existing get subcommands ---
    def test_get_file(self):
        assert _parse_arv_activity("arv get file /src/main.py") == ("arv_get_file", "/src/main.py")

    def test_get_file_with_range(self):
        assert _parse_arv_activity("arv get file /src/main.py -r 1:50") == ("arv_get_file", "/src/main.py -r 1:50")

    def test_get_index(self):
        assert _parse_arv_activity("arv get index") == ("arv_get_index", "")

    def test_get_search(self):
        assert _parse_arv_activity("arv get search TODO") == ("arv_get_search", "TODO")

    def test_get_tree(self):
        assert _parse_arv_activity("arv get tree src -d 3") == ("arv_get_tree", "src -d 3")

    def test_get_context(self):
        assert _parse_arv_activity("arv get context src/main.py") == ("arv_get_context", "src/main.py")

    def test_get_thread(self):
        assert _parse_arv_activity("arv get thread iss-abc") == ("arv_get_thread", "iss-abc")

    def test_get_delta(self):
        assert _parse_arv_activity("arv get delta") == ("arv_get_delta", "")

    def test_get_confirmed(self):
        assert _parse_arv_activity("arv get confirmed") == ("arv_get_confirmed", "")

    # --- new get subcommands ---
    def test_get_status(self):
        assert _parse_arv_activity("arv get status") == ("arv_get_status", "")

    def test_get_issues(self):
        assert _parse_arv_activity("arv get issues") == ("arv_get_issues", "")

    def test_get_actionable(self):
        assert _parse_arv_activity("arv get actionable") == ("arv_get_actionable", "")

    def test_get_pending(self):
        assert _parse_arv_activity("arv get pending") == ("arv_get_pending", "")

    def test_get_report(self):
        assert _parse_arv_activity("arv get report") == ("arv_get_report", "")

    def test_get_agents(self):
        assert _parse_arv_activity("arv get agents") == ("arv_get_agents", "")

    def test_get_runtime(self):
        assert _parse_arv_activity("arv get runtime claude-opus") == ("arv_get_runtime", "claude-opus")

    def test_get_assist(self):
        assert _parse_arv_activity("arv get assist iss-abc") == ("arv_get_assist", "iss-abc")

    def test_get_sessions(self):
        assert _parse_arv_activity("arv get sessions") == ("arv_get_sessions", "")

    def test_get_presets(self):
        assert _parse_arv_activity("arv get presets") == ("arv_get_presets", "")

    def test_get_models(self):
        assert _parse_arv_activity("arv get models") == ("arv_get_models", "")

    # --- existing post commands ---
    def test_report(self):
        result = _parse_arv_activity("arv report -n title -s high --file src/a.py")
        assert result == ("arv_report", "-n title -s high --file src/a.py")

    def test_summary(self):
        assert _parse_arv_activity("arv summary done") == ("arv_summary", "done")

    def test_opinion(self):
        result = _parse_arv_activity("arv opinion iss-1 -a fix_required")
        assert result == ("arv_opinion", "iss-1 -a fix_required")

    def test_respond(self):
        result = _parse_arv_activity("arv respond iss-1 -a accept")
        assert result == ("arv_respond", "iss-1 -a accept")

    def test_ping(self):
        result = _parse_arv_activity("arv ping http://localhost:3000/cb")
        assert result == ("arv_ping", "http://localhost:3000/cb")

    # --- new post commands ---
    def test_dismiss(self):
        result = _parse_arv_activity("arv dismiss iss-1 -b reason")
        assert result == ("arv_dismiss", "iss-1 -b reason")

    def test_finish(self):
        assert _parse_arv_activity("arv finish") == ("arv_finish", "")

    def test_start(self):
        assert _parse_arv_activity("arv start") == ("arv_start", "")

    def test_activate(self):
        assert _parse_arv_activity("arv activate") == ("arv_activate", "")

    def test_fix_complete(self):
        result = _parse_arv_activity("arv fix-complete -c abc123")
        assert result == ("arv_fix-complete", "-c abc123")

    def test_assist(self):
        result = _parse_arv_activity("arv assist iss-1 -b help")
        assert result == ("arv_assist", "iss-1 -b help")

    def test_chat(self):
        result = _parse_arv_activity("arv chat opus -b hello")
        assert result == ("arv_chat", "opus -b hello")

    def test_impl_context(self):
        result = _parse_arv_activity("arv impl-context -b summary")
        assert result == ("arv_impl-context", "-b summary")

    # --- nested subcommands (session, preset, agent) ---
    def test_session_list(self):
        assert _parse_arv_activity("arv session list") == ("arv_session_list", "")

    def test_session_create(self):
        result = _parse_arv_activity("arv session create --base main --head feat")
        assert result == ("arv_session_create", "--base main --head feat")

    def test_session_delete(self):
        assert _parse_arv_activity("arv session delete") == ("arv_session_delete", "")

    def test_preset_list(self):
        assert _parse_arv_activity("arv preset list") == ("arv_preset_list", "")

    def test_preset_add(self):
        result = _parse_arv_activity("arv preset add --id my-claude --client claude-code")
        assert result == ("arv_preset_add", "--id my-claude --client claude-code")

    def test_preset_delete(self):
        assert _parse_arv_activity("arv preset delete my-claude") == ("arv_preset_delete", "my-claude")

    def test_agent_list(self):
        assert _parse_arv_activity("arv agent list") == ("arv_agent_list", "")

    def test_agent_add(self):
        result = _parse_arv_activity("arv agent add --id codex --client codex")
        assert result == ("arv_agent_add", "--id codex --client codex")

    def test_agent_remove(self):
        assert _parse_arv_activity("arv agent remove codex") == ("arv_agent_remove", "codex")

    # --- edge cases ---
    def test_bare_arv(self):
        assert _parse_arv_activity("arv") == ("arv", "arv")

    def test_extract_activity_delegates(self):
        """_extract_activity delegates arv commands to _parse_arv_activity."""
        result = _extract_activity("Bash", {"command": "arv get file /src/a.py"})
        assert result == ("arv_get_file", "/src/a.py")

        result = _extract_activity("Bash", {"command": "arv report -n Bug -s high --file x.py"})
        assert result == ("arv_report", "-n Bug -s high --file x.py")


class TestIsCcSessionId:
    def test_valid_uuid(self):
        assert ClaudeCodeTrigger._is_cc_session_id("a1b2c3d4-e5f6-7890-abcd-ef1234567890") is True

    def test_placeholder_hex(self):
        assert ClaudeCodeTrigger._is_cc_session_id("abcdef123456") is False

    def test_empty(self):
        assert ClaudeCodeTrigger._is_cc_session_id("") is False

    def test_none(self):
        assert ClaudeCodeTrigger._is_cc_session_id(None) is False

    def test_uppercase_uuid(self):
        assert ClaudeCodeTrigger._is_cc_session_id("A1B2C3D4-E5F6-7890-ABCD-EF1234567890") is True


class TestSessionResume:
    @pytest.mark.asyncio
    async def test_session_id_extracted_from_result(self):
        """First call extracts session_id from result event into _sessions."""
        trigger = ClaudeCodeTrigger()
        await trigger.create_session("opus")
        real_uuid = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"

        lines = [_result_line("ok", session_id=real_uuid)]

        async def fake_exec(*args, **kwargs):
            return _make_proc(lines)

        with patch("ai_review.trigger.cc.asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await trigger.send_prompt("sess1", "opus", "review")

        assert result.success is True
        assert trigger._sessions["opus"] == real_uuid
        await trigger.close()

    @pytest.mark.asyncio
    async def test_followup_uses_resume(self):
        """When _sessions has a real UUID, --resume is included and --allowedTools is still present."""
        trigger = ClaudeCodeTrigger()
        real_uuid = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        trigger._sessions["opus"] = real_uuid
        captured_args = []

        async def fake_exec(*args, **kwargs):
            captured_args.extend(args)
            return _make_proc([_result_line("ok", session_id=real_uuid)])

        with patch("ai_review.trigger.cc.asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await trigger.send_prompt("sess1", "opus", "discuss")

        assert result.success is True
        assert "--resume" in captured_args
        idx = captured_args.index("--resume")
        assert captured_args[idx + 1] == real_uuid
        # --allowedTools is still present
        assert "--allowedTools" in captured_args
        await trigger.close()

    @pytest.mark.asyncio
    async def test_session_id_not_overwritten_on_resume(self):
        """On resume calls, _sessions keeps the original UUID (not overwritten)."""
        trigger = ClaudeCodeTrigger()
        real_uuid = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        trigger._sessions["opus"] = real_uuid

        # Result event contains a different session_id
        different_uuid = "11111111-2222-3333-4444-555555555555"
        lines = [_result_line("ok", session_id=different_uuid)]

        async def fake_exec(*args, **kwargs):
            return _make_proc(lines)

        with patch("ai_review.trigger.cc.asyncio.create_subprocess_exec", side_effect=fake_exec):
            await trigger.send_prompt("sess1", "opus", "followup")

        # Original UUID preserved because is_resume=True skips extraction
        assert trigger._sessions["opus"] == real_uuid
        await trigger.close()

    @pytest.mark.asyncio
    async def test_missing_session_id_graceful(self):
        """If result has no session_id, placeholder stays (next call is also fresh)."""
        trigger = ClaudeCodeTrigger()
        placeholder = await trigger.create_session("opus")

        lines = [_result_line("ok")]  # no session_id in result

        async def fake_exec(*args, **kwargs):
            return _make_proc(lines)

        with patch("ai_review.trigger.cc.asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await trigger.send_prompt("sess1", "opus", "review")

        assert result.success is True
        # Placeholder is preserved
        assert trigger._sessions["opus"] == placeholder
        assert not ClaudeCodeTrigger._is_cc_session_id(placeholder)
        await trigger.close()


class TestClose:
    @pytest.mark.asyncio
    async def test_close_kills_stuck_process(self):
        trigger = ClaudeCodeTrigger()
        proc = AsyncMock()
        proc.returncode = None
        proc.wait = AsyncMock(side_effect=[asyncio.TimeoutError(), asyncio.TimeoutError()])
        proc.terminate = Mock()
        proc.kill = Mock()
        trigger._procs.add(proc)

        await trigger.close()

        proc.terminate.assert_called_once()
        proc.kill.assert_called_once()
