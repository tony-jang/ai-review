"""Tests for issue deduplication."""

import pytest

from ai_review.dedup import _is_duplicate, _title_similar, deduplicate_issues
from ai_review.models import Issue, Opinion, OpinionAction, Severity


def _make_issue(title: str, file: str, line: int | None = None, raised_by: str = "opus") -> Issue:
    return Issue(
        title=title,
        severity=Severity.HIGH,
        file=file,
        line=line,
        description=f"Description for {title}",
        raised_by=raised_by,
        thread=[
            Opinion(
                model_id=raised_by,
                action=OpinionAction.RAISE,
                reasoning=f"Description for {title}",
                suggested_severity=Severity.HIGH,
            )
        ],
    )


class TestTitleSimilarity:
    def test_identical(self):
        assert _title_similar("Missing error handling", "Missing error handling") is True

    def test_similar(self):
        assert _title_similar("Missing error handling in auth", "Missing error handling") is True

    def test_different(self):
        assert _title_similar("SQL injection vulnerability", "Missing error handling") is False

    def test_empty(self):
        assert _title_similar("", "something") is False
        assert _title_similar("something", "") is False


class TestIsDuplicate:
    def test_same_file_same_line_similar_title(self):
        a = _make_issue("Missing auth check", "api.py", 42)
        b = _make_issue("Auth check missing", "api.py", 44)
        assert _is_duplicate(a, b) is True

    def test_same_file_different_line_similar_title(self):
        a = _make_issue("Missing auth check", "api.py", 10)
        b = _make_issue("Auth check missing", "api.py", 100)
        # Lines too far apart, but title similar + same file
        assert _is_duplicate(a, b) is True

    def test_different_files(self):
        a = _make_issue("Missing auth check", "api.py", 42)
        b = _make_issue("Missing auth check", "db.py", 42)
        assert _is_duplicate(a, b) is False

    def test_same_file_different_title(self):
        a = _make_issue("SQL injection", "api.py", 42)
        b = _make_issue("Performance issue", "api.py", 42)
        assert _is_duplicate(a, b) is False


class TestDeduplicateIssues:
    def test_no_duplicates(self):
        issues = [
            _make_issue("SQL injection", "api.py", raised_by="opus"),
            _make_issue("Performance issue", "db.py", raised_by="gpt"),
        ]
        result = deduplicate_issues(issues)
        assert len(result) == 2

    def test_merges_duplicates(self):
        issues = [
            _make_issue("Missing auth check", "api.py", 42, "opus"),
            _make_issue("Auth check missing in api", "api.py", 44, "gpt"),
        ]
        result = deduplicate_issues(issues)
        assert len(result) == 1
        # Thread should include both the raiser + merged agree
        assert len(result[0].thread) == 2
        assert result[0].thread[0].model_id == "opus"
        assert result[0].thread[1].model_id == "gpt"
        assert result[0].thread[1].action == OpinionAction.FIX_REQUIRED

    def test_keeps_highest_severity(self):
        issue_high = _make_issue("Auth issue", "api.py", 42, "opus")
        issue_high.severity = Severity.HIGH

        issue_critical = _make_issue("Auth check missing issue", "api.py", 44, "gpt")
        issue_critical.severity = Severity.CRITICAL

        result = deduplicate_issues([issue_high, issue_critical])
        assert len(result) == 1
        assert result[0].severity == Severity.CRITICAL

    def test_empty_list(self):
        assert deduplicate_issues([]) == []

    def test_three_way_merge(self):
        issues = [
            _make_issue("Missing auth check", "api.py", 42, "opus"),
            _make_issue("Auth check missing", "api.py", 43, "gpt"),
            _make_issue("No auth on endpoint", "api.py", 42, "gemini"),
        ]
        result = deduplicate_issues(issues)
        # "No auth on endpoint" may or may not merge depending on word overlap
        # but "Missing auth check" and "Auth check missing" should merge
        assert len(result) <= 2

    def test_does_not_add_self_agree_when_same_model_duplicates(self):
        issues = [
            _make_issue("Missing auth check", "api.py", 42, "codex1"),
            _make_issue("Auth check missing in endpoint", "api.py", 43, "codex1"),
        ]
        result = deduplicate_issues(issues)
        assert len(result) == 1
        # Keep only the original raise from codex1 (no self-agree line)
        assert len(result[0].thread) == 1
        assert result[0].thread[0].model_id == "codex1"
        assert result[0].thread[0].action == OpinionAction.RAISE
