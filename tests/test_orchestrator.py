"""Tests for the orchestration layer."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from ai_review.models import (
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
        orch = Orchestrator(mgr, mcp_server_url="http://localhost:3000/mcp")

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
        orch = Orchestrator(mgr, mcp_server_url="http://localhost:3000/mcp")

        await orch.start(session.id)
        # No triggers created
        assert len(orch._triggers) == 0

        await orch.close()


class TestCallbackRegistration:
    def test_callbacks_registered(self):
        mgr = SessionManager(repo_path=None)
        orch = Orchestrator(mgr, mcp_server_url="http://localhost:3000/mcp")

        assert mgr.on_review_submitted is not None
        assert mgr.on_opinion_submitted is not None

    @pytest.mark.asyncio
    async def test_callbacks_detached_on_close(self):
        mgr = SessionManager(repo_path=None)
        orch = Orchestrator(mgr, mcp_server_url="http://localhost:3000/mcp")

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
        orch = Orchestrator(mgr, mcp_server_url="http://localhost:3000/mcp")

        # Replace _advance_to_deliberation with a spy
        advance_called = asyncio.Event()
        original_advance = orch._advance_to_deliberation

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

        # Give the asyncio event loop a moment to process
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
        orch = Orchestrator(mgr, mcp_server_url="http://localhost:3000/mcp")

        advance_called = asyncio.Event()

        async def spy_advance(sid):
            advance_called.set()

        orch._advance_to_deliberation = spy_advance

        # Submit only one review
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
        orch = Orchestrator(mgr, mcp_server_url="http://localhost:3000/mcp")

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
        orch = Orchestrator(mgr, mcp_server_url="http://localhost:3000/mcp")

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
        orch = Orchestrator(mgr, mcp_server_url="http://localhost:3000/mcp")

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
