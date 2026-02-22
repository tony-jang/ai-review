"""Consensus judgment for issue deliberation.

Uses confidence-weighted voting to determine consensus.
Each opinion's weight is its confidence value (0.0–1.0, default 1.0).
"""

from __future__ import annotations

from collections import Counter

from ai_review.models import Issue, OpinionAction, Severity

# Actions that count toward the "no fix" direction in voting
_NO_FIX_ACTIONS = frozenset({
    OpinionAction.NO_FIX,
    OpinionAction.FALSE_POSITIVE,
    OpinionAction.WITHDRAW,
})

# Severity ordering for conservative tie-breaking (higher index = more severe)
_SEVERITY_ORDER: dict[Severity, int] = {
    Severity.LOW: 0,
    Severity.MEDIUM: 1,
    Severity.HIGH: 2,
    Severity.CRITICAL: 3,
}


def check_consensus(
    issue: Issue, threshold: float = 2.0, *, total_voters: int = 0,
) -> bool:
    """Check if an issue has reached consensus via weighted voting.

    Each model's first decisive vote (RAISE/FIX_REQUIRED or NO_FIX) is counted
    with its confidence as weight.  COMMENT votes are excluded from the tally.

    Returns True if weighted fix_required >= threshold OR weighted no_fix >= threshold.
    When *total_voters* > 0 and all voters have responded, returns True unconditionally
    (majority fallback to prevent deadlock when confidence weights are too low).
    """
    if not issue.thread:
        return False

    weighted_fix = 0.0
    weighted_no_fix = 0.0
    voters: set[str] = set()

    for op in issue.thread:
        if op.action == OpinionAction.STATUS_CHANGE:
            continue
        if op.model_id in voters:
            continue
        if op.action in (OpinionAction.FIX_REQUIRED, OpinionAction.RAISE):
            weighted_fix += max(0.0, min(op.confidence, 1.0))
            voters.add(op.model_id)
        elif op.action in _NO_FIX_ACTIONS:
            weighted_no_fix += max(0.0, min(op.confidence, 1.0))
            voters.add(op.model_id)

    if weighted_fix >= threshold or weighted_no_fix >= threshold:
        return True

    # Majority fallback: all voters responded but threshold not met
    if total_voters > 0 and len(voters) >= total_voters:
        return True

    return False


def determine_consensus_type(issue: Issue) -> str:
    """Determine the consensus type: 'fix_required', 'dismissed', or 'undecided'."""
    weighted_fix = 0.0
    weighted_no_fix = 0.0
    voters: set[str] = set()

    for op in issue.thread:
        if op.action == OpinionAction.STATUS_CHANGE:
            continue
        if op.model_id in voters:
            continue
        if op.action in (OpinionAction.FIX_REQUIRED, OpinionAction.RAISE):
            weighted_fix += max(0.0, min(op.confidence, 1.0))
            voters.add(op.model_id)
        elif op.action in _NO_FIX_ACTIONS:
            weighted_no_fix += max(0.0, min(op.confidence, 1.0))
            voters.add(op.model_id)

    if weighted_fix > weighted_no_fix:
        return "fix_required"
    if weighted_no_fix > weighted_fix:
        return "dismissed"
    return "undecided"


def determine_final_severity(issue: Issue) -> Severity:
    """Determine the final severity by confidence-weighted voting.

    If majority weighted vote is NO_FIX → DISMISSED.
    Otherwise → confidence-weighted severity vote with conservative tie-breaking
    (higher severity wins on tie).
    Falls back to original severity if no severity votes.
    """
    weighted_fix = 0.0
    weighted_no_fix = 0.0
    severity_weights: dict[Severity, float] = {}
    voters: set[str] = set()

    for op in issue.thread:
        if op.action == OpinionAction.STATUS_CHANGE:
            continue
        if op.model_id in voters:
            continue
        voters.add(op.model_id)
        conf = max(0.0, min(op.confidence, 1.0))

        if op.action in _NO_FIX_ACTIONS:
            weighted_no_fix += conf
        elif op.action in (OpinionAction.RAISE, OpinionAction.FIX_REQUIRED):
            weighted_fix += conf
            if op.suggested_severity:
                severity_weights[op.suggested_severity] = (
                    severity_weights.get(op.suggested_severity, 0.0) + conf
                )

    # If more weighted no_fix than fix → dismissed
    if weighted_no_fix > weighted_fix:
        return Severity.DISMISSED

    # Confidence-weighted severity vote with conservative tie-breaking
    if severity_weights:
        max_weight = max(severity_weights.values())
        # Among severities with the max weight, pick the highest (conservative)
        candidates = [s for s, w in severity_weights.items() if w == max_weight]
        return max(candidates, key=lambda s: _SEVERITY_ORDER.get(s, 0))

    return issue.severity


def apply_consensus(
    issues: list[Issue],
    threshold: int | float = 2,
    *,
    total_voters: int = 0,
) -> list[Issue]:
    """Apply consensus checks to all issues and set final severity + consensus_type."""
    for issue in issues:
        if issue.consensus and issue.consensus_type == "closed":
            continue  # already closed via WITHDRAW — skip
        issue.consensus = check_consensus(
            issue, float(threshold), total_voters=total_voters,
        )
        if issue.consensus:
            issue.consensus_type = determine_consensus_type(issue)
            issue.final_severity = determine_final_severity(issue)
        else:
            issue.consensus_type = None
            issue.final_severity = None

    return issues
