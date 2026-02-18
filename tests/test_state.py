"""Tests for state machine."""

import pytest

from ai_review.models import ReviewSession, SessionStatus
from ai_review.state import (
    InvalidTransitionError,
    TRANSITIONS,
    can_transition,
    transition,
)


class TestTransitions:
    """Valid transition tests."""

    @pytest.mark.parametrize(
        "from_status,to_status",
        [
            (SessionStatus.IDLE, SessionStatus.COLLECTING),
            (SessionStatus.COLLECTING, SessionStatus.REVIEWING),
            (SessionStatus.REVIEWING, SessionStatus.DEDUP),
            (SessionStatus.DEDUP, SessionStatus.DELIBERATING),
            (SessionStatus.DELIBERATING, SessionStatus.COMPLETE),
            (SessionStatus.DELIBERATING, SessionStatus.FIXING),
            (SessionStatus.AGENT_RESPONSE, SessionStatus.DELIBERATING),
            (SessionStatus.AGENT_RESPONSE, SessionStatus.FIXING),
            (SessionStatus.AGENT_RESPONSE, SessionStatus.COMPLETE),
            (SessionStatus.FIXING, SessionStatus.VERIFYING),
            (SessionStatus.FIXING, SessionStatus.COMPLETE),
            (SessionStatus.VERIFYING, SessionStatus.FIXING),
            (SessionStatus.VERIFYING, SessionStatus.COMPLETE),
        ],
    )
    def test_valid_transitions(self, from_status, to_status):
        session = ReviewSession(status=from_status)
        result = transition(session, to_status)
        assert result.status == to_status

    def test_full_lifecycle(self):
        session = ReviewSession()
        assert session.status == SessionStatus.IDLE

        transition(session, SessionStatus.COLLECTING)
        assert session.status == SessionStatus.COLLECTING

        transition(session, SessionStatus.REVIEWING)
        assert session.status == SessionStatus.REVIEWING

        transition(session, SessionStatus.DEDUP)
        assert session.status == SessionStatus.DEDUP

        transition(session, SessionStatus.DELIBERATING)
        assert session.status == SessionStatus.DELIBERATING

        transition(session, SessionStatus.COMPLETE)
        assert session.status == SessionStatus.COMPLETE


class TestInvalidTransitions:
    """Invalid transition tests."""

    @pytest.mark.parametrize(
        "from_status,to_status",
        [
            (SessionStatus.IDLE, SessionStatus.REVIEWING),
            (SessionStatus.IDLE, SessionStatus.COMPLETE),
            (SessionStatus.COLLECTING, SessionStatus.IDLE),
            (SessionStatus.COLLECTING, SessionStatus.DELIBERATING),
            (SessionStatus.REVIEWING, SessionStatus.IDLE),
            (SessionStatus.REVIEWING, SessionStatus.COLLECTING),
            (SessionStatus.COMPLETE, SessionStatus.IDLE),
            (SessionStatus.COMPLETE, SessionStatus.REVIEWING),
            (SessionStatus.AGENT_RESPONSE, SessionStatus.REVIEWING),
            (SessionStatus.AGENT_RESPONSE, SessionStatus.IDLE),
            (SessionStatus.AGENT_RESPONSE, SessionStatus.DEDUP),
            (SessionStatus.DELIBERATING, SessionStatus.AGENT_RESPONSE),
            (SessionStatus.FIXING, SessionStatus.DELIBERATING),
            (SessionStatus.VERIFYING, SessionStatus.DELIBERATING),
        ],
    )
    def test_invalid_transitions_raise(self, from_status, to_status):
        session = ReviewSession(status=from_status)
        with pytest.raises(InvalidTransitionError) as exc_info:
            transition(session, to_status)
        assert exc_info.value.from_status == from_status
        assert exc_info.value.to_status == to_status

    def test_no_self_transition(self):
        for status in SessionStatus:
            if status == SessionStatus.DELIBERATING:
                continue  # DELIBERATING allows self-transition for multi-turn loops
            session = ReviewSession(status=status)
            with pytest.raises(InvalidTransitionError):
                transition(session, status)

    def test_lifecycle_with_agent_response(self):
        """Backward compat: AGENT_RESPONSE → COMPLETE still works."""
        session = ReviewSession(status=SessionStatus.AGENT_RESPONSE)
        transition(session, SessionStatus.COMPLETE)
        assert session.status == SessionStatus.COMPLETE

    def test_lifecycle_deliberating_to_fixing(self):
        """New flow: DELIBERATING → FIXING directly."""
        session = ReviewSession()
        transition(session, SessionStatus.COLLECTING)
        transition(session, SessionStatus.REVIEWING)
        transition(session, SessionStatus.DEDUP)
        transition(session, SessionStatus.DELIBERATING)
        transition(session, SessionStatus.FIXING)
        assert session.status == SessionStatus.FIXING

    def test_lifecycle_with_fixing_verifying(self):
        session = ReviewSession()
        transition(session, SessionStatus.COLLECTING)
        transition(session, SessionStatus.REVIEWING)
        transition(session, SessionStatus.DEDUP)
        transition(session, SessionStatus.DELIBERATING)
        transition(session, SessionStatus.FIXING)
        assert session.status == SessionStatus.FIXING
        transition(session, SessionStatus.VERIFYING)
        assert session.status == SessionStatus.VERIFYING
        # Re-fix cycle
        transition(session, SessionStatus.FIXING)
        transition(session, SessionStatus.VERIFYING)
        # Complete after verification
        transition(session, SessionStatus.COMPLETE)
        assert session.status == SessionStatus.COMPLETE

    def test_agent_response_to_deliberating_for_dispute(self):
        """Backward compat: AGENT_RESPONSE → DELIBERATING for disputes."""
        session = ReviewSession(status=SessionStatus.AGENT_RESPONSE)
        transition(session, SessionStatus.DELIBERATING)
        assert session.status == SessionStatus.DELIBERATING

    def test_agent_response_to_fixing_compat(self):
        """Backward compat: AGENT_RESPONSE → FIXING still works."""
        session = ReviewSession(status=SessionStatus.AGENT_RESPONSE)
        transition(session, SessionStatus.FIXING)
        assert session.status == SessionStatus.FIXING

    def test_deliberating_self_transition(self):
        session = ReviewSession(status=SessionStatus.DELIBERATING)
        result = transition(session, SessionStatus.DELIBERATING)
        assert result.status == SessionStatus.DELIBERATING

    def test_no_backward_transition(self):
        session = ReviewSession(status=SessionStatus.REVIEWING)
        with pytest.raises(InvalidTransitionError):
            transition(session, SessionStatus.IDLE)


class TestCanTransition:
    def test_valid(self):
        session = ReviewSession(status=SessionStatus.IDLE)
        assert can_transition(session, SessionStatus.COLLECTING) is True

    def test_invalid(self):
        session = ReviewSession(status=SessionStatus.IDLE)
        assert can_transition(session, SessionStatus.COMPLETE) is False

    def test_complete_has_no_transitions(self):
        session = ReviewSession(status=SessionStatus.COMPLETE)
        for status in SessionStatus:
            assert can_transition(session, status) is False


class TestTransitionsCompleteness:
    """Ensure all states are covered in the transition map."""

    def test_all_states_in_transitions(self):
        for status in SessionStatus:
            assert status in TRANSITIONS, f"{status} missing from TRANSITIONS"
