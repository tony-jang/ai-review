"""Session management: CRUD, review/opinion submission, event publishing."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ai_review.git_diff import collect_diff, get_current_branch, get_diff_summary, parse_diff
from ai_review.knowledge import load_config, load_knowledge
from ai_review.models import (
    AgentChatMessage,
    AgentState,
    AgentStatus,
    AgentTaskType,
    DiffFile,
    Issue,
    Knowledge,
    ModelConfig,
    Opinion,
    OpinionAction,
    RawIssue,
    Review,
    ReviewSession,
    SessionStatus,
    Severity,
    _utcnow,
)
from ai_review.sse import SSEBroker
from ai_review.state import InvalidTransitionError, transition

logger = logging.getLogger(__name__)


class SessionManager:
    """Manages review sessions and orchestrates state transitions."""

    def __init__(self, repo_path: str | None = None) -> None:
        self.repo_path = repo_path
        self.sessions: dict[str, ReviewSession] = {}
        self.broker = SSEBroker()
        self._current_session_id: str | None = None
        self._state_file = self._resolve_state_file(repo_path)

        # Optional callbacks — set by Orchestrator to drive automation.
        # When None the manager behaves as before (manual mode).
        self.on_review_submitted: Callable[[str, str], Any] | None = None  # (session_id, model_id)
        self.on_opinion_submitted: Callable[[str, str, str], Any] | None = None  # (session_id, issue_id, model_id)
        self._load_state()

    @property
    def current_session(self) -> ReviewSession | None:
        if self._current_session_id:
            return self.sessions.get(self._current_session_id)
        return None

    def get_session(self, session_id: str) -> ReviewSession:
        session = self.sessions.get(session_id)
        if not session:
            raise KeyError(f"Session not found: {session_id}")
        return session

    def list_sessions(self) -> list[dict]:
        """Return summary list of all sessions, sorted newest first."""
        return [
            {
                "session_id": s.id,
                "status": s.status.value,
                "base": s.base,
                "head": s.head,
                "review_count": len(s.reviews),
                "issue_count": len(s.issues),
                "files_changed": len(s.diff),
                "created_at": s.created_at.isoformat(),
            }
            for s in sorted(self.sessions.values(), key=lambda x: x.created_at, reverse=True)
        ]

    def delete_session(self, session_id: str) -> None:
        """Delete a session. Raises KeyError if not found."""
        if session_id not in self.sessions:
            raise KeyError(f"Session not found: {session_id}")
        del self.sessions[session_id]
        if self._current_session_id == session_id:
            self._current_session_id = None
        self.persist()

    def set_current_session(self, session_id: str) -> None:
        """Set the active session. Raises KeyError if not found."""
        if session_id not in self.sessions:
            raise KeyError(f"Session not found: {session_id}")
        self._current_session_id = session_id

    @staticmethod
    def _resolve_state_file(repo_path: str | None) -> Path:
        root = Path(repo_path) if repo_path else Path.cwd()
        return root / ".ai-review" / "runtime" / "sessions.json"

    def _load_state(self) -> None:
        if not self._state_file.exists():
            return
        try:
            raw = json.loads(self._state_file.read_text(encoding="utf-8"))
            sessions = raw.get("sessions", [])
            loaded: dict[str, ReviewSession] = {}
            for item in sessions:
                session = ReviewSession.model_validate(item)
                for agent in session.agent_states.values():
                    if agent.status == AgentStatus.REVIEWING:
                        agent.status = AgentStatus.FAILED
                        agent.last_reason = "interrupted: server restarted"
                        agent.updated_at = _utcnow()
                loaded[session.id] = session
            self.sessions = loaded
            current = raw.get("current_session_id")
            self._current_session_id = current if current in loaded else None
        except Exception:
            logger.exception("Failed to load persisted session state from %s", self._state_file)

    def persist(self) -> None:
        """Persist all sessions to disk for restart recovery."""
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "current_session_id": self._current_session_id,
                "sessions": [s.model_dump(mode="json") for s in self.sessions.values()],
            }
            temp = self._state_file.with_suffix(".tmp")
            temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            temp.replace(self._state_file)
        except Exception:
            logger.exception("Failed to persist session state to %s", self._state_file)

    async def start_review(self, base: str = "main") -> dict:
        """Start a new review session: collect diff and knowledge."""
        # Load config
        config = load_config(self.repo_path) if self.repo_path else None

        session = ReviewSession(base=base)
        if config:
            session.config = config

        self.sessions[session.id] = session
        self._current_session_id = session.id

        # Transition to COLLECTING
        transition(session, SessionStatus.COLLECTING)
        self.broker.publish("phase_change", {"status": "collecting", "session_id": session.id})

        # Collect diff
        if self.repo_path:
            session.head = await get_current_branch(self.repo_path)
            session.diff = await collect_diff(base, self.repo_path)
            session.knowledge = load_knowledge(self.repo_path)

        # Transition to REVIEWING
        transition(session, SessionStatus.REVIEWING)
        self.broker.publish("phase_change", {"status": "reviewing", "session_id": session.id})

        summary = get_diff_summary(session.diff)
        summary["session_id"] = session.id
        summary["head"] = session.head
        self.persist()
        return summary

    def get_review_context(self, session_id: str, file: str | None = None) -> dict:
        """Return diff + knowledge for the session."""
        session = self.get_session(session_id)

        if file:
            diff_content = "\n".join(
                f.content for f in session.diff if f.path == file and f.content
            )
        else:
            diff_content = "\n".join(f.content for f in session.diff if f.content)

        return {
            "diff": diff_content,
            "knowledge": session.knowledge.model_dump(mode="json"),
            "files": [f.path for f in session.diff],
        }

    def get_context_index(self, session_id: str) -> dict:
        """Return a lightweight index for targeted context exploration."""
        session = self.get_session(session_id)

        files = []
        for f in session.diff:
            files.append({
                "path": f.path,
                "status": self._infer_file_status(f.content),
                "additions": f.additions,
                "deletions": f.deletions,
                "hunks": self._extract_hunks(f.content),
            })

        return {
            "session_id": session.id,
            "base": session.base,
            "head": session.head,
            "files": files,
            "suggested_commands": [
                f"git diff {session.base}...HEAD -- <path>",
                "sed -n '<start>,<end>p' <path>",
                "rg '<symbol-or-keyword>' <path>",
            ],
        }

    @staticmethod
    def _infer_file_status(diff_content: str) -> str:
        """Infer file status from unified diff headers."""
        if not diff_content:
            return "unknown"
        if "new file mode" in diff_content:
            return "added"
        if "deleted file mode" in diff_content:
            return "deleted"
        if "rename from " in diff_content and "rename to " in diff_content:
            return "renamed"
        return "modified"

    @staticmethod
    def _extract_hunks(diff_content: str) -> list[dict[str, int]]:
        """Extract unified diff hunk ranges for quick navigation."""
        if not diff_content:
            return []
        hunks = []
        for old_start, old_lines, new_start, new_lines in re.findall(
            r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@",
            diff_content,
            flags=re.MULTILINE,
        ):
            hunks.append({
                "old_start": int(old_start),
                "old_lines": int(old_lines or 1),
                "new_start": int(new_start),
                "new_lines": int(new_lines or 1),
            })
        return hunks

    def submit_review(
        self, session_id: str, model_id: str, issues: list[dict], summary: str = ""
    ) -> dict:
        """Submit a review with issues."""
        session = self.get_session(session_id)

        if session.status != SessionStatus.REVIEWING:
            raise ValueError(f"Cannot submit review in {session.status.value} state")

        raw_issues = [RawIssue(**i) for i in issues]
        review = Review(model_id=model_id, issues=raw_issues, summary=summary)
        session.reviews.append(review)

        self.broker.publish(
            "review_submitted",
            {
                "session_id": session_id,
                "model_id": model_id,
                "issue_count": len(raw_issues),
            },
        )

        result = {
            "status": "accepted",
            "review_count": len(session.reviews),
            "issue_count": len(raw_issues),
        }

        if self.on_review_submitted is not None:
            self.on_review_submitted(session_id, model_id)

        self.persist()
        return result

    def get_all_reviews(self, session_id: str) -> list[dict]:
        """Get all submitted reviews."""
        session = self.get_session(session_id)
        return [r.model_dump(mode="json") for r in session.reviews]

    def create_issues_from_reviews(self, session_id: str) -> list[Issue]:
        """Create Issue objects from all submitted RawIssues (pre-dedup)."""
        session = self.get_session(session_id)

        issues: list[Issue] = []
        for review in session.reviews:
            for raw in review.issues:
                issue = Issue(
                    title=raw.title,
                    severity=raw.severity,
                    file=raw.file,
                    line=raw.line,
                    description=raw.description,
                    suggestion=raw.suggestion,
                    raised_by=review.model_id,
                    thread=[
                        Opinion(
                            model_id=review.model_id,
                            action=OpinionAction.RAISE,
                            reasoning=raw.description,
                            suggested_severity=raw.severity,
                            turn=0,
                        )
                    ],
                )
                issues.append(issue)

        session.issues = issues
        self.persist()
        return issues

    def get_issues(self, session_id: str) -> list[dict]:
        """Get all issues for a session."""
        session = self.get_session(session_id)
        return [i.model_dump(mode="json") for i in session.issues]

    def get_issue_thread(self, session_id: str, issue_id: str) -> dict:
        """Get a specific issue with its thread."""
        session = self.get_session(session_id)
        for issue in session.issues:
            if issue.id == issue_id:
                return issue.model_dump(mode="json")
        raise KeyError(f"Issue not found: {issue_id}")

    def submit_opinion(
        self,
        session_id: str,
        issue_id: str,
        model_id: str,
        action: str,
        reasoning: str,
        suggested_severity: str | None = None,
        mentions: list[str] | None = None,
    ) -> dict:
        """Submit an opinion on an issue."""
        session = self.get_session(session_id)
        human_like_models = {"human", "human-assist"}
        is_human_like = model_id in human_like_models

        is_human_reopen = is_human_like and session.status == SessionStatus.COMPLETE
        if session.status not in (SessionStatus.DELIBERATING, SessionStatus.REVIEWING) and not is_human_reopen:
            raise ValueError(f"Cannot submit opinion in {session.status.value} state")

        for issue in session.issues:
            if issue.id == issue_id:
                # Human 의견은 새 턴을 열고 이슈를 재오픈해 모든 에이전트가 다시 검토하도록 유도
                if is_human_like:
                    issue.turn += 1
                    issue.consensus = False
                    issue.final_severity = None
                target_turn = issue.turn

                # Reject duplicate opinion from same model in same turn
                if not is_human_like and any(
                    op.model_id == model_id and op.turn == target_turn
                    for op in issue.thread
                ):
                    return {"status": "duplicate", "thread_length": len(issue.thread), "turn": target_turn}

                sev = Severity(suggested_severity) if suggested_severity else None
                opinion = Opinion(
                    model_id=model_id,
                    action=OpinionAction(action),
                    reasoning=reasoning,
                    suggested_severity=sev,
                    turn=target_turn,
                    mentions=sorted(set((mentions or []) + self._extract_mentions(reasoning, session))),
                )
                issue.thread.append(opinion)

                self.broker.publish(
                    "opinion_submitted",
                    {
                        "session_id": session_id,
                        "issue_id": issue_id,
                        "model_id": model_id,
                        "action": action,
                        "turn": target_turn,
                    },
                )

                result = {"status": "accepted", "thread_length": len(issue.thread), "turn": target_turn}

                if is_human_reopen:
                    session.status = SessionStatus.DELIBERATING
                    self.broker.publish(
                        "phase_change",
                        {"status": "deliberating", "session_id": session_id},
                    )

                if self.on_opinion_submitted is not None:
                    self.on_opinion_submitted(session_id, issue_id, model_id)

                self.persist()
                return result

        raise KeyError(f"Issue not found: {issue_id}")

    def get_pending_issues(self, session_id: str, model_id: str) -> list[dict]:
        """Get issues where the model hasn't responded for the current issue turn."""
        session = self.get_session(session_id)
        pending = []
        for issue in session.issues:
            if issue.consensus:
                continue
            latest_model_turn = max(
                (op.turn for op in issue.thread if op.model_id == model_id),
                default=-1,
            )
            if latest_model_turn < issue.turn:
                pending.append(issue.model_dump(mode="json"))
        return pending

    def get_session_status(self, session_id: str) -> dict:
        """Get current session status."""
        session = self.get_session(session_id)
        return {
            "session_id": session.id,
            "status": session.status.value,
            "base": session.base,
            "head": session.head,
            "review_count": len(session.reviews),
            "issue_count": len(session.issues),
            "files_changed": len(session.diff),
            "files": [
                {"path": f.path, "additions": f.additions, "deletions": f.deletions}
                for f in session.diff
            ],
            "agents": self._get_agent_statuses(session),
        }

    def _get_agent_statuses(self, session: ReviewSession) -> list[dict]:
        """Build agent status list for the UI."""
        result = []
        # Include configured models even if not yet triggered.
        for mc in session.config.models:
            if mc.id not in session.agent_states:
                session.agent_states[mc.id] = AgentState(
                    model_id=mc.id,
                    status=AgentStatus.WAITING,
                    task_type=AgentTaskType.REVIEW,
                )

        for model_id, agent in session.agent_states.items():
            elapsed = None
            if agent.started_at:
                end = agent.submitted_at or _utcnow()
                elapsed = (end - agent.started_at).total_seconds()
            result.append({
                "model_id": model_id,
                "status": agent.status.value,
                "task_type": agent.task_type.value,
                "prompt_preview": agent.prompt_preview,
                "elapsed_seconds": round(elapsed, 1) if elapsed is not None else None,
                "last_reason": agent.last_reason,
                "role": next(
                    (m.role for m in session.config.models if m.id == model_id), ""
                ),
            })
        return result

    def update_agent_runtime(
        self,
        session_id: str,
        model_id: str,
        *,
        reason: str | None = None,
        output: str | None = None,
        error: str | None = None,
    ) -> None:
        """Update runtime telemetry for one agent."""
        session = self.get_session(session_id)
        agent = session.agent_states.get(model_id)
        if not agent:
            return
        if reason is not None:
            agent.last_reason = reason
        if output is not None:
            agent.last_output = output
        if error is not None:
            agent.last_error = error
        agent.updated_at = _utcnow()
        self.persist()

    def get_agent_runtime(self, session_id: str, model_id: str) -> dict:
        """Get current runtime information for one agent."""
        session = self.get_session(session_id)
        agent = session.agent_states.get(model_id)
        if not agent:
            raise KeyError(f"Agent not found: {model_id}")

        elapsed = None
        if agent.started_at:
            end = agent.submitted_at or _utcnow()
            elapsed = round((end - agent.started_at).total_seconds(), 1)

        pending = self.get_pending_issues(session_id, model_id)
        return {
            "model_id": model_id,
            "status": agent.status.value,
            "task_type": agent.task_type.value,
            "role": next((m.role for m in session.config.models if m.id == model_id), ""),
            "prompt_preview": agent.prompt_preview,
            "prompt_full": agent.prompt_full,
            "started_at": agent.started_at.isoformat() if agent.started_at else None,
            "submitted_at": agent.submitted_at.isoformat() if agent.submitted_at else None,
            "elapsed_seconds": elapsed,
            "last_reason": agent.last_reason,
            "last_output": agent.last_output,
            "last_error": agent.last_error,
            "updated_at": agent.updated_at.isoformat() if agent.updated_at else None,
            "pending_count": len(pending),
            "pending_issue_ids": [p["id"] for p in pending],
        }

    def list_agents(self, session_id: str) -> list[dict]:
        """List configured agents for a session."""
        session = self.get_session(session_id)
        return [m.model_dump(mode="json") for m in session.config.models]

    def add_agent(self, session_id: str, model: dict) -> dict:
        """Add a model config to the session."""
        session = self.get_session(session_id)
        mc = ModelConfig(**model)
        if any(m.id == mc.id for m in session.config.models):
            raise ValueError(f"Agent already exists: {mc.id}")
        session.config.models.append(mc)
        self.persist()
        return mc.model_dump(mode="json")

    def remove_agent(self, session_id: str, model_id: str) -> dict:
        """Remove a model config from the session."""
        session = self.get_session(session_id)
        before = len(session.config.models)
        session.config.models = [m for m in session.config.models if m.id != model_id]
        if len(session.config.models) == before:
            raise KeyError(f"Agent not found: {model_id}")
        session.agent_states.pop(model_id, None)
        session.client_sessions.pop(model_id, None)
        session.agent_chats.pop(model_id, None)
        self.persist()
        return {"status": "removed", "model_id": model_id}

    def get_agent_chat(self, session_id: str, model_id: str) -> list[dict]:
        """Get direct chat history with an agent."""
        session = self.get_session(session_id)
        return [m.model_dump(mode="json") for m in session.agent_chats.get(model_id, [])]

    def append_agent_chat(
        self, session_id: str, model_id: str, role: str, content: str
    ) -> None:
        """Append direct chat message for an agent."""
        session = self.get_session(session_id)
        session.agent_chats.setdefault(model_id, []).append(
            AgentChatMessage(role=role, content=content)
        )
        self.persist()

    def add_manual_issue(
        self,
        session_id: str,
        title: str,
        severity: str,
        file: str,
        line: int | None,
        description: str,
        suggestion: str = "",
    ) -> dict:
        """Add a manually created issue to the session."""
        session = self.get_session(session_id)
        if session.status not in (SessionStatus.REVIEWING, SessionStatus.DELIBERATING):
            raise ValueError(f"Cannot add issue in {session.status.value} state")

        issue = Issue(
            title=title,
            severity=Severity(severity),
            file=file,
            line=line,
            description=description,
            suggestion=suggestion,
            raised_by="human",
            thread=[
                Opinion(
                    model_id="human",
                    action=OpinionAction.RAISE,
                    reasoning=description,
                    suggested_severity=Severity(severity),
                )
            ],
        )
        session.issues.append(issue)
        self.broker.publish(
            "issue_created",
            {"session_id": session_id, "issue_id": issue.id, "title": title},
        )
        self.persist()
        return issue.model_dump(mode="json")

    def get_final_report(self, session_id: str) -> dict:
        """Generate the final report."""
        session = self.get_session(session_id)

        issues_data = []
        consensus_count = 0
        dismissed_count = 0

        for issue in session.issues:
            issues_data.append({
                "title": issue.title,
                "final_severity": (issue.final_severity or issue.severity).value,
                "consensus": issue.consensus,
                "file": issue.file,
                "line": issue.line,
                "thread_summary": f"{len(issue.thread)} opinions",
            })
            if issue.consensus:
                consensus_count += 1
            if issue.final_severity == Severity.DISMISSED:
                dismissed_count += 1

        total_raw = sum(len(r.issues) for r in session.reviews)

        return {
            "session_id": session.id,
            "issues": issues_data,
            "stats": {
                "total_issues_found": total_raw,
                "after_dedup": len(session.issues),
                "consensus_reached": consensus_count,
                "dismissed": dismissed_count,
            },
        }

    def _extract_mentions(self, text: str, session: ReviewSession) -> list[str]:
        """Extract @model mentions from free-form text."""
        if not text:
            return []
        mentioned = re.findall(r"@([A-Za-z0-9_-]+)", text)
        valid_ids = {m.id for m in session.config.models}
        return [m for m in mentioned if m in valid_ids]
