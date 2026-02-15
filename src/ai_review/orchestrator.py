"""Orchestration layer — connects session manager, triggers, and consensus loop."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from ai_review.consensus import apply_consensus
from ai_review.dedup import deduplicate_issues
from ai_review.models import AgentState, AgentStatus, AgentTaskType, IssueResponseAction, ModelConfig, OpinionAction, SessionStatus, _utcnow
from ai_review.prompts import build_deliberation_prompt, build_review_prompt, build_verification_prompt
from ai_review.state import can_transition, transition
from ai_review.trigger.base import TriggerEngine
from ai_review.trigger.cc import ClaudeCodeTrigger
from ai_review.trigger.codex import CodexTrigger
from ai_review.trigger.gemini import GeminiTrigger
from ai_review.trigger.opencode import OpenCodeTrigger

if TYPE_CHECKING:
    from ai_review.session_manager import SessionManager

logger = logging.getLogger(__name__)
MAX_RUNTIME_TEXT = 12000


class Orchestrator:
    """Drives the full review lifecycle: trigger → review → dedup → deliberation → complete."""

    def __init__(self, manager: SessionManager, api_base_url: str) -> None:
        self.manager = manager
        self.api_base_url = api_base_url
        self._close_timeout_seconds = 5.0
        self._trigger_retry_delays: list[float] = [1.0, 2.0]
        # Session-scoped: session_id -> model_id -> engine
        self._triggers: dict[str, dict[str, TriggerEngine]] = {}
        # Session-scoped: session_id -> list of tasks
        self._pending_tasks: dict[str, list[asyncio.Task]] = {}

        # Register callbacks on the manager
        manager.on_review_submitted = self._on_review_submitted
        manager.on_opinion_submitted = self._on_opinion_submitted
        manager.on_issue_responded = self._on_issue_responded
        manager.on_fix_completed = self._on_fix_completed

    # --- Public API ---

    async def start(self, session_id: str) -> None:
        """Initialize triggers for all configured models and fire review prompts."""
        session = self.manager.get_session(session_id)
        config = session.config

        if not config.models:
            logger.info("No models configured — staying in manual mode")
            return

        # Create triggers per model and initialize agent states
        enabled_models = [mc for mc in config.models if mc.enabled]
        if not enabled_models:
            logger.info("No enabled models — staying in manual mode")
            return

        session_triggers = self._triggers.setdefault(session_id, {})
        session_tasks = self._pending_tasks.setdefault(session_id, [])

        for mc in enabled_models:
            trigger = self._create_trigger(mc.client_type)
            session_triggers[mc.id] = trigger
            session.agent_states[mc.id] = AgentState(model_id=mc.id)
            self.manager.ensure_agent_access_key(session_id, mc.id)

        # Fire-and-forget review prompts for each model
        for mc in enabled_models:
            trigger = session_triggers[mc.id]
            client_sid = await trigger.create_session(mc.id)
            session.client_sessions[mc.id] = client_sid
            agent_key = self.manager.ensure_agent_access_key(session_id, mc.id)

            ic = session.implementation_context
            ic_dict = ic.model_dump(mode="json") if ic else None
            prompt = build_review_prompt(
                session_id=session_id,
                model_config=mc,
                api_base_url=self.api_base_url,
                agent_key=agent_key,
                implementation_context=ic_dict,
            )

            # Mark agent as reviewing
            session.agent_states[mc.id].status = AgentStatus.REVIEWING
            session.agent_states[mc.id].task_type = AgentTaskType.REVIEW
            session.agent_states[mc.id].prompt_preview = prompt[:200]
            session.agent_states[mc.id].prompt_full = prompt
            session.agent_states[mc.id].started_at = _utcnow()
            session.agent_states[mc.id].submitted_at = None
            self.manager.update_agent_runtime(
                session_id,
                mc.id,
                reason="review trigger started",
                output="",
                error="",
            )
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
                self._fire_trigger(session_id, trigger, client_sid, mc.id, prompt, model_config=mc),
                name=f"review-{session_id[:8]}-{mc.id}",
            )
            session_tasks.append(task)

        logger.info("Triggered %d models for review", len(enabled_models))
        self.manager.persist()

    async def close(self) -> None:
        """Cancel pending tasks and close all triggers."""
        for session in self.manager.sessions.values():
            for agent in session.agent_states.values():
                if agent.status == AgentStatus.REVIEWING:
                    agent.status = AgentStatus.FAILED
                    agent.submitted_at = _utcnow()
                    agent.last_reason = "cancelled: server shutdown"
                    agent.updated_at = _utcnow()

        all_tasks: list[asyncio.Task] = []
        for tasks in self._pending_tasks.values():
            all_tasks.extend(tasks)
        for task in all_tasks:
            task.cancel()
        if all_tasks:
            await asyncio.gather(*all_tasks, return_exceptions=True)
        self._pending_tasks.clear()

        all_triggers: list[TriggerEngine] = []
        for triggers in self._triggers.values():
            all_triggers.extend(triggers.values())
        await self._close_triggers(all_triggers, reason="server shutdown")
        self._triggers.clear()

        # Detach callbacks
        self.manager.on_review_submitted = None
        self.manager.on_opinion_submitted = None
        self.manager.on_issue_responded = None
        self.manager.on_fix_completed = None
        self.manager.persist()

    async def stop_session(self, session_id: str) -> None:
        """Cancel pending tasks and close triggers for a single session."""
        session = self.manager.sessions.get(session_id)
        if session:
            for agent in session.agent_states.values():
                if agent.status == AgentStatus.REVIEWING:
                    agent.status = AgentStatus.FAILED
                    agent.submitted_at = _utcnow()
                    agent.last_reason = "cancelled: session stopped"
                    agent.updated_at = _utcnow()

        tasks = self._pending_tasks.pop(session_id, [])
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        triggers = self._triggers.pop(session_id, {})
        await self._close_triggers(list(triggers.values()), reason=f"stop session {session_id}")

        self.manager.persist()

    async def add_agent(self, session_id: str, model_id: str) -> None:
        """Dynamically add an agent and trigger the current phase task."""
        session = self.manager.get_session(session_id)
        mc = next((m for m in session.config.models if m.id == model_id), None)
        if mc is None:
            raise KeyError(f"Agent config not found: {model_id}")
        if not mc.enabled:
            logger.info("Agent %s is disabled — skipping", model_id)
            return

        session_triggers = self._triggers.setdefault(session_id, {})
        if model_id in session_triggers:
            return

        trigger = self._create_trigger(mc.client_type)
        session_triggers[model_id] = trigger
        session.agent_states[model_id] = AgentState(model_id=model_id)
        agent_key = self.manager.ensure_agent_access_key(session_id, model_id)
        client_sid = await trigger.create_session(model_id)
        session.client_sessions[model_id] = client_sid

        if session.status == SessionStatus.DELIBERATING:
            pending = self.manager.get_pending_issues(session_id, model_id)
            if not pending:
                session.agent_states[model_id].status = AgentStatus.SUBMITTED
                session.agent_states[model_id].submitted_at = _utcnow()
                return
            issue_ids = [p["id"] for p in pending]
            round_turn = max(int(p.get("turn", 0)) for p in pending)
            prompt = build_deliberation_prompt(
                session_id=session_id,
                model_config=mc,
                issue_ids=issue_ids,
                api_base_url=self.api_base_url,
                turn=round_turn,
                agent_key=agent_key,
            )
            session.agent_states[model_id].task_type = AgentTaskType.DELIBERATION
        else:
            ic = session.implementation_context
            ic_dict = ic.model_dump(mode="json") if ic else None
            prompt = build_review_prompt(
                session_id=session_id,
                model_config=mc,
                api_base_url=self.api_base_url,
                agent_key=agent_key,
                implementation_context=ic_dict,
            )
            session.agent_states[model_id].task_type = AgentTaskType.REVIEW

        session.agent_states[model_id].status = AgentStatus.REVIEWING
        session.agent_states[model_id].prompt_preview = prompt[:200]
        session.agent_states[model_id].prompt_full = prompt
        session.agent_states[model_id].started_at = _utcnow()
        session.agent_states[model_id].submitted_at = None
        self.manager.update_agent_runtime(
            session_id,
            model_id,
            reason=f"{session.agent_states[model_id].task_type.value} trigger started",
            output="",
            error="",
        )
        self.manager.broker.publish(
            "agent_status",
            {
                "session_id": session_id,
                "model_id": model_id,
                "status": "reviewing",
                "task_type": session.agent_states[model_id].task_type.value,
                "prompt_preview": prompt[:200],
            },
        )
        session_tasks = self._pending_tasks.setdefault(session_id, [])
        task = asyncio.create_task(
            self._fire_trigger(session_id, trigger, client_sid, model_id, prompt, model_config=mc),
            name=f"dynamic-{session_id[:8]}-{model_id}",
        )
        session_tasks.append(task)
        self.manager.persist()

    async def remove_agent(self, session_id: str, model_id: str) -> None:
        """Dynamically remove an agent from orchestration."""
        session_triggers = self._triggers.get(session_id, {})
        trigger = session_triggers.pop(model_id, None)
        if trigger is not None:
            await self._close_triggers([trigger], reason=f"remove agent {model_id}")
        self.manager.get_session(session_id).agent_states.pop(model_id, None)
        self.manager.get_session(session_id).client_sessions.pop(model_id, None)
        self._maybe_advance(session_id)
        self.manager.persist()

    async def chat_with_agent(self, session_id: str, model_id: str, message: str) -> str:
        """Directly send a user message to a specific agent."""
        session = self.manager.get_session(session_id)
        mc = next((m for m in session.config.models if m.id == model_id), None)
        if mc is None:
            raise KeyError(f"Agent not configured: {model_id}")

        session_triggers = self._triggers.setdefault(session_id, {})
        trigger = session_triggers.get(model_id)
        if trigger is None:
            trigger = self._create_trigger(mc.client_type)
            session_triggers[model_id] = trigger

        client_sid = session.client_sessions.get(model_id)
        if not client_sid:
            client_sid = await trigger.create_session(model_id)
            session.client_sessions[model_id] = client_sid

        result = await trigger.send_prompt(client_sid, model_id, message)
        if not result.success:
            raise ValueError(result.error or "agent chat failed")
        return result.output or "(empty response)"

    # --- Callbacks ---

    def _on_review_submitted(self, session_id: str, model_id: str) -> None:
        """Called after each review submission. Advances to deliberation when all agents are done."""
        session = self.manager.get_session(session_id)

        # Update agent state
        if model_id in session.agent_states:
            session.agent_states[model_id].status = AgentStatus.SUBMITTED
            session.agent_states[model_id].submitted_at = _utcnow()
            self.manager.update_agent_runtime(session_id, model_id, reason="review submitted")
            self.manager.broker.publish(
                "agent_status",
                {"session_id": session_id, "model_id": model_id, "status": "submitted"},
            )

        self._maybe_advance(session_id)
        self.manager.persist()

    def _maybe_advance(self, session_id: str) -> None:
        """Advance to deliberation if all agents are done (submitted or failed) and at least one review exists."""
        session = self.manager.get_session(session_id)
        enabled_ids = {m.id for m in session.config.models if m.enabled}
        expected = len(enabled_ids)
        if expected == 0:
            return

        finished = sum(
            1 for mid, a in session.agent_states.items()
            if mid in enabled_ids and a.status in (AgentStatus.SUBMITTED, AgentStatus.FAILED)
        )
        if finished >= expected and len(session.reviews) > 0:
            logger.info("All %d agents finished (%d reviews) — advancing", expected, len(session.reviews))
            asyncio.ensure_future(self._advance_to_deliberation(session_id))

    def _on_opinion_submitted(self, session_id: str, issue_id: str, model_id: str) -> None:
        """Called after each opinion. Re-checks consensus and triggers next round or finishes."""
        human_like = {"human", "human-assist"}
        if model_id not in human_like:
            self.manager.update_agent_runtime(session_id, model_id, reason=f"opinion submitted for {issue_id}")

        session = self.manager.get_session(session_id)
        if session.status == SessionStatus.VERIFYING:
            asyncio.ensure_future(self._check_verification_complete(session_id))
            return

        if model_id in human_like:
            # Human comment opens a new turn on the issue. Re-trigger pending agents immediately.
            asyncio.ensure_future(self._trigger_deliberation_round(session_id))
        asyncio.ensure_future(self._check_and_advance(session_id))

    def _on_issue_responded(self, session_id: str, issue_id: str, action: str) -> None:
        """Called after a coding agent submits a response to an issue."""
        if action == IssueResponseAction.DISPUTE.value:
            asyncio.ensure_future(self._handle_dispute_redeliberation(session_id))
        else:
            asyncio.ensure_future(self._check_all_responses_complete(session_id))

    async def _handle_dispute_redeliberation(self, session_id: str) -> None:
        """Handle dispute: clear responses and re-enter deliberation."""
        session = self.manager.get_session(session_id)
        session.issue_responses = []
        if can_transition(session, SessionStatus.DELIBERATING):
            transition(session, SessionStatus.DELIBERATING)
            self.manager.broker.publish(
                "phase_change", {"status": "deliberating", "session_id": session_id}
            )
        self.manager.persist()
        await self._trigger_deliberation_round(session_id)

    async def _check_all_responses_complete(self, session_id: str) -> None:
        """Check if all confirmed issues have been responded to. Transition to FIXING or finish."""
        status = self.manager.get_issue_response_status(session_id)
        if not status["all_responded"]:
            return
        session = self.manager.get_session(session_id)
        has_dispute = any(
            r.action == IssueResponseAction.DISPUTE
            for r in session.issue_responses
        )
        if has_dispute:
            return  # dispute triggers re-deliberation separately

        # All accepted/partial → transition to FIXING
        if can_transition(session, SessionStatus.FIXING):
            transition(session, SessionStatus.FIXING)
            self.manager.broker.publish(
                "phase_change", {"status": "fixing", "session_id": session_id}
            )
            self.manager.persist()
            return

        # Fallback: AGENT_RESPONSE → COMPLETE (backward compat)
        await self._finish(session_id)

    def _on_fix_completed(self, session_id: str) -> None:
        """Called after a fix commit is submitted. Starts verification."""
        asyncio.ensure_future(self._start_verification(session_id))

    async def _start_verification(self, session_id: str) -> None:
        """Send verification prompts to all enabled models for delta review."""
        session = self.manager.get_session(session_id)
        if session.status != SessionStatus.VERIFYING:
            return

        # Increment issue turns so verification opinions have a distinct turn
        for issue in session.issues:
            if issue.consensus_type == "fix_required":
                issue.turn += 1

        for mc in (m for m in session.config.models if m.enabled):
            agent_key = self.manager.ensure_agent_access_key(session_id, mc.id)
            prompt = build_verification_prompt(
                session_id=session_id,
                model_config=mc,
                api_base_url=self.api_base_url,
                verification_round=session.verification_round,
                agent_key=agent_key,
            )

            # Update agent state for verification
            if mc.id in session.agent_states:
                session.agent_states[mc.id].status = AgentStatus.REVIEWING
                session.agent_states[mc.id].task_type = AgentTaskType.VERIFICATION
                session.agent_states[mc.id].started_at = _utcnow()
                session.agent_states[mc.id].submitted_at = None
                session.agent_states[mc.id].prompt_preview = prompt[:200]
                session.agent_states[mc.id].prompt_full = prompt
                self.manager.update_agent_runtime(
                    session_id, mc.id,
                    reason="verification trigger started",
                    output="", error="",
                )
                self.manager.broker.publish("agent_status", {
                    "session_id": session_id,
                    "model_id": mc.id,
                    "status": "reviewing",
                    "task_type": "verification",
                    "prompt_preview": prompt[:200],
                })

            session_triggers = self._triggers.get(session_id, {})
            trigger = session_triggers.get(mc.id)
            if not trigger:
                continue

            client_sid = session.client_sessions.get(mc.id)
            if not client_sid:
                client_sid = await trigger.create_session(mc.id)
                session.client_sessions[mc.id] = client_sid

            session_tasks = self._pending_tasks.setdefault(session_id, [])
            task = asyncio.create_task(
                self._fire_trigger(
                    session_id, trigger, client_sid, mc.id, prompt,
                    model_config=mc,
                ),
                name=f"verify-{session_id[:8]}-{mc.id}",
            )
            session_tasks.append(task)

        self.manager.persist()

    async def _check_verification_complete(self, session_id: str) -> None:
        """Check if all verification opinions are in. Decide next state."""
        session = self.manager.get_session(session_id)
        if session.status != SessionStatus.VERIFYING:
            return

        # Check if all verification agents have finished (not REVIEWING)
        enabled_ids = {m.id for m in session.config.models if m.enabled}
        for mid in enabled_ids:
            agent = session.agent_states.get(mid)
            if agent and agent.task_type == AgentTaskType.VERIFICATION:
                if agent.status == AgentStatus.REVIEWING:
                    return  # Still working

        # All agents done. Check if original issues are resolved.
        confirmed_issues = [
            i for i in session.issues if i.consensus_type == "fix_required"
        ]
        all_resolved = True
        for issue in confirmed_issues:
            verification_opinions = [
                op for op in issue.thread
                if op.turn == issue.turn and op.action != OpinionAction.RAISE
            ]
            if any(op.action == OpinionAction.FIX_REQUIRED for op in verification_opinions):
                all_resolved = False
                break

        if all_resolved:
            await self._finish(session_id)
        elif session.verification_round >= session.config.max_verification_rounds:
            logger.info(
                "Max verification rounds (%d) reached — forcing complete",
                session.config.max_verification_rounds,
            )
            await self._finish(session_id)
        else:
            # Some issues not fixed → back to FIXING for another attempt
            if can_transition(session, SessionStatus.FIXING):
                transition(session, SessionStatus.FIXING)
                self.manager.broker.publish(
                    "phase_change", {"status": "fixing", "session_id": session_id}
                )
            self.manager.persist()

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

        self.manager.persist()
        await self._trigger_deliberation_round(session_id)

    async def _trigger_deliberation_round(self, session_id: str) -> None:
        """Send deliberation prompts to each model for their pending issues."""
        session = self.manager.get_session(session_id)

        for mc in (m for m in session.config.models if m.enabled):
            agent = session.agent_states.get(mc.id)
            # Skip if deliberation is already running or was just triggered (WAITING after deliberation)
            if agent and agent.task_type == AgentTaskType.DELIBERATION:
                if agent.status in (AgentStatus.REVIEWING, AgentStatus.WAITING):
                    continue
            pending = self.manager.get_pending_issues(session_id, mc.id)
            if not pending:
                continue

            issue_ids = [p["id"] for p in pending]
            round_turn = max(int(p.get("turn", 0)) for p in pending)
            agent_key = self.manager.ensure_agent_access_key(session_id, mc.id)
            prompt = build_deliberation_prompt(
                session_id=session_id,
                model_config=mc,
                issue_ids=issue_ids,
                api_base_url=self.api_base_url,
                turn=round_turn,
                agent_key=agent_key,
            )

            # Update agent state for deliberation
            if mc.id in session.agent_states:
                session.agent_states[mc.id].status = AgentStatus.REVIEWING
                session.agent_states[mc.id].task_type = AgentTaskType.DELIBERATION
                session.agent_states[mc.id].started_at = _utcnow()
                session.agent_states[mc.id].submitted_at = None
                session.agent_states[mc.id].prompt_preview = prompt[:200]
                session.agent_states[mc.id].prompt_full = prompt
                self.manager.update_agent_runtime(
                    session_id,
                    mc.id,
                    reason="deliberation trigger started",
                    output="",
                    error="",
                )
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

            session_triggers = self._triggers.get(session_id, {})
            trigger = session_triggers.get(mc.id)
            if not trigger:
                continue

            client_sid = session.client_sessions.get(mc.id)
            if not client_sid:
                client_sid = await trigger.create_session(mc.id)
                session.client_sessions[mc.id] = client_sid

            session_tasks = self._pending_tasks.setdefault(session_id, [])
            task = asyncio.create_task(
                self._fire_trigger(session_id, trigger, client_sid, mc.id, prompt, model_config=mc),
                name=f"deliberate-{session_id[:8]}-{mc.id}",
            )
            session_tasks.append(task)
        self.manager.persist()

    async def _check_and_advance(self, session_id: str) -> None:
        """Re-apply consensus. If all resolved or max turns, finish. Else next round."""
        session = self.manager.get_session(session_id)
        apply_consensus(session.issues, session.config.consensus_threshold)

        # Determine current turn (max turn across issues)
        max_turn = max((i.turn for i in session.issues), default=0)

        all_consensus = all(i.consensus for i in session.issues)

        if all_consensus or max_turn >= session.config.max_turns:
            await self._try_agent_response_or_finish(session_id)
            return

        # Check if all models have responded in this round
        all_responded = True
        for mc in (m for m in session.config.models if m.enabled):
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
                await self._try_agent_response_or_finish(session_id)
            else:
                await self._trigger_deliberation_round(session_id)
            return

        # Not all responded yet. Ensure non-busy pending agents are triggered.
        await self._trigger_deliberation_round(session_id)
        self.manager.persist()

    async def _try_agent_response_or_finish(self, session_id: str) -> None:
        """Transition to AGENT_RESPONSE if there are confirmed issues, otherwise finish."""
        session = self.manager.get_session(session_id)
        has_confirmed = any(i.consensus_type == "fix_required" for i in session.issues)
        if has_confirmed and can_transition(session, SessionStatus.AGENT_RESPONSE):
            transition(session, SessionStatus.AGENT_RESPONSE)
            self.manager.broker.publish(
                "phase_change", {"status": "agent_response", "session_id": session_id}
            )
            self.manager.persist()
            return
        await self._finish(session_id)

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
        self.manager.persist()

    # --- Helpers ---

    def _create_trigger(self, client_type: str) -> TriggerEngine:
        """Factory for trigger engines."""
        if client_type == "opencode":
            return OpenCodeTrigger()
        if client_type == "codex":
            return CodexTrigger()
        if client_type == "gemini":
            return GeminiTrigger()
        return ClaudeCodeTrigger()

    async def _close_triggers(self, triggers: list[TriggerEngine], *, reason: str) -> None:
        if not triggers:
            return

        async def _safe_close(trigger: TriggerEngine) -> None:
            try:
                await asyncio.wait_for(
                    trigger.close(),
                    timeout=self._close_timeout_seconds,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "Trigger close timed out after %.1fs during %s",
                    self._close_timeout_seconds,
                    reason,
                )
            except Exception:
                logger.exception("Trigger close failed during %s", reason)

        await asyncio.gather(*(_safe_close(trigger) for trigger in triggers))

    async def _fire_trigger(
        self, session_id: str, trigger: TriggerEngine, client_session_id: str, model_id: str, prompt: str,
        *, model_config: "ModelConfig | None" = None,
    ) -> None:
        """Fire a trigger and log the result (fire-and-forget wrapper).

        On transient ``Exception``, retries up to ``len(_trigger_retry_delays)``
        times with exponential back-off.  ``CancelledError`` and
        ``TriggerResult(success=False)`` are **not** retried.
        """
        attempts = [0.0, *self._trigger_retry_delays]  # first attempt + retries
        last_exc: Exception | None = None

        for attempt_idx, delay in enumerate(attempts):
            if delay:
                await asyncio.sleep(delay)

            try:
                result = await trigger.send_prompt(client_session_id, model_id, prompt, model_config=model_config)
            except asyncio.CancelledError:
                logger.debug("Trigger %s cancelled", model_id)
                self._mark_agent_failed(session_id, model_id, "cancelled")
                return
            except Exception as exc:
                last_exc = exc
                remaining = len(attempts) - attempt_idx - 1
                if remaining > 0:
                    logger.warning("Trigger %s attempt %d failed (%s), %d retries left", model_id, attempt_idx + 1, exc, remaining)
                    continue
                logger.exception("Trigger %s unexpected error (attempts exhausted)", model_id)
                self._mark_agent_failed(session_id, model_id, str(exc))
                return

            # send_prompt returned a TriggerResult
            if session_id:
                self.manager.update_agent_runtime(
                    session_id,
                    model_id,
                    output=self._clip_runtime_text(result.output),
                    error=self._clip_runtime_text(result.error),
                )
            if result.success:
                logger.info("Trigger %s succeeded", model_id)
                if session_id:
                    self.manager.update_agent_runtime(session_id, model_id, reason="trigger completed")
            else:
                logger.warning("Trigger %s failed: %s", model_id, result.error)
                self._mark_agent_failed(session_id, model_id, result.error or "trigger failed")
                return
            break  # success — exit retry loop

        # Trigger completed "successfully" but no review was actually submitted
        # (e.g. sandbox blocked network access). Mark as failed.
        if session_id:
            session = self.manager.get_session(session_id)
            agent = session.agent_states.get(model_id)
            if agent and agent.status == AgentStatus.REVIEWING:
                if agent.task_type in (AgentTaskType.DELIBERATION, AgentTaskType.VERIFICATION):
                    logger.info("Trigger %s completed without opinion — set waiting", model_id)
                    self._mark_agent_waiting(session_id, model_id, f"{agent.task_type.value} pending")
                    if agent.task_type == AgentTaskType.VERIFICATION:
                        session = self.manager.get_session(session_id)
                        if session.status == SessionStatus.VERIFYING:
                            asyncio.ensure_future(self._check_verification_complete(session_id))
                else:
                    logger.warning("Trigger %s completed but no review submitted", model_id)
                    self._mark_agent_failed(session_id, model_id, "completed without submitting review")

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
        self.manager.update_agent_runtime(session_id, model_id, reason=reason)
        self.manager.broker.publish(
            "agent_status",
            {"session_id": session_id, "model_id": model_id, "status": "failed", "reason": reason},
        )
        self._maybe_advance(session_id)
        self.manager.persist()

    def _mark_agent_waiting(self, session_id: str | None, model_id: str, reason: str) -> None:
        """Mark an agent as waiting (non-fatal) and publish the event."""
        if not session_id:
            return
        try:
            session = self.manager.get_session(session_id)
        except KeyError:
            return
        agent = session.agent_states.get(model_id)
        if not agent:
            return
        agent.status = AgentStatus.WAITING
        if agent.submitted_at is None:
            agent.submitted_at = _utcnow()
        self.manager.update_agent_runtime(session_id, model_id, reason=reason)
        self.manager.broker.publish(
            "agent_status",
            {"session_id": session_id, "model_id": model_id, "status": "waiting", "reason": reason},
        )
        self.manager.persist()

    @staticmethod
    def _clip_runtime_text(value: str) -> str:
        text = (value or "").strip()
        if len(text) <= MAX_RUNTIME_TEXT:
            return text
        omitted = len(text) - MAX_RUNTIME_TEXT
        return text[:MAX_RUNTIME_TEXT] + f"\n\n... ({omitted} chars omitted)"
