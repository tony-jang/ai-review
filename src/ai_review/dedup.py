"""Issue deduplication — merge similar issues from different reviewers."""

from __future__ import annotations

from ai_review.models import Issue, Opinion, OpinionAction, Severity


def deduplicate_issues(issues: list[Issue]) -> list[Issue]:
    """Deduplicate issues based on file, line, and title similarity.

    Returns a new list with duplicates merged into the original issue's thread.

    NOTE: Dedup is temporarily disabled — the current word-overlap algorithm
    does not work with Korean titles.  Returns issues as-is until a better
    strategy (char n-gram / line-range overlap / LLM) is implemented.
    """
    return list(issues)


def _is_duplicate(a: Issue, b: Issue) -> bool:
    """Check if two issues are duplicates."""
    # Same file is required
    if a.file != b.file:
        return False

    # Same line (if both have lines)
    if a.line is not None and b.line is not None:
        if abs(a.line - b.line) <= 5:
            return _title_similar(a.title, b.title)

    # Title similarity alone (same file)
    return _title_similar(a.title, b.title)


def _title_similar(a: str, b: str) -> bool:
    """Simple word-overlap based similarity check."""
    words_a = set(a.lower().split())
    words_b = set(b.lower().split())

    if not words_a or not words_b:
        return False

    overlap = len(words_a & words_b)
    total = min(len(words_a), len(words_b))

    return overlap / total >= 0.5 if total > 0 else False


def _merge_issues(primary: Issue, duplicates: list[Issue]) -> Issue:
    """Merge duplicate issues into the primary, keeping the highest severity."""
    # Use highest severity
    severity_order = [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW]
    best_severity = primary.severity
    for dup in duplicates:
        if severity_order.index(dup.severity) < severity_order.index(best_severity):
            best_severity = dup.severity

    primary.severity = best_severity

    # Merge threads (add duplicate raisers as "agree" opinions).
    # Do not add self-agree or duplicate-agree from the same model.
    existing_models = {op.model_id for op in primary.thread}
    for dup in duplicates:
        if dup.raised_by in existing_models:
            continue
        primary.thread.append(
            Opinion(
                model_id=dup.raised_by,
                action=OpinionAction.FIX_REQUIRED,
                reasoning=f"[Merged duplicate] {dup.description}",
                suggested_severity=dup.severity,
                turn=primary.turn,
            )
        )
        existing_models.add(dup.raised_by)

    return primary
