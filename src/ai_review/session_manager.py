"""Session management: CRUD, review/opinion submission, event publishing."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from ai_review.git_diff import collect_diff, get_current_branch, get_diff_summary, parse_diff
from ai_review.knowledge import load_config, load_knowledge
from ai_review.models import (
    DiffFile,
    Issue,
    Knowledge,
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


class SessionManager:
    """Manages review sessions and orchestrates state transitions."""

    def __init__(self, repo_path: str | None = None) -> None:
        self.repo_path = repo_path
        self.sessions: dict[str, ReviewSession] = {}
        self.broker = SSEBroker()
        self._current_session_id: str | None = None

        # Optional callbacks — set by Orchestrator to drive automation.
        # When None the manager behaves as before (manual mode).
        self.on_review_submitted: Callable[[str, str], Any] | None = None  # (session_id, model_id)
        self.on_opinion_submitted: Callable[[str, str, str], Any] | None = None  # (session_id, issue_id, model_id)

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
                        )
                    ],
                )
                issues.append(issue)

        session.issues = issues
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
    ) -> dict:
        """Submit an opinion on an issue."""
        session = self.get_session(session_id)

        if session.status not in (SessionStatus.DELIBERATING, SessionStatus.REVIEWING):
            raise ValueError(f"Cannot submit opinion in {session.status.value} state")

        for issue in session.issues:
            if issue.id == issue_id:
                sev = Severity(suggested_severity) if suggested_severity else None
                opinion = Opinion(
                    model_id=model_id,
                    action=OpinionAction(action),
                    reasoning=reasoning,
                    suggested_severity=sev,
                )
                issue.thread.append(opinion)

                self.broker.publish(
                    "opinion_submitted",
                    {
                        "session_id": session_id,
                        "issue_id": issue_id,
                        "model_id": model_id,
                        "action": action,
                    },
                )

                result = {"status": "accepted", "thread_length": len(issue.thread)}

                if self.on_opinion_submitted is not None:
                    self.on_opinion_submitted(session_id, issue_id, model_id)

                return result

        raise KeyError(f"Issue not found: {issue_id}")

    def get_pending_issues(self, session_id: str, model_id: str) -> list[dict]:
        """Get issues where the model hasn't participated yet (neither raised nor opined)."""
        session = self.get_session(session_id)
        pending = []
        for issue in session.issues:
            model_participated = any(
                op.model_id == model_id for op in issue.thread
            )
            if not model_participated:
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
                "role": next(
                    (m.role for m in session.config.models if m.id == model_id), ""
                ),
            })
        return result

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
