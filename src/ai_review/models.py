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
    AGREE = "agree"
    DISAGREE = "disagree"
    CLARIFY = "clarify"


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
    description: str
    suggestion: str = ""


class Review(BaseModel):
    model_id: str
    issues: list[RawIssue]
    summary: str = ""
    submitted_at: datetime = Field(default_factory=_utcnow)


# --- Issue & Opinion ---


class Opinion(BaseModel):
    model_id: str
    action: OpinionAction
    reasoning: str
    suggested_severity: Severity | None = None
    timestamp: datetime = Field(default_factory=_utcnow)


class AssistMessage(BaseModel):
    role: str  # "user" or "assistant"
    content: str
    timestamp: datetime = Field(default_factory=_utcnow)


class Issue(BaseModel):
    id: str = Field(default_factory=_uuid)
    title: str
    severity: Severity
    file: str
    line: int | None = None
    description: str = ""
    suggestion: str = ""
    raised_by: str = ""
    thread: list[Opinion] = Field(default_factory=list)
    consensus: bool | None = None
    final_severity: Severity | None = None
    turn: int = 0
    assist_messages: list[AssistMessage] = Field(default_factory=list)


# --- Session Config ---


class ModelConfig(BaseModel):
    id: str
    client_type: str = "claude-code"
    provider: str = ""
    model_id: str = ""
    role: str = ""


class AgentStatus(str, enum.Enum):
    WAITING = "waiting"
    REVIEWING = "reviewing"
    SUBMITTED = "submitted"
    FAILED = "failed"


class AgentTaskType(str, enum.Enum):
    REVIEW = "review"
    DELIBERATION = "deliberation"


class AgentState(BaseModel):
    model_id: str
    status: AgentStatus = AgentStatus.WAITING
    task_type: AgentTaskType = AgentTaskType.REVIEW
    prompt_preview: str = ""
    started_at: datetime | None = None
    submitted_at: datetime | None = None


class SessionConfig(BaseModel):
    models: list[ModelConfig] = Field(default_factory=list)
    max_turns: int = 3
    consensus_threshold: int = 2


# --- Session ---


class ReviewSession(BaseModel):
    id: str = Field(default_factory=_uuid)
    base: str = "main"
    head: str = ""
    status: SessionStatus = SessionStatus.IDLE
    diff: list[DiffFile] = Field(default_factory=list)
    knowledge: Knowledge = Field(default_factory=Knowledge)
    reviews: list[Review] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    client_sessions: dict[str, str] = Field(default_factory=dict)
    agent_states: dict[str, AgentState] = Field(default_factory=dict)
    config: SessionConfig = Field(default_factory=SessionConfig)
    created_at: datetime = Field(default_factory=_utcnow)
