"""State machine for review sessions."""

from __future__ import annotations

from ai_review.models import ReviewSession, SessionStatus

# Valid transitions: from_status -> set of allowed to_statuses
TRANSITIONS: dict[SessionStatus, set[SessionStatus]] = {
    SessionStatus.IDLE: {SessionStatus.COLLECTING},
    SessionStatus.COLLECTING: {SessionStatus.REVIEWING},
    SessionStatus.REVIEWING: {SessionStatus.DEDUP},
    SessionStatus.DEDUP: {SessionStatus.DELIBERATING},
    SessionStatus.DELIBERATING: {SessionStatus.DELIBERATING, SessionStatus.AGENT_RESPONSE, SessionStatus.COMPLETE},
    SessionStatus.AGENT_RESPONSE: {SessionStatus.DELIBERATING, SessionStatus.FIXING, SessionStatus.COMPLETE},
    SessionStatus.FIXING: {SessionStatus.VERIFYING},
    SessionStatus.VERIFYING: {SessionStatus.FIXING, SessionStatus.COMPLETE},
    SessionStatus.COMPLETE: set(),
}


class InvalidTransitionError(Exception):
    def __init__(self, from_status: SessionStatus, to_status: SessionStatus) -> None:
        self.from_status = from_status
        self.to_status = to_status
        super().__init__(
            f"Invalid transition: {from_status.value} -> {to_status.value}"
        )


def transition(session: ReviewSession, to: SessionStatus) -> ReviewSession:
    """Transition session to a new status. Raises InvalidTransitionError if not allowed."""
    allowed = TRANSITIONS.get(session.status, set())
    if to not in allowed:
        raise InvalidTransitionError(session.status, to)
    session.status = to
    return session


def can_transition(session: ReviewSession, to: SessionStatus) -> bool:
    """Check if a transition is valid without performing it."""
    allowed = TRANSITIONS.get(session.status, set())
    return to in allowed
