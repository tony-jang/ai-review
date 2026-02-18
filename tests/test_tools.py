"""Tests for MCP tools (via SessionManager, since @mcp.tool wraps functions)."""

import pytest

from ai_review.session_manager import SessionManager
from ai_review.tools import set_manager, _get_manager


@pytest.fixture
def manager():
    mgr = SessionManager()
    set_manager(mgr)
    yield mgr
    set_manager(None)


class TestManagerViaTools:
    """Test the tool logic through the SessionManager that tools delegate to."""

    @pytest.mark.asyncio
    async def test_start_review(self, manager):
        result = await manager.start_review("main", head="HEAD", repo_path="/tmp")
        assert "session_id" in result
        assert result["files_changed"] == 0

    @pytest.mark.asyncio
    async def test_get_review_context(self, manager):
        start = await manager.start_review(head="HEAD", repo_path="/tmp")
        ctx = manager.get_review_context(start["session_id"])
        assert "diff" in ctx
        assert "knowledge" in ctx

    @pytest.mark.asyncio
    async def test_submit_review(self, manager):
        start = await manager.start_review(head="HEAD", repo_path="/tmp")
        result = manager.submit_review(
            start["session_id"],
            "opus",
            [{"title": "Bug", "severity": "high", "file": "a.py", "description": "d"}],
        )
        assert result["status"] == "accepted"

    @pytest.mark.asyncio
    async def test_get_session_status(self, manager):
        start = await manager.start_review(head="HEAD", repo_path="/tmp")
        status = manager.get_session_status(start["session_id"])
        assert status["status"] == "reviewing"

    @pytest.mark.asyncio
    async def test_get_final_report(self, manager):
        start = await manager.start_review(head="HEAD", repo_path="/tmp")
        manager.submit_review(
            start["session_id"],
            "opus",
            [{"title": "Bug", "severity": "high", "file": "a.py", "description": "d"}],
        )
        report = manager.get_final_report(start["session_id"])
        assert report["stats"]["total_issues_found"] == 1

    @pytest.mark.asyncio
    async def test_full_flow(self, manager):
        """E2E flow: start → review → create issues → opinion → report."""
        # Start
        start = await manager.start_review(head="HEAD", repo_path="/tmp")
        sid = start["session_id"]

        # Submit reviews from two models
        manager.submit_review(
            sid, "opus",
            [{"title": "SQL Injection", "severity": "critical", "file": "db.py", "description": "Unsafe query"}],
        )
        manager.submit_review(
            sid, "gpt",
            [{"title": "Perf issue", "severity": "medium", "file": "api.py", "description": "Slow query"}],
        )

        # Create issues
        issues = manager.create_issues_from_reviews(sid)
        assert len(issues) == 2

        # Submit opinions
        manager.submit_opinion(
            sid, issues[0].id, "gpt", "agree", "Confirmed SQL injection risk", "critical"
        )
        manager.submit_opinion(
            sid, issues[1].id, "opus", "disagree", "Acceptable latency", "low"
        )

        # Check pending (opus has no pending since it raised issue 0 and opined on issue 1)
        pending_opus = manager.get_pending_issues(sid, "opus")
        assert len(pending_opus) == 0

        # Final report
        report = manager.get_final_report(sid)
        assert report["stats"]["total_issues_found"] == 2
        assert report["stats"]["after_dedup"] == 2

    def test_get_manager_not_set(self):
        set_manager(None)
        with pytest.raises(RuntimeError, match="not initialized"):
            _get_manager()
