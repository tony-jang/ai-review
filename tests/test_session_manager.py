"""Tests for SessionManager."""

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest

from ai_review.models import AgentState, AgentStatus, DiffFile, IssueProgressStatus, IssueResponseAction, ModelConfig, OpinionAction, SessionStatus, Severity, _utcnow
from ai_review.session_manager import SessionManager


@pytest.fixture
def manager(tmp_path, monkeypatch) -> SessionManager:
    monkeypatch.chdir(tmp_path)
    return SessionManager()


class TestStartReview:
    @pytest.mark.asyncio
    async def test_creates_session(self, manager, tmp_path):
        result = await manager.start_review("main", repo_path=str(tmp_path), head="test-branch")
        assert "session_id" in result
        assert result["files_changed"] == 0  # no repo path

    @pytest.mark.asyncio
    async def test_session_in_reviewing_state(self, manager, tmp_path):
        result = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        session = manager.get_session(result["session_id"])
        assert session.status == SessionStatus.REVIEWING

    @pytest.mark.asyncio
    async def test_current_session_set(self, manager, tmp_path):
        await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        assert manager.current_session is not None


class TestSubmitReview:
    @pytest.mark.asyncio
    async def test_submit(self, manager, tmp_path):
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
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
    async def test_multiple_reviews(self, manager, tmp_path):
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]

        manager.submit_review(sid, "opus", [], "No issues")
        manager.submit_review(sid, "gpt", [], "No issues")

        reviews = manager.get_all_reviews(sid)
        assert len(reviews) == 2

    @pytest.mark.asyncio
    async def test_cannot_submit_in_wrong_state(self, manager, tmp_path):
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]
        session = manager.get_session(sid)
        session.status = SessionStatus.COMPLETE

        with pytest.raises(ValueError, match="Cannot submit review"):
            manager.submit_review(sid, "opus", [], "")


class TestAccessKeys:
    @pytest.mark.asyncio
    async def test_agent_access_key_is_stable_per_model(self, manager, tmp_path):
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]
        k1 = manager.ensure_agent_access_key(sid, "codex")
        k2 = manager.ensure_agent_access_key(sid, "codex")
        assert k1 == k2
        assert len(k1) >= 32

    @pytest.mark.asyncio
    async def test_human_assist_access_key_rotates(self, manager, tmp_path):
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]
        k1 = manager.issue_human_assist_access_key(sid)
        k2 = manager.issue_human_assist_access_key(sid)
        assert k1 != k2
        session = manager.get_session(sid)
        assert session.human_assist_access_key == k2


class TestReadFile:
    @pytest.mark.asyncio
    async def test_read_file(self, manager, tmp_path):
        (tmp_path / "hello.py").write_text("line1\nline2\nline3\nline4\nline5\n")
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]

        result = manager.read_file(sid, "hello.py")
        assert result["path"] == "hello.py"
        assert result["total_lines"] == 5
        assert len(result["lines"]) == 5
        assert result["lines"][0] == {"number": 1, "content": "line1"}

    @pytest.mark.asyncio
    async def test_read_file_with_range(self, manager, tmp_path):
        (tmp_path / "hello.py").write_text("\n".join(f"line{i}" for i in range(1, 11)))
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]

        result = manager.read_file(sid, "hello.py", start=3, end=5)
        assert result["start_line"] == 3
        assert result["end_line"] == 5
        assert len(result["lines"]) == 3
        assert result["lines"][0]["content"] == "line3"

    @pytest.mark.asyncio
    async def test_read_file_not_found(self, manager, tmp_path):
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]
        with pytest.raises(FileNotFoundError):
            manager.read_file(sid, "nonexistent.py")

    @pytest.mark.asyncio
    async def test_read_file_outside_repo(self, manager, tmp_path):
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]
        with pytest.raises(PermissionError):
            manager.read_file(sid, "../../etc/passwd")


class TestGetTree:
    @pytest.mark.asyncio
    async def test_get_tree(self, manager, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("pass")
        (tmp_path / "README.md").write_text("hi")
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]

        result = manager.get_tree(sid)
        assert result["path"] == "."
        names = {e["name"] for e in result["entries"]}
        assert "src" in names
        assert "README.md" in names

    @pytest.mark.asyncio
    async def test_excludes_git(self, manager, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "config").write_text("")
        (tmp_path / "code.py").write_text("pass")
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]

        result = manager.get_tree(sid)
        names = {e["name"] for e in result["entries"]}
        assert ".git" not in names
        assert "code.py" in names

    @pytest.mark.asyncio
    async def test_depth_limit(self, manager, tmp_path):
        (tmp_path / "a" / "b" / "c").mkdir(parents=True)
        (tmp_path / "a" / "b" / "c" / "deep.py").write_text("pass")
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]

        result = manager.get_tree(sid, depth=1)
        a_entry = next(e for e in result["entries"] if e["name"] == "a")
        assert a_entry["children"] == []  # depth=1 means no children

    @pytest.mark.asyncio
    async def test_outside_repo(self, manager, tmp_path):
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]
        with pytest.raises(PermissionError):
            manager.get_tree(sid, path="../../..")


class TestRecordActivity:
    @pytest.mark.asyncio
    async def test_records_activity(self, manager, tmp_path):
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]

        recorded = manager.record_activity(sid, "alpha", "view_file", "main.py:1-10")
        assert recorded is True
        session = manager.get_session(sid)
        assert len(session.agent_activities) == 1
        assert session.agent_activities[0].model_id == "alpha"

    @pytest.mark.asyncio
    async def test_dedup_suppresses_same_activity(self, manager, tmp_path):
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]

        manager.record_activity(sid, "alpha", "view_file", "main.py:1-10")
        suppressed = manager.record_activity(sid, "alpha", "view_file", "main.py:1-10")
        assert suppressed is False
        session = manager.get_session(sid)
        assert len(session.agent_activities) == 1

    @pytest.mark.asyncio
    async def test_different_target_not_suppressed(self, manager, tmp_path):
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]

        manager.record_activity(sid, "alpha", "view_file", "main.py:1-10")
        recorded = manager.record_activity(sid, "alpha", "view_file", "utils.py:1-5")
        assert recorded is True
        session = manager.get_session(sid)
        assert len(session.agent_activities) == 2

    @pytest.mark.asyncio
    async def test_resolve_model_id_from_key(self, manager, tmp_path):
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]
        key = manager.ensure_agent_access_key(sid, "opus")

        assert manager.resolve_model_id_from_key(sid, key) == "opus"
        assert manager.resolve_model_id_from_key(sid, "invalid") is None


class TestSearchCode:
    @pytest.mark.asyncio
    async def test_search_finds_match(self, manager, tmp_path):
        (tmp_path / "hello.py").write_text("def greet():\n    print('hello')\n")
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]

        result = await manager.search_code(sid, "greet")
        assert result["total_matches"] >= 1
        assert result["results"][0]["file"] == "hello.py"
        assert result["results"][0]["line"] == 1

    @pytest.mark.asyncio
    async def test_search_no_match(self, manager, tmp_path):
        (tmp_path / "hello.py").write_text("pass\n")
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]

        result = await manager.search_code(sid, "nonexistent_symbol")
        assert result["total_matches"] == 0

    @pytest.mark.asyncio
    async def test_search_glob_filter(self, manager, tmp_path):
        (tmp_path / "hello.py").write_text("target\n")
        (tmp_path / "hello.js").write_text("target\n")
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]

        result = await manager.search_code(sid, "target", glob="*.py")
        files = {r["file"] for r in result["results"]}
        assert "hello.py" in files
        assert "hello.js" not in files

    @pytest.mark.asyncio
    async def test_search_max_results(self, manager, tmp_path):
        (tmp_path / "many.py").write_text("\n".join(f"match_{i}" for i in range(50)))
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]

        result = await manager.search_code(sid, "match_", max_results=5)
        assert len(result["results"]) <= 5

    @pytest.mark.asyncio
    async def test_search_python_fallback(self, manager, tmp_path, monkeypatch):
        (tmp_path / "code.py").write_text("def my_func():\n    pass\n")
        monkeypatch.setattr("shutil.which", lambda cmd: None)
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]

        result = await manager.search_code(sid, "my_func")
        assert result["total_matches"] >= 1


class TestGetReviewContext:
    @pytest.mark.asyncio
    async def test_returns_context(self, manager, tmp_path):
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]

        ctx = manager.get_review_context(sid)
        assert "diff" in ctx
        assert "knowledge" in ctx
        assert "files" in ctx

    @pytest.mark.asyncio
    async def test_returns_context_index(self, manager, tmp_path):
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
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


class TestCreateIssues:
    @pytest.mark.asyncio
    async def test_creates_issues_from_reviews(self, manager, tmp_path):
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
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
    async def test_issues_retrievable(self, manager, tmp_path):
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]

        manager.submit_review(
            sid, "opus",
            [{"title": "Bug", "severity": "high", "file": "x.py", "description": "d"}],
        )
        manager.create_issues_from_reviews(sid)

        issues = manager.get_issues(sid)
        assert len(issues) == 1
        assert issues[0]["title"] == "Bug"

    @pytest.mark.asyncio
    async def test_normalizes_line_range_from_review(self, manager, tmp_path):
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]

        manager.submit_review(
            sid,
            "opus",
            [{
                "title": "Range bug",
                "severity": "high",
                "file": "x.py",
                "line_start": 20,
                "line_end": 10,
                "description": "range provided in reverse order",
            }],
        )
        issues = manager.create_issues_from_reviews(sid)
        issue = issues[0]
        assert issue.line == 10
        assert issue.line_start == 10
        assert issue.line_end == 20


class TestSubmitOpinion:
    @pytest.mark.asyncio
    async def test_submit_opinion(self, manager, tmp_path):
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
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
    async def test_issue_not_found(self, manager, tmp_path):
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]

        with pytest.raises(KeyError, match="Issue not found"):
            manager.submit_opinion(sid, "nonexistent", "gpt", "agree", "ok")

    @pytest.mark.asyncio
    async def test_extracts_mentions(self, manager, tmp_path):
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
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
    async def test_human_can_reopen_after_complete(self, manager, tmp_path):
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
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
    async def test_non_human_opinion_still_blocked_in_complete(self, manager, tmp_path):
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
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
    async def test_human_assist_can_reopen_after_complete(self, manager, tmp_path):
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
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


class TestFalsePositiveWithdraw:
    @pytest.mark.asyncio
    async def test_false_positive_rejected_from_raiser(self, manager, tmp_path):
        """Original raiser cannot submit false_positive on own issue."""
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]
        manager.submit_review(
            sid, "opus",
            [{"title": "Bug", "severity": "high", "file": "x.py", "description": "d"}],
        )
        issues = manager.create_issues_from_reviews(sid)
        issue_id = issues[0].id

        with pytest.raises(ValueError, match="Original raiser cannot submit false_positive"):
            manager.submit_opinion(sid, issue_id, "opus", "false_positive", "Not real")

    @pytest.mark.asyncio
    async def test_false_positive_accepted_from_non_raiser(self, manager, tmp_path):
        """Non-raiser can submit false_positive."""
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]
        manager.submit_review(
            sid, "opus",
            [{"title": "Bug", "severity": "high", "file": "x.py", "description": "d"}],
        )
        issues = manager.create_issues_from_reviews(sid)
        issue_id = issues[0].id

        result = manager.submit_opinion(sid, issue_id, "gpt", "false_positive", "Not a real issue")
        assert result["status"] == "accepted"
        issue = manager.get_session(sid).issues[0]
        assert issue.thread[-1].action == OpinionAction.FALSE_POSITIVE

    @pytest.mark.asyncio
    async def test_withdraw_rejected_from_non_raiser(self, manager, tmp_path):
        """Non-raiser cannot withdraw an issue."""
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]
        manager.submit_review(
            sid, "opus",
            [{"title": "Bug", "severity": "high", "file": "x.py", "description": "d"}],
        )
        issues = manager.create_issues_from_reviews(sid)
        issue_id = issues[0].id

        with pytest.raises(ValueError, match="Only the original raiser can withdraw"):
            manager.submit_opinion(sid, issue_id, "gpt", "withdraw", "Withdraw this")

    @pytest.mark.asyncio
    async def test_withdraw_closes_issue_immediately(self, manager, tmp_path):
        """WITHDRAW by raiser should immediately close the issue."""
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]
        manager.submit_review(
            sid, "opus",
            [{"title": "Bug", "severity": "high", "file": "x.py", "description": "d"}],
        )
        issues = manager.create_issues_from_reviews(sid)
        issue = issues[0]

        result = manager.submit_opinion(sid, issue.id, "opus", "withdraw", "I agree, false alarm")
        assert result["status"] == "accepted"
        assert issue.consensus is True
        assert issue.consensus_type == "closed"
        assert issue.final_severity == Severity.DISMISSED
        assert issue.progress_status == IssueProgressStatus.WONT_FIX


class TestGetPendingIssues:
    @pytest.mark.asyncio
    async def test_pending(self, manager, tmp_path):
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
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
    async def test_no_pending_after_opinion(self, manager, tmp_path):
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
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
    async def test_human_comment_reopens_pending_for_all_agents(self, manager, tmp_path):
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
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
    async def test_status(self, manager, tmp_path):
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]

        status = manager.get_session_status(sid)
        assert status["status"] == "reviewing"
        assert status["session_id"] == sid

    @pytest.mark.asyncio
    async def test_status_includes_agent_activities(self, manager, tmp_path):
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]

        manager.record_activity(sid, "alpha", "Read", "/src/a.py")
        manager.record_activity(sid, "alpha", "arv_report", "-n Bug -s high")
        manager.record_activity(sid, "beta", "Grep", "pattern:TODO")

        status = manager.get_session_status(sid)
        activities = status["agent_activities"]
        assert "alpha" in activities
        assert "beta" in activities
        assert len(activities["alpha"]) == 2
        assert len(activities["beta"]) == 1
        # Most recent first
        assert activities["alpha"][0]["action"] == "arv_report"
        assert activities["alpha"][1]["action"] == "Read"

    @pytest.mark.asyncio
    async def test_status_activities_empty_when_none(self, manager, tmp_path):
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]

        status = manager.get_session_status(sid)
        assert status["agent_activities"] == {}


class TestFinalReport:
    @pytest.mark.asyncio
    async def test_report(self, manager, tmp_path):
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
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


class TestGetFinalReportEnhanced:
    @pytest.mark.asyncio
    async def test_report_includes_lifecycle_fields(self, manager, tmp_path):
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]

        manager.submit_review(
            sid, "opus",
            [{"title": "Bug", "severity": "high", "file": "x.py", "description": "desc", "suggestion": "fix it"}],
        )
        manager.create_issues_from_reviews(sid)
        session = manager.get_session(sid)
        session.issues[0].consensus_type = "fix_required"

        report = manager.get_final_report(sid)
        assert "status" in report
        assert "issue_responses" in report
        assert "fix_commits" in report
        assert "verification_round" in report
        assert "implementation_context" in report

    @pytest.mark.asyncio
    async def test_issue_has_new_fields(self, manager, tmp_path):
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]

        manager.submit_review(
            sid, "opus",
            [{"title": "Bug", "severity": "high", "file": "x.py", "description": "desc", "suggestion": "fix it"}],
        )
        manager.create_issues_from_reviews(sid)
        session = manager.get_session(sid)
        session.issues[0].consensus_type = "fix_required"

        report = manager.get_final_report(sid)
        issue = report["issues"][0]
        assert "id" in issue
        assert "consensus_type" in issue
        assert issue["consensus_type"] == "fix_required"
        assert "description" in issue
        assert "suggestion" in issue
        assert "desc" in issue["description"]
        assert "fix it" in issue["suggestion"]

    @pytest.mark.asyncio
    async def test_stats_separates_fix_required_and_dismissed(self, manager, tmp_path):
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]

        manager.submit_review(
            sid, "opus",
            [
                {"title": "Bug A", "severity": "high", "file": "a.py", "description": "d"},
                {"title": "Bug B", "severity": "low", "file": "b.py", "description": "d"},
                {"title": "Bug C", "severity": "medium", "file": "c.py", "description": "d"},
            ],
        )
        manager.create_issues_from_reviews(sid)
        session = manager.get_session(sid)
        session.issues[0].consensus_type = "fix_required"
        session.issues[1].consensus_type = "dismissed"
        session.issues[2].consensus_type = "undecided"

        report = manager.get_final_report(sid)
        assert report["stats"]["fix_required"] == 1
        assert report["stats"]["dismissed"] == 1
        assert report["stats"]["consensus_reached"] == 2

    @pytest.mark.asyncio
    async def test_report_includes_issue_responses(self, manager, tmp_path):
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]

        manager.submit_review(
            sid, "opus",
            [{"title": "Bug", "severity": "high", "file": "x.py", "description": "d"}],
        )
        manager.create_issues_from_reviews(sid)
        session = manager.get_session(sid)
        session.issues[0].consensus_type = "fix_required"
        session.status = SessionStatus.AGENT_RESPONSE
        manager.submit_issue_response(sid, session.issues[0].id, "accept", "will fix")

        report = manager.get_final_report(sid)
        assert len(report["issue_responses"]) == 1
        assert report["issue_responses"][0]["action"] == "accept"

    @pytest.mark.asyncio
    async def test_report_includes_fix_commits(self, manager, tmp_path):
        sid, session = await _setup_fixing_session(manager, tmp_path)
        mock_delta = [DiffFile(path="db.py", additions=3, deletions=1, content="+fix")]

        with patch("ai_review.session_manager.collect_delta_diff", new_callable=AsyncMock, return_value=mock_delta):
            await manager.submit_fix_complete(sid, "def456", submitted_by="coding-agent")

        report = manager.get_final_report(sid)
        assert len(report["fix_commits"]) == 1
        assert report["fix_commits"][0]["commit_hash"] == "def456"
        assert report["verification_round"] == 1

    @pytest.mark.asyncio
    async def test_report_includes_implementation_context(self, manager, tmp_path):
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]

        manager.submit_implementation_context(sid, {
            "summary": "Add caching",
            "decisions": ["Use Redis"],
        })

        report = manager.get_final_report(sid)
        assert report["implementation_context"] is not None
        assert report["implementation_context"]["summary"] == "Add caching"

    @pytest.mark.asyncio
    async def test_report_empty_session(self, manager, tmp_path):
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]

        report = manager.get_final_report(sid)
        assert report["issues"] == []
        assert report["issue_responses"] == []
        assert report["fix_commits"] == []
        assert report["implementation_context"] is None
        assert report["stats"]["fix_required"] == 0
        assert report["stats"]["dismissed"] == 0


class TestGeneratePrMarkdown:
    @pytest.mark.asyncio
    async def test_includes_issue_table(self, manager, tmp_path):
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]

        manager.submit_review(
            sid, "opus",
            [
                {"title": "SQL injection", "severity": "high", "file": "db.py", "description": "raw sql"},
                {"title": "Memory leak", "severity": "medium", "file": "pool.py", "description": "not closed"},
            ],
        )
        manager.create_issues_from_reviews(sid)
        session = manager.get_session(sid)
        session.issues[0].consensus_type = "fix_required"
        session.issues[1].consensus_type = "dismissed"

        md = manager.generate_pr_markdown(sid)
        assert "## AI Review Summary" in md
        assert "Fix Required: 1" in md
        assert "Dismissed: 1" in md
        assert "| 1 |" in md
        assert "| 2 |" in md
        assert "SQL injection" in md
        assert "db.py" in md

    @pytest.mark.asyncio
    async def test_includes_fix_commits(self, manager, tmp_path):
        sid, session = await _setup_fixing_session(manager, tmp_path)
        mock_delta = [DiffFile(path="db.py", additions=3, deletions=1, content="+fix")]

        with patch("ai_review.session_manager.collect_delta_diff", new_callable=AsyncMock, return_value=mock_delta):
            await manager.submit_fix_complete(
                sid, "def456789",
                issues_addressed=[session.issues[0].id],
                submitted_by="coding-agent",
            )

        md = manager.generate_pr_markdown(sid)
        assert "### Fix Commits" in md
        assert "def4567" in md
        assert "coding-agent" in md

    @pytest.mark.asyncio
    async def test_includes_verification(self, manager, tmp_path):
        sid, session = await _setup_fixing_session(manager, tmp_path)
        mock_delta = [DiffFile(path="db.py", additions=3, deletions=1, content="+fix")]

        with patch("ai_review.session_manager.collect_delta_diff", new_callable=AsyncMock, return_value=mock_delta):
            await manager.submit_fix_complete(
                sid, "def456",
                issues_addressed=[i.id for i in session.issues],
                submitted_by="coding-agent",
            )

        md = manager.generate_pr_markdown(sid)
        assert "### Verification" in md
        assert "Rounds: 1" in md
        assert "All issues resolved" in md

    @pytest.mark.asyncio
    async def test_minimal_session(self, manager, tmp_path):
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]

        md = manager.generate_pr_markdown(sid)
        assert "## AI Review Summary" in md
        assert "Issues Found: 0" in md


class TestAddManualIssue:
    @pytest.mark.asyncio
    async def test_adds_issue(self, manager, tmp_path):
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
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
    async def test_cannot_add_in_idle_state(self, manager, tmp_path):
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]
        session = manager.get_session(sid)
        session.status = SessionStatus.IDLE

        with pytest.raises(ValueError, match="Cannot add issue"):
            manager.add_manual_issue(sid, "Bug", "high", "a.py", None, "desc")

    @pytest.mark.asyncio
    async def test_issue_has_raise_opinion(self, manager, tmp_path):
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]

        result = manager.add_manual_issue(
            sid, "Bug", "medium", "x.py", None, "description",
        )
        assert len(result["thread"]) == 1
        assert result["thread"][0]["action"] == "raise"
        assert result["thread"][0]["model_id"] == "human"


class TestUpdateAgent:
    @pytest.mark.asyncio
    async def test_updates_fields(self, manager, tmp_path):
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
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
    async def test_id_immutable(self, manager, tmp_path):
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]
        manager.add_agent(sid, {"id": "opus"})

        result = manager.update_agent(sid, "opus", {"id": "hacked"})
        assert result["id"] == "opus"

    @pytest.mark.asyncio
    async def test_nonexistent_raises(self, manager, tmp_path):
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]

        with pytest.raises(KeyError, match="Agent not found"):
            manager.update_agent(sid, "nonexistent", {"role": "test"})


class TestListSessions:
    @pytest.mark.asyncio
    async def test_empty(self, manager):
        assert manager.list_sessions() == []

    @pytest.mark.asyncio
    async def test_returns_all_sessions(self, manager, tmp_path):
        await manager.start_review("main", repo_path=str(tmp_path), head="test-branch")
        await manager.start_review("develop", repo_path=str(tmp_path), head="test-branch")

        sessions = manager.list_sessions()
        assert len(sessions) == 2
        # Sorted newest first
        assert sessions[0]["base"] in ("main", "develop")

    @pytest.mark.asyncio
    async def test_summary_fields(self, manager, tmp_path):
        await manager.start_review("main", repo_path=str(tmp_path), head="test-branch")
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
    async def test_deletes_session(self, manager, tmp_path):
        result = await manager.start_review("main", repo_path=str(tmp_path), head="test-branch")
        sid = result["session_id"]

        manager.delete_session(sid)
        assert sid not in manager.sessions
        assert manager.list_sessions() == []

    @pytest.mark.asyncio
    async def test_clears_current_if_deleted(self, manager, tmp_path):
        result = await manager.start_review("main", repo_path=str(tmp_path), head="test-branch")
        sid = result["session_id"]
        assert manager.current_session is not None

        manager.delete_session(sid)
        assert manager.current_session is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent_raises(self, manager):
        with pytest.raises(KeyError, match="Session not found"):
            manager.delete_session("nonexistent")

    @pytest.mark.asyncio
    async def test_delete_preserves_other_sessions(self, manager, tmp_path):
        r1 = await manager.start_review("main", repo_path=str(tmp_path), head="test-branch")
        r2 = await manager.start_review("develop", repo_path=str(tmp_path), head="test-branch")

        manager.delete_session(r1["session_id"])
        assert len(manager.sessions) == 1
        assert r2["session_id"] in manager.sessions


class TestSetCurrentSession:
    @pytest.mark.asyncio
    async def test_switches_session(self, manager, tmp_path):
        r1 = await manager.start_review("main", repo_path=str(tmp_path), head="test-branch")
        r2 = await manager.start_review("develop", repo_path=str(tmp_path), head="test-branch")

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


class TestAgentPresets:
    @pytest.mark.asyncio
    async def test_preset_crud(self, manager):
        added = manager.add_agent_preset({
            "id": "codex-sec",
            "client_type": "codex",
            "role": "security",
            "color": "#22C55E",
        })
        assert added["id"] == "codex-sec"

        listed = manager.list_agent_presets()
        assert any(p["id"] == "codex-sec" for p in listed)

        updated = manager.update_agent_preset("codex-sec", {"role": "security reviewer", "enabled": False})
        assert updated["role"] == "security reviewer"
        assert updated["enabled"] is False

        removed = manager.remove_agent_preset("codex-sec")
        assert removed["status"] == "removed"

    @pytest.mark.asyncio
    async def test_start_review_uses_selected_presets(self, manager, tmp_path):
        manager.add_agent_preset({
            "id": "codex-p1",
            "client_type": "codex",
            "role": "security",
        })
        manager.add_agent_preset({
            "id": "gemini-p1",
            "client_type": "gemini",
            "role": "performance",
        })

        started = await manager.start_review("main", repo_path=str(tmp_path), head="test-branch", preset_ids=["gemini-p1", "codex-p1"])
        session = manager.get_session(started["session_id"])
        assert [m.id for m in session.config.models] == ["gemini-p1", "codex-p1"]

    @pytest.mark.asyncio
    async def test_start_review_unknown_preset_raises(self, manager, tmp_path):
        with pytest.raises(ValueError, match="Unknown preset ids"):
            await manager.start_review("main", repo_path=str(tmp_path), head="test-branch", preset_ids=["missing"])

    @pytest.mark.asyncio
    async def test_start_review_empty_preset_ids_raises(self, manager, tmp_path):
        with pytest.raises(ValueError, match="at least one preset"):
            await manager.start_review("main", repo_path=str(tmp_path), head="test-branch", preset_ids=[])


class TestSubmitImplementationContext:
    @pytest.mark.asyncio
    async def test_submit_in_reviewing(self, manager, tmp_path):
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]

        result = manager.submit_implementation_context(sid, {
            "summary": "Add caching",
            "decisions": ["Use Redis"],
            "submitted_by": "coding-agent",
        })
        assert result["summary"] == "Add caching"
        assert result["decisions"] == ["Use Redis"]
        assert result["submitted_by"] == "coding-agent"
        assert result["submitted_at"] is not None

    @pytest.mark.asyncio
    async def test_submit_in_collecting(self, manager, tmp_path):
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]
        session = manager.get_session(sid)
        session.status = SessionStatus.COLLECTING

        result = manager.submit_implementation_context(sid, {"summary": "WIP"})
        assert result["summary"] == "WIP"

    @pytest.mark.asyncio
    async def test_submit_rejects_in_complete(self, manager, tmp_path):
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]
        session = manager.get_session(sid)
        session.status = SessionStatus.COMPLETE

        with pytest.raises(ValueError, match="Cannot submit implementation context"):
            manager.submit_implementation_context(sid, {"summary": "too late"})

    @pytest.mark.asyncio
    async def test_context_included_in_review_context(self, manager, tmp_path):
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]

        manager.submit_implementation_context(sid, {
            "summary": "Refactor auth module",
            "tradeoffs": ["Breaks backward compat"],
        })

        ctx = manager.get_review_context(sid)
        assert "implementation_context" in ctx
        assert ctx["implementation_context"]["summary"] == "Refactor auth module"
        assert ctx["implementation_context"]["tradeoffs"] == ["Breaks backward compat"]

    @pytest.mark.asyncio
    async def test_no_context_omits_key(self, manager, tmp_path):
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]

        ctx = manager.get_review_context(sid)
        assert "implementation_context" not in ctx


class TestAgentElapsed:
    @pytest.mark.asyncio
    async def test_elapsed_freezes_while_waiting_and_ticks_while_reviewing(self, manager, tmp_path):
        started = await manager.start_review("main", repo_path=str(tmp_path), head="test-branch")
        sid = started["session_id"]
        session = manager.get_session(sid)

        now = _utcnow()
        session.agent_states["codex"] = AgentState(
            model_id="codex",
            status=AgentStatus.WAITING,
            started_at=now - timedelta(minutes=10),
            updated_at=now - timedelta(minutes=2),
        )

        frozen_1 = manager.get_agent_runtime(sid, "codex")["elapsed_seconds"]
        frozen_2 = manager.get_agent_runtime(sid, "codex")["elapsed_seconds"]
        assert frozen_1 == frozen_2
        assert frozen_1 == pytest.approx(480, abs=1.0)

        session.agent_states["codex"].status = AgentStatus.REVIEWING
        ticking = manager.get_agent_runtime(sid, "codex")["elapsed_seconds"]
        assert ticking is not None
        assert ticking > frozen_2


def _setup_confirmed_session(manager, tmp_path):
    """Helper: create a session with confirmed issues."""
    import asyncio
    from ai_review.consensus import apply_consensus

    loop = asyncio.get_event_loop()
    start = loop.run_until_complete(manager.start_review(repo_path=str(tmp_path), head="test-branch"))
    sid = start["session_id"]
    manager.submit_review(
        sid, "opus",
        [
            {"title": "SQL injection", "severity": "critical", "file": "db.py", "description": "raw sql"},
            {"title": "Minor style", "severity": "low", "file": "style.py", "description": "naming"},
        ],
    )
    manager.submit_review(
        sid, "gpt",
        [
            {"title": "SQL injection dupe", "severity": "high", "file": "db.py", "description": "raw sql"},
        ],
    )
    issues = manager.create_issues_from_reviews(sid)
    session = manager.get_session(sid)

    # Submit opinions to reach consensus
    for issue in issues:
        for model in ["opus", "gpt"]:
            if issue.raised_by != model:
                manager.submit_opinion(
                    sid, issue.id, model,
                    "fix_required", "확인", "high",
                )

    apply_consensus(issues, session.config.consensus_threshold)
    session.status = SessionStatus.AGENT_RESPONSE
    manager.persist()
    return sid, session


class TestGetConfirmedIssues:
    @pytest.mark.asyncio
    async def test_returns_only_fix_required(self, manager, tmp_path):
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]
        manager.submit_review(
            sid, "opus",
            [{"title": "Bug", "severity": "high", "file": "x.py", "description": "d"}],
        )
        issues = manager.create_issues_from_reviews(sid)
        issues[0].consensus = True
        issues[0].consensus_type = "fix_required"
        issues[0].final_severity = Severity.HIGH

        result = manager.get_confirmed_issues(sid)
        assert result["total_confirmed"] == 1
        assert result["issues"][0]["title"] == "Bug"
        assert "consensus_summary" in result["issues"][0]

    @pytest.mark.asyncio
    async def test_empty_when_no_fix_required(self, manager, tmp_path):
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]
        manager.submit_review(
            sid, "opus",
            [{"title": "Bug", "severity": "high", "file": "x.py", "description": "d"}],
        )
        issues = manager.create_issues_from_reviews(sid)
        issues[0].consensus = True
        issues[0].consensus_type = "dismissed"

        result = manager.get_confirmed_issues(sid)
        assert result["total_confirmed"] == 0
        assert result["total_dismissed"] == 1

    @pytest.mark.asyncio
    async def test_consensus_summary_in_result(self, manager, tmp_path):
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]
        manager.submit_review(
            sid, "opus",
            [{"title": "Bug", "severity": "high", "file": "x.py", "description": "d"}],
        )
        issues = manager.create_issues_from_reviews(sid)
        issues[0].consensus_type = "fix_required"
        # thread has one RAISE opinion already
        result = manager.get_confirmed_issues(sid)
        assert "consensus_summary" in result["issues"][0]


class TestSubmitIssueResponse:
    @pytest.mark.asyncio
    async def test_accept(self, manager, tmp_path):
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]
        manager.submit_review(
            sid, "opus",
            [{"title": "Bug", "severity": "high", "file": "x.py", "description": "d"}],
        )
        issues = manager.create_issues_from_reviews(sid)
        issue = issues[0]
        issue.consensus_type = "fix_required"
        session = manager.get_session(sid)
        session.status = SessionStatus.AGENT_RESPONSE

        result = manager.submit_issue_response(sid, issue.id, "accept", "Will fix")
        assert result["status"] == "accepted"
        assert result["action"] == "accept"
        assert len(session.issue_responses) == 1

    @pytest.mark.asyncio
    async def test_dispute_adds_no_fix_opinion(self, manager, tmp_path):
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]
        manager.submit_review(
            sid, "opus",
            [{"title": "Bug", "severity": "high", "file": "x.py", "description": "d"}],
        )
        issues = manager.create_issues_from_reviews(sid)
        issue = issues[0]
        issue.consensus_type = "fix_required"
        session = manager.get_session(sid)
        session.status = SessionStatus.AGENT_RESPONSE
        original_turn = issue.turn

        result = manager.submit_issue_response(
            sid, issue.id, "dispute", "Not a real bug", submitted_by="coding-agent"
        )
        assert result["status"] == "accepted"
        assert issue.turn == original_turn + 1
        assert issue.consensus is False
        assert issue.consensus_type is None
        assert issue.final_severity is None
        # Thread should have a NO_FIX opinion with [DISPUTE] prefix
        last_opinion = issue.thread[-1]
        assert last_opinion.action == OpinionAction.NO_FIX
        assert "[DISPUTE]" in last_opinion.reasoning

    @pytest.mark.asyncio
    async def test_partial(self, manager, tmp_path):
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]
        manager.submit_review(
            sid, "opus",
            [{"title": "Bug", "severity": "high", "file": "x.py", "description": "d"}],
        )
        issues = manager.create_issues_from_reviews(sid)
        issue = issues[0]
        issue.consensus_type = "fix_required"
        session = manager.get_session(sid)
        session.status = SessionStatus.AGENT_RESPONSE

        result = manager.submit_issue_response(
            sid, issue.id, "partial", "Partially fixed", proposed_change="Changed X"
        )
        assert result["status"] == "accepted"
        assert result["action"] == "partial"

    @pytest.mark.asyncio
    async def test_duplicate_rejected(self, manager, tmp_path):
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]
        manager.submit_review(
            sid, "opus",
            [{"title": "Bug", "severity": "high", "file": "x.py", "description": "d"}],
        )
        issues = manager.create_issues_from_reviews(sid)
        issue = issues[0]
        issue.consensus_type = "fix_required"
        session = manager.get_session(sid)
        session.status = SessionStatus.AGENT_RESPONSE

        manager.submit_issue_response(sid, issue.id, "accept", "ok")
        with pytest.raises(ValueError, match="Duplicate"):
            manager.submit_issue_response(sid, issue.id, "accept", "again")

    @pytest.mark.asyncio
    async def test_nonexistent_issue_raises(self, manager, tmp_path):
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]
        session = manager.get_session(sid)
        session.status = SessionStatus.AGENT_RESPONSE

        with pytest.raises(KeyError, match="Issue not found"):
            manager.submit_issue_response(sid, "nonexistent", "accept")

    @pytest.mark.asyncio
    async def test_wrong_state_raises(self, manager, tmp_path):
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]
        session = manager.get_session(sid)
        session.status = SessionStatus.COMPLETE

        with pytest.raises(ValueError, match="Cannot submit issue response"):
            manager.submit_issue_response(sid, "any", "accept")

    @pytest.mark.asyncio
    async def test_callback_called(self, manager, tmp_path):
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]
        manager.submit_review(
            sid, "opus",
            [{"title": "Bug", "severity": "high", "file": "x.py", "description": "d"}],
        )
        issues = manager.create_issues_from_reviews(sid)
        issue = issues[0]
        issue.consensus_type = "fix_required"
        session = manager.get_session(sid)
        session.status = SessionStatus.AGENT_RESPONSE

        callback_args = []
        manager.on_issue_responded = lambda sid, iid, action: callback_args.append((sid, iid, action))

        manager.submit_issue_response(sid, issue.id, "accept", "ok")
        assert len(callback_args) == 1
        assert callback_args[0] == (sid, issue.id, "accept")


class TestGetIssueResponseStatus:
    @pytest.mark.asyncio
    async def test_tracking(self, manager, tmp_path):
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]
        manager.submit_review(
            sid, "opus",
            [
                {"title": "Bug A", "severity": "high", "file": "a.py", "description": "d"},
                {"title": "Bug B", "severity": "medium", "file": "b.py", "description": "d"},
            ],
        )
        issues = manager.create_issues_from_reviews(sid)
        for issue in issues:
            issue.consensus_type = "fix_required"
        session = manager.get_session(sid)
        session.status = SessionStatus.AGENT_RESPONSE

        status = manager.get_issue_response_status(sid)
        assert status["total_confirmed"] == 2
        assert status["total_responded"] == 0
        assert status["all_responded"] is False
        assert len(status["pending_ids"]) == 2

        manager.submit_issue_response(sid, issues[0].id, "accept", "ok")
        status = manager.get_issue_response_status(sid)
        assert status["total_responded"] == 1
        assert status["all_responded"] is False

        manager.submit_issue_response(sid, issues[1].id, "accept", "ok")
        status = manager.get_issue_response_status(sid)
        assert status["total_responded"] == 2
        assert status["all_responded"] is True
        assert len(status["pending_ids"]) == 0


async def _setup_fixing_session(manager, tmp_path):
    """Helper: create a session in FIXING state with confirmed issues."""
    from ai_review.consensus import apply_consensus

    start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
    sid = start["session_id"]
    manager.submit_review(
        sid, "opus",
        [{"title": "SQL injection", "severity": "critical", "file": "db.py", "description": "raw sql"}],
    )
    manager.submit_review(
        sid, "gpt",
        [{"title": "SQL injection dupe", "severity": "high", "file": "db.py", "description": "raw sql"}],
    )
    issues = manager.create_issues_from_reviews(sid)
    session = manager.get_session(sid)

    for issue in issues:
        for model in ["opus", "gpt"]:
            if issue.raised_by != model:
                manager.submit_opinion(
                    sid, issue.id, model,
                    "fix_required", "confirmed", "high",
                )

    apply_consensus(issues, session.config.consensus_threshold)
    session.status = SessionStatus.FIXING
    session.head = "abc123"
    manager.persist()
    return sid, session


class TestSubmitFixComplete:
    @pytest.mark.asyncio
    async def test_success(self, manager, tmp_path):
        sid, session = await _setup_fixing_session(manager, tmp_path)
        mock_delta = [DiffFile(path="db.py", additions=5, deletions=2, content="+fix")]

        with patch("ai_review.session_manager.collect_delta_diff", new_callable=AsyncMock, return_value=mock_delta):
            result = await manager.submit_fix_complete(sid, "def456")

        assert result["status"] == "accepted"
        assert result["commit_hash"] == "def456"
        assert result["delta_files_changed"] == 1

    @pytest.mark.asyncio
    async def test_wrong_state_raises(self, manager, tmp_path):
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]

        with pytest.raises(ValueError, match="Cannot submit fix-complete"):
            await manager.submit_fix_complete(sid, "abc")

    @pytest.mark.asyncio
    async def test_commit_recorded(self, manager, tmp_path):
        sid, session = await _setup_fixing_session(manager, tmp_path)

        with patch("ai_review.session_manager.collect_delta_diff", new_callable=AsyncMock, return_value=[]):
            await manager.submit_fix_complete(sid, "def456", submitted_by="coding-agent")

        assert len(session.fix_commits) == 1
        assert session.fix_commits[0].commit_hash == "def456"
        assert session.fix_commits[0].submitted_by == "coding-agent"

    @pytest.mark.asyncio
    async def test_delta_diff_collected(self, manager, tmp_path):
        sid, session = await _setup_fixing_session(manager, tmp_path)
        mock_delta = [DiffFile(path="db.py", additions=3, deletions=1, content="+patched")]

        with patch("ai_review.session_manager.collect_delta_diff", new_callable=AsyncMock, return_value=mock_delta):
            await manager.submit_fix_complete(sid, "def456")

        assert len(session.delta_diff) == 1
        assert session.delta_diff[0].path == "db.py"

    @pytest.mark.asyncio
    async def test_verification_round_incremented(self, manager, tmp_path):
        sid, session = await _setup_fixing_session(manager, tmp_path)
        assert session.verification_round == 0

        with patch("ai_review.session_manager.collect_delta_diff", new_callable=AsyncMock, return_value=[]):
            await manager.submit_fix_complete(sid, "def456")

        assert session.verification_round == 1

    @pytest.mark.asyncio
    async def test_head_updated(self, manager, tmp_path):
        sid, session = await _setup_fixing_session(manager, tmp_path)
        assert session.head == "abc123"

        with patch("ai_review.session_manager.collect_delta_diff", new_callable=AsyncMock, return_value=[]):
            await manager.submit_fix_complete(sid, "def456")

        assert session.head == "def456"

    @pytest.mark.asyncio
    async def test_transitions_to_verifying(self, manager, tmp_path):
        sid, session = await _setup_fixing_session(manager, tmp_path)

        with patch("ai_review.session_manager.collect_delta_diff", new_callable=AsyncMock, return_value=[]):
            await manager.submit_fix_complete(sid, "def456")

        assert session.status == SessionStatus.VERIFYING

    @pytest.mark.asyncio
    async def test_callback_called(self, manager, tmp_path):
        sid, session = await _setup_fixing_session(manager, tmp_path)
        callback_args = []
        manager.on_fix_completed = lambda s: callback_args.append(s)

        with patch("ai_review.session_manager.collect_delta_diff", new_callable=AsyncMock, return_value=[]):
            await manager.submit_fix_complete(sid, "def456")

        assert callback_args == [sid]

    @pytest.mark.asyncio
    async def test_auto_fills_issues_addressed(self, manager, tmp_path):
        sid, session = await _setup_fixing_session(manager, tmp_path)
        confirmed_ids = sorted(
            i.id for i in session.issues if i.consensus_type == "fix_required"
        )

        with patch("ai_review.session_manager.collect_delta_diff", new_callable=AsyncMock, return_value=[]):
            result = await manager.submit_fix_complete(sid, "def456")

        assert result["issues_addressed"] == confirmed_ids

    @pytest.mark.asyncio
    async def test_nonexistent_issue_raises(self, manager, tmp_path):
        sid, session = await _setup_fixing_session(manager, tmp_path)

        with pytest.raises(KeyError, match="not found or not fix_required"):
            with patch("ai_review.session_manager.collect_delta_diff", new_callable=AsyncMock, return_value=[]):
                await manager.submit_fix_complete(sid, "def456", issues_addressed=["nonexistent"])


class TestGetDeltaContext:
    @pytest.mark.asyncio
    async def test_full_fields(self, manager, tmp_path):
        sid, session = await _setup_fixing_session(manager, tmp_path)
        session.delta_diff = [DiffFile(path="db.py", additions=3, deletions=1, content="+fix")]

        with patch("ai_review.session_manager.collect_delta_diff", new_callable=AsyncMock, return_value=session.delta_diff):
            await manager.submit_fix_complete(sid, "def456")

        ctx = manager.get_delta_context(sid)
        assert ctx["session_id"] == sid
        assert "delta_diff" in ctx
        assert "delta_files" in ctx
        assert "verification_round" in ctx
        assert "fix_commits" in ctx
        assert "original_issues" in ctx
        assert ctx["delta_files"] == ["db.py"]
        assert ctx["verification_round"] == 1

    @pytest.mark.asyncio
    async def test_original_issues_only_fix_required(self, manager, tmp_path):
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]
        manager.submit_review(
            sid, "opus",
            [
                {"title": "Bug A", "severity": "high", "file": "a.py", "description": "d"},
                {"title": "Bug B", "severity": "low", "file": "b.py", "description": "d"},
            ],
        )
        issues = manager.create_issues_from_reviews(sid)
        issues[0].consensus_type = "fix_required"
        issues[1].consensus_type = "dismissed"

        ctx = manager.get_delta_context(sid)
        assert len(ctx["original_issues"]) == 1
        assert ctx["original_issues"][0]["title"] == "Bug A"


class TestGetActionableIssues:
    @pytest.mark.asyncio
    async def test_returns_only_fix_required(self, manager, tmp_path):
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]

        manager.submit_review(
            sid, "opus",
            [
                {"title": "Bug A", "severity": "high", "file": "a.py", "description": "d"},
                {"title": "Bug B", "severity": "low", "file": "b.py", "description": "d"},
            ],
        )
        manager.create_issues_from_reviews(sid)
        session = manager.get_session(sid)
        session.issues[0].consensus_type = "fix_required"
        session.issues[1].consensus_type = "dismissed"

        result = manager.get_actionable_issues(sid)
        assert result["total"] == 1
        assert result["unaddressed"] == 1
        assert result["issues"][0]["title"] == "Bug A"

    @pytest.mark.asyncio
    async def test_addressed_classification(self, manager, tmp_path):
        sid, session = await _setup_fixing_session(manager, tmp_path)
        mock_delta = [DiffFile(path="db.py", additions=3, deletions=1, content="+fix")]

        with patch("ai_review.session_manager.collect_delta_diff", new_callable=AsyncMock, return_value=mock_delta):
            await manager.submit_fix_complete(
                sid, "def456",
                issues_addressed=[session.issues[0].id],
            )

        result = manager.get_actionable_issues(sid)
        addressed = [i for i in result["issues"] if i["addressed"]]
        unaddressed = [i for i in result["issues"] if not i["addressed"]]
        assert len(addressed) >= 1
        assert result["unaddressed"] == len(unaddressed)

    @pytest.mark.asyncio
    async def test_by_file_grouping(self, manager, tmp_path):
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]

        manager.submit_review(
            sid, "opus",
            [
                {"title": "Bug A", "severity": "high", "file": "db.py", "description": "d"},
                {"title": "Bug B", "severity": "high", "file": "db.py", "description": "d2"},
                {"title": "Bug C", "severity": "high", "file": "pool.py", "description": "d3"},
            ],
        )
        manager.create_issues_from_reviews(sid)
        session = manager.get_session(sid)
        for issue in session.issues:
            issue.consensus_type = "fix_required"

        result = manager.get_actionable_issues(sid)
        assert "db.py" in result["by_file"]
        assert "pool.py" in result["by_file"]
        assert len(result["by_file"]["db.py"]) == 2
        assert len(result["by_file"]["pool.py"]) == 1

    @pytest.mark.asyncio
    async def test_empty_session(self, manager, tmp_path):
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]

        result = manager.get_actionable_issues(sid)
        assert result["total"] == 0
        assert result["unaddressed"] == 0
        assert result["issues"] == []
        assert result["by_file"] == {}


class TestBackwardCompatibility:
    @pytest.mark.asyncio
    async def test_report_without_new_fields(self, manager, tmp_path):
        """Session with no new M1~M4 data should still produce a valid report."""
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]

        manager.submit_review(
            sid, "opus",
            [{"title": "Bug", "severity": "high", "file": "x.py", "description": "d"}],
        )
        manager.create_issues_from_reviews(sid)

        report = manager.get_final_report(sid)
        # Original fields preserved
        assert "session_id" in report
        assert "issues" in report
        assert "stats" in report
        assert report["stats"]["total_issues_found"] == 1
        assert report["stats"]["after_dedup"] == 1
        assert "consensus_reached" in report["stats"]
        assert "dismissed" in report["stats"]
        # New fields present but empty/null
        assert report["issue_responses"] == []
        assert report["fix_commits"] == []
        assert report["verification_round"] == 0
        assert report["implementation_context"] is None
        assert report["stats"]["fix_required"] == 0

    @pytest.mark.asyncio
    async def test_actionable_issues_empty_session(self, manager, tmp_path):
        """Session with no issues should return empty actionable issues."""
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]

        result = manager.get_actionable_issues(sid)
        assert result["total"] == 0
        assert result["unaddressed"] == 0
        assert result["issues"] == []
        assert result["by_file"] == {}

    @pytest.mark.asyncio
    async def test_pr_markdown_minimal_session(self, manager, tmp_path):
        """Minimal session should produce valid markdown."""
        start = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        sid = start["session_id"]

        md = manager.generate_pr_markdown(sid)
        assert isinstance(md, str)
        assert "## AI Review Summary" in md
        assert "Issues Found: 0" in md
        # No fix commits or verification sections
        assert "### Fix Commits" not in md
        assert "### Verification" not in md


class TestStartReviewWithContext:
    """C1: start_review() with inline implementation_context."""

    @pytest.mark.asyncio
    async def test_inline_context_applied(self, manager, tmp_path):
        result = await manager.start_review(
            repo_path=str(tmp_path), head="test-branch",
            implementation_context={"summary": "test", "decisions": ["d1"]},
        )
        session = manager.get_session(result["session_id"])
        assert session.implementation_context is not None
        assert session.implementation_context.summary == "test"
        assert session.implementation_context.decisions == ["d1"]

    @pytest.mark.asyncio
    async def test_no_context_keeps_none(self, manager, tmp_path):
        result = await manager.start_review(repo_path=str(tmp_path), head="test-branch")
        session = manager.get_session(result["session_id"])
        assert session.implementation_context is None


class TestDismissIssue:
    """C4: dismiss_issue() in FIXING state."""

    def _setup_fixing_session(self, manager):
        """Create a session in FIXING state with a fix_required issue."""
        from ai_review.models import Issue, ReviewSession, Severity
        from ai_review.state import transition

        session = ReviewSession(base="main", status=SessionStatus.IDLE)
        manager.sessions[session.id] = session
        transition(session, SessionStatus.COLLECTING)
        transition(session, SessionStatus.REVIEWING)
        transition(session, SessionStatus.DEDUP)
        transition(session, SessionStatus.DELIBERATING)
        transition(session, SessionStatus.FIXING)

        issue = Issue(
            title="Bug", severity=Severity.HIGH, file="x.py",
            description="d", raised_by="opus",
            consensus=True, consensus_type="fix_required",
        )
        session.issues.append(issue)
        manager.persist()
        return session, issue

    def test_dismiss_success(self, manager):
        session, issue = self._setup_fixing_session(manager)
        result = manager.dismiss_issue(session.id, issue.id, "Not critical", "tony")
        assert result["status"] == "dismissed"
        assert len(session.dismissals) == 1
        assert session.dismissals[0].issue_id == issue.id

    def test_dismiss_wrong_state(self, manager):
        from ai_review.models import Issue, ReviewSession, Severity
        from ai_review.state import transition

        session = ReviewSession(base="main", status=SessionStatus.IDLE)
        manager.sessions[session.id] = session
        transition(session, SessionStatus.COLLECTING)
        transition(session, SessionStatus.REVIEWING)
        transition(session, SessionStatus.DEDUP)
        transition(session, SessionStatus.DELIBERATING)
        transition(session, SessionStatus.FIXING)
        transition(session, SessionStatus.VERIFYING)
        transition(session, SessionStatus.COMPLETE)
        issue = Issue(
            title="Bug", severity=Severity.HIGH, file="x.py",
            description="d", consensus=True, consensus_type="fix_required",
        )
        session.issues.append(issue)
        with pytest.raises(ValueError, match="Cannot dismiss"):
            manager.dismiss_issue(session.id, issue.id)

    def test_dismiss_non_fix_required(self, manager):
        from ai_review.models import Issue, Severity

        session, _ = self._setup_fixing_session(manager)
        nit = Issue(
            title="Nit", severity=Severity.LOW, file="y.py",
            description="d", consensus=True, consensus_type="dismissed",
        )
        session.issues.append(nit)
        with pytest.raises(ValueError, match="Can only dismiss fix_required"):
            manager.dismiss_issue(session.id, nit.id)

    def test_dismiss_duplicate(self, manager):
        session, issue = self._setup_fixing_session(manager)
        manager.dismiss_issue(session.id, issue.id)
        with pytest.raises(ValueError, match="Already dismissed"):
            manager.dismiss_issue(session.id, issue.id)

    def test_dismiss_not_found(self, manager):
        session, _ = self._setup_fixing_session(manager)
        with pytest.raises(KeyError, match="Issue not found"):
            manager.dismiss_issue(session.id, "nonexistent")

    def test_actionable_includes_dismissed_flag(self, manager):
        session, issue = self._setup_fixing_session(manager)
        manager.dismiss_issue(session.id, issue.id, "Not needed")
        result = manager.get_actionable_issues(session.id)
        assert result["issues"][0]["dismissed"] is True

    def test_report_includes_dismissals(self, manager):
        session, issue = self._setup_fixing_session(manager)
        manager.dismiss_issue(session.id, issue.id, "Not needed", "tony")
        report = manager.get_final_report(session.id)
        assert len(report["dismissals"]) == 1
        assert report["dismissals"][0]["issue_id"] == issue.id


class TestPersistDebounce:
    """Tests for debounced persist() and explicit flush()."""

    def test_sync_fallback_writes_immediately(self, tmp_path, monkeypatch):
        """persist() with no running event loop writes synchronously."""
        monkeypatch.chdir(tmp_path)
        mgr = SessionManager()
        assert not mgr._state_file.exists()
        mgr.persist()
        assert mgr._state_file.exists()

    @pytest.mark.asyncio
    async def test_debounce_coalesces_writes(self, tmp_path, monkeypatch):
        """Multiple persist() calls within debounce window produce one write."""
        monkeypatch.chdir(tmp_path)
        mgr = SessionManager()
        write_count = 0
        original_write = mgr._write_snapshot

        def counting_write(payload):
            nonlocal write_count
            write_count += 1
            original_write(payload)

        mgr._write_snapshot = counting_write
        write_count = 0  # reset after init writes

        # Fire 5 rapid persists
        for _ in range(5):
            mgr.persist()

        # No write yet (debounce pending)
        assert mgr._dirty is True

        # Wait for debounce to fire + to_thread to complete
        await asyncio.sleep(0.3)

        assert write_count == 1
        assert mgr._dirty is False

    @pytest.mark.asyncio
    async def test_flush_writes_immediately(self, tmp_path, monkeypatch):
        """flush() bypasses debounce and writes immediately."""
        monkeypatch.chdir(tmp_path)
        mgr = SessionManager()
        write_count = 0
        original_write = mgr._write_snapshot

        def counting_write(payload):
            nonlocal write_count
            write_count += 1
            original_write(payload)

        mgr._write_snapshot = counting_write
        write_count = 0

        mgr.persist()
        assert mgr._dirty is True

        await mgr.flush()
        assert mgr._dirty is False
        assert write_count == 1

    @pytest.mark.asyncio
    async def test_flush_noop_when_clean(self, tmp_path, monkeypatch):
        """flush() does nothing when not dirty."""
        monkeypatch.chdir(tmp_path)
        mgr = SessionManager()

        # Clear any pending dirty state from __init__
        await mgr.flush()

        write_count = 0
        original_write = mgr._write_snapshot

        def counting_write(payload):
            nonlocal write_count
            write_count += 1
            original_write(payload)

        mgr._write_snapshot = counting_write

        await mgr.flush()
        assert write_count == 0
