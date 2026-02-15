"""Tests for data models."""

from datetime import datetime, timezone

from ai_review.models import (
    AgentActivity,
    AgentTaskType,
    DiffFile,
    ImplementationContext,
    Issue,
    IssueResponse,
    IssueResponseAction,
    Knowledge,
    ModelConfig,
    Opinion,
    OpinionAction,
    RawIssue,
    Review,
    ReviewSession,
    Severity,
    SessionConfig,
    SessionStatus,
)


class TestSeverity:
    def test_values(self):
        assert Severity.CRITICAL == "critical"
        assert Severity.HIGH == "high"
        assert Severity.MEDIUM == "medium"
        assert Severity.LOW == "low"
        assert Severity.DISMISSED == "dismissed"

    def test_from_string(self):
        assert Severity("critical") == Severity.CRITICAL


class TestSessionStatus:
    def test_all_states_exist(self):
        expected = {"idle", "collecting", "reviewing", "dedup", "deliberating", "agent_response", "complete"}
        actual = {s.value for s in SessionStatus}
        assert actual == expected


class TestDiffFile:
    def test_creation(self):
        df = DiffFile(path="foo.py", additions=5, deletions=2, content="diff content")
        assert df.path == "foo.py"
        assert df.additions == 5
        assert df.deletions == 2

    def test_defaults(self):
        df = DiffFile(path="bar.py")
        assert df.additions == 0
        assert df.deletions == 0
        assert df.content == ""


class TestKnowledge:
    def test_defaults(self):
        k = Knowledge()
        assert k.conventions == ""
        assert k.decisions == ""
        assert k.extra == {}

    def test_with_extra(self):
        k = Knowledge(extra={"architecture": "microservices"})
        assert k.extra["architecture"] == "microservices"


class TestRawIssue:
    def test_required_fields(self):
        issue = RawIssue(
            title="Bug",
            severity=Severity.HIGH,
            file="main.py",
            description="Something broken",
        )
        assert issue.title == "Bug"
        assert issue.line is None
        assert issue.line_start is None
        assert issue.line_end is None
        assert issue.suggestion == ""

    def test_all_fields(self, sample_raw_issues):
        issue = sample_raw_issues[0]
        assert issue.line == 5
        assert issue.suggestion == "Add try/except block."


class TestReview:
    def test_creation(self, sample_raw_issues):
        review = Review(model_id="opus", issues=sample_raw_issues, summary="Looks OK")
        assert review.model_id == "opus"
        assert len(review.issues) == 2
        assert isinstance(review.submitted_at, datetime)

    def test_empty_issues(self):
        review = Review(model_id="gpt", issues=[])
        assert review.issues == []
        assert review.summary == ""


class TestOpinion:
    def test_creation(self):
        op = Opinion(
            model_id="opus",
            action=OpinionAction.FIX_REQUIRED,
            reasoning="Valid concern",
            suggested_severity=Severity.MEDIUM,
        )
        assert op.action == OpinionAction.FIX_REQUIRED
        assert op.suggested_severity == Severity.MEDIUM

    def test_without_severity(self):
        op = Opinion(
            model_id="gpt",
            action=OpinionAction.COMMENT,
            reasoning="Need more context",
        )
        assert op.suggested_severity is None

    def test_confidence_default(self):
        op = Opinion(model_id="a", action=OpinionAction.FIX_REQUIRED, reasoning="r")
        assert op.confidence == 1.0

    def test_confidence_custom(self):
        op = Opinion(model_id="a", action=OpinionAction.FIX_REQUIRED, reasoning="r", confidence=0.7)
        assert op.confidence == 0.7

    def test_old_opinion_json_without_confidence(self):
        """Backward compat: old JSON without confidence deserializes as 1.0."""
        data = {"model_id": "a", "action": "fix_required", "reasoning": "r"}
        op = Opinion.model_validate(data)
        assert op.confidence == 1.0


class TestIssue:
    def test_creation(self):
        issue = Issue(
            title="Security flaw",
            severity=Severity.CRITICAL,
            file="auth.py",
            raised_by="opus",
        )
        assert len(issue.id) == 12
        assert issue.thread == []
        assert issue.consensus is None
        assert issue.consensus_type is None
        assert issue.turn == 0

    def test_with_thread(self):
        op = Opinion(
            model_id="gpt",
            action=OpinionAction.FIX_REQUIRED,
            reasoning="Confirmed",
        )
        issue = Issue(
            title="Bug",
            severity=Severity.HIGH,
            file="main.py",
            thread=[op],
        )
        assert len(issue.thread) == 1


class TestModelConfig:
    def test_defaults(self):
        mc = ModelConfig(id="opus")
        assert mc.client_type == "claude-code"
        assert mc.provider == ""
        assert mc.model_id == ""
        assert mc.test_endpoint == ""
        assert mc.role == ""
        assert mc.description == ""
        assert mc.color == ""
        assert mc.avatar == ""
        assert mc.system_prompt == ""
        assert mc.temperature is None
        assert mc.review_focus == []
        assert mc.enabled is True

    def test_with_all_fields(self):
        mc = ModelConfig(
            id="security-bot",
            client_type="codex",
            provider="openai",
            model_id="gpt-5-codex",
            test_endpoint="https://example.com/health",
            role="Security Reviewer",
            description="Specializes in finding security vulnerabilities",
            color="#EF4444",
            avatar="shield",
            system_prompt="Focus on OWASP Top 10",
            temperature=0.3,
            review_focus=["security", "auth", "injection"],
            enabled=True,
        )
        assert mc.id == "security-bot"
        assert mc.color == "#EF4444"
        assert mc.temperature == 0.3
        assert mc.test_endpoint == "https://example.com/health"
        assert len(mc.review_focus) == 3
        assert "injection" in mc.review_focus

    def test_backward_compatible_without_new_fields(self):
        """Old-style config with only original fields should work."""
        mc = ModelConfig(id="opus", client_type="claude-code", provider="anthropic", model_id="opus", role="general")
        assert mc.enabled is True
        assert mc.system_prompt == ""
        assert mc.review_focus == []

    def test_serialization_roundtrip(self):
        mc = ModelConfig(
            id="test",
            color="#8B5CF6",
            system_prompt="Be thorough.",
            temperature=0.7,
            review_focus=["perf"],
            enabled=False,
        )
        data = mc.model_dump()
        restored = ModelConfig.model_validate(data)
        assert restored.color == "#8B5CF6"
        assert restored.temperature == 0.7
        assert restored.enabled is False
        assert restored.review_focus == ["perf"]


class TestIssueResponseAction:
    def test_values(self):
        assert IssueResponseAction.ACCEPT == "accept"
        assert IssueResponseAction.DISPUTE == "dispute"
        assert IssueResponseAction.PARTIAL == "partial"

    def test_from_string(self):
        assert IssueResponseAction("accept") == IssueResponseAction.ACCEPT
        assert IssueResponseAction("dispute") == IssueResponseAction.DISPUTE
        assert IssueResponseAction("partial") == IssueResponseAction.PARTIAL


class TestIssueResponse:
    def test_creation(self):
        ir = IssueResponse(issue_id="abc123", action=IssueResponseAction.ACCEPT)
        assert ir.issue_id == "abc123"
        assert ir.action == IssueResponseAction.ACCEPT
        assert ir.reasoning == ""
        assert ir.proposed_change == ""
        assert ir.submitted_by == ""
        assert ir.submitted_at is not None

    def test_all_fields(self):
        ir = IssueResponse(
            issue_id="abc123",
            action=IssueResponseAction.DISPUTE,
            reasoning="This is not a real bug",
            proposed_change="Remove the check",
            submitted_by="coding-agent",
        )
        assert ir.reasoning == "This is not a real bug"
        assert ir.proposed_change == "Remove the check"
        assert ir.submitted_by == "coding-agent"

    def test_serialization_roundtrip(self):
        ir = IssueResponse(
            issue_id="def456",
            action=IssueResponseAction.PARTIAL,
            reasoning="Partially valid",
        )
        data = ir.model_dump(mode="json")
        restored = IssueResponse.model_validate(data)
        assert restored.issue_id == "def456"
        assert restored.action == IssueResponseAction.PARTIAL
        assert restored.reasoning == "Partially valid"

    def test_session_default_empty(self):
        session = ReviewSession()
        assert session.issue_responses == []

    def test_backward_compat_old_json(self):
        """Old session JSON without issue_responses deserializes fine."""
        data = {"id": "old123", "base": "main", "status": "idle"}
        session = ReviewSession.model_validate(data)
        assert session.issue_responses == []

    def test_agent_task_type_agent_response(self):
        assert AgentTaskType.AGENT_RESPONSE == "agent_response"
        assert AgentTaskType("agent_response") == AgentTaskType.AGENT_RESPONSE


class TestAgentActivity:
    def test_creation(self):
        act = AgentActivity(model_id="alpha", action="view_file", target="src/main.py:1-10")
        assert act.model_id == "alpha"
        assert act.action == "view_file"
        assert act.target == "src/main.py:1-10"
        assert act.timestamp is not None

    def test_serialization_roundtrip(self):
        act = AgentActivity(model_id="beta", action="search", target="search:greet")
        data = act.model_dump(mode="json")
        restored = AgentActivity.model_validate(data)
        assert restored.model_id == "beta"

    def test_session_default_empty(self):
        session = ReviewSession()
        assert session.agent_activities == []

    def test_session_without_activities_field(self):
        """Backward compat: old JSON without agent_activities deserializes fine."""
        data = {"id": "test123", "base": "main", "status": "idle"}
        session = ReviewSession.model_validate(data)
        assert session.agent_activities == []


class TestImplementationContext:
    def test_default_values(self):
        ic = ImplementationContext()
        assert ic.summary == ""
        assert ic.decisions == []
        assert ic.tradeoffs == []
        assert ic.known_issues == []
        assert ic.out_of_scope == []
        assert ic.submitted_by == ""
        assert ic.submitted_at is None

    def test_serialization_roundtrip(self):
        ic = ImplementationContext(
            summary="Add caching layer",
            decisions=["Use Redis for cache"],
            tradeoffs=["Memory vs speed"],
            known_issues=["No TTL yet"],
            out_of_scope=["Cache invalidation"],
            submitted_by="coding-agent",
        )
        data = ic.model_dump(mode="json")
        restored = ImplementationContext.model_validate(data)
        assert restored.summary == "Add caching layer"
        assert restored.decisions == ["Use Redis for cache"]
        assert restored.submitted_by == "coding-agent"

    def test_session_without_context(self):
        """Old session JSON without implementation_context deserializes as None."""
        data = {"id": "old123", "base": "main", "status": "idle"}
        session = ReviewSession.model_validate(data)
        assert session.implementation_context is None


class TestReviewSession:
    def test_defaults(self):
        session = ReviewSession()
        assert session.status == SessionStatus.IDLE
        assert session.base == "main"
        assert session.diff == []
        assert session.reviews == []
        assert session.issues == []
        assert session.client_sessions == {}
        assert session.agent_activities == []
        assert len(session.id) == 12

    def test_with_data(self, sample_session):
        assert len(sample_session.diff) == 2
        assert sample_session.knowledge.conventions == "Use snake_case for functions."
        assert sample_session.config.max_turns == 3

    def test_serialization_roundtrip(self, sample_session):
        data = sample_session.model_dump()
        restored = ReviewSession.model_validate(data)
        assert restored.id == sample_session.id
        assert restored.base == sample_session.base
        assert len(restored.diff) == len(sample_session.diff)

    def test_json_roundtrip(self, sample_session):
        json_str = sample_session.model_dump_json()
        restored = ReviewSession.model_validate_json(json_str)
        assert restored.id == sample_session.id
        assert restored.status == sample_session.status
