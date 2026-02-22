"""Tests for issue progress status tracking."""

import pytest

from ai_review.consensus import check_consensus, determine_consensus_type, determine_final_severity
from ai_review.models import (
    Issue,
    IssueProgressStatus,
    Opinion,
    OpinionAction,
    SessionStatus,
    Severity,
)
from ai_review.session_manager import SessionManager


def _make_opinion(model_id, action, severity=None, confidence=1.0):
    return Opinion(
        model_id=model_id,
        action=action,
        reasoning=f"{model_id} says {action.value}",
        suggested_severity=severity,
        confidence=confidence,
    )


@pytest.fixture
def manager(tmp_path, monkeypatch) -> SessionManager:
    monkeypatch.chdir(tmp_path)
    return SessionManager()


@pytest.fixture
async def session_with_issue(manager, tmp_path):
    """Create a session in DELIBERATING state with one issue."""
    result = await manager.start_review("main", repo_path=str(tmp_path), head="test-branch")
    sid = result["session_id"]
    session = manager.get_session(sid)
    manager.submit_review(sid, "reviewer-a", [{
        "title": "Bug found",
        "severity": "high",
        "file": "main.py",
        "description": "There is a bug",
    }])
    issues = manager.create_issues_from_reviews(sid)
    session.status = SessionStatus.DELIBERATING
    return sid, issues[0]


class TestIssueProgressStatusModel:
    def test_default_progress_status(self):
        issue = Issue(title="test", severity=Severity.HIGH, file="t.py")
        assert issue.progress_status == IssueProgressStatus.REPORTED

    def test_status_change_opinion_fields(self):
        op = Opinion(
            model_id="bot",
            action=OpinionAction.STATUS_CHANGE,
            reasoning="changed",
            status_value="fixed",
        )
        assert op.action == OpinionAction.STATUS_CHANGE
        assert op.status_value == "fixed"

    def test_status_value_none_for_normal_opinion(self):
        op = Opinion(
            model_id="bot",
            action=OpinionAction.FIX_REQUIRED,
            reasoning="fix it",
        )
        assert op.status_value is None


class TestChangeIssueStatus:
    @pytest.mark.asyncio
    async def test_author_reported_to_fixed(self, manager, session_with_issue):
        sid, issue = session_with_issue
        result = manager.change_issue_status(sid, issue.id, "fixed", author="coder")
        assert result["status"] == "changed"
        assert result["progress_status"] == "fixed"
        assert result["previous_status"] == "reported"
        assert issue.progress_status == IssueProgressStatus.FIXED

    @pytest.mark.asyncio
    async def test_author_reported_to_wont_fix(self, manager, session_with_issue):
        sid, issue = session_with_issue
        result = manager.change_issue_status(sid, issue.id, "wont_fix", author="coder")
        assert result["progress_status"] == "wont_fix"
        assert issue.progress_status == IssueProgressStatus.WONT_FIX

    @pytest.mark.asyncio
    async def test_reviewer_fixed_to_completed(self, manager, session_with_issue):
        sid, issue = session_with_issue
        manager.change_issue_status(sid, issue.id, "fixed", author="coder")
        result = manager.change_issue_status(sid, issue.id, "completed", author="reviewer-a")
        assert result["progress_status"] == "completed"
        assert issue.progress_status == IssueProgressStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_reviewer_fixed_to_reported(self, manager, session_with_issue):
        sid, issue = session_with_issue
        manager.change_issue_status(sid, issue.id, "fixed", author="coder")
        result = manager.change_issue_status(sid, issue.id, "reported", author="reviewer-a")
        assert result["progress_status"] == "reported"

    @pytest.mark.asyncio
    async def test_author_wont_fix_to_reported(self, manager, session_with_issue):
        sid, issue = session_with_issue
        manager.change_issue_status(sid, issue.id, "wont_fix", author="coder")
        result = manager.change_issue_status(sid, issue.id, "reported", author="coder")
        assert result["progress_status"] == "reported"

    @pytest.mark.asyncio
    async def test_invalid_status_value(self, manager, session_with_issue):
        sid, issue = session_with_issue
        with pytest.raises(ValueError, match="Invalid status"):
            manager.change_issue_status(sid, issue.id, "bogus", author="coder")

    @pytest.mark.asyncio
    async def test_author_cannot_complete(self, manager, session_with_issue):
        sid, issue = session_with_issue
        manager.change_issue_status(sid, issue.id, "fixed", author="coder")
        with pytest.raises(ValueError, match="전환을 할 수 없습니다"):
            manager.change_issue_status(sid, issue.id, "completed", author="coder")

    @pytest.mark.asyncio
    async def test_reviewer_cannot_mark_fixed(self, manager, session_with_issue):
        sid, issue = session_with_issue
        with pytest.raises(ValueError, match="전환을 할 수 없습니다"):
            manager.change_issue_status(sid, issue.id, "fixed", author="reviewer-a")

    @pytest.mark.asyncio
    async def test_issue_not_found(self, manager, session_with_issue):
        sid, _ = session_with_issue
        with pytest.raises(KeyError, match="Issue not found"):
            manager.change_issue_status(sid, "nonexistent", "fixed", author="coder")

    @pytest.mark.asyncio
    async def test_status_change_adds_opinion_to_thread(self, manager, session_with_issue):
        sid, issue = session_with_issue
        initial_thread_len = len(issue.thread)
        manager.change_issue_status(sid, issue.id, "fixed", author="coder")
        assert len(issue.thread) == initial_thread_len + 1
        last_op = issue.thread[-1]
        assert last_op.action == OpinionAction.STATUS_CHANGE
        assert last_op.status_value == "fixed"
        assert last_op.model_id == "coder"

    @pytest.mark.asyncio
    async def test_status_change_custom_reasoning(self, manager, session_with_issue):
        sid, issue = session_with_issue
        manager.change_issue_status(sid, issue.id, "fixed", author="coder", reasoning="커밋 abc123에서 수정")
        last_op = issue.thread[-1]
        assert last_op.reasoning == "커밋 abc123에서 수정"

    @pytest.mark.asyncio
    async def test_callback_is_called(self, manager, session_with_issue):
        sid, issue = session_with_issue
        calls = []
        manager.on_issue_status_changed = lambda *args: calls.append(args)
        manager.change_issue_status(sid, issue.id, "fixed", author="coder")
        assert len(calls) == 1
        assert calls[0] == (sid, issue.id, "fixed", "coder")


class TestSubmitOpinionRejectsStatusChange:
    @pytest.mark.asyncio
    async def test_submit_opinion_rejects_status_change(self, manager, session_with_issue):
        sid, issue = session_with_issue
        with pytest.raises(ValueError, match="STATUS_CHANGE는 직접 제출할 수 없습니다"):
            manager.submit_opinion(sid, issue.id, "someone", "status_change", "test")


class TestConsensusSkipsStatusChange:
    def test_status_change_ignored_in_check_consensus(self):
        opinions = [
            _make_opinion("a", OpinionAction.RAISE, Severity.HIGH),
            _make_opinion("b", OpinionAction.FIX_REQUIRED, Severity.HIGH),
            Opinion(
                model_id="coder",
                action=OpinionAction.STATUS_CHANGE,
                reasoning="status changed",
                status_value="fixed",
            ),
        ]
        issue = Issue(title="test", severity=Severity.HIGH, file="t.py", thread=opinions)
        assert check_consensus(issue, threshold=2.0) is True
        assert determine_consensus_type(issue) == "fix_required"

    def test_status_change_ignored_in_severity(self):
        opinions = [
            _make_opinion("a", OpinionAction.RAISE, Severity.HIGH),
            _make_opinion("b", OpinionAction.FIX_REQUIRED, Severity.MEDIUM),
            Opinion(
                model_id="coder",
                action=OpinionAction.STATUS_CHANGE,
                reasoning="status changed",
                status_value="completed",
            ),
        ]
        issue = Issue(title="test", severity=Severity.HIGH, file="t.py", thread=opinions)
        severity = determine_final_severity(issue)
        assert severity in (Severity.HIGH, Severity.MEDIUM)


class TestFullStatusLifecycle:
    @pytest.mark.asyncio
    async def test_full_cycle(self, manager, session_with_issue):
        sid, issue = session_with_issue

        manager.change_issue_status(sid, issue.id, "fixed", author="coder")
        assert issue.progress_status == IssueProgressStatus.FIXED

        manager.change_issue_status(sid, issue.id, "reported", author="reviewer-a")
        assert issue.progress_status == IssueProgressStatus.REPORTED

        manager.change_issue_status(sid, issue.id, "fixed", author="coder")
        assert issue.progress_status == IssueProgressStatus.FIXED

        manager.change_issue_status(sid, issue.id, "completed", author="reviewer-a")
        assert issue.progress_status == IssueProgressStatus.COMPLETED

        status_changes = [op for op in issue.thread if op.action == OpinionAction.STATUS_CHANGE]
        assert len(status_changes) == 4

    @pytest.mark.asyncio
    async def test_wont_fix_reopen_cycle(self, manager, session_with_issue):
        sid, issue = session_with_issue

        manager.change_issue_status(sid, issue.id, "wont_fix", author="coder")
        assert issue.progress_status == IssueProgressStatus.WONT_FIX

        manager.change_issue_status(sid, issue.id, "reported", author="coder")
        assert issue.progress_status == IssueProgressStatus.REPORTED
