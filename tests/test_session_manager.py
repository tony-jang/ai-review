"""Tests for SessionManager."""

import pytest

from ai_review.models import SessionStatus, Severity
from ai_review.session_manager import SessionManager


@pytest.fixture
def manager() -> SessionManager:
    return SessionManager()


class TestStartReview:
    @pytest.mark.asyncio
    async def test_creates_session(self, manager):
        result = await manager.start_review("main")
        assert "session_id" in result
        assert result["files_changed"] == 0  # no repo path

    @pytest.mark.asyncio
    async def test_session_in_reviewing_state(self, manager):
        result = await manager.start_review()
        session = manager.get_session(result["session_id"])
        assert session.status == SessionStatus.REVIEWING

    @pytest.mark.asyncio
    async def test_current_session_set(self, manager):
        await manager.start_review()
        assert manager.current_session is not None


class TestSubmitReview:
    @pytest.mark.asyncio
    async def test_submit(self, manager):
        start = await manager.start_review()
        sid = start["session_id"]

        result = manager.submit_review(
            sid,
            "opus",
            [
                {
                    "title": "Bug",
                    "severity": "high",
                    "file": "main.py",
                    "description": "Found a bug",
                }
            ],
            summary="One issue found",
        )
        assert result["status"] == "accepted"
        assert result["issue_count"] == 1

    @pytest.mark.asyncio
    async def test_multiple_reviews(self, manager):
        start = await manager.start_review()
        sid = start["session_id"]

        manager.submit_review(sid, "opus", [], "No issues")
        manager.submit_review(sid, "gpt", [], "No issues")

        reviews = manager.get_all_reviews(sid)
        assert len(reviews) == 2

    @pytest.mark.asyncio
    async def test_cannot_submit_in_wrong_state(self, manager):
        start = await manager.start_review()
        sid = start["session_id"]
        session = manager.get_session(sid)
        session.status = SessionStatus.COMPLETE

        with pytest.raises(ValueError, match="Cannot submit review"):
            manager.submit_review(sid, "opus", [], "")


class TestGetReviewContext:
    @pytest.mark.asyncio
    async def test_returns_context(self, manager):
        start = await manager.start_review()
        sid = start["session_id"]

        ctx = manager.get_review_context(sid)
        assert "diff" in ctx
        assert "knowledge" in ctx
        assert "files" in ctx


class TestCreateIssues:
    @pytest.mark.asyncio
    async def test_creates_issues_from_reviews(self, manager):
        start = await manager.start_review()
        sid = start["session_id"]

        manager.submit_review(
            sid,
            "opus",
            [
                {"title": "Bug A", "severity": "high", "file": "a.py", "description": "desc"},
                {"title": "Bug B", "severity": "low", "file": "b.py", "description": "desc"},
            ],
        )
        manager.submit_review(
            sid,
            "gpt",
            [
                {"title": "Bug C", "severity": "medium", "file": "c.py", "description": "desc"},
            ],
        )

        issues = manager.create_issues_from_reviews(sid)
        assert len(issues) == 3
        assert issues[0].raised_by == "opus"
        assert issues[2].raised_by == "gpt"
        # Each issue should have a RAISE opinion from the reviewer
        assert issues[0].thread[0].action.value == "raise"

    @pytest.mark.asyncio
    async def test_issues_retrievable(self, manager):
        start = await manager.start_review()
        sid = start["session_id"]

        manager.submit_review(
            sid, "opus",
            [{"title": "Bug", "severity": "high", "file": "x.py", "description": "d"}],
        )
        manager.create_issues_from_reviews(sid)

        issues = manager.get_issues(sid)
        assert len(issues) == 1
        assert issues[0]["title"] == "Bug"


class TestSubmitOpinion:
    @pytest.mark.asyncio
    async def test_submit_opinion(self, manager):
        start = await manager.start_review()
        sid = start["session_id"]

        manager.submit_review(
            sid, "opus",
            [{"title": "Bug", "severity": "high", "file": "x.py", "description": "d"}],
        )
        issues = manager.create_issues_from_reviews(sid)
        issue_id = issues[0].id

        # Need to be in DELIBERATING or REVIEWING state
        result = manager.submit_opinion(
            sid, issue_id, "gpt", "agree", "I agree this is important", "high"
        )
        assert result["status"] == "accepted"
        assert result["thread_length"] == 2  # RAISE + AGREE

    @pytest.mark.asyncio
    async def test_issue_not_found(self, manager):
        start = await manager.start_review()
        sid = start["session_id"]

        with pytest.raises(KeyError, match="Issue not found"):
            manager.submit_opinion(sid, "nonexistent", "gpt", "agree", "ok")


class TestGetPendingIssues:
    @pytest.mark.asyncio
    async def test_pending(self, manager):
        start = await manager.start_review()
        sid = start["session_id"]

        manager.submit_review(
            sid, "opus",
            [{"title": "Bug", "severity": "high", "file": "x.py", "description": "d"}],
        )
        manager.create_issues_from_reviews(sid)

        # gpt hasn't submitted any opinion yet
        pending = manager.get_pending_issues(sid, "gpt")
        assert len(pending) == 1

    @pytest.mark.asyncio
    async def test_no_pending_after_opinion(self, manager):
        start = await manager.start_review()
        sid = start["session_id"]

        manager.submit_review(
            sid, "opus",
            [{"title": "Bug", "severity": "high", "file": "x.py", "description": "d"}],
        )
        issues = manager.create_issues_from_reviews(sid)
        issue_id = issues[0].id

        manager.submit_opinion(sid, issue_id, "gpt", "agree", "ok")

        pending = manager.get_pending_issues(sid, "gpt")
        assert len(pending) == 0


class TestSessionStatus:
    @pytest.mark.asyncio
    async def test_status(self, manager):
        start = await manager.start_review()
        sid = start["session_id"]

        status = manager.get_session_status(sid)
        assert status["status"] == "reviewing"
        assert status["session_id"] == sid


class TestFinalReport:
    @pytest.mark.asyncio
    async def test_report(self, manager):
        start = await manager.start_review()
        sid = start["session_id"]

        manager.submit_review(
            sid, "opus",
            [
                {"title": "Bug", "severity": "high", "file": "x.py", "description": "d"},
                {"title": "Perf", "severity": "low", "file": "y.py", "description": "d"},
            ],
        )
        manager.create_issues_from_reviews(sid)

        report = manager.get_final_report(sid)
        assert report["stats"]["total_issues_found"] == 2
        assert report["stats"]["after_dedup"] == 2
        assert len(report["issues"]) == 2


class TestAddManualIssue:
    @pytest.mark.asyncio
    async def test_adds_issue(self, manager):
        start = await manager.start_review()
        sid = start["session_id"]

        result = manager.add_manual_issue(
            sid, "Manual Bug", "high", "main.py", 42,
            "Found manually", "Fix this",
        )
        assert result["title"] == "Manual Bug"
        assert result["raised_by"] == "human"
        assert result["severity"] == "high"

        session = manager.get_session(sid)
        assert len(session.issues) == 1
        assert session.issues[0].raised_by == "human"

    @pytest.mark.asyncio
    async def test_cannot_add_in_idle_state(self, manager):
        start = await manager.start_review()
        sid = start["session_id"]
        session = manager.get_session(sid)
        session.status = SessionStatus.IDLE

        with pytest.raises(ValueError, match="Cannot add issue"):
            manager.add_manual_issue(sid, "Bug", "high", "a.py", None, "desc")

    @pytest.mark.asyncio
    async def test_issue_has_raise_opinion(self, manager):
        start = await manager.start_review()
        sid = start["session_id"]

        result = manager.add_manual_issue(
            sid, "Bug", "medium", "x.py", None, "description",
        )
        assert len(result["thread"]) == 1
        assert result["thread"][0]["action"] == "raise"
        assert result["thread"][0]["model_id"] == "human"


class TestSessionNotFound:
    def test_get_nonexistent_session(self, manager):
        with pytest.raises(KeyError, match="Session not found"):
            manager.get_session("nonexistent")
