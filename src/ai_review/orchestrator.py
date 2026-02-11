"""Orchestration layer — connects session manager, triggers, and consensus loop."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from ai_review.consensus import apply_consensus
from ai_review.dedup import deduplicate_issues
from ai_review.models import AgentState, AgentStatus, AgentTaskType, SessionStatus, _utcnow
from ai_review.prompts import build_deliberation_prompt, build_review_prompt
from ai_review.state import can_transition, transition
from ai_review.trigger.base import TriggerEngine
from ai_review.trigger.cc import ClaudeCodeTrigger
from ai_review.trigger.codex import CodexTrigger
from ai_review.trigger.opencode import OpenCodeTrigger

if TYPE_CHECKING:
    from ai_review.session_manager import SessionManager

logger = logging.getLogger(__name__)


class Orchestrator:
    """Drives the full review lifecycle: trigger → review → dedup → deliberation → complete."""

    def __init__(self, manager: SessionManager, api_base_url: str) -> None:
        self.manager = manager
        self.api_base_url = api_base_url
        self._triggers: dict[str, TriggerEngine] = {}  # model_id -> engine
        self._pending_tasks: list[asyncio.Task] = []

        # Register callbacks on the manager
        manager.on_review_submitted = self._on_review_submitted
        manager.on_opinion_submitted = self._on_opinion_submitted

    # --- Public API ---

    async def start(self, session_id: str) -> None:
        """Initialize triggers for all configured models and fire review prompts."""
        session = self.manager.get_session(session_id)
        config = session.config

        if not config.models:
            logger.info("No models configured — staying in manual mode")
            return

        # Create triggers per model and initialize agent states
        for mc in config.models:
            trigger = self._create_trigger(mc.client_type)
            self._triggers[mc.id] = trigger
            session.agent_states[mc.id] = AgentState(model_id=mc.id)

        # Fire-and-forget review prompts for each model
        for mc in config.models:
            trigger = self._triggers[mc.id]
            client_sid = await trigger.create_session(mc.id)
            session.client_sessions[mc.id] = client_sid

            prompt = build_review_prompt(
                session_id=session_id,
                model_id=mc.id,
                role=mc.role,
                api_base_url=self.api_base_url,
            )

            # Mark agent as reviewing
            session.agent_states[mc.id].status = AgentStatus.REVIEWING
            session.agent_states[mc.id].task_type = AgentTaskType.REVIEW
            session.agent_states[mc.id].prompt_preview = prompt[:200]
            session.agent_states[mc.id].started_at = _utcnow()
            self.manager.broker.publish(
                "agent_status",
                {
                    "session_id": session_id,
                    "model_id": mc.id,
                    "status": "reviewing",
                    "task_type": "review",
                    "prompt_preview": prompt[:200],
                },
            )

            task = asyncio.create_task(
                self._fire_trigger(trigger, client_sid, mc.id, prompt),
                name=f"review-{mc.id}",
            )
            self._pending_tasks.append(task)

        logger.info("Triggered %d models for review", len(config.models))

    async def close(self) -> None:
        """Cancel pending tasks and close all triggers."""
        for task in self._pending_tasks:
            task.cancel()
        self._pending_tasks.clear()

        for trigger in self._triggers.values():
            await trigger.close()
        self._triggers.clear()

        # Detach callbacks
        self.manager.on_review_submitted = None
        self.manager.on_opinion_submitted = None

    # --- Callbacks ---

    def _on_review_submitted(self, session_id: str, model_id: str) -> None:
        """Called after each review submission. Advances to deliberation when all agents are done."""
        session = self.manager.get_session(session_id)

        # Update agent state
        if model_id in session.agent_states:
            session.agent_states[model_id].status = AgentStatus.SUBMITTED
            session.agent_states[model_id].submitted_at = _utcnow()
            self.manager.broker.publish(
                "agent_status",
                {"session_id": session_id, "model_id": model_id, "status": "submitted"},
            )

        self._maybe_advance(session_id)

    def _maybe_advance(self, session_id: str) -> None:
        """Advance to deliberation if all agents are done (submitted or failed) and at least one review exists."""
        session = self.manager.get_session(session_id)
        expected = len(session.config.models)
        if expected == 0:
            return

        finished = sum(
            1 for a in session.agent_states.values()
            if a.status in (AgentStatus.SUBMITTED, AgentStatus.FAILED)
        )
        if finished >= expected and len(session.reviews) > 0:
            logger.info("All %d agents finished (%d reviews) — advancing", expected, len(session.reviews))
            asyncio.ensure_future(self._advance_to_deliberation(session_id))

    def _on_opinion_submitted(self, session_id: str, issue_id: str, model_id: str) -> None:
        """Called after each opinion. Re-checks consensus and triggers next round or finishes."""
        asyncio.ensure_future(self._check_and_advance(session_id))

    # --- Internal flow ---

    async def _advance_to_deliberation(self, session_id: str) -> None:
        """Transition through DEDUP → DELIBERATING and trigger first deliberation round."""
        session = self.manager.get_session(session_id)

        # REVIEWING → DEDUP
        if can_transition(session, SessionStatus.DEDUP):
            transition(session, SessionStatus.DEDUP)
            self.manager.broker.publish(
                "phase_change", {"status": "dedup", "session_id": session_id}
            )

        # Create issues + dedup
        if not session.issues:
            issues = self.manager.create_issues_from_reviews(session_id)
            deduped = deduplicate_issues(issues)
            session.issues = deduped

        apply_consensus(session.issues, session.config.consensus_threshold)

        # Check if already all consensus
        if all(i.consensus for i in session.issues):
            await self._finish(session_id)
            return

        # DEDUP → DELIBERATING
        if can_transition(session, SessionStatus.DELIBERATING):
            transition(session, SessionStatus.DELIBERATING)
            self.manager.broker.publish(
                "phase_change", {"status": "deliberating", "session_id": session_id}
            )

        await self._trigger_deliberation_round(session_id)

    async def _trigger_deliberation_round(self, session_id: str) -> None:
        """Send deliberation prompts to each model for their pending issues."""
        session = self.manager.get_session(session_id)

        for mc in session.config.models:
            pending = self.manager.get_pending_issues(session_id, mc.id)
            if not pending:
                continue

            issue_ids = [p["id"] for p in pending]
            prompt = build_deliberation_prompt(
                session_id=session_id,
                model_id=mc.id,
                issue_ids=issue_ids,
                api_base_url=self.api_base_url,
            )

            # Update agent state for deliberation
            if mc.id in session.agent_states:
                session.agent_states[mc.id].status = AgentStatus.REVIEWING
                session.agent_states[mc.id].task_type = AgentTaskType.DELIBERATION
                session.agent_states[mc.id].started_at = _utcnow()
                session.agent_states[mc.id].submitted_at = None
                session.agent_states[mc.id].prompt_preview = prompt[:200]
                self.manager.broker.publish(
                    "agent_status",
                    {
                        "session_id": session_id,
                        "model_id": mc.id,
                        "status": "reviewing",
                        "task_type": "deliberation",
                        "prompt_preview": prompt[:200],
                    },
                )

            trigger = self._triggers.get(mc.id)
            if not trigger:
                continue

            client_sid = session.client_sessions.get(mc.id)
            if not client_sid:
                client_sid = await trigger.create_session(mc.id)
                session.client_sessions[mc.id] = client_sid

            task = asyncio.create_task(
                self._fire_trigger(trigger, client_sid, mc.id, prompt),
                name=f"deliberate-{mc.id}",
            )
            self._pending_tasks.append(task)

    async def _check_and_advance(self, session_id: str) -> None:
        """Re-apply consensus. If all resolved or max turns, finish. Else next round."""
        session = self.manager.get_session(session_id)
        apply_consensus(session.issues, session.config.consensus_threshold)

        # Determine current turn (max turn across issues)
        max_turn = max((i.turn for i in session.issues), default=0)

        all_consensus = all(i.consensus for i in session.issues)

        if all_consensus or max_turn >= session.config.max_turns:
            await self._finish(session_id)
            return

        # Check if all models have responded in this round
        all_responded = True
        for mc in session.config.models:
            pending = self.manager.get_pending_issues(session_id, mc.id)
            if pending:
                all_responded = False
                break

        if all_responded:
            # Increment turn for all non-consensus issues
            for issue in session.issues:
                if not issue.consensus:
                    issue.turn += 1

            # Self-transition for next deliberation round
            if can_transition(session, SessionStatus.DELIBERATING):
                transition(session, SessionStatus.DELIBERATING)

            apply_consensus(session.issues, session.config.consensus_threshold)

            if all(i.consensus for i in session.issues):
                await self._finish(session_id)
            else:
                await self._trigger_deliberation_round(session_id)

    async def _finish(self, session_id: str) -> None:
        """Transition to COMPLETE and clean up triggers."""
        session = self.manager.get_session(session_id)

        # Ensure final consensus is applied
        apply_consensus(session.issues, session.config.consensus_threshold)

        if can_transition(session, SessionStatus.COMPLETE):
            transition(session, SessionStatus.COMPLETE)
            self.manager.broker.publish(
                "phase_change", {"status": "complete", "session_id": session_id}
            )

        logger.info("Session %s complete", session_id)

    # --- Helpers ---

    def _create_trigger(self, client_type: str) -> TriggerEngine:
        """Factory for trigger engines."""
        if client_type == "opencode":
            return OpenCodeTrigger()
        if client_type == "codex":
            return CodexTrigger()
        return ClaudeCodeTrigger()

    async def _fire_trigger(
        self, trigger: TriggerEngine, client_session_id: str, model_id: str, prompt: str
    ) -> None:
        """Fire a trigger and log the result (fire-and-forget wrapper)."""
        session_id = self._session_id_for_model(model_id)
        try:
            result = await trigger.send_prompt(client_session_id, model_id, prompt)
            if result.success:
                logger.info("Trigger %s succeeded", model_id)
            else:
                logger.warning("Trigger %s failed: %s", model_id, result.error)
                self._mark_agent_failed(session_id, model_id, result.error or "trigger failed")
                return
        except asyncio.CancelledError:
            logger.debug("Trigger %s cancelled", model_id)
            self._mark_agent_failed(session_id, model_id, "cancelled")
            return
        except Exception as exc:
            logger.exception("Trigger %s unexpected error", model_id)
            self._mark_agent_failed(session_id, model_id, str(exc))
            return

        # Trigger completed "successfully" but no review was actually submitted
        # (e.g. sandbox blocked network access). Mark as failed.
        if session_id:
            session = self.manager.get_session(session_id)
            agent = session.agent_states.get(model_id)
            if agent and agent.status == AgentStatus.REVIEWING:
                logger.warning("Trigger %s completed but no review submitted", model_id)
                self._mark_agent_failed(session_id, model_id, "completed without submitting review")

    def _session_id_for_model(self, model_id: str) -> str | None:
        """Find the session ID that contains this model's agent state."""
        for sid, session in self.manager.sessions.items():
            if model_id in session.agent_states:
                return sid
        return None

    def _mark_agent_failed(self, session_id: str | None, model_id: str, reason: str) -> None:
        """Mark an agent as failed and publish the event."""
        if not session_id:
            return
        try:
            session = self.manager.get_session(session_id)
        except KeyError:
            return
        agent = session.agent_states.get(model_id)
        if not agent:
            return
        agent.status = AgentStatus.FAILED
        agent.submitted_at = _utcnow()
        self.manager.broker.publish(
            "agent_status",
            {"session_id": session_id, "model_id": model_id, "status": "failed", "reason": reason},
        )
        self._maybe_advance(session_id)
