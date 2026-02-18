"""Session management: CRUD, review/opinion submission, event publishing."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ai_review.git_diff import collect_delta_diff, collect_diff, get_current_branch, get_diff_summary, list_branches, parse_diff, validate_repo
from ai_review.knowledge import load_config, load_knowledge
from ai_review.models import (
    AgentActivity,
    AgentChatMessage,
    AgentState,
    AgentStatus,
    AgentTaskType,
    DiffFile,
    FixCommit,
    ImplementationContext,
    Issue,
    IssueDismissal,
    IssueResponse,
    IssueResponseAction,
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

    _DEBOUNCE_SECONDS = 0.1

    def __init__(self) -> None:
        self.sessions: dict[str, ReviewSession] = {}
        self.agent_presets: dict[str, ModelConfig] = {}
        self.broker = SSEBroker()
        self._current_session_id: str | None = None
        self._state_file = self._resolve_state_file()

        # Debounced persist state
        self._dirty = False
        self._flush_handle: asyncio.TimerHandle | None = None
        self._flush_lock = asyncio.Lock()

        # Optional callbacks — set by Orchestrator to drive automation.
        # When None the manager behaves as before (manual mode).
        self.on_review_submitted: Callable[[str, str], Any] | None = None  # (session_id, model_id)
        self.on_opinion_submitted: Callable[[str, str, str], Any] | None = None  # (session_id, issue_id, model_id)
        self.on_issue_responded: Callable[[str, str, str], Any] | None = None  # (session_id, issue_id, action)
        self.on_fix_completed: Callable[[str], Any] | None = None  # (session_id,)
        self.on_issue_dismissed: Callable[[str, str], Any] | None = None  # (session_id, issue_id)
        self._load_state()
        self._ensure_default_presets()

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
                "repo_path": s.repo_path,
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
    def _resolve_state_file() -> Path:
        return Path.cwd() / ".ai-review" / "runtime" / "sessions.json"

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
                        if agent.submitted_at is None:
                            agent.submitted_at = _utcnow()
                        agent.last_reason = "interrupted: server restarted"
                        agent.updated_at = _utcnow()
                loaded[session.id] = session
            self.sessions = loaded
            presets = raw.get("agent_presets", [])
            loaded_presets: dict[str, ModelConfig] = {}
            if isinstance(presets, dict):
                presets = list(presets.values())
            for item in presets:
                mc = ModelConfig.model_validate(item)
                loaded_presets[mc.id] = mc
            self.agent_presets = loaded_presets
            current = raw.get("current_session_id")
            self._current_session_id = current if current in loaded else None
        except Exception:
            logger.exception("Failed to load persisted session state from %s", self._state_file)

    _DEFAULT_PRESETS: list[dict[str, str]] = [
        {"id": "preset-claude-code", "client_type": "claude-code", "role": "general", "color": "#8B5CF6", "avatar": "🟣"},
        {"id": "preset-codex", "client_type": "codex", "role": "general", "color": "#22C55E", "avatar": "🟢"},
        {"id": "preset-gemini", "client_type": "gemini", "role": "general", "color": "#3B82F6", "avatar": "🔵"},
    ]

    def _ensure_default_presets(self) -> None:
        """Seed default presets only on first run (no presets at all)."""
        if self.agent_presets:
            return
        for preset in self._DEFAULT_PRESETS:
            self.agent_presets[preset["id"]] = ModelConfig(**preset)
        self.persist()

    def persist(self) -> None:
        """Mark state as dirty and schedule a debounced flush.

        Falls back to synchronous write when no event loop is running
        (e.g. during ``__init__`` or in synchronous tests).
        """
        self._dirty = True
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No event loop — write synchronously.
            self._sync_write()
            return
        if self._flush_handle is not None:
            self._flush_handle.cancel()
        self._flush_handle = loop.call_later(
            self._DEBOUNCE_SECONDS, self._enqueue_flush,
        )

    def _enqueue_flush(self) -> None:
        """Callback from debounce timer; creates an async flush task."""
        self._flush_handle = None
        asyncio.ensure_future(self._flush_async())

    async def _flush_async(self) -> None:
        """Perform the actual I/O flush in a worker thread (with lock)."""
        async with self._flush_lock:
            if not self._dirty:
                return
            snapshot = self._build_snapshot()
            self._dirty = False
            await asyncio.to_thread(self._write_snapshot, snapshot)

    async def flush(self) -> None:
        """Immediate flush for shutdown — cancels any pending debounce."""
        if self._flush_handle is not None:
            self._flush_handle.cancel()
            self._flush_handle = None
        if self._dirty:
            await self._flush_async()

    def _build_snapshot(self) -> dict:
        """Build a JSON-serializable state snapshot (CPU-bound, runs on main thread)."""
        return {
            "current_session_id": self._current_session_id,
            "sessions": [s.model_dump(mode="json") for s in self.sessions.values()],
            "agent_presets": [p.model_dump(mode="json") for p in self.agent_presets.values()],
        }

    def _write_snapshot(self, payload: dict) -> None:
        """Write snapshot to disk (safe to call from worker thread)."""
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            temp = self._state_file.with_suffix(".tmp")
            temp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8",
            )
            temp.replace(self._state_file)
        except Exception:
            logger.exception("Failed to persist session state to %s", self._state_file)

    def _sync_write(self) -> None:
        """Synchronous fallback for when no event loop is available."""
        if not self._dirty:
            return
        snapshot = self._build_snapshot()
        self._dirty = False
        self._write_snapshot(snapshot)

    async def start_review(
        self,
        base: str = "main",
        *,
        head: str,
        repo_path: str,
        preset_ids: list[str] | None = None,
        implementation_context: dict | None = None,
    ) -> dict:
        """Start a new review session: collect diff and knowledge."""
        # Load config
        config = load_config(repo_path)

        session = ReviewSession(base=base, repo_path=repo_path)
        if preset_ids is not None:
            if not isinstance(preset_ids, list):
                raise ValueError("preset_ids must be a list")
            if not preset_ids:
                raise ValueError("preset_ids must include at least one preset id")
            resolved: list[ModelConfig] = []
            missing: list[str] = []
            for preset_id in preset_ids:
                mc = self.agent_presets.get(str(preset_id))
                if mc is None:
                    missing.append(str(preset_id))
                    continue
                resolved.append(ModelConfig.model_validate(mc.model_dump(mode="json")))
            if missing:
                raise ValueError(f"Unknown preset ids: {', '.join(missing)}")
            session.config.models = resolved
        elif config and config.models:
            session.config = config
        else:
            # Use all enabled agent presets as default reviewers
            enabled = [
                ModelConfig.model_validate(mc.model_dump(mode="json"))
                for mc in self.agent_presets.values()
                if mc.enabled
            ]
            if enabled:
                session.config.models = enabled

        self.sessions[session.id] = session
        self._current_session_id = session.id

        # Transition to COLLECTING
        transition(session, SessionStatus.COLLECTING)
        self.broker.publish("phase_change", {"status": "collecting", "session_id": session.id})

        # Collect diff
        session.head = head
        session.diff = await collect_diff(base, repo_path, head=head)
        session.knowledge = load_knowledge(repo_path)

        # Transition to REVIEWING
        transition(session, SessionStatus.REVIEWING)
        self.broker.publish("phase_change", {"status": "reviewing", "session_id": session.id})

        # Apply inline implementation context if provided
        if implementation_context:
            self.submit_implementation_context(session.id, implementation_context)

        summary = get_diff_summary(session.diff)
        summary["session_id"] = session.id
        summary["head"] = session.head
        self.persist()
        return summary

    MAX_FILE_LINES = 2000

    def read_file(
        self,
        session_id: str,
        file_path: str,
        start: int | None = None,
        end: int | None = None,
    ) -> dict:
        """Read source file content with optional line range.

        Returns lines from the repo working tree (not diff).
        Access is restricted to files within session.repo_path.
        """
        session = self.get_session(session_id)
        repo_root = Path(session.repo_path).expanduser().resolve() if session.repo_path else Path.cwd().resolve()

        target = Path(file_path).expanduser()
        resolved = target.resolve() if target.is_absolute() else (repo_root / target).resolve()

        # Block access outside repo boundary
        try:
            resolved.relative_to(repo_root)
        except ValueError:
            raise PermissionError(f"Access denied: path is outside repository ({file_path})")

        if not resolved.is_file():
            raise FileNotFoundError(f"File not found: {file_path}")

        all_lines = resolved.read_text(encoding="utf-8", errors="replace").splitlines()
        total = len(all_lines)

        # Normalize range (1-based)
        s = max(1, start or 1)
        e = min(total, end or total)
        if e - s + 1 > self.MAX_FILE_LINES:
            e = s + self.MAX_FILE_LINES - 1

        selected = all_lines[s - 1 : e]

        return {
            "path": file_path,
            "start_line": s,
            "end_line": e,
            "total_lines": total,
            "content": "\n".join(selected),
            "lines": [{"number": s + i, "content": line} for i, line in enumerate(selected)],
        }

    _TREE_EXCLUDE = frozenset({
        ".git", "node_modules", "__pycache__", ".venv", "venv",
        ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache",
        ".eggs", "dist", "build", ".ai-review",
    })
    MAX_TREE_DEPTH = 5

    def get_tree(
        self,
        session_id: str,
        path: str = "",
        depth: int = 2,
    ) -> dict:
        """Return directory tree under session repo_path."""
        session = self.get_session(session_id)
        repo_root = Path(session.repo_path).expanduser().resolve() if session.repo_path else Path.cwd().resolve()

        target = (repo_root / path).resolve() if path else repo_root
        try:
            target.relative_to(repo_root)
        except ValueError:
            raise PermissionError(f"Access denied: path is outside repository ({path})")

        if not target.is_dir():
            raise FileNotFoundError(f"Directory not found: {path}")

        depth = max(1, min(depth, self.MAX_TREE_DEPTH))
        entries = self._walk_tree(target, depth)
        return {"path": path or ".", "entries": entries}

    def _walk_tree(self, directory: Path, depth: int) -> list[dict]:
        if depth <= 0:
            return []
        entries: list[dict] = []
        try:
            children = sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except PermissionError:
            return []
        for child in children:
            if child.name in self._TREE_EXCLUDE:
                continue
            if child.name.startswith(".") and child.is_dir():
                continue
            if child.is_dir():
                entries.append({
                    "name": child.name,
                    "type": "directory",
                    "children": self._walk_tree(child, depth - 1),
                })
            elif child.is_file():
                try:
                    size = child.stat().st_size
                except OSError:
                    size = 0
                entries.append({"name": child.name, "type": "file", "size": size})
        return entries

    MAX_SEARCH_RESULTS = 100
    SEARCH_TIMEOUT = 5.0

    async def search_code(
        self,
        session_id: str,
        query: str,
        glob: str | None = None,
        max_results: int = 30,
        context_lines: int = 1,
    ) -> dict:
        """Search code in repo using ripgrep with Python fallback."""
        session = self.get_session(session_id)
        repo_root = Path(session.repo_path).expanduser().resolve() if session.repo_path else Path.cwd().resolve()

        max_results = max(1, min(max_results, self.MAX_SEARCH_RESULTS))

        if shutil.which("rg"):
            return await self._search_rg(repo_root, query, glob, max_results, context_lines)
        return self._search_python(repo_root, query, glob, max_results, context_lines)

    async def _search_rg(
        self, root: Path, query: str, glob: str | None, max_results: int, context_lines: int,
    ) -> dict:
        cmd = [
            "rg", "--json",
            "-m", str(max_results),
            "-C", str(context_lines),
            "--max-filesize", "1M",
        ]
        if glob:
            cmd.extend(["-g", glob])
        cmd.extend([query, str(root)])

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=self.SEARCH_TIMEOUT)
        except asyncio.TimeoutError:
            return {"query": query, "glob": glob, "results": [], "total_matches": 0, "truncated": False, "error": "timeout"}

        results: list[dict] = []
        total = 0
        for line in stdout.decode("utf-8", errors="replace").splitlines():
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") == "match":
                data = obj["data"]
                path = data["path"]["text"]
                try:
                    rel = str(Path(path).relative_to(root))
                except ValueError:
                    rel = path
                results.append({
                    "file": rel,
                    "line": data["line_number"],
                    "content": data["lines"]["text"].rstrip("\n"),
                })
                total += 1

        return {
            "query": query,
            "glob": glob,
            "results": results[:max_results],
            "total_matches": total,
            "truncated": total > max_results,
        }

    def _search_python(
        self, root: Path, query: str, glob_pattern: str | None, max_results: int, context_lines: int,
    ) -> dict:
        """Pure-Python fallback when rg is not installed."""
        import fnmatch

        try:
            pattern = re.compile(query)
        except re.error:
            pattern = re.compile(re.escape(query))

        results: list[dict] = []
        total = 0
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            if any(part in self._TREE_EXCLUDE for part in path.parts):
                continue
            if glob_pattern and not fnmatch.fnmatch(path.name, glob_pattern):
                continue
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            for i, line in enumerate(lines):
                if pattern.search(line):
                    total += 1
                    if len(results) < max_results:
                        results.append({
                            "file": str(path.relative_to(root)),
                            "line": i + 1,
                            "content": line.rstrip("\n"),
                        })

        return {
            "query": query,
            "glob": glob_pattern,
            "results": results,
            "total_matches": total,
            "truncated": total > max_results,
        }

    def submit_implementation_context(self, session_id: str, data: dict) -> dict:
        """Submit implementation context for a session.

        Allowed in COLLECTING or REVIEWING states only.
        """
        session = self.get_session(session_id)
        if session.status not in (SessionStatus.COLLECTING, SessionStatus.REVIEWING):
            raise ValueError(
                f"Cannot submit implementation context in {session.status.value} state"
            )

        ic = ImplementationContext(
            summary=data.get("summary", ""),
            decisions=data.get("decisions", []),
            tradeoffs=data.get("tradeoffs", []),
            known_issues=data.get("known_issues", []),
            out_of_scope=data.get("out_of_scope", []),
            submitted_by=data.get("submitted_by", ""),
            submitted_at=_utcnow(),
        )
        session.implementation_context = ic

        self.broker.publish("context_submitted", {
            "session_id": session_id,
            "submitted_by": ic.submitted_by,
        })
        self.persist()
        return ic.model_dump(mode="json")

    def get_review_context(self, session_id: str, file: str | None = None) -> dict:
        """Return diff + knowledge + implementation context for the session."""
        session = self.get_session(session_id)

        if file:
            diff_content = "\n".join(
                f.content for f in session.diff if f.path == file and f.content
            )
        else:
            diff_content = "\n".join(f.content for f in session.diff if f.content)

        result: dict = {
            "diff": diff_content,
            "knowledge": session.knowledge.model_dump(mode="json"),
            "files": [f.path for f in session.diff],
        }
        if session.implementation_context is not None:
            result["implementation_context"] = session.implementation_context.model_dump(mode="json")
        return result

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

    @staticmethod
    def _normalize_issue_lines(
        line: int | None,
        line_start: int | None,
        line_end: int | None,
    ) -> tuple[int | None, int | None, int | None]:
        """Normalize line/range fields while keeping backward compatibility."""
        start = line_start if line_start is not None else line
        end = line_end if line_end is not None else start

        if start is None and end is not None:
            start = end
        if start is not None and end is not None and end < start:
            start, end = end, start

        normalized_line = line if line is not None else start
        return normalized_line, start, end

    @staticmethod
    def _looks_like_markdown(text: str) -> bool:
        return bool(
            re.search(
                r"(^\s{0,3}#{1,6}\s)|(^\s{0,3}[-*+]\s)|(^\s{0,3}\d+\.\s)|(```)|(`[^`\n]+`)|(^\s{0,3}>)",
                text,
                flags=re.MULTILINE,
            )
        )

    @classmethod
    def _ensure_issue_markdown(cls, text: str, heading: str) -> str:
        content = (text or "").strip()
        if not content:
            return ""
        if cls._looks_like_markdown(content):
            return content
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        if not lines:
            return ""
        if len(lines) == 1:
            body = lines[0]
        else:
            body = "\n".join(f"- {line}" for line in lines)
        return f"### {heading}\n{body}"

    def submit_review(
        self, session_id: str, model_id: str, issues: list[dict], summary: str = ""
    ) -> dict:
        """Submit a review with issues."""
        session = self.get_session(session_id)

        if session.status not in (SessionStatus.REVIEWING, SessionStatus.VERIFYING):
            raise ValueError(f"Cannot submit review in {session.status.value} state")

        normalized_issues: list[dict] = []
        for issue in issues:
            item = dict(issue)
            item["description"] = self._ensure_issue_markdown(str(item.get("description", "")), "문제")
            if "suggestion" in item:
                item["suggestion"] = self._ensure_issue_markdown(str(item.get("suggestion", "")), "개선 제안")
            normalized_issues.append(item)

        raw_issues = [RawIssue(**i) for i in normalized_issues]
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

    def submit_review_issue(self, session_id: str, model_id: str, issue: dict) -> dict:
        """Submit a single review issue to the pending buffer."""
        session = self.get_session(session_id)
        if session.status not in (SessionStatus.REVIEWING, SessionStatus.VERIFYING):
            raise ValueError(f"Cannot submit review issue in {session.status.value} state")

        item = dict(issue)
        item["description"] = self._ensure_issue_markdown(str(item.get("description", "")), "문제")
        if "suggestion" in item:
            item["suggestion"] = self._ensure_issue_markdown(str(item.get("suggestion", "")), "개선 제안")

        session.pending_review_issues.setdefault(model_id, []).append(item)
        self.persist()
        return {"status": "accepted", "pending_count": len(session.pending_review_issues[model_id])}

    def complete_review(self, session_id: str, model_id: str, summary: str = "") -> dict:
        """Flush pending issues into a Review and finalize."""
        session = self.get_session(session_id)
        issues = session.pending_review_issues.pop(model_id, [])
        self.persist()
        return self.submit_review(session_id, model_id, issues, summary)

    def ensure_agent_access_key(self, session_id: str, model_id: str) -> str:
        """Get or create an access key for one configured agent."""
        session = self.get_session(session_id)
        existing = session.agent_access_keys.get(model_id)
        if existing:
            return existing
        key = secrets.token_hex(24)
        session.agent_access_keys[model_id] = key
        self.persist()
        return key

    def issue_human_assist_access_key(self, session_id: str) -> str:
        """Issue (rotate) access key for human-assist mediator."""
        session = self.get_session(session_id)
        key = secrets.token_hex(24)
        session.human_assist_access_key = key
        self.persist()
        return key

    def get_all_reviews(self, session_id: str) -> list[dict]:
        """Get all submitted reviews."""
        session = self.get_session(session_id)
        return [r.model_dump(mode="json") for r in session.reviews]

    @staticmethod
    def _normalize_notes(items: list[str] | None) -> list[str]:
        normalized: list[str] = []
        for item in items or []:
            text = str(item).strip()
            if text:
                normalized.append(text)
        return normalized

    @staticmethod
    def _current_turn(session: ReviewSession) -> int:
        return max((i.turn for i in session.issues), default=0)

    def create_issues_from_reviews(self, session_id: str) -> list[Issue]:
        """Create Issue objects from all submitted RawIssues (pre-dedup)."""
        session = self.get_session(session_id)

        issues: list[Issue] = []
        for review in session.reviews:
            for raw in review.issues:
                normalized_line, normalized_start, normalized_end = self._normalize_issue_lines(
                    raw.line,
                    raw.line_start,
                    raw.line_end,
                )
                issue = Issue(
                    title=raw.title,
                    severity=raw.severity,
                    file=raw.file,
                    line=normalized_line,
                    line_start=normalized_start,
                    line_end=normalized_end,
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

    def get_confirmed_issues(self, session_id: str) -> dict:
        """Get issues with consensus_type == 'fix_required'."""
        session = self.get_session(session_id)
        confirmed = []
        dismissed = 0
        undecided = 0
        for issue in session.issues:
            if issue.consensus_type == "fix_required":
                # Build consensus summary from thread
                fix_count = sum(1 for op in issue.thread if op.action == OpinionAction.FIX_REQUIRED)
                no_fix_count = sum(1 for op in issue.thread if op.action == OpinionAction.NO_FIX)
                consensus_summary = f"{fix_count} fix_required, {no_fix_count} no_fix"
                confirmed.append({
                    "id": issue.id,
                    "title": issue.title,
                    "severity": issue.severity.value,
                    "file": issue.file,
                    "line_start": issue.line_start,
                    "line_end": issue.line_end,
                    "description": issue.description,
                    "suggestion": issue.suggestion,
                    "consensus_summary": consensus_summary,
                })
            elif issue.consensus_type == "dismissed":
                dismissed += 1
            else:
                undecided += 1
        return {
            "issues": confirmed,
            "session_id": session_id,
            "total_confirmed": len(confirmed),
            "total_dismissed": dismissed,
            "total_undecided": undecided,
        }

    def submit_issue_response(
        self,
        session_id: str,
        issue_id: str,
        action: str,
        reasoning: str = "",
        proposed_change: str = "",
        submitted_by: str = "",
    ) -> dict:
        """Submit a coding agent's response to a confirmed issue."""
        session = self.get_session(session_id)
        if session.status not in (SessionStatus.AGENT_RESPONSE, SessionStatus.DELIBERATING):
            raise ValueError(f"Cannot submit issue response in {session.status.value} state")

        # Find the issue
        issue = None
        for i in session.issues:
            if i.id == issue_id:
                issue = i
                break
        if issue is None:
            raise KeyError(f"Issue not found: {issue_id}")

        # Reject duplicate response
        if any(r.issue_id == issue_id for r in session.issue_responses):
            raise ValueError(f"Duplicate response for issue: {issue_id}")

        response_action = IssueResponseAction(action)
        ir = IssueResponse(
            issue_id=issue_id,
            action=response_action,
            reasoning=reasoning,
            proposed_change=proposed_change,
            submitted_by=submitted_by,
        )
        session.issue_responses.append(ir)

        # For dispute: reset consensus and add NO_FIX opinion
        if response_action == IssueResponseAction.DISPUTE:
            issue.turn += 1
            issue.consensus = False
            issue.consensus_type = None
            issue.final_severity = None
            opinion = Opinion(
                model_id=submitted_by or "coding-agent",
                action=OpinionAction.NO_FIX,
                reasoning=f"[DISPUTE] {reasoning}",
                turn=issue.turn,
            )
            issue.thread.append(opinion)

        self.broker.publish("issue_response", {
            "session_id": session_id,
            "issue_id": issue_id,
            "action": action,
            "submitted_by": submitted_by,
        })

        if self.on_issue_responded is not None:
            self.on_issue_responded(session_id, issue_id, action)

        self.persist()
        return {
            "status": "accepted",
            "issue_id": issue_id,
            "action": action,
        }

    def get_issue_response_status(self, session_id: str) -> dict:
        """Get the status of issue responses for a session."""
        session = self.get_session(session_id)
        confirmed_ids = {
            i.id for i in session.issues if i.consensus_type == "fix_required"
        }
        responded_ids = {r.issue_id for r in session.issue_responses}
        pending_ids = sorted(confirmed_ids - responded_ids)
        return {
            "total_confirmed": len(confirmed_ids),
            "total_responded": len(responded_ids & confirmed_ids),
            "pending_ids": pending_ids,
            "all_responded": len(pending_ids) == 0 and len(confirmed_ids) > 0,
        }

    async def submit_fix_complete(
        self,
        session_id: str,
        commit_hash: str,
        issues_addressed: list[str] | None = None,
        submitted_by: str = "",
    ) -> dict:
        """Record a fix commit, collect delta diff, and transition to VERIFYING."""
        session = self.get_session(session_id)
        if session.status != SessionStatus.FIXING:
            raise ValueError(f"Cannot submit fix-complete in {session.status.value} state")

        # Determine which issues are addressed
        confirmed_ids = {
            i.id for i in session.issues if i.consensus_type == "fix_required"
        }
        if issues_addressed is not None:
            for iid in issues_addressed:
                if iid not in confirmed_ids:
                    raise KeyError(f"Issue not found or not fix_required: {iid}")
        else:
            issues_addressed = sorted(confirmed_ids)

        # Record the fix commit
        fix_commit = FixCommit(
            commit_hash=commit_hash,
            issues_addressed=issues_addressed,
            submitted_by=submitted_by,
        )
        session.fix_commits.append(fix_commit)

        # Collect delta diff from previous head to the new commit
        prev_head = session.head
        if prev_head and session.repo_path:
            session.delta_diff = await collect_delta_diff(
                prev_head, commit_hash, repo_path=session.repo_path,
            )
        else:
            session.delta_diff = []

        # Update head and verification round
        session.head = commit_hash
        session.verification_round += 1

        # Transition to VERIFYING
        transition(session, SessionStatus.VERIFYING)
        self.broker.publish("phase_change", {
            "status": "verifying",
            "session_id": session_id,
            "verification_round": session.verification_round,
        })

        if self.on_fix_completed is not None:
            self.on_fix_completed(session_id)

        self.persist()
        return {
            "status": "accepted",
            "commit_hash": commit_hash,
            "issues_addressed": issues_addressed,
            "delta_files_changed": len(session.delta_diff),
            "verification_round": session.verification_round,
        }

    def get_delta_context(self, session_id: str) -> dict:
        """Return delta diff context for verification review."""
        session = self.get_session(session_id)
        confirmed = [
            {
                "id": i.id,
                "title": i.title,
                "severity": i.severity.value,
                "file": i.file,
                "description": i.description,
            }
            for i in session.issues
            if i.consensus_type == "fix_required"
        ]
        return {
            "session_id": session_id,
            "delta_diff": [d.model_dump(mode="json") for d in session.delta_diff],
            "delta_files": [d.path for d in session.delta_diff],
            "verification_round": session.verification_round,
            "fix_commits": [fc.model_dump(mode="json") for fc in session.fix_commits],
            "original_issues": confirmed,
        }

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
        confidence: float = 1.0,
    ) -> dict:
        """Submit an opinion on an issue."""
        session = self.get_session(session_id)
        human_like_models = {"human", "human-assist"}
        is_human_like = model_id in human_like_models

        is_human_reopen = is_human_like and session.status == SessionStatus.COMPLETE
        if session.status not in (SessionStatus.DELIBERATING, SessionStatus.REVIEWING, SessionStatus.VERIFYING) and not is_human_reopen:
            raise ValueError(f"Cannot submit opinion in {session.status.value} state")

        for issue in session.issues:
            if issue.id == issue_id:
                parsed_action = OpinionAction(action)

                # FALSE_POSITIVE validation: raiser cannot mark own issue
                if parsed_action == OpinionAction.FALSE_POSITIVE:
                    if model_id == issue.raised_by:
                        raise ValueError("Original raiser cannot submit false_positive on own issue")

                # WITHDRAW validation: only raiser can withdraw
                if parsed_action == OpinionAction.WITHDRAW:
                    if model_id != issue.raised_by:
                        raise ValueError("Only the original raiser can withdraw an issue")

                # Human 의견은 새 턴을 열고 이슈를 재오픈해 모든 에이전트가 다시 검토하도록 유도
                if is_human_like:
                    issue.turn += 1
                    issue.consensus = False
                    issue.final_severity = None
                target_turn = issue.turn

                # Reject duplicate opinion from same model in same turn
                # WITHDRAW bypasses duplicate check (raiser already has RAISE in same turn)
                if not is_human_like and parsed_action != OpinionAction.WITHDRAW and any(
                    op.model_id == model_id and op.turn == target_turn
                    for op in issue.thread
                ):
                    return {"status": "duplicate", "thread_length": len(issue.thread), "turn": target_turn}

                sev = Severity(suggested_severity) if suggested_severity else None
                opinion = Opinion(
                    model_id=model_id,
                    action=parsed_action,
                    reasoning=reasoning,
                    suggested_severity=sev,
                    confidence=max(0.0, min(float(confidence), 1.0)),
                    turn=target_turn,
                    mentions=sorted(set((mentions or []) + self._extract_mentions(reasoning, session))),
                )
                issue.thread.append(opinion)

                # WITHDRAW: immediately close the issue
                if parsed_action == OpinionAction.WITHDRAW:
                    issue.consensus = True
                    issue.consensus_type = "closed"
                    issue.final_severity = Severity.DISMISSED

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
            "current_turn": self._current_turn(session),
            "issue_count": len(session.issues),
            "files_changed": len(session.diff),
            "files": [
                {"path": f.path, "additions": f.additions, "deletions": f.deletions}
                for f in session.diff
            ],
            "agents": self._get_agent_statuses(session),
            "agent_activities": self._get_agent_activities_summary(session),
        }

    def _get_agent_activities_summary(self, session: ReviewSession) -> dict[str, list[dict]]:
        """Group recent activities by model_id for UI restoration on refresh."""
        grouped: dict[str, list[dict]] = {}
        for act in session.agent_activities:
            grouped.setdefault(act.model_id, [])
        # Collect up to MAX recent activities per model
        _MAX = 50
        for act in reversed(session.agent_activities):
            bucket = grouped.setdefault(act.model_id, [])
            if len(bucket) >= _MAX:
                continue
            bucket.append({
                "action": act.action,
                "target": act.target,
                "ts": act.timestamp.isoformat(),
            })
        return grouped

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
            elapsed = self._compute_agent_elapsed(agent)
            mc = next((m for m in session.config.models if m.id == model_id), None)
            result.append({
                "model_id": model_id,
                "status": agent.status.value,
                "task_type": agent.task_type.value,
                "prompt_preview": agent.prompt_preview,
                "elapsed_seconds": round(elapsed, 1) if elapsed is not None else None,
                "last_reason": agent.last_reason,
                "role": mc.role if mc else "",
                "color": mc.color if mc else "",
                "enabled": mc.enabled if mc else True,
                "description": mc.description if mc else "",
            })
        return result

    def _compute_agent_elapsed(self, agent: AgentState) -> float | None:
        """Compute elapsed time for the current task run.

        - REVIEWING: keep ticking with current wall clock.
        - WAITING/SUBMITTED/FAILED: freeze at terminal timestamp (submitted/updated).
        """
        if not agent.started_at:
            return None
        if agent.status == AgentStatus.REVIEWING:
            end = _utcnow()
        else:
            end = agent.submitted_at or agent.updated_at or agent.started_at
        elapsed = (end - agent.started_at).total_seconds()
        return max(elapsed, 0.0)

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

        elapsed = self._compute_agent_elapsed(agent)

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
            "elapsed_seconds": round(elapsed, 1) if elapsed is not None else None,
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

    def list_agent_presets(self) -> list[dict]:
        """List all persisted agent presets."""
        return [m.model_dump(mode="json") for m in sorted(self.agent_presets.values(), key=lambda x: x.id)]

    def add_agent_preset(self, model: dict) -> dict:
        """Add a new agent preset."""
        mc = ModelConfig(**model)
        if mc.id in self.agent_presets:
            raise ValueError(f"Agent preset already exists: {mc.id}")
        self.agent_presets[mc.id] = mc
        self.persist()
        return mc.model_dump(mode="json")

    def update_agent_preset(self, preset_id: str, updates: dict) -> dict:
        """Update fields on an existing agent preset."""
        mc = self.agent_presets.get(preset_id)
        if mc is None:
            raise KeyError(f"Agent preset not found: {preset_id}")
        updates.pop("id", None)
        for key, value in updates.items():
            if hasattr(mc, key):
                setattr(mc, key, value)
        self.persist()
        return mc.model_dump(mode="json")

    def remove_agent_preset(self, preset_id: str) -> dict:
        """Remove an agent preset."""
        if preset_id not in self.agent_presets:
            raise KeyError(f"Agent preset not found: {preset_id}")
        del self.agent_presets[preset_id]
        self.persist()
        return {"status": "removed", "preset_id": preset_id}

    def add_agent(self, session_id: str, model: dict) -> dict:
        """Add a model config to the session."""
        session = self.get_session(session_id)
        mc = ModelConfig(**model)
        if any(m.id == mc.id for m in session.config.models):
            raise ValueError(f"Agent already exists: {mc.id}")
        session.config.models.append(mc)
        self.persist()
        return mc.model_dump(mode="json")

    def update_agent(self, session_id: str, model_id: str, updates: dict) -> dict:
        """Update fields on an existing model config."""
        session = self.get_session(session_id)
        mc = next((m for m in session.config.models if m.id == model_id), None)
        if mc is None:
            raise KeyError(f"Agent not found: {model_id}")
        # id is immutable
        updates.pop("id", None)
        for key, value in updates.items():
            if hasattr(mc, key):
                setattr(mc, key, value)
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

    _ACTIVITY_DEDUP_SECONDS = 10.0

    def record_activity(
        self, session_id: str, model_id: str, action: str, target: str,
    ) -> bool:
        """Record an agent activity event. Returns False if suppressed as duplicate."""
        session = self.get_session(session_id)
        now = _utcnow()

        # Dedup: suppress same model+action+target within threshold
        for act in reversed(session.agent_activities):
            if act.model_id != model_id:
                continue
            if act.action == action and act.target == target:
                elapsed = (now - act.timestamp).total_seconds()
                if elapsed < self._ACTIVITY_DEDUP_SECONDS:
                    return False
                break

        activity = AgentActivity(model_id=model_id, action=action, target=target, timestamp=now)
        session.agent_activities.append(activity)

        self.broker.publish("agent_activity", {
            "session_id": session_id,
            "model_id": model_id,
            "action": action,
            "target": target,
            "timestamp": now.isoformat(),
        })
        return True

    def resolve_model_id_from_key(self, session_id: str, agent_key: str) -> str | None:
        """Reverse-lookup model_id from agent access key."""
        session = self.get_session(session_id)
        for mid, key in session.agent_access_keys.items():
            if key == agent_key:
                return mid
        return None

    def add_manual_issue(
        self,
        session_id: str,
        title: str,
        severity: str,
        file: str,
        line: int | None,
        description: str,
        suggestion: str = "",
        line_start: int | None = None,
        line_end: int | None = None,
    ) -> dict:
        """Add a manually created issue to the session."""
        session = self.get_session(session_id)
        if session.status not in (SessionStatus.REVIEWING, SessionStatus.DELIBERATING):
            raise ValueError(f"Cannot add issue in {session.status.value} state")

        normalized_line, normalized_start, normalized_end = self._normalize_issue_lines(
            line,
            line_start,
            line_end,
        )

        issue = Issue(
            title=title,
            severity=Severity(severity),
            file=file,
            line=normalized_line,
            line_start=normalized_start,
            line_end=normalized_end,
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
        fix_required_count = 0
        dismissed_count = 0

        for issue in session.issues:
            issues_data.append({
                "id": issue.id,
                "title": issue.title,
                "final_severity": (issue.final_severity or issue.severity).value,
                "consensus": issue.consensus,
                "consensus_type": issue.consensus_type,
                "file": issue.file,
                "line": issue.line,
                "line_start": issue.line_start,
                "line_end": issue.line_end,
                "description": issue.description,
                "suggestion": issue.suggestion,
                "thread_summary": f"{len(issue.thread)} opinions",
            })
            if issue.consensus_type == "fix_required":
                fix_required_count += 1
            elif issue.consensus_type == "dismissed":
                dismissed_count += 1

        total_raw = sum(len(r.issues) for r in session.reviews)

        responses = [
            {
                "issue_id": r.issue_id,
                "action": r.action.value if hasattr(r.action, "value") else r.action,
                "reasoning": r.reasoning,
            }
            for r in session.issue_responses
        ]

        commits = [fc.model_dump(mode="json") for fc in session.fix_commits]

        dismissals_data = [d.model_dump(mode="json") for d in session.dismissals]

        return {
            "session_id": session.id,
            "status": session.status.value,
            "issues": issues_data,
            "issue_responses": responses,
            "fix_commits": commits,
            "dismissals": dismissals_data,
            "verification_round": session.verification_round,
            "implementation_context": (
                session.implementation_context.model_dump(mode="json")
                if session.implementation_context else None
            ),
            "stats": {
                "total_issues_found": total_raw,
                "after_dedup": len(session.issues),
                "consensus_reached": fix_required_count + dismissed_count,
                "fix_required": fix_required_count,
                "dismissed": dismissed_count,
            },
        }

    def generate_pr_markdown(self, session_id: str) -> str:
        """Generate a PR description markdown from the final report."""
        report = self.get_final_report(session_id)
        stats = report["stats"]
        lines: list[str] = []

        lines.append("## AI Review Summary")
        lines.append("")
        lines.append(
            f"### Issues Found: {stats['after_dedup']}"
            f" (Fix Required: {stats['fix_required']}, Dismissed: {stats['dismissed']})"
        )
        lines.append("")

        if report["issues"]:
            lines.append("| # | Severity | File | Title | Status |")
            lines.append("|---|----------|------|-------|--------|")
            for idx, issue in enumerate(report["issues"], 1):
                status = issue.get("consensus_type") or "pending"
                lines.append(
                    f"| {idx} | {issue['final_severity']} | {issue['file']}"
                    f" | {issue['title']} | {status} |"
                )
            lines.append("")

        if report["fix_commits"]:
            lines.append("### Fix Commits")
            for fc in report["fix_commits"]:
                short_hash = fc["commit_hash"][:7]
                by = fc.get("submitted_by") or "unknown"
                addressed = fc.get("issues_addressed") or []
                # Find file names for addressed issues
                file_set: list[str] = []
                for issue in report["issues"]:
                    if issue["id"] in addressed:
                        if issue["file"] not in file_set:
                            file_set.append(issue["file"])
                files_str = ", ".join(file_set) if file_set else "general"
                lines.append(f"- `{short_hash}` \u2014 {files_str} (by {by})")
            lines.append("")

        vr = report.get("verification_round", 0)
        if vr > 0:
            lines.append("### Verification")
            lines.append(f"- Rounds: {vr}")
            unresolved = stats["fix_required"] - stats.get("dismissed", 0)
            # Check if all fix_required issues are addressed
            addressed_ids: set[str] = set()
            for fc in report["fix_commits"]:
                addressed_ids.update(fc.get("issues_addressed") or [])
            fix_issues = [i for i in report["issues"] if i.get("consensus_type") == "fix_required"]
            all_resolved = all(i["id"] in addressed_ids for i in fix_issues) if fix_issues else True
            result_text = "All issues resolved" if all_resolved else "Some issues remain unresolved"
            lines.append(f"- Result: {result_text}")
            lines.append("")

        return "\n".join(lines)

    def dismiss_issue(
        self,
        session_id: str,
        issue_id: str,
        reasoning: str = "",
        dismissed_by: str = "",
    ) -> dict:
        """Dismiss a fix_required issue during FIXING state."""
        session = self.get_session(session_id)
        if session.status != SessionStatus.FIXING:
            raise ValueError(f"Cannot dismiss in {session.status.value} state")
        issue = next((i for i in session.issues if i.id == issue_id), None)
        if issue is None:
            raise KeyError(f"Issue not found: {issue_id}")
        if issue.consensus_type != "fix_required":
            raise ValueError("Can only dismiss fix_required issues")
        if any(d.issue_id == issue_id for d in session.dismissals):
            raise ValueError(f"Already dismissed: {issue_id}")
        dismissal = IssueDismissal(
            issue_id=issue_id, reasoning=reasoning, dismissed_by=dismissed_by,
        )
        session.dismissals.append(dismissal)
        self.broker.publish("issue_dismissed", {
            "session_id": session_id, "issue_id": issue_id,
        })
        if self.on_issue_dismissed is not None:
            self.on_issue_dismissed(session_id, issue_id)
        self.persist()
        return {"status": "dismissed", "issue_id": issue_id}

    def get_actionable_issues(self, session_id: str) -> dict:
        """Return unresolved fix_required issues grouped by file."""
        session = self.get_session(session_id)

        addressed_ids: set[str] = set()
        for fc in session.fix_commits:
            addressed_ids.update(fc.issues_addressed)

        dismissed_ids = {d.issue_id for d in session.dismissals}

        actionable: list[dict] = []
        by_file: dict[str, list] = {}

        for issue in session.issues:
            if issue.consensus_type != "fix_required":
                continue
            entry = {
                "id": issue.id,
                "title": issue.title,
                "severity": (issue.final_severity or issue.severity).value,
                "file": issue.file,
                "line_start": issue.line_start,
                "line_end": issue.line_end,
                "description": issue.description,
                "suggestion": issue.suggestion,
                "addressed": issue.id in addressed_ids,
                "dismissed": issue.id in dismissed_ids,
            }
            actionable.append(entry)
            by_file.setdefault(issue.file, []).append(entry)

        return {
            "session_id": session_id,
            "total": len(actionable),
            "unaddressed": len([a for a in actionable if not a["addressed"]]),
            "issues": actionable,
            "by_file": by_file,
        }

    def _extract_mentions(self, text: str, session: ReviewSession) -> list[str]:
        """Extract @model mentions from free-form text."""
        if not text:
            return []
        mentioned = re.findall(r"@([A-Za-z0-9_-]+)", text)
        valid_ids = {m.id for m in session.config.models}
        return [m for m in mentioned if m in valid_ids]
