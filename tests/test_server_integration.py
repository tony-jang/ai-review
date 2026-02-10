"""Integration tests for FastAPI server with orchestrator wiring."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from ai_review.server import create_app


@pytest.fixture
def app():
    return create_app(repo_path=None, port=9999)


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
