"""Shared fixtures for AI Review tests."""

import pytest

from ai_review.models import (
    DiffFile,
    Issue,
    Knowledge,
    Opinion,
    OpinionAction,
    RawIssue,
    Review,
    ReviewSession,
    Severity,
    SessionConfig,
)


@pytest.fixture(autouse=True)
def _isolate_session_storage(tmp_path, monkeypatch):
    """Prevent tests from writing sessions to the real project .ai-review/ directory."""
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def sample_diff_files() -> list[DiffFile]:
    return [
        DiffFile(
            path="src/main.py",
            additions=10,
            deletions=3,
            content="@@ -1,3 +1,10 @@\n+import os\n+\n def main():\n-    pass\n+    print('hello')",
        ),
        DiffFile(
            path="tests/test_main.py",
            additions=5,
            deletions=0,
            content="@@ -0,0 +1,5 @@\n+def test_main():\n+    assert True",
        ),
    ]


@pytest.fixture
def sample_knowledge() -> Knowledge:
    return Knowledge(
        conventions="Use snake_case for functions.",
        decisions="We use pytest for testing.",
    )


@pytest.fixture
def sample_raw_issues() -> list[RawIssue]:
    return [
        RawIssue(
            title="Missing error handling",
            severity=Severity.HIGH,
            file="src/main.py",
            line=5,
            description="No try/except around file operations.",
            suggestion="Add try/except block.",
        ),
        RawIssue(
            title="Unused import",
            severity=Severity.LOW,
            file="src/main.py",
            line=1,
            description="os is imported but never used.",
        ),
    ]


@pytest.fixture
def sample_session(sample_diff_files, sample_knowledge) -> ReviewSession:
    return ReviewSession(
        base="main",
        diff=sample_diff_files,
        knowledge=sample_knowledge,
        config=SessionConfig(max_turns=3, consensus_threshold=2),
    )
