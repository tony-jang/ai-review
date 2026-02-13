"""Integration tests for FastAPI server with orchestrator wiring."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from ai_review.server import create_app


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
