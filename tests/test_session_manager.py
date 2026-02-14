"""Tests for SessionManager."""

from datetime import timedelta

import pytest

from ai_review.models import AgentState, AgentStatus, DiffFile, ModelConfig, SessionStatus, Severity, _utcnow
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


class TestOverallReview:
    @pytest.mark.asyncio
    async def test_submit_and_update_same_turn(self, manager):
        start = await manager.start_review()
        sid = start["session_id"]

        accepted = manager.submit_overall_review(
            sid,
            model_id="codex",
            task_type="review",
            turn=0,
            merge_decision="needs_discussion",
            summary="초기 판단",
            highlights=["핵심 포인트"],
        )
        assert accepted["status"] == "accepted"
        assert accepted["overall_review_count"] == 1

        updated = manager.submit_overall_review(
            sid,
            model_id="codex",
            task_type="review",
            turn=0,
            merge_decision="not_mergeable",
            summary="결론: 머지 불가",
            blockers=["성능 회귀"],
        )
        assert updated["status"] == "updated"
        assert updated["overall_review_count"] == 1

        reviews = manager.get_overall_reviews(sid)
        assert len(reviews) == 1
        assert reviews[0]["merge_decision"] == "not_mergeable"
        assert reviews[0]["summary"] == "결론: 머지 불가"
        assert reviews[0]["blockers"] == ["성능 회귀"]

    @pytest.mark.asyncio
    async def test_deliberation_default_turn_uses_current_issue_turn(self, manager):
        start = await manager.start_review()
        sid = start["session_id"]
        manager.submit_review(
            sid,
            "opus",
            [{"title": "Bug", "severity": "high", "file": "x.py", "description": "d"}],
        )
        issues = manager.create_issues_from_reviews(sid)
        issues[0].turn = 2

        result = manager.submit_overall_review(
            sid,
            model_id="gemini",
            task_type="deliberation",
            merge_decision="mergeable",
            summary="턴 2 기준 머지 가능",
        )
        assert result["turn"] == 2

    @pytest.mark.asyncio
    async def test_session_status_includes_overall_review_meta(self, manager):
        start = await manager.start_review()
        sid = start["session_id"]
        manager.submit_overall_review(
            sid,
            model_id="codex",
            task_type="review",
            turn=0,
            merge_decision="needs_discussion",
            summary="요약",
        )
        status = manager.get_session_status(sid)
        assert status["overall_review_count"] == 1
        assert status["current_turn"] == 0

    @pytest.mark.asyncio
    async def test_agent_access_key_is_stable_per_model(self, manager):
        start = await manager.start_review()
        sid = start["session_id"]
        k1 = manager.ensure_agent_access_key(sid, "codex")
        k2 = manager.ensure_agent_access_key(sid, "codex")
        assert k1 == k2
        assert len(k1) >= 32

    @pytest.mark.asyncio
    async def test_human_assist_access_key_rotates(self, manager):
        start = await manager.start_review()
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
        start = await manager.start_review()
        sid = start["session_id"]

        result = manager.read_file(sid, "hello.py")
        assert result["path"] == "hello.py"
        assert result["total_lines"] == 5
        assert len(result["lines"]) == 5
        assert result["lines"][0] == {"number": 1, "content": "line1"}

    @pytest.mark.asyncio
    async def test_read_file_with_range(self, manager, tmp_path):
        (tmp_path / "hello.py").write_text("\n".join(f"line{i}" for i in range(1, 11)))
        start = await manager.start_review()
        sid = start["session_id"]

        result = manager.read_file(sid, "hello.py", start=3, end=5)
        assert result["start_line"] == 3
        assert result["end_line"] == 5
        assert len(result["lines"]) == 3
        assert result["lines"][0]["content"] == "line3"

    @pytest.mark.asyncio
    async def test_read_file_not_found(self, manager):
        start = await manager.start_review()
        sid = start["session_id"]
        with pytest.raises(FileNotFoundError):
            manager.read_file(sid, "nonexistent.py")

    @pytest.mark.asyncio
    async def test_read_file_outside_repo(self, manager):
        start = await manager.start_review()
        sid = start["session_id"]
        with pytest.raises(PermissionError):
            manager.read_file(sid, "../../etc/passwd")


class TestGetTree:
    @pytest.mark.asyncio
    async def test_get_tree(self, manager, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("pass")
        (tmp_path / "README.md").write_text("hi")
        start = await manager.start_review()
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
        start = await manager.start_review()
        sid = start["session_id"]

        result = manager.get_tree(sid)
        names = {e["name"] for e in result["entries"]}
        assert ".git" not in names
        assert "code.py" in names

    @pytest.mark.asyncio
    async def test_depth_limit(self, manager, tmp_path):
        (tmp_path / "a" / "b" / "c").mkdir(parents=True)
        (tmp_path / "a" / "b" / "c" / "deep.py").write_text("pass")
        start = await manager.start_review()
        sid = start["session_id"]

        result = manager.get_tree(sid, depth=1)
        a_entry = next(e for e in result["entries"] if e["name"] == "a")
        assert a_entry["children"] == []  # depth=1 means no children

    @pytest.mark.asyncio
    async def test_outside_repo(self, manager):
        start = await manager.start_review()
        sid = start["session_id"]
        with pytest.raises(PermissionError):
            manager.get_tree(sid, path="../../..")


class TestRecordActivity:
    @pytest.mark.asyncio
    async def test_records_activity(self, manager):
        start = await manager.start_review()
        sid = start["session_id"]

        recorded = manager.record_activity(sid, "alpha", "view_file", "main.py:1-10")
        assert recorded is True
        session = manager.get_session(sid)
        assert len(session.agent_activities) == 1
        assert session.agent_activities[0].model_id == "alpha"

    @pytest.mark.asyncio
    async def test_dedup_suppresses_same_activity(self, manager):
        start = await manager.start_review()
        sid = start["session_id"]

        manager.record_activity(sid, "alpha", "view_file", "main.py:1-10")
        suppressed = manager.record_activity(sid, "alpha", "view_file", "main.py:1-10")
        assert suppressed is False
        session = manager.get_session(sid)
        assert len(session.agent_activities) == 1

    @pytest.mark.asyncio
    async def test_different_target_not_suppressed(self, manager):
        start = await manager.start_review()
        sid = start["session_id"]

        manager.record_activity(sid, "alpha", "view_file", "main.py:1-10")
        recorded = manager.record_activity(sid, "alpha", "view_file", "utils.py:1-5")
        assert recorded is True
        session = manager.get_session(sid)
        assert len(session.agent_activities) == 2

    @pytest.mark.asyncio
    async def test_resolve_model_id_from_key(self, manager):
        start = await manager.start_review()
        sid = start["session_id"]
        key = manager.ensure_agent_access_key(sid, "opus")

        assert manager.resolve_model_id_from_key(sid, key) == "opus"
        assert manager.resolve_model_id_from_key(sid, "invalid") is None


class TestSearchCode:
    @pytest.mark.asyncio
    async def test_search_finds_match(self, manager, tmp_path):
        (tmp_path / "hello.py").write_text("def greet():\n    print('hello')\n")
        start = await manager.start_review()
        sid = start["session_id"]

        result = await manager.search_code(sid, "greet")
        assert result["total_matches"] >= 1
        assert result["results"][0]["file"] == "hello.py"
        assert result["results"][0]["line"] == 1

    @pytest.mark.asyncio
    async def test_search_no_match(self, manager, tmp_path):
        (tmp_path / "hello.py").write_text("pass\n")
        start = await manager.start_review()
        sid = start["session_id"]

        result = await manager.search_code(sid, "nonexistent_symbol")
        assert result["total_matches"] == 0

    @pytest.mark.asyncio
    async def test_search_glob_filter(self, manager, tmp_path):
        (tmp_path / "hello.py").write_text("target\n")
        (tmp_path / "hello.js").write_text("target\n")
        start = await manager.start_review()
        sid = start["session_id"]

        result = await manager.search_code(sid, "target", glob="*.py")
        files = {r["file"] for r in result["results"]}
        assert "hello.py" in files
        assert "hello.js" not in files

    @pytest.mark.asyncio
    async def test_search_max_results(self, manager, tmp_path):
        (tmp_path / "many.py").write_text("\n".join(f"match_{i}" for i in range(50)))
        start = await manager.start_review()
        sid = start["session_id"]

        result = await manager.search_code(sid, "match_", max_results=5)
        assert len(result["results"]) <= 5

    @pytest.mark.asyncio
    async def test_search_python_fallback(self, manager, tmp_path, monkeypatch):
        (tmp_path / "code.py").write_text("def my_func():\n    pass\n")
        monkeypatch.setattr("shutil.which", lambda cmd: None)
        start = await manager.start_review()
        sid = start["session_id"]

        result = await manager.search_code(sid, "my_func")
        assert result["total_matches"] >= 1


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
        assert "available_apis" in idx


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

    @pytest.mark.asyncio
    async def test_normalizes_line_range_from_review(self, manager):
        start = await manager.start_review()
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
    async def test_start_review_uses_selected_presets(self, manager):
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

        started = await manager.start_review("main", preset_ids=["gemini-p1", "codex-p1"])
        session = manager.get_session(started["session_id"])
        assert [m.id for m in session.config.models] == ["gemini-p1", "codex-p1"]

    @pytest.mark.asyncio
    async def test_start_review_unknown_preset_raises(self, manager):
        with pytest.raises(ValueError, match="Unknown preset ids"):
            await manager.start_review("main", preset_ids=["missing"])

    @pytest.mark.asyncio
    async def test_start_review_empty_preset_ids_raises(self, manager):
        with pytest.raises(ValueError, match="at least one preset"):
            await manager.start_review("main", preset_ids=[])


class TestAgentElapsed:
    @pytest.mark.asyncio
    async def test_elapsed_freezes_while_waiting_and_ticks_while_reviewing(self, manager):
        started = await manager.start_review("main")
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
