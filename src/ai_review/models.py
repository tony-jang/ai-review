"""Data models for AI Review System."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, Field


class Severity(str, enum.Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    DISMISSED = "dismissed"


class SessionStatus(str, enum.Enum):
    IDLE = "idle"
    COLLECTING = "collecting"
    REVIEWING = "reviewing"
    DEDUP = "dedup"
    DELIBERATING = "deliberating"
    COMPLETE = "complete"


class OpinionAction(str, enum.Enum):
    RAISE = "raise"
    FIX_REQUIRED = "fix_required"
    NO_FIX = "no_fix"
    COMMENT = "comment"

    # Backward compatibility aliases
    @classmethod
    def _missing_(cls, value: object):
        aliases = {"agree": cls.FIX_REQUIRED, "disagree": cls.NO_FIX, "clarify": cls.COMMENT}
        if isinstance(value, str) and value.lower() in aliases:
            return aliases[value.lower()]
        return None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return uuid.uuid4().hex[:12]


# --- Diff ---


class DiffFile(BaseModel):
    path: str
    additions: int = 0
    deletions: int = 0
    content: str = ""


# --- Knowledge ---


class Knowledge(BaseModel):
    conventions: str = ""
    decisions: str = ""
    ignore_rules: str = ""
    review_examples: str = ""
    extra: dict[str, str] = Field(default_factory=dict)


# --- Review ---


class RawIssue(BaseModel):
    title: str
    severity: Severity
    file: str
    line: int | None = None
    line_start: int | None = None
    line_end: int | None = None
    description: str
    suggestion: str = ""


class Review(BaseModel):
    model_id: str
    issues: list[RawIssue]
    summary: str = ""
    turn: int = 0
    submitted_at: datetime = Field(default_factory=_utcnow)


# --- Issue & Opinion ---


class Opinion(BaseModel):
    model_id: str
    action: OpinionAction
    reasoning: str
    suggested_severity: Severity | None = None
    confidence: float = 1.0
    turn: int = 0
    mentions: list[str] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=_utcnow)


class AssistMessage(BaseModel):
    role: str  # "user" or "assistant"
    content: str
    timestamp: datetime = Field(default_factory=_utcnow)


class AgentChatMessage(BaseModel):
    role: str  # "user" or "assistant"
    content: str
    timestamp: datetime = Field(default_factory=_utcnow)


class Issue(BaseModel):
    id: str = Field(default_factory=_uuid)
    title: str
    severity: Severity
    file: str
    line: int | None = None
    line_start: int | None = None
    line_end: int | None = None
    description: str = ""
    suggestion: str = ""
    raised_by: str = ""
    thread: list[Opinion] = Field(default_factory=list)
    consensus: bool | None = None
    consensus_type: str | None = None  # "fix_required", "dismissed", "undecided"
    final_severity: Severity | None = None
    turn: int = 0
    assist_messages: list[AssistMessage] = Field(default_factory=list)


# --- Session Config ---


class ModelConfig(BaseModel):
    id: str
    client_type: str = "claude-code"
    provider: str = ""
    model_id: str = ""
    test_endpoint: str = ""
    role: str = ""
    description: str = ""
    color: str = ""
    avatar: str = ""
    system_prompt: str = ""
    temperature: float | None = None
    review_focus: list[str] = Field(default_factory=list)
    enabled: bool = True
    strictness: str = "balanced"  # "strict" | "balanced" | "lenient"


class AgentStatus(str, enum.Enum):
    """Status of an AI agent within a review session.

    Lifecycle: REVIEWING -> SUBMITTED | FAILED | WAITING
    """

    WAITING = "waiting"       # Deliberation round: opinion not yet submitted (non-fatal)
    REVIEWING = "reviewing"   # Actively processing a review or deliberation prompt
    SUBMITTED = "submitted"   # Successfully submitted review or opinion
    FAILED = "failed"         # Error or completed without submitting


class AgentTaskType(str, enum.Enum):
    REVIEW = "review"
    DELIBERATION = "deliberation"


class MergeDecision(str, enum.Enum):
    MERGEABLE = "mergeable"
    NOT_MERGEABLE = "not_mergeable"
    NEEDS_DISCUSSION = "needs_discussion"


class OverallReview(BaseModel):
    model_id: str
    task_type: AgentTaskType = AgentTaskType.REVIEW
    turn: int = 0
    merge_decision: MergeDecision = MergeDecision.NEEDS_DISCUSSION
    summary: str = ""
    highlights: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    submitted_at: datetime = Field(default_factory=_utcnow)


class AgentState(BaseModel):
    model_id: str
    status: AgentStatus = AgentStatus.WAITING
    task_type: AgentTaskType = AgentTaskType.REVIEW
    prompt_preview: str = ""
    prompt_full: str = ""
    started_at: datetime | None = None
    submitted_at: datetime | None = None
    last_reason: str = ""
    last_output: str = ""
    last_error: str = ""
    updated_at: datetime | None = None


class SessionConfig(BaseModel):
    models: list[ModelConfig] = Field(default_factory=list)
    max_turns: int = 3
    consensus_threshold: int = 2


# --- Agent Activity ---


class AgentActivity(BaseModel):
    model_id: str
    action: str  # "view_file", "search", "view_tree", "view_diff", "view_context", "view_index"
    target: str  # "src/main.py:10-50", "search:func_name", "tree:src/"
    timestamp: datetime = Field(default_factory=_utcnow)


# --- Implementation Context ---


class ImplementationContext(BaseModel):
    summary: str = ""
    decisions: list[str] = Field(default_factory=list)
    tradeoffs: list[str] = Field(default_factory=list)
    known_issues: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)
    submitted_by: str = ""
    submitted_at: datetime | None = None


# --- Session ---


class ReviewSession(BaseModel):
    id: str = Field(default_factory=_uuid)
    base: str = "main"
    head: str = ""
    repo_path: str = ""
    status: SessionStatus = SessionStatus.IDLE
    diff: list[DiffFile] = Field(default_factory=list)
    knowledge: Knowledge = Field(default_factory=Knowledge)
    reviews: list[Review] = Field(default_factory=list)
    overall_reviews: list[OverallReview] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    agent_access_keys: dict[str, str] = Field(default_factory=dict)
    human_assist_access_key: str | None = None
    client_sessions: dict[str, str] = Field(default_factory=dict)
    agent_states: dict[str, AgentState] = Field(default_factory=dict)
    agent_chats: dict[str, list[AgentChatMessage]] = Field(default_factory=dict)
    agent_activities: list[AgentActivity] = Field(default_factory=list)
    implementation_context: ImplementationContext | None = None
    config: SessionConfig = Field(default_factory=SessionConfig)
    created_at: datetime = Field(default_factory=_utcnow)
