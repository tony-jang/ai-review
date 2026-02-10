"""Orchestration layer — connects session manager, triggers, and consensus loop."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from ai_review.consensus import apply_consensus
from ai_review.dedup import deduplicate_issues
from ai_review.models import SessionStatus
from ai_review.prompts import build_deliberation_prompt, build_review_prompt
from ai_review.state import can_transition, transition
from ai_review.trigger.base import TriggerEngine
from ai_review.trigger.cc import ClaudeCodeTrigger
from ai_review.trigger.opencode import OpenCodeTrigger

if TYPE_CHECKING:
    from ai_review.session_manager import SessionManager

logger = logging.getLogger(__name__)


class Orchestrator:
    """Drives the full review lifecycle: trigger → review → dedup → deliberation → complete."""

    def __init__(self, manager: SessionManager, mcp_server_url: str) -> None:
        self.manager = manager
        self.mcp_server_url = mcp_server_url
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

        # Create triggers per model
        for mc in config.models:
            trigger = self._create_trigger(mc.client_type)
            self._triggers[mc.id] = trigger

        # Fire-and-forget review prompts for each model
        for mc in config.models:
            trigger = self._triggers[mc.id]
            client_sid = await trigger.create_session(mc.id)
            session.client_sessions[mc.id] = client_sid

            prompt = build_review_prompt(
                session_id=session_id,
                model_id=mc.id,
                role=mc.role,
                mcp_server_url=self.mcp_server_url,
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
        """Called after each review submission. Advances to deliberation when all reviews are in."""
        session = self.manager.get_session(session_id)
        expected = len(session.config.models)

        if expected == 0:
            return

        if len(session.reviews) >= expected:
            logger.info("All %d reviews received — advancing to deliberation", expected)
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
                mcp_server_url=self.mcp_server_url,
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
        return ClaudeCodeTrigger(mcp_server_url=self.mcp_server_url)

    async def _fire_trigger(
        self, trigger: TriggerEngine, client_session_id: str, model_id: str, prompt: str
    ) -> None:
        """Fire a trigger and log the result (fire-and-forget wrapper)."""
        try:
            result = await trigger.send_prompt(client_session_id, model_id, prompt)
            if result.success:
                logger.info("Trigger %s succeeded", model_id)
            else:
                logger.warning("Trigger %s failed: %s", model_id, result.error)
        except asyncio.CancelledError:
            logger.debug("Trigger %s cancelled", model_id)
        except Exception:
            logger.exception("Trigger %s unexpected error", model_id)
