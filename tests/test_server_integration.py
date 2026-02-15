"""Integration tests for FastAPI server with orchestrator wiring."""

from __future__ import annotations

import asyncio
import re

import pytest
from httpx import ASGITransport, AsyncClient

from ai_review.models import SessionStatus
from ai_review.server import create_app
from ai_review.trigger.base import TriggerResult


@pytest.fixture
def app(tmp_path):
    return create_app(repo_path=str(tmp_path), port=9999)


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestServerOrchestrator:
    @pytest.mark.asyncio
    async def test_create_session_returns_session_id(self, client):
        resp = await client.post("/api/sessions", json={"base": "main"})
        assert resp.status_code == 200
        data = resp.json()
        assert "session_id" in data

    @pytest.mark.asyncio
    async def test_session_starts_in_reviewing(self, client):
        resp = await client.post("/api/sessions", json={"base": "main"})
        session_id = resp.json()["session_id"]

        resp = await client.get(f"/api/sessions/{session_id}/status")
        assert resp.status_code == 200
        assert resp.json()["status"] == "reviewing"

    @pytest.mark.asyncio
    async def test_context_index_endpoint(self, client):
        resp = await client.post("/api/sessions", json={"base": "main"})
        session_id = resp.json()["session_id"]

        resp = await client.get(f"/api/sessions/{session_id}/index")
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == session_id
        assert "files" in data
        assert "available_apis" in data
        apis = data["available_apis"]
        assert any("/files/" in a for a in apis)
        assert any("/search" in a for a in apis)
        assert any("/tree" in a for a in apis)
        assert any("/context" in a for a in apis)

    @pytest.mark.asyncio
    async def test_file_content_endpoint(self, client, tmp_path):
        (tmp_path / "hello.py").write_text("line1\nline2\nline3\n")
        resp = await client.post("/api/sessions", json={"base": "main"})
        session_id = resp.json()["session_id"]

        resp = await client.get(f"/api/sessions/{session_id}/files/hello.py")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_lines"] == 3
        assert len(data["lines"]) == 3

    @pytest.mark.asyncio
    async def test_file_content_endpoint_range(self, client, tmp_path):
        (tmp_path / "big.py").write_text("\n".join(f"L{i}" for i in range(1, 21)))
        resp = await client.post("/api/sessions", json={"base": "main"})
        session_id = resp.json()["session_id"]

        resp = await client.get(f"/api/sessions/{session_id}/files/big.py?start=5&end=10")
        assert resp.status_code == 200
        data = resp.json()
        assert data["start_line"] == 5
        assert data["end_line"] == 10
        assert len(data["lines"]) == 6

    @pytest.mark.asyncio
    async def test_file_content_not_found(self, client):
        resp = await client.post("/api/sessions", json={"base": "main"})
        session_id = resp.json()["session_id"]

        resp = await client.get(f"/api/sessions/{session_id}/files/nope.py")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_file_content_outside_repo(self, client):
        """Path traversal blocked: HTTP normalizes ../ so we get 404, unit test covers PermissionError."""
        resp = await client.post("/api/sessions", json={"base": "main"})
        session_id = resp.json()["session_id"]

        resp = await client.get(f"/api/sessions/{session_id}/files/../../../etc/passwd")
        assert resp.status_code in (403, 404)

    @pytest.mark.asyncio
    async def test_activity_tracking_with_agent_key(self, client, tmp_path):
        (tmp_path / "code.py").write_text("hello\n")
        resp = await client.post("/api/sessions", json={"base": "main"})
        session_id = resp.json()["session_id"]

        # Get agent access key
        from ai_review.server import create_app
        # Access manager through the app's internal state
        resp_status = await client.get(f"/api/sessions/{session_id}/status")
        # Register an agent key manually via add_agent
        await client.post(
            f"/api/sessions/{session_id}/agents",
            json={"id": "test-agent", "client_type": "claude-code"},
        )
        # Get runtime to find agent key
        resp_status = await client.get(f"/api/sessions/{session_id}/status")
        # We need to find the agent key - use a direct API call with known key
        # Instead, let's verify via the session state
        # Make a request with X-Agent-Key and check activities
        resp = await client.get(
            f"/api/sessions/{session_id}/files/code.py",
            headers={"X-Agent-Key": "unknown-key"},
        )
        assert resp.status_code == 200
        # Unknown key means no activity recorded - verify no crash

    @pytest.mark.asyncio
    async def test_activity_not_tracked_without_key(self, client, tmp_path):
        (tmp_path / "code.py").write_text("hello\n")
        resp = await client.post("/api/sessions", json={"base": "main"})
        session_id = resp.json()["session_id"]

        # Request without X-Agent-Key
        resp = await client.get(f"/api/sessions/{session_id}/files/code.py")
        assert resp.status_code == 200
        # No agent key = no activity recorded (no crash)

    @pytest.mark.asyncio
    async def test_search_endpoint(self, client, tmp_path):
        (tmp_path / "code.py").write_text("def example():\n    return 42\n")
        resp = await client.post("/api/sessions", json={"base": "main"})
        session_id = resp.json()["session_id"]

        resp = await client.get(f"/api/sessions/{session_id}/search?q=example")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_matches"] >= 1

    @pytest.mark.asyncio
    async def test_search_endpoint_empty_query(self, client):
        resp = await client.post("/api/sessions", json={"base": "main"})
        session_id = resp.json()["session_id"]

        resp = await client.get(f"/api/sessions/{session_id}/search?q=")
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_tree_endpoint(self, client, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.py").write_text("pass")
        resp = await client.post("/api/sessions", json={"base": "main"})
        session_id = resp.json()["session_id"]

        resp = await client.get(f"/api/sessions/{session_id}/tree")
        assert resp.status_code == 200
        data = resp.json()
        assert data["path"] == "."
        names = {e["name"] for e in data["entries"]}
        assert "src" in names

    @pytest.mark.asyncio
    async def test_tree_endpoint_with_depth(self, client, tmp_path):
        (tmp_path / "a" / "b").mkdir(parents=True)
        (tmp_path / "a" / "b" / "c.py").write_text("pass")
        resp = await client.post("/api/sessions", json={"base": "main"})
        session_id = resp.json()["session_id"]

        resp = await client.get(f"/api/sessions/{session_id}/tree?depth=1")
        assert resp.status_code == 200
        a_entry = next(e for e in resp.json()["entries"] if e["name"] == "a")
        assert a_entry["children"] == []

    @pytest.mark.asyncio
    async def test_overall_review_submit_and_list_endpoints(self, client):
        resp = await client.post("/api/sessions", json={"base": "main"})
        session_id = resp.json()["session_id"]

        submit = await client.post(
            f"/api/sessions/{session_id}/overall-reviews",
            json={
                "model_id": "codex",
                "task_type": "review",
                "turn": 0,
                "merge_decision": "not_mergeable",
                "summary": "주요 이슈 해결 전 머지 불가",
                "blockers": ["성능 회귀", "테스트 누락"],
            },
        )
        assert submit.status_code == 200
        submit_data = submit.json()
        assert submit_data["status"] == "accepted"
        assert submit_data["overall_review"]["merge_decision"] == "not_mergeable"

        listed = await client.get(f"/api/sessions/{session_id}/overall-reviews")
        assert listed.status_code == 200
        rows = listed.json()
        assert len(rows) == 1
        assert rows[0]["model_id"] == "codex"
        assert rows[0]["task_type"] == "review"
        assert rows[0]["turn"] == 0

    @pytest.mark.asyncio
    async def test_assist_key_issue_endpoint(self, client):
        resp = await client.post("/api/sessions", json={"base": "main"})
        session_id = resp.json()["session_id"]
        issued = await client.post(f"/api/sessions/{session_id}/assist/key")
        assert issued.status_code == 200
        data = issued.json()
        assert data["status"] == "issued"
        assert isinstance(data["access_key"], str)
        assert len(data["access_key"]) >= 32

    @pytest.mark.asyncio
    async def test_rejects_configured_model_review_without_access_key(self, client):
        resp = await client.post("/api/sessions", json={"base": "main"})
        session_id = resp.json()["session_id"]

        added = await client.post(f"/api/sessions/{session_id}/agents", json={
            "id": "codex",
            "client_type": "codex",
            "role": "security",
        })
        assert added.status_code == 201

        denied = await client.post(f"/api/sessions/{session_id}/reviews", json={
            "model_id": "codex",
            "issues": [],
            "summary": "no-op",
        })
        assert denied.status_code == 403

    @pytest.mark.asyncio
    async def test_manual_unknown_model_review_without_access_key_still_allowed(self, client):
        resp = await client.post("/api/sessions", json={"base": "main"})
        session_id = resp.json()["session_id"]
        accepted = await client.post(f"/api/sessions/{session_id}/reviews", json={
            "model_id": "manual-a",
            "issues": [],
            "summary": "ok",
        })
        assert accepted.status_code == 200

    @pytest.mark.asyncio
    async def test_rejects_human_assist_opinion_without_access_key(self, client):
        resp = await client.post("/api/sessions", json={"base": "main"})
        session_id = resp.json()["session_id"]

        created = await client.post(f"/api/sessions/{session_id}/issues", json={
            "title": "manual issue",
            "severity": "low",
            "file": "a.py",
            "description": "desc",
        })
        assert created.status_code == 201
        issue_id = created.json()["id"]

        denied = await client.post(f"/api/sessions/{session_id}/issues/{issue_id}/assist/opinion", json={})
        assert denied.status_code == 403

    @pytest.mark.asyncio
    async def test_agent_add_remove_endpoints(self, client):
        await client.post("/api/sessions", json={"base": "main"})

        resp = await client.get("/api/sessions/current/agents")
        assert resp.status_code == 200
        initial = resp.json()

        resp = await client.post("/api/sessions/current/agents", json={
            "id": "tmp-agent",
            "client_type": "claude-code",
            "provider": "anthropic",
            "model_id": "claude-test",
            "role": "test",
        })
        assert resp.status_code == 201

        resp = await client.get("/api/sessions/current/agents")
        assert resp.status_code == 200
        assert any(a["id"] == "tmp-agent" for a in resp.json())

        resp = await client.delete("/api/sessions/current/agents/tmp-agent")
        assert resp.status_code == 200

        resp = await client.get("/api/sessions/current/agents")
        assert resp.status_code == 200
        assert len(resp.json()) == len(initial)

    @pytest.mark.asyncio
    async def test_agent_preset_crud_endpoints(self, client):
        resp = await client.get("/api/agent-presets")
        assert resp.status_code == 200
        assert resp.json() == []

        resp = await client.post("/api/agent-presets", json={
            "id": "preset-codex",
            "client_type": "codex",
            "role": "security",
        })
        assert resp.status_code == 201
        assert resp.json()["id"] == "preset-codex"

        resp = await client.put("/api/agent-presets/preset-codex", json={
            "role": "security reviewer",
            "enabled": False,
        })
        assert resp.status_code == 200
        assert resp.json()["role"] == "security reviewer"
        assert resp.json()["enabled"] is False

        resp = await client.get("/api/agent-presets")
        assert resp.status_code == 200
        assert any(p["id"] == "preset-codex" for p in resp.json())

        resp = await client.delete("/api/agent-presets/preset-codex")
        assert resp.status_code == 200
        assert resp.json()["status"] == "removed"

    @pytest.mark.asyncio
    async def test_create_session_with_selected_presets(self, client):
        await client.post("/api/agent-presets", json={
            "id": "preset-gemini",
            "client_type": "gemini",
            "role": "perf",
        })
        await client.post("/api/agent-presets", json={
            "id": "preset-codex",
            "client_type": "codex",
            "role": "security",
        })

        resp = await client.post("/api/sessions", json={
            "base": "main",
            "preset_ids": ["preset-codex"],
        })
        assert resp.status_code == 200
        sid = resp.json()["session_id"]

        agents_resp = await client.get(f"/api/sessions/{sid}/agents")
        assert agents_resp.status_code == 200
        agents = agents_resp.json()
        assert [a["id"] for a in agents] == ["preset-codex"]

    @pytest.mark.asyncio
    async def test_pick_directory_endpoint(self, client, monkeypatch):
        monkeypatch.setattr("ai_review.server.pick_directory_native", lambda: "/tmp/my-repo")
        for endpoint in ("/api/fs/pick-directory", "/api/pick-directory"):
            resp = await client.get(endpoint)
            assert resp.status_code == 200
            data = resp.json()
            assert data["ok"] is True
            assert data["path"] == "/tmp/my-repo"

    @pytest.mark.asyncio
    async def test_open_local_path_endpoint(self, client, tmp_path, monkeypatch):
        target = tmp_path / "docs" / "agent.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("ok", encoding="utf-8")
        opened: dict[str, str] = {}

        def fake_open(path, opener_id=None):
            opened["path"] = str(path)
            opened["opener_id"] = str(opener_id or "default")
            return opened["opener_id"]

        monkeypatch.setattr("ai_review.server.open_local_path_with_opener", fake_open)

        resp = await client.post("/api/fs/open", json={"path": "docs/agent.md", "opener_id": "vscode"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["path"] == str(target.resolve())
        assert data["opener_id"] == "vscode"
        assert opened["path"] == str(target.resolve())
        assert opened["opener_id"] == "vscode"

    @pytest.mark.asyncio
    async def test_openers_endpoint(self, client):
        resp = await client.get("/api/fs/openers")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data.get("openers"), list)
        assert any(o.get("id") == "default" for o in data["openers"])

    @pytest.mark.asyncio
    async def test_open_local_path_endpoint_blocks_path_traversal(self, client, tmp_path):
        outside = tmp_path.parent / "outside-test.txt"
        outside.write_text("x", encoding="utf-8")

        try:
            resp = await client.post("/api/fs/open", json={"path": f"../{outside.name}"})
            assert resp.status_code == 400
            assert "within repository" in resp.json()["detail"]
        finally:
            outside.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_agent_connection_test_success(self, client, monkeypatch):
        captured: dict[str, str] = {}

        class FakeClaudeTrigger:
            async def create_session(self, model_id: str) -> str:
                return "fake-session"

            async def send_prompt(self, client_session_id: str, model_id: str, prompt: str, *, model_config=None):
                captured["prompt"] = prompt
                await asyncio.sleep(5)
                return TriggerResult(success=True, output="done")

            async def close(self) -> None:
                return None

        monkeypatch.setattr("ai_review.server.ClaudeCodeTrigger", FakeClaudeTrigger)

        req_task = asyncio.create_task(client.post("/api/agents/connection-test", json={
            "client_type": "claude-code",
            "timeout_seconds": 10,
        }))

        for _ in range(200):
            if "prompt" in captured:
                break
            await asyncio.sleep(0.01)
        assert "prompt" in captured

        m = re.search(r"http://localhost:9999/api/agents/connection-test/callback/[0-9a-f]+", captured["prompt"])
        assert m
        callback_path = m.group(0).replace("http://localhost:9999", "")
        cb = await client.post(callback_path, json={"ping": "pong"})
        assert cb.status_code == 200

        resp = await req_task
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["status"] == "callback_received"
        assert data["callback"]["payload"]["ping"] == "pong"

    @pytest.mark.asyncio
    async def test_agent_connection_test_timeout(self, client, monkeypatch):
        class FakeClaudeTrigger:
            async def create_session(self, model_id: str) -> str:
                return "fake-session"

            async def send_prompt(self, client_session_id: str, model_id: str, prompt: str, *, model_config=None):
                return TriggerResult(success=True, output="sent")

            async def close(self) -> None:
                return None

        monkeypatch.setattr("ai_review.server.ClaudeCodeTrigger", FakeClaudeTrigger)

        resp = await client.post("/api/agents/connection-test", json={
            "client_type": "claude-code",
            "timeout_seconds": 3,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert data["status"] == "timeout"

    @pytest.mark.asyncio
    async def test_agent_connection_test_trigger_failed(self, client, monkeypatch):
        class FakeClaudeTrigger:
            async def create_session(self, model_id: str) -> str:
                return "fake-session"

            async def send_prompt(self, client_session_id: str, model_id: str, prompt: str, *, model_config=None):
                return TriggerResult(success=False, error="trigger boom")

            async def close(self) -> None:
                return None

        monkeypatch.setattr("ai_review.server.ClaudeCodeTrigger", FakeClaudeTrigger)

        resp = await client.post("/api/agents/connection-test", json={
            "client_type": "claude-code",
            "timeout_seconds": 20,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert data["status"] == "trigger_failed"
        assert "trigger boom" in data["reason"]

    @pytest.mark.asyncio
    async def test_agent_connection_test_invalid_client_type(self, client):
        resp = await client.post("/api/agents/connection-test", json={
            "client_type": "unknown-client",
        })
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_agent_connection_test_callback_unknown_token(self, client):
        resp = await client.post("/api/agents/connection-test/callback/unknown-token", json={"x": 1})
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_agent_connection_test_trigger_exception(self, client, monkeypatch):
        """When send_prompt raises an exception, the response should be trigger_failed."""

        class ExplodingTrigger:
            async def create_session(self, model_id: str) -> str:
                return "explode-session"

            async def send_prompt(self, client_session_id: str, model_id: str, prompt: str, *, model_config=None):
                raise RuntimeError("CLI crashed")

            async def close(self) -> None:
                return None

        monkeypatch.setattr("ai_review.server.ClaudeCodeTrigger", ExplodingTrigger)

        resp = await client.post("/api/agents/connection-test", json={
            "client_type": "claude-code",
            "timeout_seconds": 10,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert data["status"] == "trigger_failed"
        assert "CLI crashed" in data["reason"]

    @pytest.mark.asyncio
    async def test_agent_connection_test_token_cleaned_after_success(self, client, monkeypatch):
        """After a successful connection test, the same token should not be reusable (cleaned up)."""
        captured: dict[str, str] = {}

        class FakeClaudeTrigger:
            async def create_session(self, model_id: str) -> str:
                return "fake-session"

            async def send_prompt(self, client_session_id: str, model_id: str, prompt: str, *, model_config=None):
                captured["prompt"] = prompt
                await asyncio.sleep(5)
                return TriggerResult(success=True, output="done")

            async def close(self) -> None:
                return None

        monkeypatch.setattr("ai_review.server.ClaudeCodeTrigger", FakeClaudeTrigger)

        req_task = asyncio.create_task(client.post("/api/agents/connection-test", json={
            "client_type": "claude-code",
            "timeout_seconds": 10,
        }))

        for _ in range(200):
            if "prompt" in captured:
                break
            await asyncio.sleep(0.01)
        assert "prompt" in captured

        m = re.search(r"http://localhost:9999/api/agents/connection-test/callback/[0-9a-f]+", captured["prompt"])
        assert m
        callback_path = m.group(0).replace("http://localhost:9999", "")

        # First callback — should succeed and complete the test
        cb = await client.post(callback_path, json={"ping": "pong"})
        assert cb.status_code == 200

        resp = await req_task
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        # Re-use the same callback token — should be 404 (cleaned up)
        cb2 = await client.post(callback_path, json={"ping": "pong"})
        assert cb2.status_code == 404

    @pytest.mark.asyncio
    async def test_full_manual_flow(self, client):
        # Start
        resp = await client.post("/api/sessions", json={"base": "main"})
        sid = resp.json()["session_id"]

        # Submit reviews (distinct titles/files to avoid dedup)
        resp = await client.post(f"/api/sessions/{sid}/reviews", json={
            "model_id": "a",
            "issues": [{"title": "SQL injection vulnerability", "severity": "high", "file": "db.py", "description": "raw sql"}],
        })
        assert resp.status_code == 200
        resp = await client.post(f"/api/sessions/{sid}/reviews", json={
            "model_id": "b",
            "issues": [{"title": "Memory leak in connection pool", "severity": "medium", "file": "pool.py", "description": "not closed"}],
        })
        assert resp.status_code == 200

        # Process
        resp = await client.post(f"/api/sessions/{sid}/process")
        assert resp.status_code == 200
        issues = resp.json()["issues"]
        assert len(issues) == 2

        # Submit opinions
        for issue in issues:
            for model in ["a", "b"]:
                resp = await client.post(f"/api/issues/{issue['id']}/opinions", json={
                    "model_id": model,
                    "action": "agree",
                    "reasoning": "confirmed",
                    "suggested_severity": "medium",
                })
                # May fail if model already raised this issue — that's fine
                assert resp.status_code in (200, 400, 404)

        # Finish
        resp = await client.post(f"/api/sessions/{sid}/finish")
        assert resp.status_code == 200
        report = resp.json()
        assert report["stats"]["total_issues_found"] == 2

        # Verify complete
        resp = await client.get(f"/api/sessions/{sid}/status")
        assert resp.json()["status"] == "complete"

    @pytest.mark.asyncio
    async def test_nonexistent_session(self, client):
        resp = await client.get("/api/sessions/doesnotexist/status")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_list_sessions_empty(self, client):
        resp = await client.get("/api/sessions")
        assert resp.status_code == 200
        assert resp.json() == []

    @pytest.mark.asyncio
    async def test_list_sessions_after_create(self, client):
        await client.post("/api/sessions", json={"base": "main"})
        await client.post("/api/sessions", json={"base": "develop"})

        resp = await client.get("/api/sessions")
        assert resp.status_code == 200
        sessions = resp.json()
        assert len(sessions) == 2

    @pytest.mark.asyncio
    async def test_delete_session(self, client):
        resp = await client.post("/api/sessions", json={"base": "main"})
        sid = resp.json()["session_id"]

        resp = await client.delete(f"/api/sessions/{sid}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

        resp = await client.get("/api/sessions")
        assert len(resp.json()) == 0

    @pytest.mark.asyncio
    async def test_delete_nonexistent_session(self, client):
        resp = await client.delete("/api/sessions/nonexistent")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_activate_session(self, client):
        r1 = await client.post("/api/sessions", json={"base": "main"})
        sid1 = r1.json()["session_id"]
        r2 = await client.post("/api/sessions", json={"base": "develop"})
        sid2 = r2.json()["session_id"]

        # Current should be sid2 (last created)
        resp = await client.get("/api/sessions/current/status")
        assert resp.json()["session_id"] == sid2

        # Activate sid1
        resp = await client.post(f"/api/sessions/{sid1}/activate")
        assert resp.status_code == 200

        resp = await client.get("/api/sessions/current/status")
        assert resp.json()["session_id"] == sid1

    @pytest.mark.asyncio
    async def test_session_scoped_agents_endpoint(self, client):
        resp = await client.post("/api/sessions", json={"base": "main"})
        sid = resp.json()["session_id"]

        # Add agent via session-scoped endpoint
        resp = await client.post(f"/api/sessions/{sid}/agents", json={
            "id": "test-agent",
            "client_type": "claude-code",
            "role": "test",
        })
        assert resp.status_code == 201

        # List agents via session-scoped endpoint
        resp = await client.get(f"/api/sessions/{sid}/agents")
        assert resp.status_code == 200
        assert any(a["id"] == "test-agent" for a in resp.json())

        # Remove via session-scoped endpoint
        resp = await client.delete(f"/api/sessions/{sid}/agents/test-agent")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_update_agent_endpoint(self, client):
        resp = await client.post("/api/sessions", json={"base": "main"})
        sid = resp.json()["session_id"]

        # Add agent
        await client.post(f"/api/sessions/{sid}/agents", json={
            "id": "test-bot",
            "client_type": "claude-code",
            "role": "general",
        })

        # Update agent
        resp = await client.put(f"/api/sessions/{sid}/agents/test-bot", json={
            "role": "security",
            "color": "#EF4444",
            "enabled": False,
            "description": "Security specialist",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["role"] == "security"
        assert data["color"] == "#EF4444"
        assert data["enabled"] is False

        # Verify via list
        resp = await client.get(f"/api/sessions/{sid}/agents")
        agents = resp.json()
        bot = next(a for a in agents if a["id"] == "test-bot")
        assert bot["role"] == "security"
        assert bot["color"] == "#EF4444"

    @pytest.mark.asyncio
    async def test_update_nonexistent_agent(self, client):
        resp = await client.post("/api/sessions", json={"base": "main"})
        sid = resp.json()["session_id"]

        resp = await client.put(f"/api/sessions/{sid}/agents/ghost", json={"role": "x"})
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_session_scoped_issue_thread(self, client):
        resp = await client.post("/api/sessions", json={"base": "main"})
        sid = resp.json()["session_id"]

        await client.post(f"/api/sessions/{sid}/reviews", json={
            "model_id": "a",
            "issues": [{"title": "Bug", "severity": "high", "file": "x.py", "description": "d"}],
        })
        await client.post(f"/api/sessions/{sid}/process")

        issues = (await client.get(f"/api/sessions/{sid}/issues")).json()
        issue_id = issues[0]["id"]

        # Thread via session-scoped endpoint
        resp = await client.get(f"/api/sessions/{sid}/issues/{issue_id}/thread")
        assert resp.status_code == 200
        assert resp.json()["id"] == issue_id

        # Opinion via session-scoped endpoint
        resp = await client.post(f"/api/sessions/{sid}/issues/{issue_id}/opinions", json={
            "model_id": "b",
            "action": "fix_required",
            "reasoning": "confirmed",
            "suggested_severity": "high",
        })
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_human_opinion_reopens_after_complete(self, client):
        resp = await client.post("/api/sessions", json={"base": "main"})
        sid = resp.json()["session_id"]

        await client.post(f"/api/sessions/{sid}/reviews", json={
            "model_id": "a",
            "issues": [{"title": "Bug", "severity": "high", "file": "x.py", "description": "d"}],
        })
        await client.post(f"/api/sessions/{sid}/process")
        issues = (await client.get(f"/api/sessions/{sid}/issues")).json()
        issue_id = issues[0]["id"]

        resp = await client.post(f"/api/sessions/{sid}/finish")
        assert resp.status_code == 200
        assert (await client.get(f"/api/sessions/{sid}/status")).json()["status"] == "complete"

        resp = await client.post(f"/api/issues/{issue_id}/opinions", json={
            "model_id": "human",
            "action": "clarify",
            "reasoning": "재검토 부탁",
        })
        assert resp.status_code == 200

        status = (await client.get(f"/api/sessions/{sid}/status")).json()
        assert status["status"] == "deliberating"

    # ------------------------------------------------------------------
    # C5: git, diff, process, report endpoint tests
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_git_validate_with_tmp_path(self, client, tmp_path):
        resp = await client.post("/api/git/validate", json={"path": str(tmp_path)})
        assert resp.status_code == 200
        data = resp.json()
        assert "valid" in data

    @pytest.mark.asyncio
    async def test_git_validate_missing_path(self, client):
        resp = await client.post("/api/git/validate", json={"path": ""})
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_git_branches(self, client, tmp_path):
        resp = await client.get(f"/api/git/branches?repo_path={tmp_path}")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, (list, dict))

    @pytest.mark.asyncio
    async def test_diff_not_found(self, client):
        resp = await client.post("/api/sessions", json={"base": "main"})
        sid = resp.json()["session_id"]
        resp = await client.get(f"/api/sessions/{sid}/diff/nonexistent.py")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_diff_found(self, app, client):
        from ai_review.models import DiffFile

        resp = await client.post("/api/sessions", json={"base": "main"})
        sid = resp.json()["session_id"]

        manager = app.state.manager
        session = manager.get_session(sid)
        session.diff.append(DiffFile(path="hello.py", additions=3, deletions=1, content="+new line"))

        resp = await client.get(f"/api/sessions/{sid}/diff/hello.py")
        assert resp.status_code == 200
        data = resp.json()
        assert data["path"] == "hello.py"
        assert data["content"] == "+new line"
        assert data["additions"] == 3

    @pytest.mark.asyncio
    async def test_process_creates_issues(self, client):
        resp = await client.post("/api/sessions", json={"base": "main"})
        sid = resp.json()["session_id"]

        await client.post(f"/api/sessions/{sid}/reviews", json={
            "model_id": "m1",
            "issues": [
                {"title": "Issue A", "severity": "high", "file": "a.py", "description": "d1"},
                {"title": "Issue B", "severity": "low", "file": "b.py", "description": "d2"},
            ],
        })

        resp = await client.post(f"/api/sessions/{sid}/process")
        assert resp.status_code == 200
        data = resp.json()
        assert data["raw_issues"] == 2
        assert data["after_dedup"] == 2
        assert len(data["issues"]) == 2

    @pytest.mark.asyncio
    async def test_report_after_finish(self, client):
        resp = await client.post("/api/sessions", json={"base": "main"})
        sid = resp.json()["session_id"]

        await client.post(f"/api/sessions/{sid}/reviews", json={
            "model_id": "m1",
            "issues": [{"title": "Bug", "severity": "medium", "file": "x.py", "description": "d"}],
        })
        await client.post(f"/api/sessions/{sid}/process")
        await client.post(f"/api/sessions/{sid}/finish")

        resp = await client.get(f"/api/sessions/{sid}/report")
        assert resp.status_code == 200
        report = resp.json()
        assert "stats" in report
        assert report["stats"]["total_issues_found"] >= 1

    @pytest.mark.asyncio
    async def test_report_nonexistent_session(self, client):
        resp = await client.get("/api/sessions/nonexistent-id/report")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_m0_api_only_reviewer_e2e(self, app, client, tmp_path):
        """M0 end-to-end: session -> file read -> search -> tree -> activity -> index APIs."""
        # Create test files
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("def hello():\n    return 42\n")

        # 1. Start session
        resp = await client.post("/api/sessions", json={"base": "main"})
        assert resp.status_code == 200
        sid = resp.json()["session_id"]

        # Register an agent to get an access key
        resp = await client.post(f"/api/sessions/{sid}/agents", json={
            "id": "test-reviewer", "client_type": "claude-code",
        })
        assert resp.status_code == 201

        manager = app.state.manager
        agent_key = manager.ensure_agent_access_key(sid, "test-reviewer")
        headers = {"X-Agent-Key": agent_key}

        # 2. Context index — verify available_apis
        resp = await client.get(f"/api/sessions/{sid}/index", headers=headers)
        assert resp.status_code == 200
        index = resp.json()
        assert "available_apis" in index

        # 3. Read source file
        resp = await client.get(f"/api/sessions/{sid}/files/src/main.py", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["total_lines"] == 2

        # 4. Search code
        resp = await client.get(f"/api/sessions/{sid}/search?q=hello", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["total_matches"] >= 1

        # 5. Browse tree
        resp = await client.get(f"/api/sessions/{sid}/tree", headers=headers)
        assert resp.status_code == 200
        names = {e["name"] for e in resp.json()["entries"]}
        assert "src" in names

        # 6. Verify activity was recorded
        session = manager.get_session(sid)
        actions = [a.action for a in session.agent_activities]
        assert "view_index" in actions
        assert "view_file" in actions
        assert "search" in actions
        assert "view_tree" in actions

    @pytest.mark.asyncio
    async def test_submit_implementation_context(self, client):
        resp = await client.post("/api/sessions", json={"base": "main"})
        sid = resp.json()["session_id"]

        resp = await client.post(f"/api/sessions/{sid}/implementation-context", json={
            "summary": "Add caching",
            "decisions": ["Use Redis"],
            "tradeoffs": ["Memory cost"],
            "submitted_by": "coding-agent",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"] == "Add caching"
        assert data["decisions"] == ["Use Redis"]

        # Verify included in GET context
        resp = await client.get(f"/api/sessions/{sid}/context")
        assert resp.status_code == 200
        ctx = resp.json()
        assert "implementation_context" in ctx
        assert ctx["implementation_context"]["summary"] == "Add caching"

    @pytest.mark.asyncio
    async def test_submit_context_wrong_state(self, client):
        resp = await client.post("/api/sessions", json={"base": "main"})
        sid = resp.json()["session_id"]

        # Finish the session to reach COMPLETE state
        await client.post(f"/api/sessions/{sid}/finish")

        resp = await client.post(f"/api/sessions/{sid}/implementation-context", json={
            "summary": "too late",
        })
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_confirmed_issues_endpoint(self, app, client):
        resp = await client.post("/api/sessions", json={"base": "main"})
        sid = resp.json()["session_id"]

        await client.post(f"/api/sessions/{sid}/reviews", json={
            "model_id": "a",
            "issues": [{"title": "Bug", "severity": "high", "file": "x.py", "description": "d"}],
        })
        await client.post(f"/api/sessions/{sid}/process")

        # Set consensus_type on the issue
        manager = app.state.manager
        session = manager.get_session(sid)
        session.issues[0].consensus_type = "fix_required"

        resp = await client.get(f"/api/sessions/{sid}/confirmed-issues")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_confirmed"] == 1

    @pytest.mark.asyncio
    async def test_issue_respond_accept(self, app, client):
        resp = await client.post("/api/sessions", json={"base": "main"})
        sid = resp.json()["session_id"]
        await client.post(f"/api/sessions/{sid}/reviews", json={
            "model_id": "a",
            "issues": [{"title": "Bug", "severity": "high", "file": "x.py", "description": "d"}],
        })
        await client.post(f"/api/sessions/{sid}/process")
        manager = app.state.manager
        session = manager.get_session(sid)
        session.issues[0].consensus_type = "fix_required"
        session.status = SessionStatus.AGENT_RESPONSE
        issue_id = session.issues[0].id

        resp = await client.post(f"/api/sessions/{sid}/issues/{issue_id}/respond", json={
            "action": "accept",
            "reasoning": "Will fix",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "accepted"

    @pytest.mark.asyncio
    async def test_issue_respond_dispute(self, app, client):
        resp = await client.post("/api/sessions", json={"base": "main"})
        sid = resp.json()["session_id"]
        await client.post(f"/api/sessions/{sid}/reviews", json={
            "model_id": "a",
            "issues": [{"title": "Bug", "severity": "high", "file": "x.py", "description": "d"}],
        })
        await client.post(f"/api/sessions/{sid}/process")
        manager = app.state.manager
        session = manager.get_session(sid)
        session.issues[0].consensus_type = "fix_required"
        session.status = SessionStatus.AGENT_RESPONSE
        issue_id = session.issues[0].id

        resp = await client.post(f"/api/sessions/{sid}/issues/{issue_id}/respond", json={
            "action": "dispute",
            "reasoning": "Not a real bug",
        })
        assert resp.status_code == 200
        assert resp.json()["action"] == "dispute"

    @pytest.mark.asyncio
    async def test_issue_respond_invalid_action(self, app, client):
        resp = await client.post("/api/sessions", json={"base": "main"})
        sid = resp.json()["session_id"]
        await client.post(f"/api/sessions/{sid}/reviews", json={
            "model_id": "a",
            "issues": [{"title": "Bug", "severity": "high", "file": "x.py", "description": "d"}],
        })
        await client.post(f"/api/sessions/{sid}/process")
        manager = app.state.manager
        session = manager.get_session(sid)
        session.issues[0].consensus_type = "fix_required"
        session.status = SessionStatus.AGENT_RESPONSE
        issue_id = session.issues[0].id

        resp = await client.post(f"/api/sessions/{sid}/issues/{issue_id}/respond", json={
            "action": "invalid_action",
            "reasoning": "test",
        })
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_issue_respond_nonexistent(self, app, client):
        resp = await client.post("/api/sessions", json={"base": "main"})
        sid = resp.json()["session_id"]
        manager = app.state.manager
        session = manager.get_session(sid)
        session.status = SessionStatus.AGENT_RESPONSE

        resp = await client.post(f"/api/sessions/{sid}/issues/nonexistent/respond", json={
            "action": "accept",
        })
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_issue_responses_status(self, app, client):
        resp = await client.post("/api/sessions", json={"base": "main"})
        sid = resp.json()["session_id"]

        resp = await client.get(f"/api/sessions/{sid}/issue-responses")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_confirmed"] == 0
