"""Tests for SessionManager."""

import pytest

from ai_review.models import DiffFile, ModelConfig, SessionStatus, Severity
from ai_review.session_manager import SessionManager


@pytest.fixture
def manager(tmp_path) -> SessionManager:
    return SessionManager(repo_path=str(tmp_path))


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

    @pytest.mark.asyncio
    async def test_returns_context_index(self, manager):
        start = await manager.start_review()
        sid = start["session_id"]
        session = manager.get_session(sid)
        session.diff = [
            DiffFile(
                path="src/main.py",
                additions=3,
                deletions=1,
                content=(
                    "diff --git a/src/main.py b/src/main.py\n"
                    "index 1..2 100644\n"
                    "--- a/src/main.py\n"
                    "+++ b/src/main.py\n"
                    "@@ -10,2 +10,4 @@\n"
                    "+print('x')\n"
                ),
            ),
        ]

        idx = manager.get_context_index(sid)
        assert idx["session_id"] == sid
        assert idx["files"][0]["path"] == "src/main.py"
        assert idx["files"][0]["status"] == "modified"
        assert idx["files"][0]["hunks"][0]["new_start"] == 10
        assert "suggested_commands" in idx


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

    @pytest.mark.asyncio
    async def test_extracts_mentions(self, manager):
        start = await manager.start_review()
        sid = start["session_id"]
        session = manager.get_session(sid)
        session.config.models = [
            ModelConfig(id="codex"),
            ModelConfig(id="opus"),
        ]

        manager.submit_review(
            sid, "opus",
            [{"title": "Bug", "severity": "high", "file": "x.py", "description": "d"}],
        )
        issues = manager.create_issues_from_reviews(sid)
        issue_id = issues[0].id

        manager.submit_opinion(sid, issue_id, "human", "clarify", "@codex 확인 부탁, @unknown 제외")
        issue = manager.get_session(sid).issues[0]
        assert issue.thread[-1].mentions == ["codex"]

    @pytest.mark.asyncio
    async def test_human_can_reopen_after_complete(self, manager):
        start = await manager.start_review()
        sid = start["session_id"]
        manager.submit_review(
            sid, "opus",
            [{"title": "Bug", "severity": "high", "file": "x.py", "description": "d"}],
        )
        issues = manager.create_issues_from_reviews(sid)
        issue = issues[0]
        issue.consensus = True
        issue.final_severity = Severity.HIGH
        session = manager.get_session(sid)
        session.status = SessionStatus.COMPLETE

        result = manager.submit_opinion(sid, issue.id, "human", "clarify", "재검토 부탁")

        assert result["status"] == "accepted"
        assert session.status == SessionStatus.DELIBERATING
        assert issue.consensus is False
        assert issue.final_severity is None
        assert issue.turn == 1

    @pytest.mark.asyncio
    async def test_non_human_opinion_still_blocked_in_complete(self, manager):
        start = await manager.start_review()
        sid = start["session_id"]
        manager.submit_review(
            sid, "opus",
            [{"title": "Bug", "severity": "high", "file": "x.py", "description": "d"}],
        )
        issues = manager.create_issues_from_reviews(sid)
        session = manager.get_session(sid)
        session.status = SessionStatus.COMPLETE

        with pytest.raises(ValueError, match="Cannot submit opinion"):
            manager.submit_opinion(sid, issues[0].id, "codex1", "agree", "ok")

    @pytest.mark.asyncio
    async def test_human_assist_can_reopen_after_complete(self, manager):
        start = await manager.start_review()
        sid = start["session_id"]
        manager.submit_review(
            sid, "opus",
            [{"title": "Bug", "severity": "high", "file": "x.py", "description": "d"}],
        )
        issues = manager.create_issues_from_reviews(sid)
        issue = issues[0]
        issue.consensus = True
        issue.final_severity = Severity.HIGH
        session = manager.get_session(sid)
        session.status = SessionStatus.COMPLETE

        result = manager.submit_opinion(sid, issue.id, "human-assist", "disagree", "근거 부족")

        assert result["status"] == "accepted"
        assert session.status == SessionStatus.DELIBERATING
        assert issue.consensus is False
        assert issue.final_severity is None
        assert issue.turn == 1


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

    @pytest.mark.asyncio
    async def test_human_comment_reopens_pending_for_all_agents(self, manager):
        start = await manager.start_review()
        sid = start["session_id"]

        manager.submit_review(
            sid, "codex1",
            [{"title": "Bug", "severity": "high", "file": "x.py", "description": "d"}],
        )
        issues = manager.create_issues_from_reviews(sid)
        issue_id = issues[0].id

        # codex2 answers first turn
        manager.submit_opinion(sid, issue_id, "codex2", "agree", "ok")
        assert manager.get_pending_issues(sid, "codex2") == []

        # human comment opens next turn
        manager.submit_opinion(sid, issue_id, "human", "clarify", "한번 더 봐줘 @codex2")
        pending = manager.get_pending_issues(sid, "codex2")
        assert len(pending) == 1


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


class TestUpdateAgent:
    @pytest.mark.asyncio
    async def test_updates_fields(self, manager):
        start = await manager.start_review()
        sid = start["session_id"]
        manager.add_agent(sid, {"id": "opus", "role": "general"})

        result = manager.update_agent(sid, "opus", {
            "role": "security",
            "color": "#EF4444",
            "description": "Security specialist",
            "enabled": False,
        })
        assert result["role"] == "security"
        assert result["color"] == "#EF4444"
        assert result["description"] == "Security specialist"
        assert result["enabled"] is False

    @pytest.mark.asyncio
    async def test_id_immutable(self, manager):
        start = await manager.start_review()
        sid = start["session_id"]
        manager.add_agent(sid, {"id": "opus"})

        result = manager.update_agent(sid, "opus", {"id": "hacked"})
        assert result["id"] == "opus"

    @pytest.mark.asyncio
    async def test_nonexistent_raises(self, manager):
        start = await manager.start_review()
        sid = start["session_id"]

        with pytest.raises(KeyError, match="Agent not found"):
            manager.update_agent(sid, "nonexistent", {"role": "test"})


class TestListSessions:
    @pytest.mark.asyncio
    async def test_empty(self, manager):
        assert manager.list_sessions() == []

    @pytest.mark.asyncio
    async def test_returns_all_sessions(self, manager):
        await manager.start_review("main")
        await manager.start_review("develop")

        sessions = manager.list_sessions()
        assert len(sessions) == 2
        # Sorted newest first
        assert sessions[0]["base"] in ("main", "develop")

    @pytest.mark.asyncio
    async def test_summary_fields(self, manager):
        await manager.start_review("main")
        sessions = manager.list_sessions()
        s = sessions[0]
        assert "session_id" in s
        assert "status" in s
        assert "base" in s
        assert "head" in s
        assert "review_count" in s
        assert "issue_count" in s
        assert "files_changed" in s
        assert "created_at" in s


class TestDeleteSession:
    @pytest.mark.asyncio
    async def test_deletes_session(self, manager):
        result = await manager.start_review("main")
        sid = result["session_id"]

        manager.delete_session(sid)
        assert sid not in manager.sessions
        assert manager.list_sessions() == []

    @pytest.mark.asyncio
    async def test_clears_current_if_deleted(self, manager):
        result = await manager.start_review("main")
        sid = result["session_id"]
        assert manager.current_session is not None

        manager.delete_session(sid)
        assert manager.current_session is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent_raises(self, manager):
        with pytest.raises(KeyError, match="Session not found"):
            manager.delete_session("nonexistent")

    @pytest.mark.asyncio
    async def test_delete_preserves_other_sessions(self, manager):
        r1 = await manager.start_review("main")
        r2 = await manager.start_review("develop")

        manager.delete_session(r1["session_id"])
        assert len(manager.sessions) == 1
        assert r2["session_id"] in manager.sessions


class TestSetCurrentSession:
    @pytest.mark.asyncio
    async def test_switches_session(self, manager):
        r1 = await manager.start_review("main")
        r2 = await manager.start_review("develop")

        # After second start, current is r2
        assert manager.current_session.id == r2["session_id"]

        manager.set_current_session(r1["session_id"])
        assert manager.current_session.id == r1["session_id"]

    @pytest.mark.asyncio
    async def test_nonexistent_raises(self, manager):
        with pytest.raises(KeyError, match="Session not found"):
            manager.set_current_session("nonexistent")


class TestSessionNotFound:
    def test_get_nonexistent_session(self, manager):
        with pytest.raises(KeyError, match="Session not found"):
            manager.get_session("nonexistent")
