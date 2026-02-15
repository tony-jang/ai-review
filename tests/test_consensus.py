"""Tests for consensus judgment."""

import pytest

from ai_review.consensus import (
    apply_consensus,
    check_consensus,
    determine_consensus_type,
    determine_final_severity,
)
from ai_review.models import Issue, Opinion, OpinionAction, Severity


def _make_opinion(
    model_id: str,
    action: OpinionAction,
    severity: Severity | None = None,
    confidence: float = 1.0,
) -> Opinion:
    return Opinion(
        model_id=model_id,
        action=action,
        reasoning=f"{model_id} says {action.value}",
        suggested_severity=severity,
        confidence=confidence,
    )


def _make_issue_with_thread(opinions: list[Opinion]) -> Issue:
    return Issue(
        title="Test issue",
        severity=Severity.HIGH,
        file="test.py",
        thread=opinions,
    )


class TestCheckConsensus:
    def test_consensus_with_agrees(self):
        opinions = [
            _make_opinion("opus", OpinionAction.RAISE, Severity.HIGH),
            _make_opinion("gpt", OpinionAction.FIX_REQUIRED, Severity.HIGH),
            _make_opinion("gemini", OpinionAction.FIX_REQUIRED, Severity.HIGH),
        ]
        issue = _make_issue_with_thread(opinions)
        assert check_consensus(issue, threshold=2) is True

    def test_no_consensus_below_threshold(self):
        opinions = [
            _make_opinion("opus", OpinionAction.RAISE, Severity.HIGH),
        ]
        issue = _make_issue_with_thread(opinions)
        assert check_consensus(issue, threshold=2) is False

    def test_consensus_with_disagrees(self):
        opinions = [
            _make_opinion("opus", OpinionAction.RAISE, Severity.HIGH),
            _make_opinion("gpt", OpinionAction.NO_FIX),
            _make_opinion("gemini", OpinionAction.NO_FIX),
        ]
        issue = _make_issue_with_thread(opinions)
        assert check_consensus(issue, threshold=2) is True

    def test_mixed_no_consensus(self):
        opinions = [
            _make_opinion("opus", OpinionAction.RAISE, Severity.HIGH),
            _make_opinion("gpt", OpinionAction.NO_FIX),
        ]
        issue = _make_issue_with_thread(opinions)
        # 1 agree (raise), 1 disagree → no consensus at threshold 2
        assert check_consensus(issue, threshold=2) is False

    def test_empty_thread(self):
        issue = _make_issue_with_thread([])
        assert check_consensus(issue) is False

    def test_clarify_doesnt_count(self):
        opinions = [
            _make_opinion("opus", OpinionAction.RAISE, Severity.HIGH),
            _make_opinion("gpt", OpinionAction.COMMENT),
        ]
        issue = _make_issue_with_thread(opinions)
        assert check_consensus(issue, threshold=2) is False


class TestDetermineFinalSeverity:
    def test_majority_agrees(self):
        opinions = [
            _make_opinion("opus", OpinionAction.RAISE, Severity.CRITICAL),
            _make_opinion("gpt", OpinionAction.FIX_REQUIRED, Severity.HIGH),
            _make_opinion("gemini", OpinionAction.FIX_REQUIRED, Severity.HIGH),
        ]
        issue = _make_issue_with_thread(opinions)
        severity = determine_final_severity(issue)
        assert severity == Severity.HIGH  # majority voted HIGH

    def test_majority_disagrees(self):
        opinions = [
            _make_opinion("opus", OpinionAction.RAISE, Severity.HIGH),
            _make_opinion("gpt", OpinionAction.NO_FIX),
            _make_opinion("gemini", OpinionAction.NO_FIX),
        ]
        issue = _make_issue_with_thread(opinions)
        severity = determine_final_severity(issue)
        assert severity == Severity.DISMISSED

    def test_no_severity_votes(self):
        opinions = [
            _make_opinion("opus", OpinionAction.RAISE, None),
            _make_opinion("gpt", OpinionAction.FIX_REQUIRED, None),
        ]
        issue = _make_issue_with_thread(opinions)
        issue.severity = Severity.MEDIUM
        severity = determine_final_severity(issue)
        assert severity == Severity.MEDIUM  # falls back to original

    def test_single_vote(self):
        opinions = [
            _make_opinion("opus", OpinionAction.RAISE, Severity.CRITICAL),
        ]
        issue = _make_issue_with_thread(opinions)
        severity = determine_final_severity(issue)
        assert severity == Severity.CRITICAL


class TestApplyConsensus:
    def test_applies_to_all_issues(self):
        issue1 = _make_issue_with_thread([
            _make_opinion("opus", OpinionAction.RAISE, Severity.HIGH),
            _make_opinion("gpt", OpinionAction.FIX_REQUIRED, Severity.HIGH),
        ])
        issue2 = _make_issue_with_thread([
            _make_opinion("opus", OpinionAction.RAISE, Severity.LOW),
            _make_opinion("gpt", OpinionAction.NO_FIX),
            _make_opinion("gemini", OpinionAction.NO_FIX),
        ])

        result = apply_consensus([issue1, issue2], threshold=2)

        assert result[0].consensus is True
        assert result[0].final_severity == Severity.HIGH

        assert result[1].consensus is True
        assert result[1].final_severity == Severity.DISMISSED

    def test_no_consensus_leaves_severity_none(self):
        issue = _make_issue_with_thread([
            _make_opinion("opus", OpinionAction.RAISE, Severity.MEDIUM),
        ])
        issue.severity = Severity.MEDIUM

        result = apply_consensus([issue], threshold=2)
        assert result[0].consensus is False
        assert result[0].final_severity is None
        assert result[0].consensus_type is None

    def test_empty_list(self):
        assert apply_consensus([]) == []

    def test_consensus_type_fix_required(self):
        issue = _make_issue_with_thread([
            _make_opinion("opus", OpinionAction.RAISE, Severity.HIGH),
            _make_opinion("gpt", OpinionAction.FIX_REQUIRED, Severity.HIGH),
        ])
        apply_consensus([issue], threshold=2)
        assert issue.consensus is True
        assert issue.consensus_type == "fix_required"

    def test_consensus_type_dismissed(self):
        issue = _make_issue_with_thread([
            _make_opinion("opus", OpinionAction.RAISE, Severity.HIGH),
            _make_opinion("gpt", OpinionAction.NO_FIX),
            _make_opinion("gemini", OpinionAction.NO_FIX),
        ])
        apply_consensus([issue], threshold=2)
        assert issue.consensus is True
        assert issue.consensus_type == "dismissed"


class TestWeightedConsensus:
    """Tests for confidence-weighted voting."""

    def test_high_confidence_reaches_threshold(self):
        opinions = [
            _make_opinion("a", OpinionAction.RAISE, Severity.HIGH, confidence=1.0),
            _make_opinion("b", OpinionAction.FIX_REQUIRED, Severity.HIGH, confidence=1.0),
        ]
        issue = _make_issue_with_thread(opinions)
        assert check_consensus(issue, threshold=2.0) is True

    def test_low_confidence_below_threshold(self):
        """Two votes with confidence=0.5 each = 1.0 total, below threshold=2."""
        opinions = [
            _make_opinion("a", OpinionAction.RAISE, Severity.HIGH, confidence=0.5),
            _make_opinion("b", OpinionAction.FIX_REQUIRED, Severity.HIGH, confidence=0.5),
        ]
        issue = _make_issue_with_thread(opinions)
        assert check_consensus(issue, threshold=2.0) is False

    def test_mixed_confidence_reaches_threshold(self):
        """1.0 + 0.5 + 0.8 = 2.3, above threshold=2."""
        opinions = [
            _make_opinion("a", OpinionAction.RAISE, Severity.HIGH, confidence=1.0),
            _make_opinion("b", OpinionAction.FIX_REQUIRED, Severity.HIGH, confidence=0.5),
            _make_opinion("c", OpinionAction.FIX_REQUIRED, Severity.HIGH, confidence=0.8),
        ]
        issue = _make_issue_with_thread(opinions)
        assert check_consensus(issue, threshold=2.0) is True

    def test_high_confidence_no_fix_wins(self):
        """Two confident no_fix vs one raise."""
        opinions = [
            _make_opinion("a", OpinionAction.RAISE, Severity.HIGH, confidence=1.0),
            _make_opinion("b", OpinionAction.NO_FIX, confidence=1.0),
            _make_opinion("c", OpinionAction.NO_FIX, confidence=1.0),
        ]
        issue = _make_issue_with_thread(opinions)
        assert check_consensus(issue, threshold=2.0) is True
        assert determine_consensus_type(issue) == "dismissed"

    def test_confidence_clamped_to_01(self):
        """Out-of-range confidence is clamped."""
        opinions = [
            _make_opinion("a", OpinionAction.RAISE, Severity.HIGH, confidence=5.0),
            _make_opinion("b", OpinionAction.FIX_REQUIRED, Severity.HIGH, confidence=-1.0),
        ]
        issue = _make_issue_with_thread(opinions)
        # 1.0 + 0.0 = 1.0, below threshold=2
        assert check_consensus(issue, threshold=2.0) is False

    def test_severity_tie_picks_higher(self):
        """When severity votes tie, pick the more severe one (conservative)."""
        opinions = [
            _make_opinion("a", OpinionAction.RAISE, Severity.MEDIUM, confidence=1.0),
            _make_opinion("b", OpinionAction.FIX_REQUIRED, Severity.HIGH, confidence=1.0),
        ]
        issue = _make_issue_with_thread(opinions)
        severity = determine_final_severity(issue)
        assert severity == Severity.HIGH

    def test_weighted_severity_vote(self):
        """Higher confidence severity wins even with fewer votes."""
        opinions = [
            _make_opinion("a", OpinionAction.RAISE, Severity.MEDIUM, confidence=0.3),
            _make_opinion("b", OpinionAction.FIX_REQUIRED, Severity.HIGH, confidence=1.0),
        ]
        issue = _make_issue_with_thread(opinions)
        severity = determine_final_severity(issue)
        assert severity == Severity.HIGH

    def test_comment_does_not_affect_consensus(self):
        """Comments do not contribute to fix/no_fix totals."""
        opinions = [
            _make_opinion("a", OpinionAction.RAISE, Severity.HIGH, confidence=1.0),
            _make_opinion("b", OpinionAction.COMMENT, confidence=1.0),
            _make_opinion("c", OpinionAction.COMMENT, confidence=1.0),
        ]
        issue = _make_issue_with_thread(opinions)
        assert check_consensus(issue, threshold=2.0) is False

    def test_only_first_vote_per_model_counts(self):
        """Duplicate votes from same model are ignored."""
        opinions = [
            _make_opinion("a", OpinionAction.RAISE, Severity.HIGH, confidence=1.0),
            _make_opinion("a", OpinionAction.FIX_REQUIRED, Severity.HIGH, confidence=1.0),
            _make_opinion("b", OpinionAction.FIX_REQUIRED, Severity.HIGH, confidence=1.0),
        ]
        issue = _make_issue_with_thread(opinions)
        # a counted once (1.0) + b (1.0) = 2.0
        assert check_consensus(issue, threshold=2.0) is True

    def test_backward_compat_default_confidence(self):
        """Opinions without explicit confidence default to 1.0."""
        op = Opinion(
            model_id="test",
            action=OpinionAction.FIX_REQUIRED,
            reasoning="test",
        )
        assert op.confidence == 1.0
