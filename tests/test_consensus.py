"""Tests for consensus judgment."""

import pytest

from ai_review.consensus import apply_consensus, check_consensus, determine_final_severity
from ai_review.models import Issue, Opinion, OpinionAction, Severity


def _make_opinion(model_id: str, action: OpinionAction, severity: Severity | None = None) -> Opinion:
    return Opinion(
        model_id=model_id,
        action=action,
        reasoning=f"{model_id} says {action.value}",
        suggested_severity=severity,
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
            _make_opinion("gpt", OpinionAction.AGREE, Severity.HIGH),
            _make_opinion("gemini", OpinionAction.AGREE, Severity.HIGH),
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
            _make_opinion("gpt", OpinionAction.DISAGREE),
            _make_opinion("gemini", OpinionAction.DISAGREE),
        ]
        issue = _make_issue_with_thread(opinions)
        assert check_consensus(issue, threshold=2) is True

    def test_mixed_no_consensus(self):
        opinions = [
            _make_opinion("opus", OpinionAction.RAISE, Severity.HIGH),
            _make_opinion("gpt", OpinionAction.DISAGREE),
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
            _make_opinion("gpt", OpinionAction.CLARIFY),
        ]
        issue = _make_issue_with_thread(opinions)
        assert check_consensus(issue, threshold=2) is False


class TestDetermineFinalSeverity:
    def test_majority_agrees(self):
        opinions = [
            _make_opinion("opus", OpinionAction.RAISE, Severity.CRITICAL),
            _make_opinion("gpt", OpinionAction.AGREE, Severity.HIGH),
            _make_opinion("gemini", OpinionAction.AGREE, Severity.HIGH),
        ]
        issue = _make_issue_with_thread(opinions)
        severity = determine_final_severity(issue)
        assert severity == Severity.HIGH  # majority voted HIGH

    def test_majority_disagrees(self):
        opinions = [
            _make_opinion("opus", OpinionAction.RAISE, Severity.HIGH),
            _make_opinion("gpt", OpinionAction.DISAGREE),
            _make_opinion("gemini", OpinionAction.DISAGREE),
        ]
        issue = _make_issue_with_thread(opinions)
        severity = determine_final_severity(issue)
        assert severity == Severity.DISMISSED

    def test_no_severity_votes(self):
        opinions = [
            _make_opinion("opus", OpinionAction.RAISE, None),
            _make_opinion("gpt", OpinionAction.AGREE, None),
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
            _make_opinion("gpt", OpinionAction.AGREE, Severity.HIGH),
        ])
        issue2 = _make_issue_with_thread([
            _make_opinion("opus", OpinionAction.RAISE, Severity.LOW),
            _make_opinion("gpt", OpinionAction.DISAGREE),
            _make_opinion("gemini", OpinionAction.DISAGREE),
        ])

        result = apply_consensus([issue1, issue2], threshold=2)

        assert result[0].consensus is True
        assert result[0].final_severity == Severity.HIGH

        assert result[1].consensus is True
        assert result[1].final_severity == Severity.DISMISSED

    def test_no_consensus_uses_original_severity(self):
        issue = _make_issue_with_thread([
            _make_opinion("opus", OpinionAction.RAISE, Severity.MEDIUM),
        ])
        issue.severity = Severity.MEDIUM

        result = apply_consensus([issue], threshold=2)
        assert result[0].consensus is False
        assert result[0].final_severity == Severity.MEDIUM

    def test_empty_list(self):
        assert apply_consensus([]) == []
