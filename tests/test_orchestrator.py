"""Tests for the orchestration layer."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from ai_review.models import (
    AgentStatus,
    AgentTaskType,
    ModelConfig,
    ReviewSession,
    SessionConfig,
    SessionStatus,
    Severity,
)
from ai_review.orchestrator import Orchestrator
from ai_review.session_manager import SessionManager
from ai_review.trigger.base import TriggerEngine, TriggerResult


# --- Mock Trigger ---


class MockTrigger(TriggerEngine):
    """A trigger that records calls instead of launching real processes."""

    def __init__(self) -> None:
        self.created_sessions: list[str] = []
        self.sent_prompts: list[tuple[str, str, str]] = []  # (sid, model, prompt)

    async def create_session(self, model_id: str) -> str:
        sid = f"mock-{model_id}"
        self.created_sessions.append(sid)
        return sid

    async def send_prompt(
        self, client_session_id: str, model_id: str, prompt: str
    ) -> TriggerResult:
        self.sent_prompts.append((client_session_id, model_id, prompt))
        return TriggerResult(success=True, output="ok", client_session_id=client_session_id)

    async def close(self) -> None:
        pass


# --- Fixtures ---


def _make_manager_with_config(models: list[ModelConfig]) -> SessionManager:
    mgr = SessionManager(repo_path=None)
    # We'll create a session manually and configure it
    session = ReviewSession(
        status=SessionStatus.REVIEWING,
        config=SessionConfig(models=models, max_turns=3, consensus_threshold=2),
    )
    mgr.sessions[session.id] = session
    mgr._current_session_id = session.id
    return mgr, session


# --- Tests ---


class TestOrchestratorStart:
    @pytest.mark.asyncio
    async def test_start_triggers_all_models(self):
        models = [
            ModelConfig(id="opus", client_type="claude-code", role="security"),
            ModelConfig(id="gpt", client_type="opencode", role="performance"),
        ]
        mgr, session = _make_manager_with_config(models)
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")

        # Replace triggers with mocks
        mock_a = MockTrigger()
        mock_b = MockTrigger()
        orch._triggers = {}

        # Patch _create_trigger to return mocks
        triggers_map = {"opus": mock_a, "gpt": mock_b}
        orch._create_trigger = lambda ct: triggers_map.get(ct, MockTrigger())

        # Manually set triggers since start() creates them
        orch._triggers = triggers_map

        # Simulate start by creating sessions and firing
        for mc in models:
            trigger = orch._triggers[mc.id]
            client_sid = await trigger.create_session(mc.id)
            session.client_sessions[mc.id] = client_sid

        assert len(session.client_sessions) == 2
        assert "opus" in session.client_sessions
        assert "gpt" in session.client_sessions

        await orch.close()

    @pytest.mark.asyncio
    async def test_start_no_models_stays_manual(self):
        mgr, session = _make_manager_with_config([])
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")

        await orch.start(session.id)
        # No triggers created
        assert len(orch._triggers) == 0

        await orch.close()


class TestCallbackRegistration:
    def test_callbacks_registered(self):
        mgr = SessionManager(repo_path=None)
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")

        assert mgr.on_review_submitted is not None
        assert mgr.on_opinion_submitted is not None

    @pytest.mark.asyncio
    async def test_callbacks_detached_on_close(self):
        mgr = SessionManager(repo_path=None)
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")

        await orch.close()

        assert mgr.on_review_submitted is None
        assert mgr.on_opinion_submitted is None


class TestReviewSubmittedCallback:
    @pytest.mark.asyncio
    async def test_advances_when_all_reviews_in(self):
        models = [
            ModelConfig(id="opus", client_type="claude-code"),
            ModelConfig(id="gpt", client_type="claude-code"),
        ]
        mgr, session = _make_manager_with_config(models)
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")

        # Initialize agent states (normally done by start())
        from ai_review.models import AgentState
        session.agent_states["opus"] = AgentState(model_id="opus")
        session.agent_states["gpt"] = AgentState(model_id="gpt")

        # Replace _advance_to_deliberation with a spy
        advance_called = asyncio.Event()

        async def spy_advance(sid):
            advance_called.set()

        orch._advance_to_deliberation = spy_advance

        # Submit reviews from both models
        mgr.submit_review(session.id, "opus", [
            {"title": "Bug A", "severity": "high", "file": "a.py", "description": "desc"}
        ])
        mgr.submit_review(session.id, "gpt", [
            {"title": "Bug B", "severity": "medium", "file": "b.py", "description": "desc"}
        ])

        await asyncio.sleep(0.05)
        assert advance_called.is_set()

        await orch.close()

    @pytest.mark.asyncio
    async def test_advances_when_some_failed_and_rest_submitted(self):
        """If one agent fails and the other submits, auto-advance should trigger."""
        models = [
            ModelConfig(id="opus", client_type="claude-code"),
            ModelConfig(id="codex", client_type="codex"),
        ]
        mgr, session = _make_manager_with_config(models)
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")

        from ai_review.models import AgentState
        session.agent_states["opus"] = AgentState(model_id="opus", status=AgentStatus.FAILED)
        session.agent_states["codex"] = AgentState(model_id="codex")

        advance_called = asyncio.Event()

        async def spy_advance(sid):
            advance_called.set()

        orch._advance_to_deliberation = spy_advance

        # Only codex submits
        mgr.submit_review(session.id, "codex", [
            {"title": "Bug", "severity": "high", "file": "a.py", "description": "desc"}
        ])

        await asyncio.sleep(0.05)
        assert advance_called.is_set()

        await orch.close()

    @pytest.mark.asyncio
    async def test_does_not_advance_when_reviews_missing(self):
        models = [
            ModelConfig(id="opus", client_type="claude-code"),
            ModelConfig(id="gpt", client_type="claude-code"),
        ]
        mgr, session = _make_manager_with_config(models)
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")

        # Initialize agent states — only opus will submit, gpt still reviewing
        from ai_review.models import AgentState
        session.agent_states["opus"] = AgentState(model_id="opus")
        session.agent_states["gpt"] = AgentState(model_id="gpt", status=AgentStatus.REVIEWING)

        advance_called = asyncio.Event()

        async def spy_advance(sid):
            advance_called.set()

        orch._advance_to_deliberation = spy_advance

        # Submit only one review — gpt still reviewing
        mgr.submit_review(session.id, "opus", [
            {"title": "Bug A", "severity": "high", "file": "a.py", "description": "desc"}
        ])

        await asyncio.sleep(0.05)
        assert not advance_called.is_set()

        await orch.close()


class TestAdvanceToDeliberation:
    @pytest.mark.asyncio
    async def test_dedup_and_deliberation_transition(self):
        models = [
            ModelConfig(id="opus", client_type="claude-code"),
            ModelConfig(id="gpt", client_type="claude-code"),
        ]
        mgr, session = _make_manager_with_config(models)
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")

        # Set up mock triggers
        mock_trigger = MockTrigger()
        orch._triggers = {"opus": mock_trigger, "gpt": mock_trigger}
        session.client_sessions = {"opus": "mock-opus", "gpt": "mock-gpt"}

        # Submit reviews
        mgr.submit_review(session.id, "opus", [
            {"title": "SQL Injection", "severity": "critical", "file": "db.py", "line": 10, "description": "Raw SQL"}
        ])
        mgr.submit_review(session.id, "gpt", [
            {"title": "Perf issue", "severity": "medium", "file": "api.py", "line": 20, "description": "N+1 query"}
        ])

        # Prevent the callback from advancing automatically
        orch._on_review_submitted = lambda sid, mid: None
        mgr.on_review_submitted = orch._on_review_submitted

        # Manually advance
        await orch._advance_to_deliberation(session.id)

        assert session.status == SessionStatus.DELIBERATING
        assert len(session.issues) > 0

        # Mock trigger should have received deliberation prompts
        # (tasks are fire-and-forget, give them a moment)
        await asyncio.sleep(0.05)
        assert len(mock_trigger.sent_prompts) > 0

        await orch.close()


class TestFinish:
    @pytest.mark.asyncio
    async def test_finish_transitions_to_complete(self):
        models = [ModelConfig(id="opus", client_type="claude-code")]
        mgr, session = _make_manager_with_config(models)
        session.status = SessionStatus.DELIBERATING
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")

        await orch._finish(session.id)

        assert session.status == SessionStatus.COMPLETE

        await orch.close()


class TestFullCycleWithMocks:
    """End-to-end test with MockTrigger simulating the full flow."""

    @pytest.mark.asyncio
    async def test_full_review_to_complete(self):
        models = [
            ModelConfig(id="opus", client_type="claude-code", role="security"),
            ModelConfig(id="gpt", client_type="claude-code", role="perf"),
        ]
        mgr, session = _make_manager_with_config(models)
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")

        # Replace trigger creation with mocks
        mock_trigger = MockTrigger()
        orch._triggers = {"opus": mock_trigger, "gpt": mock_trigger}
        session.client_sessions = {"opus": "mock-opus", "gpt": "mock-gpt"}

        # Disable auto-advance for manual control
        mgr.on_review_submitted = None
        mgr.on_opinion_submitted = None

        # Phase 1: Submit reviews
        mgr.submit_review(session.id, "opus", [
            {"title": "SQL Injection", "severity": "critical", "file": "db.py", "line": 10, "description": "Raw SQL"},
        ])
        mgr.submit_review(session.id, "gpt", [
            {"title": "N+1 query", "severity": "medium", "file": "api.py", "description": "Inefficient query"},
        ])

        assert session.status == SessionStatus.REVIEWING
        assert len(session.reviews) == 2

        # Phase 2: Advance to deliberation
        await orch._advance_to_deliberation(session.id)
        assert session.status == SessionStatus.DELIBERATING
        assert len(session.issues) == 2

        # Phase 3: Submit opinions (both models opine on each other's issues)
        for issue in session.issues:
            for mc in models:
                if issue.raised_by != mc.id:
                    mgr.submit_opinion(
                        session.id, issue.id, mc.id,
                        "agree", "Confirmed", "high"
                    )

        # Phase 4: Re-apply consensus and finish
        await orch._check_and_advance(session.id)

        # Both issues should have consensus (2 agrees each: raiser + other)
        assert session.status == SessionStatus.COMPLETE
        for issue in session.issues:
            assert issue.consensus is True


class SlowMockTrigger(TriggerEngine):
    """A trigger that blocks until explicitly released."""

    def __init__(self) -> None:
        self.release = asyncio.Event()

    async def create_session(self, model_id: str) -> str:
        return f"slow-{model_id}"

    async def send_prompt(self, client_session_id, model_id, prompt):
        await self.release.wait()
        return TriggerResult(success=True, output="ok", client_session_id=client_session_id)

    async def close(self):
        self.release.set()


class TestAgentStateTracking:
    @pytest.mark.asyncio
    async def test_agent_states_initialized_on_start(self):
        models = [
            ModelConfig(id="opus", client_type="claude-code", role="security"),
            ModelConfig(id="gpt", client_type="claude-code", role="performance"),
        ]
        mgr, session = _make_manager_with_config(models)
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")

        # Use slow trigger so agents stay in REVIEWING
        slow = SlowMockTrigger()
        orch._create_trigger = lambda ct: slow

        await orch.start(session.id)
        await asyncio.sleep(0.05)

        assert "opus" in session.agent_states
        assert "gpt" in session.agent_states
        assert session.agent_states["opus"].status == AgentStatus.REVIEWING
        assert session.agent_states["gpt"].status == AgentStatus.REVIEWING
        assert session.agent_states["opus"].started_at is not None

        await orch.close()

    @pytest.mark.asyncio
    async def test_agent_marked_submitted_after_review(self):
        models = [
            ModelConfig(id="opus", client_type="claude-code"),
            ModelConfig(id="gpt", client_type="claude-code"),
        ]
        mgr, session = _make_manager_with_config(models)
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")

        # Use slow trigger so gpt stays in REVIEWING
        slow = SlowMockTrigger()
        orch._create_trigger = lambda ct: slow

        await orch.start(session.id)
        await asyncio.sleep(0.05)

        # Prevent auto-advance
        original = orch._advance_to_deliberation
        orch._advance_to_deliberation = AsyncMock()

        mgr.submit_review(session.id, "opus", [
            {"title": "Bug", "severity": "high", "file": "a.py", "description": "desc"}
        ])

        assert session.agent_states["opus"].status == AgentStatus.SUBMITTED
        assert session.agent_states["opus"].submitted_at is not None
        # gpt hasn't submitted yet — trigger still running
        assert session.agent_states["gpt"].status == AgentStatus.REVIEWING

        await orch.close()


class TestAgentFailureTracking:
    @pytest.mark.asyncio
    async def test_agent_marked_failed_on_trigger_error(self):
        """When a trigger returns success=False, the agent should be marked FAILED."""
        models = [ModelConfig(id="codex", client_type="codex")]
        mgr, session = _make_manager_with_config(models)
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")

        class FailingTrigger(TriggerEngine):
            async def create_session(self, model_id: str) -> str:
                return "fail-session"

            async def send_prompt(self, client_session_id, model_id, prompt):
                return TriggerResult(
                    success=False, error="sandbox blocked", client_session_id=client_session_id
                )

            async def close(self):
                pass

        orch._create_trigger = lambda ct: FailingTrigger()

        await orch.start(session.id)
        await asyncio.sleep(0.1)

        assert session.agent_states["codex"].status == AgentStatus.FAILED
        assert session.agent_states["codex"].submitted_at is not None

        await orch.close()

    @pytest.mark.asyncio
    async def test_agent_marked_failed_on_trigger_exception(self):
        """When a trigger raises an exception, the agent should be marked FAILED."""
        models = [ModelConfig(id="codex", client_type="codex")]
        mgr, session = _make_manager_with_config(models)
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")

        class ExplodingTrigger(TriggerEngine):
            async def create_session(self, model_id: str) -> str:
                return "explode-session"

            async def send_prompt(self, client_session_id, model_id, prompt):
                raise RuntimeError("CLI not found")

            async def close(self):
                pass

        orch._create_trigger = lambda ct: ExplodingTrigger()

        await orch.start(session.id)
        await asyncio.sleep(0.1)

        assert session.agent_states["codex"].status == AgentStatus.FAILED

        await orch.close()

    @pytest.mark.asyncio
    async def test_agent_marked_failed_when_no_review_submitted(self):
        """When trigger succeeds but no review is submitted, agent should be marked FAILED."""
        models = [ModelConfig(id="codex", client_type="codex")]
        mgr, session = _make_manager_with_config(models)
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")

        # MockTrigger returns success=True but never calls submit_review
        orch._create_trigger = lambda ct: MockTrigger()

        await orch.start(session.id)
        await asyncio.sleep(0.1)

        # Agent should be FAILED because trigger "succeeded" but no review was submitted
        assert session.agent_states["codex"].status == AgentStatus.FAILED

        await orch.close()


class TestAgentTaskType:
    @pytest.mark.asyncio
    async def test_review_task_type_on_start(self):
        models = [
            ModelConfig(id="opus", client_type="claude-code", role="security"),
        ]
        mgr, session = _make_manager_with_config(models)
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")

        orch._create_trigger = lambda ct: MockTrigger()

        await orch.start(session.id)
        await asyncio.sleep(0.05)

        agent = session.agent_states["opus"]
        assert agent.task_type == AgentTaskType.REVIEW
        assert agent.prompt_preview != ""
        assert len(agent.prompt_preview) <= 200

        await orch.close()

    @pytest.mark.asyncio
    async def test_deliberation_task_type(self):
        models = [
            ModelConfig(id="opus", client_type="claude-code"),
            ModelConfig(id="gpt", client_type="claude-code"),
        ]
        mgr, session = _make_manager_with_config(models)
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")

        mock_trigger = MockTrigger()
        orch._triggers = {"opus": mock_trigger, "gpt": mock_trigger}
        session.client_sessions = {"opus": "mock-opus", "gpt": "mock-gpt"}

        # Initialize agent states
        from ai_review.models import AgentState
        session.agent_states["opus"] = AgentState(model_id="opus")
        session.agent_states["gpt"] = AgentState(model_id="gpt")

        # Submit reviews and advance
        mgr.on_review_submitted = None
        mgr.submit_review(session.id, "opus", [
            {"title": "Bug", "severity": "high", "file": "a.py", "description": "desc"}
        ])
        mgr.submit_review(session.id, "gpt", [
            {"title": "Perf", "severity": "medium", "file": "b.py", "description": "desc"}
        ])

        await orch._advance_to_deliberation(session.id)
        await asyncio.sleep(0.05)

        # At least one agent should be in DELIBERATION task_type
        delib_agents = [
            a for a in session.agent_states.values()
            if a.task_type == AgentTaskType.DELIBERATION
        ]
        assert len(delib_agents) > 0

        await orch.close()
