"""Tests for the orchestration layer."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock

import pytest

from ai_review.consensus import apply_consensus
from ai_review.models import (
    AgentState,
    AgentStatus,
    AgentTaskType,
    IssueResponseAction,
    ModelConfig,
    OpinionAction,
    ReviewSession,
    SessionConfig,
    SessionStatus,
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
        self, client_session_id: str, model_id: str, prompt: str, model_config=None
    ) -> TriggerResult:
        self.sent_prompts.append((client_session_id, model_id, prompt))
        return TriggerResult(success=True, output="ok", client_session_id=client_session_id)

    async def close(self) -> None:
        pass


# --- Fixtures ---


def _make_manager_with_config(models: list[ModelConfig]) -> SessionManager:
    mgr = SessionManager()
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

        # Patch _create_trigger to return mocks
        triggers_map = {"opus": mock_a, "gpt": mock_b}
        orch._create_trigger = lambda ct: triggers_map.get(ct, MockTrigger())

        # Manually set triggers since start() creates them (session-scoped)
        orch._triggers[session.id] = triggers_map

        # Simulate start by creating sessions and firing
        for mc in models:
            trigger = orch._triggers[session.id][mc.id]
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
        mgr = SessionManager()
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")

        assert mgr.on_review_submitted is not None
        assert mgr.on_opinion_submitted is not None

    @pytest.mark.asyncio
    async def test_callbacks_detached_on_close(self):
        mgr = SessionManager()
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

        # Set up mock triggers (session-scoped)
        mock_trigger = MockTrigger()
        orch._triggers[session.id] = {"opus": mock_trigger, "gpt": mock_trigger}
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

        # Replace trigger creation with mocks (session-scoped)
        mock_trigger = MockTrigger()
        orch._triggers[session.id] = {"opus": mock_trigger, "gpt": mock_trigger}
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

        # Phase 4: Re-apply consensus and advance
        await orch._check_and_advance(session.id)

        # Both issues have fix_required consensus → FIXING directly (single-round)
        for issue in session.issues:
            assert issue.consensus is True
        assert session.status == SessionStatus.FIXING


class SlowMockTrigger(TriggerEngine):
    """A trigger that blocks until explicitly released."""

    def __init__(self) -> None:
        self.release = asyncio.Event()

    async def create_session(self, model_id: str) -> str:
        return f"slow-{model_id}"

    async def send_prompt(self, client_session_id, model_id, prompt, model_config=None):
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
        orch._create_trigger = lambda ct: SlowMockTrigger()

        await orch.start(session.id)
        await asyncio.sleep(0.05)

        assert "opus" in session.agent_states
        assert "gpt" in session.agent_states
        assert session.agent_states["opus"].status == AgentStatus.REVIEWING
        assert session.agent_states["gpt"].status == AgentStatus.REVIEWING
        assert session.agent_states["opus"].started_at is not None
        assert session.agent_states["opus"].submitted_at is None

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
        orch._create_trigger = lambda ct: SlowMockTrigger()

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

            async def send_prompt(self, client_session_id, model_id, prompt, model_config=None):
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

            async def send_prompt(self, client_session_id, model_id, prompt, model_config=None):
                raise RuntimeError("CLI not found")

            async def close(self):
                pass

        orch._create_trigger = lambda ct: ExplodingTrigger()
        orch._trigger_retry_delays = []  # disable retries for this test

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

    @pytest.mark.asyncio
    async def test_deliberation_without_submission_becomes_waiting(self):
        """In deliberation, missing submission should become WAITING (not FAILED)."""
        models = [ModelConfig(id="codex", client_type="codex")]
        mgr, session = _make_manager_with_config(models)
        session.status = SessionStatus.DELIBERATING
        session.agent_states["codex"] = AgentState(
            model_id="codex",
            status=AgentStatus.REVIEWING,
            task_type=AgentTaskType.DELIBERATION,
        )
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")

        await orch._fire_trigger(session.id, MockTrigger(), "mock-codex", "codex", "deliberate")
        assert session.agent_states["codex"].status == AgentStatus.WAITING
        assert session.agent_states["codex"].submitted_at is not None

        await orch.close()


class TestFireTriggerRetry:
    """Tests for _fire_trigger retry logic."""

    @pytest.mark.asyncio
    async def test_retry_succeeds_after_transient_failure(self):
        """First attempt fails, retry succeeds — agent should NOT be FAILED."""
        models = [ModelConfig(id="codex", client_type="codex")]
        mgr, session = _make_manager_with_config(models)
        session.agent_states["codex"] = AgentState(model_id="codex", status=AgentStatus.REVIEWING)
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")
        orch._trigger_retry_delays = [0.0]  # fast retry

        call_count = 0

        class TransientTrigger(TriggerEngine):
            async def create_session(self, model_id: str) -> str:
                return "t-session"

            async def send_prompt(self, client_session_id, model_id, prompt, model_config=None):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise RuntimeError("transient failure")
                return TriggerResult(success=True, output="ok", client_session_id=client_session_id)

            async def close(self):
                pass

        trigger = TransientTrigger()
        await orch._fire_trigger(session.id, trigger, "t-session", "codex", "review this")

        assert call_count == 2
        agent = session.agent_states["codex"]
        # Retry succeeded: runtime output was recorded and reason is "trigger completed",
        # NOT the transient exception message.
        assert agent.last_output == "ok"
        assert agent.last_reason != "transient failure"
        await orch.close()

    @pytest.mark.asyncio
    async def test_no_retry_on_trigger_result_failure(self):
        """TriggerResult(success=False) should NOT be retried."""
        models = [ModelConfig(id="codex", client_type="codex")]
        mgr, session = _make_manager_with_config(models)
        session.agent_states["codex"] = AgentState(model_id="codex", status=AgentStatus.REVIEWING)
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")
        orch._trigger_retry_delays = [0.0, 0.0]

        call_count = 0

        class FailResultTrigger(TriggerEngine):
            async def create_session(self, model_id: str) -> str:
                return "f-session"

            async def send_prompt(self, client_session_id, model_id, prompt, model_config=None):
                nonlocal call_count
                call_count += 1
                return TriggerResult(success=False, error="bad input")

            async def close(self):
                pass

        trigger = FailResultTrigger()
        await orch._fire_trigger(session.id, trigger, "f-session", "codex", "review this")

        assert call_count == 1
        assert session.agent_states["codex"].status == AgentStatus.FAILED
        await orch.close()

    @pytest.mark.asyncio
    async def test_all_retries_exhausted(self):
        """All attempts fail with exceptions — agent should be FAILED."""
        models = [ModelConfig(id="codex", client_type="codex")]
        mgr, session = _make_manager_with_config(models)
        session.agent_states["codex"] = AgentState(model_id="codex", status=AgentStatus.REVIEWING)
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")
        orch._trigger_retry_delays = [0.0, 0.0]  # 2 retries = 3 total attempts

        call_count = 0

        class AlwaysFailTrigger(TriggerEngine):
            async def create_session(self, model_id: str) -> str:
                return "a-session"

            async def send_prompt(self, client_session_id, model_id, prompt, model_config=None):
                nonlocal call_count
                call_count += 1
                raise RuntimeError("persistent failure")

            async def close(self):
                pass

        trigger = AlwaysFailTrigger()
        await orch._fire_trigger(session.id, trigger, "a-session", "codex", "review this")

        assert call_count == 3
        assert session.agent_states["codex"].status == AgentStatus.FAILED
        await orch.close()


class TestDisabledAgentSkip:
    @pytest.mark.asyncio
    async def test_disabled_agent_not_triggered(self):
        """Agents with enabled=False should not be triggered on start."""
        models = [
            ModelConfig(id="opus", client_type="claude-code", role="security"),
            ModelConfig(id="gpt", client_type="claude-code", role="perf", enabled=False),
        ]
        mgr, session = _make_manager_with_config(models)
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")

        orch._create_trigger = lambda ct: MockTrigger()

        await orch.start(session.id)
        await asyncio.sleep(0.05)

        assert "opus" in session.agent_states
        assert "gpt" not in session.agent_states
        session_triggers = orch._triggers.get(session.id, {})
        assert "opus" in session_triggers
        assert "gpt" not in session_triggers

        await orch.close()

    @pytest.mark.asyncio
    async def test_all_disabled_stays_manual(self):
        """If all models are disabled, orchestrator stays in manual mode."""
        models = [
            ModelConfig(id="opus", enabled=False),
            ModelConfig(id="gpt", enabled=False),
        ]
        mgr, session = _make_manager_with_config(models)
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")

        await orch.start(session.id)

        session_triggers = orch._triggers.get(session.id, {})
        assert len(session_triggers) == 0
        assert len(session.agent_states) == 0

        await orch.close()

    @pytest.mark.asyncio
    async def test_add_disabled_agent_is_noop(self):
        """Adding a disabled agent via add_agent should be a no-op."""
        models = [
            ModelConfig(id="opus", client_type="claude-code"),
            ModelConfig(id="gpt", client_type="claude-code", enabled=False),
        ]
        mgr, session = _make_manager_with_config(models)
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")

        await orch.add_agent(session.id, "gpt")

        assert "gpt" not in session.agent_states
        session_triggers = orch._triggers.get(session.id, {})
        assert "gpt" not in session_triggers

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
        orch._triggers[session.id] = {"opus": mock_trigger, "gpt": mock_trigger}
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


class TestSessionIsolation:
    """Two concurrent sessions with the same model_id must not interfere."""

    @pytest.mark.asyncio
    async def test_two_sessions_same_model_id(self):
        models = [ModelConfig(id="opus", client_type="claude-code", role="security")]
        mgr = SessionManager()
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")

        # Create two sessions with the same model config
        s1 = ReviewSession(
            status=SessionStatus.REVIEWING,
            config=SessionConfig(models=models, max_turns=3, consensus_threshold=2),
        )
        s2 = ReviewSession(
            status=SessionStatus.REVIEWING,
            config=SessionConfig(models=models, max_turns=3, consensus_threshold=2),
        )
        mgr.sessions[s1.id] = s1
        mgr.sessions[s2.id] = s2

        orch._create_trigger = lambda ct: SlowMockTrigger()

        await orch.start(s1.id)
        await orch.start(s2.id)
        await asyncio.sleep(0.05)

        # Each session has its own trigger entry
        assert s1.id in orch._triggers
        assert s2.id in orch._triggers
        assert "opus" in orch._triggers[s1.id]
        assert "opus" in orch._triggers[s2.id]

        # Each session has its own pending tasks
        assert len(orch._pending_tasks.get(s1.id, [])) == 1
        assert len(orch._pending_tasks.get(s2.id, [])) == 1

        # Both sessions have independent agent states
        assert s1.agent_states["opus"].status == AgentStatus.REVIEWING
        assert s2.agent_states["opus"].status == AgentStatus.REVIEWING

        # Stopping one session should not affect the other
        await orch.stop_session(s1.id)
        assert s1.id not in orch._triggers
        assert s2.id in orch._triggers
        assert s1.agent_states["opus"].status == AgentStatus.FAILED
        assert s2.agent_states["opus"].status == AgentStatus.REVIEWING

        await orch.close()

    @pytest.mark.asyncio
    async def test_stop_session_cancels_only_that_session(self):
        models = [
            ModelConfig(id="opus", client_type="claude-code"),
            ModelConfig(id="gpt", client_type="claude-code"),
        ]
        mgr = SessionManager()
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")

        s1 = ReviewSession(
            status=SessionStatus.REVIEWING,
            config=SessionConfig(models=models, max_turns=3, consensus_threshold=2),
        )
        mgr.sessions[s1.id] = s1

        slow = SlowMockTrigger()
        orch._create_trigger = lambda ct: slow

        await orch.start(s1.id)
        await asyncio.sleep(0.05)

        assert len(orch._pending_tasks.get(s1.id, [])) == 2

        await orch.stop_session(s1.id)

        assert s1.id not in orch._pending_tasks
        assert s1.id not in orch._triggers
        # All agents marked failed
        for agent in s1.agent_states.values():
            assert agent.status == AgentStatus.FAILED

        await orch.close()


class TestIssueResponseCallback:
    def test_callback_registered(self):
        mgr = SessionManager()
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")
        assert mgr.on_issue_responded is not None

    @pytest.mark.asyncio
    async def test_callback_detached_on_close(self):
        mgr = SessionManager()
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")
        await orch.close()
        assert mgr.on_issue_responded is None


class TestAgentResponseFlow:
    @pytest.mark.asyncio
    async def test_deliberation_to_fixing(self):
        """When all issues reach consensus with fix_required, transition to FIXING directly."""
        models = [
            ModelConfig(id="opus", client_type="claude-code"),
            ModelConfig(id="gpt", client_type="claude-code"),
        ]
        mgr, session = _make_manager_with_config(models)
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")

        mock_trigger = MockTrigger()
        orch._triggers[session.id] = {"opus": mock_trigger, "gpt": mock_trigger}
        session.client_sessions = {"opus": "mock-opus", "gpt": "mock-gpt"}

        # Disable auto-advance
        mgr.on_review_submitted = None
        mgr.on_opinion_submitted = None
        mgr.on_issue_responded = None

        # Submit reviews
        mgr.submit_review(session.id, "opus", [
            {"title": "SQL Injection", "severity": "critical", "file": "db.py", "description": "Raw SQL"},
        ])
        mgr.submit_review(session.id, "gpt", [
            {"title": "Perf issue", "severity": "medium", "file": "api.py", "description": "N+1 query"},
        ])

        await orch._advance_to_deliberation(session.id)
        assert session.status == SessionStatus.DELIBERATING

        # Submit opinions (all agree → consensus)
        for issue in session.issues:
            for mc in models:
                if issue.raised_by != mc.id:
                    mgr.submit_opinion(
                        session.id, issue.id, mc.id,
                        "fix_required", "Confirmed", "high",
                    )

        await orch._check_and_advance(session.id)
        assert session.status == SessionStatus.FIXING

        await orch.close()

    @pytest.mark.asyncio
    async def test_all_dismissed_skip_agent_response(self):
        """When all issues are dismissed, skip AGENT_RESPONSE and go to COMPLETE."""
        models = [
            ModelConfig(id="opus", client_type="claude-code"),
            ModelConfig(id="gpt", client_type="claude-code"),
            ModelConfig(id="codex", client_type="codex"),
        ]
        mgr, session = _make_manager_with_config(models)
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")

        mock_trigger = MockTrigger()
        orch._triggers[session.id] = {"opus": mock_trigger, "gpt": mock_trigger, "codex": mock_trigger}
        session.client_sessions = {"opus": "mock-opus", "gpt": "mock-gpt", "codex": "mock-codex"}

        mgr.on_review_submitted = None
        mgr.on_opinion_submitted = None
        mgr.on_issue_responded = None

        mgr.submit_review(session.id, "opus", [
            {"title": "Minor style", "severity": "low", "file": "style.py", "description": "naming"},
        ])
        mgr.submit_review(session.id, "gpt", [])
        mgr.submit_review(session.id, "codex", [])

        await orch._advance_to_deliberation(session.id)

        # gpt and codex both disagree (no_fix) → dismissed (weight 2.0 >= threshold 2)
        for issue in session.issues:
            mgr.submit_opinion(session.id, issue.id, "gpt", "no_fix", "Not an issue")
            mgr.submit_opinion(session.id, issue.id, "codex", "no_fix", "Agreed, not an issue")

        await orch._check_and_advance(session.id)
        # All dismissed, no fix_required → should be COMPLETE
        assert session.status == SessionStatus.COMPLETE

        await orch.close()

    @pytest.mark.asyncio
    async def test_dispute_triggers_redeliberation_from_agent_response(self):
        """Backward compat: dispute from AGENT_RESPONSE re-enters DELIBERATING."""
        models = [
            ModelConfig(id="opus", client_type="claude-code"),
            ModelConfig(id="gpt", client_type="claude-code"),
        ]
        mgr, session = _make_manager_with_config(models)
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")

        mock_trigger = MockTrigger()
        orch._triggers[session.id] = {"opus": mock_trigger, "gpt": mock_trigger}
        session.client_sessions = {"opus": "mock-opus", "gpt": "mock-gpt"}

        mgr.on_review_submitted = None
        mgr.on_opinion_submitted = None

        mgr.submit_review(session.id, "opus", [
            {"title": "Bug", "severity": "high", "file": "x.py", "description": "d"},
        ])
        mgr.submit_review(session.id, "gpt", [])

        await orch._advance_to_deliberation(session.id)

        # Both agree → consensus
        for issue in session.issues:
            mgr.submit_opinion(session.id, issue.id, "gpt", "fix_required", "ok", "high")

        # Force AGENT_RESPONSE state for backward compat testing
        from ai_review.state import transition
        await orch._check_and_advance(session.id)
        # Now in FIXING; manually set to AGENT_RESPONSE for compat test
        session.status = SessionStatus.AGENT_RESPONSE

        mgr.on_issue_responded = orch._on_issue_responded

        # Submit dispute
        issue = session.issues[0]
        mgr.submit_issue_response(
            session.id, issue.id, "dispute",
            reasoning="Not a real bug",
            submitted_by="coding-agent",
        )

        await asyncio.sleep(0.05)
        assert session.status == SessionStatus.DELIBERATING
        assert session.issue_responses == []  # Cleared for re-deliberation

        await orch.close()

    @pytest.mark.asyncio
    async def test_consensus_fix_required_goes_to_fixing(self):
        """fix_required consensus transitions directly to FIXING (no AGENT_RESPONSE)."""
        models = [
            ModelConfig(id="opus", client_type="claude-code"),
            ModelConfig(id="gpt", client_type="claude-code"),
        ]
        mgr, session = _make_manager_with_config(models)
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")

        mock_trigger = MockTrigger()
        orch._triggers[session.id] = {"opus": mock_trigger, "gpt": mock_trigger}
        session.client_sessions = {"opus": "mock-opus", "gpt": "mock-gpt"}

        mgr.on_review_submitted = None
        mgr.on_opinion_submitted = None

        mgr.submit_review(session.id, "opus", [
            {"title": "Bug", "severity": "high", "file": "x.py", "description": "d"},
        ])
        mgr.submit_review(session.id, "gpt", [])

        await orch._advance_to_deliberation(session.id)

        for issue in session.issues:
            mgr.submit_opinion(session.id, issue.id, "gpt", "fix_required", "ok", "high")

        await orch._check_and_advance(session.id)
        assert session.status == SessionStatus.FIXING

        await orch.close()


class TestAgentResponseE2E:
    """End-to-end tests covering full flow (new: DELIBERATING → FIXING directly)."""

    @pytest.mark.asyncio
    async def test_full_flow_deliberation_to_fixing(self):
        """review → deliberation → fixing (skipping agent_response)"""
        models = [
            ModelConfig(id="opus", client_type="claude-code"),
            ModelConfig(id="gpt", client_type="claude-code"),
        ]
        mgr, session = _make_manager_with_config(models)
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")

        mock_trigger = MockTrigger()
        orch._triggers[session.id] = {"opus": mock_trigger, "gpt": mock_trigger}
        session.client_sessions = {"opus": "mock-opus", "gpt": "mock-gpt"}

        mgr.on_review_submitted = None
        mgr.on_opinion_submitted = None

        # Review
        mgr.submit_review(session.id, "opus", [
            {"title": "Bug A", "severity": "high", "file": "a.py", "description": "d"},
        ])
        mgr.submit_review(session.id, "gpt", [
            {"title": "Bug B", "severity": "medium", "file": "b.py", "description": "d"},
        ])

        # Deliberation
        await orch._advance_to_deliberation(session.id)
        assert session.status == SessionStatus.DELIBERATING

        # Opinions → consensus
        for issue in session.issues:
            for mc in models:
                if issue.raised_by != mc.id:
                    mgr.submit_opinion(
                        session.id, issue.id, mc.id,
                        "fix_required", "Confirmed", "high",
                    )

        await orch._check_and_advance(session.id)
        assert session.status == SessionStatus.FIXING

        await orch.close()

    @pytest.mark.asyncio
    async def test_full_flow_to_fixing(self):
        """review → deliberation → FIXING (new flow, no agent_response)."""
        models = [
            ModelConfig(id="opus", client_type="claude-code"),
            ModelConfig(id="gpt", client_type="claude-code"),
        ]
        mgr, session = _make_manager_with_config(models)
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")

        mock_trigger = MockTrigger()
        orch._triggers[session.id] = {"opus": mock_trigger, "gpt": mock_trigger}
        session.client_sessions = {"opus": "mock-opus", "gpt": "mock-gpt"}

        mgr.on_review_submitted = None
        mgr.on_opinion_submitted = None

        mgr.submit_review(session.id, "opus", [
            {"title": "Bug", "severity": "high", "file": "x.py", "description": "d"},
        ])
        mgr.submit_review(session.id, "gpt", [])

        # Deliberation → consensus
        await orch._advance_to_deliberation(session.id)
        for issue in session.issues:
            mgr.submit_opinion(session.id, issue.id, "gpt", "fix_required", "ok", "high")

        await orch._check_and_advance(session.id)
        # New flow: DELIBERATING → FIXING directly
        assert session.status == SessionStatus.FIXING

        await orch.close()

    @pytest.mark.asyncio
    async def test_no_confirmed_issues_skip_agent_response(self):
        """When no fix_required issues, skip AGENT_RESPONSE and finish directly."""
        models = [
            ModelConfig(id="opus", client_type="claude-code"),
            ModelConfig(id="gpt", client_type="claude-code"),
            ModelConfig(id="codex", client_type="codex"),
        ]
        mgr, session = _make_manager_with_config(models)
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")

        mock_trigger = MockTrigger()
        orch._triggers[session.id] = {"opus": mock_trigger, "gpt": mock_trigger, "codex": mock_trigger}
        session.client_sessions = {"opus": "mock-opus", "gpt": "mock-gpt", "codex": "mock-codex"}

        mgr.on_review_submitted = None
        mgr.on_opinion_submitted = None
        mgr.on_issue_responded = None

        mgr.submit_review(session.id, "opus", [
            {"title": "Nit", "severity": "low", "file": "style.py", "description": "naming"},
        ])
        mgr.submit_review(session.id, "gpt", [])
        mgr.submit_review(session.id, "codex", [])

        await orch._advance_to_deliberation(session.id)

        # Both say no_fix → dismissed
        for issue in session.issues:
            mgr.submit_opinion(session.id, issue.id, "gpt", "no_fix", "nah")
            mgr.submit_opinion(session.id, issue.id, "codex", "no_fix", "nah")

        await orch._check_and_advance(session.id)
        assert session.status == SessionStatus.COMPLETE

        await orch.close()

    @pytest.mark.asyncio
    async def test_backward_compat_deliberation_to_complete(self):
        """Backward compat: finish API still works from DELIBERATING."""
        models = [ModelConfig(id="opus", client_type="claude-code")]
        mgr, session = _make_manager_with_config(models)
        session.status = SessionStatus.DELIBERATING
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")

        # No fix_required issues → _finish should go to COMPLETE
        await orch._finish(session.id)
        assert session.status == SessionStatus.COMPLETE

        await orch.close()


class TestFixCompletedCallback:
    def test_on_fix_completed_registered(self):
        mgr = SessionManager()
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")
        assert mgr.on_fix_completed is not None

    @pytest.mark.asyncio
    async def test_on_fix_completed_detached_on_close(self):
        mgr = SessionManager()
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")
        await orch.close()
        assert mgr.on_fix_completed is None


class TestVerificationFlow:
    """Tests for the FIXING → VERIFYING → COMPLETE/FIXING loop."""

    @pytest.mark.asyncio
    async def test_deliberation_to_fixing_transition(self):
        """fix_required consensus → FIXING directly."""
        models = [
            ModelConfig(id="opus", client_type="claude-code"),
            ModelConfig(id="gpt", client_type="claude-code"),
        ]
        mgr, session = _make_manager_with_config(models)
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")

        mock_trigger = MockTrigger()
        orch._triggers[session.id] = {"opus": mock_trigger, "gpt": mock_trigger}
        session.client_sessions = {"opus": "mock-opus", "gpt": "mock-gpt"}

        mgr.on_review_submitted = None
        mgr.on_opinion_submitted = None

        mgr.submit_review(session.id, "opus", [
            {"title": "Bug", "severity": "high", "file": "x.py", "description": "d"},
        ])
        mgr.submit_review(session.id, "gpt", [])

        await orch._advance_to_deliberation(session.id)

        for issue in session.issues:
            mgr.submit_opinion(session.id, issue.id, "gpt", "fix_required", "ok", "high")

        await orch._check_and_advance(session.id)
        assert session.status == SessionStatus.FIXING

        await orch.close()

    @pytest.mark.asyncio
    async def test_verification_all_resolved_then_complete(self):
        """Original reporter says no_fix → COMPLETE."""
        models = [
            ModelConfig(id="opus", client_type="claude-code"),
            ModelConfig(id="gpt", client_type="claude-code"),
        ]
        mgr, session = _make_manager_with_config(models)
        session.status = SessionStatus.VERIFYING
        session.verification_round = 1
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")

        # Create a confirmed issue raised by "opus" at turn 1
        from ai_review.models import Issue, Opinion, Severity
        issue = Issue(
            title="Bug", severity=Severity.HIGH, file="x.py",
            description="d", raised_by="opus",
            consensus=True, consensus_type="fix_required", turn=1,
        )
        session.issues.append(issue)

        # Set agent states as done (WAITING, not REVIEWING)
        session.agent_states["opus"] = AgentState(
            model_id="opus", status=AgentStatus.WAITING, task_type=AgentTaskType.VERIFICATION,
        )
        session.agent_states["gpt"] = AgentState(
            model_id="gpt", status=AgentStatus.WAITING, task_type=AgentTaskType.VERIFICATION,
        )

        # Original reporter (opus) says no_fix (= resolved)
        mgr.on_opinion_submitted = None
        mgr.submit_opinion(session.id, issue.id, "opus", "no_fix", "Fixed correctly")

        await orch._check_verification_complete(session.id)
        assert session.status == SessionStatus.COMPLETE

        await orch.close()

    @pytest.mark.asyncio
    async def test_verification_reporter_says_unresolved_then_fixing(self):
        """Original reporter says fix_required → back to FIXING."""
        models = [
            ModelConfig(id="opus", client_type="claude-code"),
            ModelConfig(id="gpt", client_type="claude-code"),
        ]
        mgr, session = _make_manager_with_config(models)
        session.status = SessionStatus.VERIFYING
        session.verification_round = 1
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")

        from ai_review.models import Issue, Severity
        issue = Issue(
            title="Bug", severity=Severity.HIGH, file="x.py",
            description="d", raised_by="opus",
            consensus=True, consensus_type="fix_required", turn=1,
        )
        session.issues.append(issue)

        session.agent_states["opus"] = AgentState(
            model_id="opus", status=AgentStatus.WAITING, task_type=AgentTaskType.VERIFICATION,
        )

        mgr.on_opinion_submitted = None
        # Original reporter (opus) says still broken
        mgr.submit_opinion(session.id, issue.id, "opus", "fix_required", "Still broken")

        await orch._check_verification_complete(session.id)
        assert session.status == SessionStatus.FIXING

        await orch.close()

    @pytest.mark.asyncio
    async def test_max_verification_rounds_force_complete(self):
        """When max_verification_rounds is exceeded, force COMPLETE."""
        models = [ModelConfig(id="opus", client_type="claude-code")]
        mgr, session = _make_manager_with_config(models)
        session.config.max_verification_rounds = 1
        session.status = SessionStatus.VERIFYING
        session.verification_round = 1
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")

        from ai_review.models import Issue, Severity
        issue = Issue(
            title="Bug", severity=Severity.HIGH, file="x.py",
            description="d", raised_by="opus",
            consensus=True, consensus_type="fix_required", turn=1,
        )
        session.issues.append(issue)

        session.agent_states["opus"] = AgentState(
            model_id="opus", status=AgentStatus.WAITING, task_type=AgentTaskType.VERIFICATION,
        )

        mgr.on_opinion_submitted = None
        mgr.submit_opinion(session.id, issue.id, "opus", "fix_required", "Still broken")

        await orch._check_verification_complete(session.id)
        # Max rounds reached → force COMPLETE despite unresolved issues
        assert session.status == SessionStatus.COMPLETE

        await orch.close()

    @pytest.mark.asyncio
    async def test_backward_compat_agent_response_to_complete(self):
        """Backward compat: _try_agent_response_or_finish from AGENT_RESPONSE still works."""
        models = [ModelConfig(id="opus", client_type="claude-code")]
        mgr, session = _make_manager_with_config(models)
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")

        # Manually set AGENT_RESPONSE (persisted session scenario)
        session.status = SessionStatus.AGENT_RESPONSE

        # No fix_required issues → COMPLETE
        await orch._try_agent_response_or_finish(session.id)
        assert session.status == SessionStatus.COMPLETE

        await orch.close()

    @pytest.mark.asyncio
    async def test_start_verification_sends_prompts(self):
        """_start_verification sends prompts only to original reporters."""
        from ai_review.models import Issue, Severity

        models = [
            ModelConfig(id="opus", client_type="claude-code"),
            ModelConfig(id="gpt", client_type="claude-code"),
        ]
        mgr, session = _make_manager_with_config(models)
        session.status = SessionStatus.VERIFYING
        session.verification_round = 1
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")

        # Add fix_required issues raised by different reporters
        issue_opus = Issue(
            title="Bug A", severity=Severity.HIGH, file="a.py",
            description="d", raised_by="opus",
            consensus=True, consensus_type="fix_required", turn=0,
        )
        issue_gpt = Issue(
            title="Bug B", severity=Severity.MEDIUM, file="b.py",
            description="d", raised_by="gpt",
            consensus=True, consensus_type="fix_required", turn=0,
        )
        session.issues = [issue_opus, issue_gpt]

        mock_trigger = MockTrigger()
        orch._triggers[session.id] = {"opus": mock_trigger, "gpt": mock_trigger}
        session.client_sessions = {"opus": "mock-opus", "gpt": "mock-gpt"}
        session.agent_states["opus"] = AgentState(model_id="opus")
        session.agent_states["gpt"] = AgentState(model_id="gpt")

        await orch._start_verification(session.id)
        await asyncio.sleep(0.05)

        # Both reporters should be in VERIFICATION task type
        assert session.agent_states["opus"].task_type == AgentTaskType.VERIFICATION
        assert session.agent_states["gpt"].task_type == AgentTaskType.VERIFICATION
        assert len(mock_trigger.sent_prompts) >= 2

        await orch.close()

    @pytest.mark.asyncio
    async def test_verification_agents_still_reviewing_waits(self):
        """_check_verification_complete should wait if reporter agent is still REVIEWING."""
        models = [ModelConfig(id="opus", client_type="claude-code")]
        mgr, session = _make_manager_with_config(models)
        session.status = SessionStatus.VERIFYING
        session.verification_round = 1
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")

        from ai_review.models import Issue, Severity
        issue = Issue(
            title="Bug", severity=Severity.HIGH, file="x.py",
            description="d", raised_by="opus",
            consensus=True, consensus_type="fix_required", turn=1,
        )
        session.issues.append(issue)

        # Agent still REVIEWING
        session.agent_states["opus"] = AgentState(
            model_id="opus", status=AgentStatus.REVIEWING, task_type=AgentTaskType.VERIFICATION,
        )

        mgr.on_opinion_submitted = None
        mgr.submit_opinion(session.id, issue.id, "opus", "no_fix", "Fixed")

        await orch._check_verification_complete(session.id)
        # Should NOT advance — agent still reviewing
        assert session.status == SessionStatus.VERIFYING

        await orch.close()


class TestDeltaReviewE2E:
    """End-to-end tests for the full delta review loop."""

    @pytest.mark.asyncio
    async def test_full_flow_accept_fix_verify_complete(self):
        """review → delib → FIXING → VERIFYING → all no_fix → COMPLETE."""
        from ai_review.models import Issue, Severity

        models = [
            ModelConfig(id="opus", client_type="claude-code"),
            ModelConfig(id="gpt", client_type="claude-code"),
        ]
        mgr, session = _make_manager_with_config(models)
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")

        mock_trigger = MockTrigger()
        orch._triggers[session.id] = {"opus": mock_trigger, "gpt": mock_trigger}
        session.client_sessions = {"opus": "mock-opus", "gpt": "mock-gpt"}

        # Phase 1: REVIEWING → DELIBERATING → FIXING
        mgr.on_review_submitted = None
        mgr.on_opinion_submitted = None

        mgr.submit_review(session.id, "opus", [
            {"title": "Bug", "severity": "high", "file": "x.py", "description": "d"},
        ])
        mgr.submit_review(session.id, "gpt", [])

        await orch._advance_to_deliberation(session.id)

        for issue in session.issues:
            mgr.submit_opinion(session.id, issue.id, "gpt", "fix_required", "ok", "high")

        await orch._check_and_advance(session.id)
        assert session.status == SessionStatus.FIXING

        issue = session.issues[0]

        # Phase 2: FIXING → VERIFYING (manual state transition, avoiding git)
        session.verification_round += 1
        session.status = SessionStatus.VERIFYING
        for iss in session.issues:
            if iss.consensus_type == "fix_required":
                iss.turn += 1

        session.agent_states["opus"] = AgentState(
            model_id="opus", status=AgentStatus.WAITING, task_type=AgentTaskType.VERIFICATION,
        )
        session.agent_states["gpt"] = AgentState(
            model_id="gpt", status=AgentStatus.WAITING, task_type=AgentTaskType.VERIFICATION,
        )

        # Phase 4: All no_fix → COMPLETE
        mgr.on_opinion_submitted = None
        mgr.submit_opinion(session.id, issue.id, "opus", "no_fix", "Fixed correctly")
        mgr.submit_opinion(session.id, issue.id, "gpt", "no_fix", "Looks good")

        await orch._check_verification_complete(session.id)
        assert session.status == SessionStatus.COMPLETE

        await orch.close()

    @pytest.mark.asyncio
    async def test_full_flow_fix_fail_refix_complete(self):
        """→ VERIFYING → reporter fix_required → FIXING → VERIFYING → reporter no_fix → COMPLETE."""
        from ai_review.models import Issue, Severity

        models = [
            ModelConfig(id="opus", client_type="claude-code"),
            ModelConfig(id="gpt", client_type="claude-code"),
        ]
        mgr, session = _make_manager_with_config(models)
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")

        # Start at VERIFYING round 1 with a confirmed issue raised by opus
        issue = Issue(
            title="Bug", severity=Severity.HIGH, file="x.py",
            description="d", raised_by="opus",
            consensus=True, consensus_type="fix_required", turn=1,
        )
        session.issues.append(issue)
        session.status = SessionStatus.VERIFYING
        session.verification_round = 1

        session.agent_states["opus"] = AgentState(
            model_id="opus", status=AgentStatus.WAITING, task_type=AgentTaskType.VERIFICATION,
        )

        # Round 1: reporter (opus) says fix_required → back to FIXING
        mgr.on_opinion_submitted = None
        mgr.submit_opinion(session.id, issue.id, "opus", "fix_required", "Still broken")

        await orch._check_verification_complete(session.id)
        assert session.status == SessionStatus.FIXING

        # Simulate second fix attempt → VERIFYING round 2
        session.verification_round += 1
        session.status = SessionStatus.VERIFYING
        issue.turn += 1

        session.agent_states["opus"] = AgentState(
            model_id="opus", status=AgentStatus.WAITING, task_type=AgentTaskType.VERIFICATION,
        )

        # Round 2: reporter (opus) says no_fix → COMPLETE
        mgr.submit_opinion(session.id, issue.id, "opus", "no_fix", "Now fixed")

        await orch._check_verification_complete(session.id)
        assert session.status == SessionStatus.COMPLETE

        await orch.close()

    @pytest.mark.asyncio
    async def test_full_flow_max_rounds_force_complete(self):
        """verification_round > max → forced COMPLETE even with unresolved issues."""
        from ai_review.models import Issue, Severity

        models = [ModelConfig(id="opus", client_type="claude-code")]
        mgr, session = _make_manager_with_config(models)
        session.config.max_verification_rounds = 2
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")

        issue = Issue(
            title="Bug", severity=Severity.HIGH, file="x.py",
            description="d", consensus=True, consensus_type="fix_required", turn=1,
        )
        session.issues.append(issue)

        # Round 1: fix_required → FIXING
        session.status = SessionStatus.VERIFYING
        session.verification_round = 1
        session.agent_states["opus"] = AgentState(
            model_id="opus", status=AgentStatus.WAITING, task_type=AgentTaskType.VERIFICATION,
        )

        mgr.on_opinion_submitted = None
        mgr.submit_opinion(session.id, issue.id, "opus", "fix_required", "Still broken")

        await orch._check_verification_complete(session.id)
        assert session.status == SessionStatus.FIXING

        # Round 2: still fix_required but max reached → COMPLETE
        session.verification_round = 2
        session.status = SessionStatus.VERIFYING
        issue.turn += 1
        session.agent_states["opus"] = AgentState(
            model_id="opus", status=AgentStatus.WAITING, task_type=AgentTaskType.VERIFICATION,
        )
        mgr.submit_opinion(session.id, issue.id, "opus", "fix_required", "Still broken round 2")

        await orch._check_verification_complete(session.id)
        assert session.status == SessionStatus.COMPLETE

        await orch.close()

    @pytest.mark.asyncio
    async def test_backward_compat_no_fixing(self):
        """All dismissed (no fix_required) → COMPLETE directly, skipping AGENT_RESPONSE/FIXING."""
        from ai_review.models import Issue, Severity

        models = [ModelConfig(id="opus", client_type="claude-code")]
        mgr, session = _make_manager_with_config(models)
        session.status = SessionStatus.DELIBERATING
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")

        # Create a dismissed issue (consensus=True, type=dismissed)
        issue = Issue(
            title="Nit", severity=Severity.LOW, file="x.py",
            description="minor", consensus=True, consensus_type="dismissed",
        )
        session.issues.append(issue)

        # _try_agent_response_or_finish: no fix_required → skip AGENT_RESPONSE → COMPLETE
        await orch._try_agent_response_or_finish(session.id)
        assert session.status == SessionStatus.COMPLETE

        await orch.close()


class TestSingleRoundDeliberation:
    """C3: Single-round deliberation goes directly to FIXING."""

    @pytest.mark.asyncio
    async def test_single_round_to_fixing(self):
        """After 1 cross-review round, fix_required issues → FIXING."""
        models = [
            ModelConfig(id="opus", client_type="claude-code"),
            ModelConfig(id="gpt", client_type="claude-code"),
        ]
        mgr, session = _make_manager_with_config(models)
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")

        mock_trigger = MockTrigger()
        orch._triggers[session.id] = {"opus": mock_trigger, "gpt": mock_trigger}
        session.client_sessions = {"opus": "mock-opus", "gpt": "mock-gpt"}

        mgr.on_review_submitted = None
        mgr.on_opinion_submitted = None

        # Submit reviews
        mgr.submit_review(session.id, "opus", [
            {"title": "Bug", "severity": "high", "file": "a.py", "description": "bug"},
        ])
        mgr.submit_review(session.id, "gpt", [
            {"title": "Perf", "severity": "medium", "file": "b.py", "description": "slow"},
        ])

        # Advance to deliberation
        await orch._advance_to_deliberation(session.id)
        assert session.status == SessionStatus.DELIBERATING

        # Cross-review: each model opines on the other's issue
        for issue in session.issues:
            for mc in models:
                if issue.raised_by != mc.id:
                    mgr.submit_opinion(session.id, issue.id, mc.id, "fix_required", "Confirmed")

        # Single round → FIXING
        await orch._check_and_advance(session.id)
        assert session.status == SessionStatus.FIXING

        await orch.close()

    @pytest.mark.asyncio
    async def test_all_dismissed_to_complete(self):
        """No fix_required after cross-review → COMPLETE directly."""
        models = [
            ModelConfig(id="opus", client_type="claude-code"),
            ModelConfig(id="gpt", client_type="claude-code"),
        ]
        mgr, session = _make_manager_with_config(models)
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")

        mock_trigger = MockTrigger()
        orch._triggers[session.id] = {"opus": mock_trigger, "gpt": mock_trigger}
        session.client_sessions = {"opus": "mock-opus", "gpt": "mock-gpt"}

        mgr.on_review_submitted = None
        mgr.on_opinion_submitted = None

        # Submit review with one issue
        mgr.submit_review(session.id, "opus", [
            {"title": "Nit", "severity": "low", "file": "a.py", "description": "minor"},
        ])
        mgr.submit_review(session.id, "gpt", [])

        await orch._advance_to_deliberation(session.id)
        assert session.status == SessionStatus.DELIBERATING

        # gpt dismisses the issue
        for issue in session.issues:
            mgr.submit_opinion(session.id, issue.id, "gpt", "no_fix", "Not important")

        await orch._check_and_advance(session.id)
        assert session.status == SessionStatus.COMPLETE

        await orch.close()


class TestScopedVerification:
    """C5: Scoped verification by original reporter."""

    @pytest.mark.asyncio
    async def test_verification_only_reporter_prompted(self):
        """Only the original reporter gets a verification prompt, not all agents."""
        from ai_review.models import Issue, Severity

        models = [
            ModelConfig(id="opus", client_type="claude-code"),
            ModelConfig(id="gpt", client_type="claude-code"),
        ]
        mgr, session = _make_manager_with_config(models)
        session.status = SessionStatus.VERIFYING
        session.verification_round = 1
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")

        # Issue raised by opus only
        issue = Issue(
            title="Bug", severity=Severity.HIGH, file="x.py",
            description="d", raised_by="opus",
            consensus=True, consensus_type="fix_required", turn=0,
        )
        session.issues.append(issue)

        mock_trigger = MockTrigger()
        orch._triggers[session.id] = {"opus": mock_trigger, "gpt": mock_trigger}
        session.client_sessions = {"opus": "mock-opus", "gpt": "mock-gpt"}
        session.agent_states["opus"] = AgentState(model_id="opus")
        session.agent_states["gpt"] = AgentState(model_id="gpt")

        await orch._start_verification(session.id)
        await asyncio.sleep(0.05)

        # Only opus should be prompted (original reporter)
        assert session.agent_states["opus"].task_type == AgentTaskType.VERIFICATION
        # gpt should NOT have been set to VERIFICATION
        assert session.agent_states["gpt"].task_type != AgentTaskType.VERIFICATION

        await orch.close()

    @pytest.mark.asyncio
    async def test_verification_resolved_by_reporter(self):
        """Original reporter says no_fix → issue resolved → COMPLETE."""
        from ai_review.models import Issue, Severity

        models = [
            ModelConfig(id="opus", client_type="claude-code"),
            ModelConfig(id="gpt", client_type="claude-code"),
        ]
        mgr, session = _make_manager_with_config(models)
        session.status = SessionStatus.VERIFYING
        session.verification_round = 1
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")

        issue = Issue(
            title="Bug", severity=Severity.HIGH, file="x.py",
            description="d", raised_by="opus",
            consensus=True, consensus_type="fix_required", turn=1,
        )
        session.issues.append(issue)
        session.agent_states["opus"] = AgentState(
            model_id="opus", status=AgentStatus.WAITING, task_type=AgentTaskType.VERIFICATION,
        )

        mgr.on_opinion_submitted = None
        mgr.submit_opinion(session.id, issue.id, "opus", "no_fix", "Fixed")

        await orch._check_verification_complete(session.id)
        assert session.status == SessionStatus.COMPLETE

        await orch.close()

    @pytest.mark.asyncio
    async def test_all_dismissed_completes_from_fixing(self):
        """All fix_required issues dismissed → COMPLETE from FIXING."""
        from ai_review.models import Issue, IssueDismissal, Severity

        models = [ModelConfig(id="opus", client_type="claude-code")]
        mgr, session = _make_manager_with_config(models)
        session.status = SessionStatus.FIXING
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")

        issue = Issue(
            title="Bug", severity=Severity.HIGH, file="x.py",
            description="d", raised_by="opus",
            consensus=True, consensus_type="fix_required",
        )
        session.issues.append(issue)

        # Dismiss the only fix_required issue via the callback
        mgr.on_issue_dismissed = orch._on_issue_dismissed
        mgr.dismiss_issue(session.id, issue.id, "Not needed", "tony")

        await asyncio.sleep(0.05)
        assert session.status == SessionStatus.COMPLETE

        await orch.close()

    @pytest.mark.asyncio
    async def test_partial_dismiss_partial_fix(self):
        """One dismissed + one fixed → COMPLETE after verification."""
        from ai_review.models import Issue, Severity

        models = [
            ModelConfig(id="opus", client_type="claude-code"),
            ModelConfig(id="gpt", client_type="claude-code"),
        ]
        mgr, session = _make_manager_with_config(models)
        session.status = SessionStatus.VERIFYING
        session.verification_round = 1
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")

        issue_a = Issue(
            title="Bug A", severity=Severity.HIGH, file="a.py",
            description="d", raised_by="opus",
            consensus=True, consensus_type="fix_required", turn=1,
        )
        issue_b = Issue(
            title="Bug B", severity=Severity.MEDIUM, file="b.py",
            description="d", raised_by="gpt",
            consensus=True, consensus_type="fix_required", turn=1,
        )
        session.issues = [issue_a, issue_b]

        # Dismiss issue_b
        from ai_review.models import IssueDismissal
        session.dismissals.append(IssueDismissal(issue_id=issue_b.id, reasoning="Not needed"))

        session.agent_states["opus"] = AgentState(
            model_id="opus", status=AgentStatus.WAITING, task_type=AgentTaskType.VERIFICATION,
        )

        mgr.on_opinion_submitted = None
        # Reporter of issue_a (opus) says no_fix → resolved
        mgr.submit_opinion(session.id, issue_a.id, "opus", "no_fix", "Fixed")

        await orch._check_verification_complete(session.id)
        # issue_a resolved by reporter, issue_b dismissed → COMPLETE
        assert session.status == SessionStatus.COMPLETE

        await orch.close()


class TestNewFlowE2E:
    """C7: End-to-end flow tests for the redesigned flow."""

    @pytest.mark.asyncio
    async def test_full_flow_create_context_start_fix_verify(self):
        """Create → start → cross-review → FIXING → fix → reporter verify → COMPLETE."""
        from ai_review.models import Issue, Severity

        models = [
            ModelConfig(id="opus", client_type="claude-code"),
            ModelConfig(id="gpt", client_type="claude-code"),
        ]
        mgr, session = _make_manager_with_config(models)
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")

        mock_trigger = MockTrigger()
        orch._triggers[session.id] = {"opus": mock_trigger, "gpt": mock_trigger}
        session.client_sessions = {"opus": "mock-opus", "gpt": "mock-gpt"}

        # Disable auto-advance for manual control
        mgr.on_review_submitted = None
        mgr.on_opinion_submitted = None

        # Phase 1: Reviews
        mgr.submit_review(session.id, "opus", [
            {"title": "SQL Injection", "severity": "critical", "file": "db.py", "description": "Raw SQL"},
        ])
        mgr.submit_review(session.id, "gpt", [
            {"title": "N+1 query", "severity": "medium", "file": "api.py", "description": "Perf issue"},
        ])

        # Phase 2: Dedup → Deliberation
        await orch._advance_to_deliberation(session.id)
        assert session.status == SessionStatus.DELIBERATING
        assert len(session.issues) == 2

        # Phase 3: Single-round cross-review (each opines on the other's issue)
        for issue in session.issues:
            for mc in models:
                if issue.raised_by != mc.id:
                    mgr.submit_opinion(session.id, issue.id, mc.id, "fix_required", "Confirmed")

        # Phase 4: DELIBERATING → FIXING directly
        await orch._check_and_advance(session.id)
        assert session.status == SessionStatus.FIXING

        # Phase 5: Simulate fix → VERIFYING (submit_fix_complete handles transition)
        mgr.on_fix_completed = None  # Manual control
        await mgr.submit_fix_complete(
            session.id, "abc1234",
            issues_addressed=[i.id for i in session.issues],
            submitted_by="coding-agent",
        )
        assert session.status == SessionStatus.VERIFYING

        # Set up reporter agent states for verification
        for issue in session.issues:
            session.agent_states[issue.raised_by] = AgentState(
                model_id=issue.raised_by, status=AgentStatus.WAITING,
                task_type=AgentTaskType.VERIFICATION,
            )

        # Phase 6: Original reporters verify their issues
        for issue in session.issues:
            if issue.consensus_type == "fix_required":
                issue.turn += 1
                mgr.submit_opinion(session.id, issue.id, issue.raised_by, "no_fix", "Fixed")

        await orch._check_verification_complete(session.id)
        assert session.status == SessionStatus.COMPLETE

        await orch.close()

    @pytest.mark.asyncio
    async def test_flow_with_dismiss(self):
        """Flow with some issues dismissed: cross-review → FIXING → dismiss → COMPLETE."""
        from ai_review.models import Issue, Severity

        models = [
            ModelConfig(id="opus", client_type="claude-code"),
            ModelConfig(id="gpt", client_type="claude-code"),
        ]
        mgr, session = _make_manager_with_config(models)
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")

        mock_trigger = MockTrigger()
        orch._triggers[session.id] = {"opus": mock_trigger, "gpt": mock_trigger}
        session.client_sessions = {"opus": "mock-opus", "gpt": "mock-gpt"}

        mgr.on_review_submitted = None
        mgr.on_opinion_submitted = None

        # Reviews
        mgr.submit_review(session.id, "opus", [
            {"title": "Bug A", "severity": "high", "file": "a.py", "description": "d"},
            {"title": "Bug B", "severity": "medium", "file": "b.py", "description": "d"},
        ])
        mgr.submit_review(session.id, "gpt", [])

        await orch._advance_to_deliberation(session.id)

        # gpt confirms both
        for issue in session.issues:
            mgr.submit_opinion(session.id, issue.id, "gpt", "fix_required", "Confirmed")

        await orch._check_and_advance(session.id)
        assert session.status == SessionStatus.FIXING

        # Register dismiss callback
        mgr.on_issue_dismissed = orch._on_issue_dismissed

        # Dismiss all issues
        for issue in session.issues:
            mgr.dismiss_issue(session.id, issue.id, "Not needed", "tony")

        await asyncio.sleep(0.05)
        assert session.status == SessionStatus.COMPLETE

        await orch.close()

    @pytest.mark.asyncio
    async def test_backward_compat_auto_start(self):
        """auto_start=true preserves legacy behavior (orchestrator starts immediately)."""
        models = [ModelConfig(id="opus", client_type="claude-code")]
        mgr, session = _make_manager_with_config(models)
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")

        mock_trigger = MockTrigger()
        # Patch _create_trigger to return our mock
        orch._create_trigger = lambda _: mock_trigger

        # Simulate auto_start behavior
        await orch.start(session.id)
        await asyncio.sleep(0.05)

        # Agent should be triggered
        assert len(mock_trigger.sent_prompts) >= 1
        assert session.agent_states["opus"].task_type == AgentTaskType.REVIEW

        await orch.close()


class TestFalsePositiveFlow:
    """Tests for the false_positive → re-review → withdraw/fix_required flow."""

    @pytest.mark.asyncio
    async def test_false_positive_triggers_re_review(self):
        """FALSE_POSITIVE opinion should trigger re-review prompt to original raiser."""
        from ai_review.models import Issue, Opinion, Severity

        models = [
            ModelConfig(id="opus", client_type="claude-code"),
            ModelConfig(id="gpt", client_type="claude-code"),
        ]
        mgr, session = _make_manager_with_config(models)
        session.status = SessionStatus.DELIBERATING
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")

        mock_trigger = MockTrigger()
        orch._triggers[session.id] = {"opus": mock_trigger, "gpt": mock_trigger}
        session.client_sessions = {"opus": "mock-opus", "gpt": "mock-gpt"}
        session.agent_states["opus"] = AgentState(model_id="opus")
        session.agent_states["gpt"] = AgentState(model_id="gpt")

        # Create issue raised by opus
        issue = Issue(
            title="Bug", severity=Severity.HIGH, file="x.py",
            description="d", raised_by="opus",
            thread=[Opinion(model_id="opus", action=OpinionAction.RAISE, reasoning="found bug")],
        )
        session.issues.append(issue)

        # gpt submits false_positive → should trigger re-review for opus
        mgr.on_opinion_submitted = orch._on_opinion_submitted
        mgr.submit_opinion(session.id, issue.id, "gpt", "false_positive", "Not a real issue")

        await asyncio.sleep(0.1)

        # opus should have been set to REVIEWING for the false_positive re-review
        assert session.agent_states["opus"].status in (AgentStatus.REVIEWING, AgentStatus.WAITING)
        # Mock trigger should have received a prompt for opus
        opus_prompts = [p for p in mock_trigger.sent_prompts if p[1] == "opus"]
        assert len(opus_prompts) >= 1
        assert "false positive" in opus_prompts[-1][2].lower()

        await orch.close()

    @pytest.mark.asyncio
    async def test_withdraw_after_false_positive_closes_issue(self):
        """WITHDRAW after FALSE_POSITIVE should close the issue and advance."""
        from ai_review.models import Issue, Opinion, Severity

        models = [
            ModelConfig(id="opus", client_type="claude-code"),
            ModelConfig(id="gpt", client_type="claude-code"),
        ]
        mgr, session = _make_manager_with_config(models)
        session.status = SessionStatus.DELIBERATING
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")

        mock_trigger = MockTrigger()
        orch._triggers[session.id] = {"opus": mock_trigger, "gpt": mock_trigger}
        session.client_sessions = {"opus": "mock-opus", "gpt": "mock-gpt"}
        session.agent_states["opus"] = AgentState(model_id="opus")
        session.agent_states["gpt"] = AgentState(model_id="gpt")

        # Create issue raised by opus; gpt already submitted false_positive
        issue = Issue(
            title="Bug", severity=Severity.HIGH, file="x.py",
            description="d", raised_by="opus",
            thread=[
                Opinion(model_id="opus", action=OpinionAction.RAISE, reasoning="found bug"),
                Opinion(model_id="gpt", action=OpinionAction.FALSE_POSITIVE, reasoning="Not real"),
            ],
        )
        session.issues.append(issue)

        # opus withdraws
        mgr.on_opinion_submitted = orch._on_opinion_submitted
        mgr.submit_opinion(session.id, issue.id, "opus", "withdraw", "Agreed, false alarm")

        await asyncio.sleep(0.1)

        assert issue.consensus is True
        assert issue.consensus_type == "closed"
        assert issue.final_severity == Severity.DISMISSED
        # With only one issue and it's closed, session should complete
        assert session.status == SessionStatus.COMPLETE

        await orch.close()

    @pytest.mark.asyncio
    async def test_fix_required_after_false_positive_continues(self):
        """FIX_REQUIRED response to false_positive continues normal deliberation."""
        from ai_review.models import Issue, Opinion, Severity

        models = [
            ModelConfig(id="opus", client_type="claude-code"),
            ModelConfig(id="gpt", client_type="claude-code"),
            ModelConfig(id="codex", client_type="codex"),
        ]
        mgr, session = _make_manager_with_config(models)
        session.status = SessionStatus.DELIBERATING
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")

        mock_trigger = MockTrigger()
        orch._triggers[session.id] = {"opus": mock_trigger, "gpt": mock_trigger, "codex": mock_trigger}
        session.client_sessions = {"opus": "mock-opus", "gpt": "mock-gpt", "codex": "mock-codex"}
        session.agent_states["opus"] = AgentState(model_id="opus")
        session.agent_states["gpt"] = AgentState(model_id="gpt")
        session.agent_states["codex"] = AgentState(model_id="codex")

        # Issue raised by opus; gpt false_positive'd it; codex hasn't voted yet
        issue = Issue(
            title="Bug", severity=Severity.HIGH, file="x.py",
            description="d", raised_by="opus",
            thread=[
                Opinion(model_id="opus", action=OpinionAction.RAISE, reasoning="found bug"),
                Opinion(model_id="gpt", action=OpinionAction.FALSE_POSITIVE, reasoning="Not real"),
            ],
        )
        session.issues.append(issue)

        # opus rejects the false_positive by insisting fix_required
        # (This goes through normal opinion flow, not the fp-specific flow)
        mgr.on_opinion_submitted = None
        mgr.submit_opinion(session.id, issue.id, "opus", "fix_required", "No, this is real", "high")

        # Issue should NOT be closed — deliberation continues
        assert issue.consensus is not True or issue.consensus_type != "closed"

        await orch.close()


class TestSafeFireAndForget:
    """Tests for _safe_fire_and_forget exception logging."""

    @pytest.mark.asyncio
    async def test_exception_is_logged(self, caplog):
        """An exception in a fire-and-forget coroutine is logged, not silently swallowed."""
        mgr = SessionManager()
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")

        async def boom():
            raise RuntimeError("kaboom")

        with caplog.at_level(logging.ERROR, logger="ai_review.orchestrator"):
            task = orch._safe_fire_and_forget(boom(), name="test-boom")
            await asyncio.sleep(0.05)

        assert any("kaboom" in r.message for r in caplog.records)
        await orch.close()

    @pytest.mark.asyncio
    async def test_successful_task_no_error_logged(self, caplog):
        """A successful fire-and-forget task should not produce error logs."""
        mgr = SessionManager()
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")

        async def ok():
            return 42

        with caplog.at_level(logging.ERROR, logger="ai_review.orchestrator"):
            task = orch._safe_fire_and_forget(ok(), name="test-ok")
            await asyncio.sleep(0.05)

        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert not any("test-ok" in r.message for r in error_records)
        await orch.close()

    @pytest.mark.asyncio
    async def test_cancelled_task_no_error_logged(self, caplog):
        """A cancelled fire-and-forget task should not produce error logs."""
        mgr = SessionManager()
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")

        async def slow():
            await asyncio.sleep(10)

        with caplog.at_level(logging.ERROR, logger="ai_review.orchestrator"):
            task = orch._safe_fire_and_forget(slow(), name="test-cancel")
            task.cancel()
            await asyncio.sleep(0.05)

        assert not any("test-cancel" in r.message for r in caplog.records if r.levelno >= logging.ERROR)
        await orch.close()


class TestActivityCallbackWiring:
    """Tests that _fire_trigger sets and clears on_activity on the trigger."""

    @pytest.mark.asyncio
    async def test_on_activity_set_and_cleared(self):
        """_fire_trigger should set on_activity before send_prompt and clear it after."""
        models = [ModelConfig(id="opus", client_type="claude-code")]
        mgr, session = _make_manager_with_config(models)
        session.agent_states["opus"] = AgentState(model_id="opus", status=AgentStatus.REVIEWING)
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")

        callback_was_set = False

        class SpyTrigger(TriggerEngine):
            async def create_session(self, model_id: str) -> str:
                return "spy-session"

            async def send_prompt(self, client_session_id, model_id, prompt, model_config=None):
                nonlocal callback_was_set
                callback_was_set = self.on_activity is not None
                return TriggerResult(success=True, output="ok", client_session_id=client_session_id)

            async def close(self):
                pass

        trigger = SpyTrigger()
        await orch._fire_trigger(session.id, trigger, "spy-session", "opus", "review")

        assert callback_was_set is True
        assert trigger.on_activity is None  # cleared after

        await orch.close()

    @pytest.mark.asyncio
    async def test_on_activity_cleared_on_exception(self):
        """on_activity should be cleared even when send_prompt raises."""
        models = [ModelConfig(id="opus", client_type="claude-code")]
        mgr, session = _make_manager_with_config(models)
        session.agent_states["opus"] = AgentState(model_id="opus", status=AgentStatus.REVIEWING)
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")
        orch._trigger_retry_delays = []  # No retries

        class ExplodingTrigger(TriggerEngine):
            async def create_session(self, model_id: str) -> str:
                return "exp-session"

            async def send_prompt(self, client_session_id, model_id, prompt, model_config=None):
                raise RuntimeError("boom")

            async def close(self):
                pass

        trigger = ExplodingTrigger()
        await orch._fire_trigger(session.id, trigger, "exp-session", "opus", "review")

        assert trigger.on_activity is None  # cleared despite exception

        await orch.close()

    @pytest.mark.asyncio
    async def test_on_activity_calls_record_activity(self):
        """on_activity callback should call manager.record_activity."""
        models = [ModelConfig(id="opus", client_type="claude-code")]
        mgr, session = _make_manager_with_config(models)
        session.agent_states["opus"] = AgentState(model_id="opus", status=AgentStatus.REVIEWING)
        orch = Orchestrator(mgr, api_base_url="http://localhost:3000")

        recorded = []
        original_record = mgr.record_activity

        def spy_record(sid, mid, action, target):
            recorded.append((sid, mid, action, target))
            return original_record(sid, mid, action, target)

        mgr.record_activity = spy_record

        class CallbackTrigger(TriggerEngine):
            async def create_session(self, model_id: str) -> str:
                return "cb-session"

            async def send_prompt(self, client_session_id, model_id, prompt, model_config=None):
                # Simulate the trigger calling on_activity
                if self.on_activity:
                    self.on_activity("Read", "/src/main.py")
                    self.on_activity("Grep", "grep:TODO")
                return TriggerResult(success=True, output="ok", client_session_id=client_session_id)

            async def close(self):
                pass

        trigger = CallbackTrigger()
        await orch._fire_trigger(session.id, trigger, "cb-session", "opus", "review")

        assert len(recorded) == 2
        assert recorded[0] == (session.id, "opus", "Read", "/src/main.py")
        assert recorded[1] == (session.id, "opus", "Grep", "grep:TODO")

        await orch.close()
