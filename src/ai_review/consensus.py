"""Consensus judgment for issue deliberation."""

from __future__ import annotations

from collections import Counter

from ai_review.models import Issue, OpinionAction, Severity


def check_consensus(issue: Issue, threshold: int = 2) -> bool:
    """Check if an issue has reached consensus.

    Consensus = at least `threshold` models agree (or disagree) on the same action.
    Returns True if consensus is reached, False otherwise.
    """
    if not issue.thread:
        return False

    # Count agree/disagree (exclude raise and clarify)
    agree_count = 0
    disagree_count = 0
    voters: set[str] = set()

    for op in issue.thread:
        if op.model_id in voters and op.action in (OpinionAction.AGREE, OpinionAction.DISAGREE):
            continue  # only count first vote per model
        if op.action == OpinionAction.AGREE or op.action == OpinionAction.RAISE:
            agree_count += 1
            voters.add(op.model_id)
        elif op.action == OpinionAction.DISAGREE:
            disagree_count += 1
            voters.add(op.model_id)

    return agree_count >= threshold or disagree_count >= threshold


def determine_final_severity(issue: Issue) -> Severity:
    """Determine the final severity by majority vote.

    If majority disagrees → DISMISSED.
    Otherwise → most suggested severity (or original).
    """
    disagree_count = 0
    agree_count = 0
    severity_votes: list[Severity] = []

    seen_models: set[str] = set()
    for op in issue.thread:
        if op.model_id in seen_models:
            continue
        seen_models.add(op.model_id)

        if op.action == OpinionAction.DISAGREE:
            disagree_count += 1
        elif op.action in (OpinionAction.RAISE, OpinionAction.AGREE):
            agree_count += 1
            if op.suggested_severity:
                severity_votes.append(op.suggested_severity)

    # If more disagree than agree → dismissed
    if disagree_count > agree_count:
        return Severity.DISMISSED

    # Majority vote on severity
    if severity_votes:
        counter = Counter(severity_votes)
        return counter.most_common(1)[0][0]

    return issue.severity


def apply_consensus(issues: list[Issue], threshold: int = 2) -> list[Issue]:
    """Apply consensus checks to all issues and set final severity."""
    for issue in issues:
        issue.consensus = check_consensus(issue, threshold)
        if issue.consensus:
            issue.final_severity = determine_final_severity(issue)
        else:
            issue.final_severity = issue.severity

    return issues
