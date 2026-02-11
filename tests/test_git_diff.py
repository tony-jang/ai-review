"""Tests for git diff parsing."""

import pytest

from ai_review.git_diff import get_current_branch, get_diff_summary, parse_diff

SAMPLE_NUMSTAT = """\
10\t3\tsrc/main.py
5\t0\ttests/test_main.py
-\t-\tassets/logo.png
"""

SAMPLE_DIFF = """\
diff --git a/src/main.py b/src/main.py
index abc1234..def5678 100644
--- a/src/main.py
+++ b/src/main.py
@@ -1,3 +1,10 @@
+import os
+
 def main():
-    pass
+    print('hello')
+    name = os.getenv('NAME')
+    if name:
+        print(f'Hello {name}')
+    else:
+        print('Hello World')
+    return 0

diff --git a/tests/test_main.py b/tests/test_main.py
new file mode 100644
index 0000000..1234567
--- /dev/null
+++ b/tests/test_main.py
@@ -0,0 +1,5 @@
+def test_main():
+    assert True
+
+def test_hello():
+    assert True
"""


class TestParseDiff:
    def test_parses_files(self):
        files = parse_diff(SAMPLE_NUMSTAT, SAMPLE_DIFF)
        paths = {f.path for f in files}
        assert "src/main.py" in paths
        assert "tests/test_main.py" in paths
        assert "assets/logo.png" in paths  # binary from numstat

    def test_file_stats(self):
        files = parse_diff(SAMPLE_NUMSTAT, SAMPLE_DIFF)
        main = next(f for f in files if f.path == "src/main.py")
        assert main.additions == 10
        assert main.deletions == 3

    def test_binary_file_stats(self):
        files = parse_diff(SAMPLE_NUMSTAT, SAMPLE_DIFF)
        logo = next(f for f in files if f.path == "assets/logo.png")
        assert logo.additions == 0
        assert logo.deletions == 0
        assert logo.content == ""

    def test_diff_content(self):
        files = parse_diff(SAMPLE_NUMSTAT, SAMPLE_DIFF)
        main = next(f for f in files if f.path == "src/main.py")
        assert "def main():" in main.content
        assert "+import os" in main.content

    def test_empty_diff(self):
        files = parse_diff("", "")
        assert files == []

    def test_numstat_only(self):
        files = parse_diff("3\t1\tfoo.py\n", "")
        assert len(files) == 1
        assert files[0].path == "foo.py"
        assert files[0].additions == 3
        assert files[0].content == ""


class TestGetCurrentBranch:
    @pytest.mark.asyncio
    async def test_returns_string(self):
        result = await get_current_branch()
        assert isinstance(result, str) and len(result) > 0


class TestGetDiffSummary:
    def test_summary(self):
        files = parse_diff(SAMPLE_NUMSTAT, SAMPLE_DIFF)
        summary = get_diff_summary(files)
        assert summary["files_changed"] == 3
        assert summary["additions"] == 15
        assert summary["deletions"] == 3
        assert "src/main.py" in summary["file_list"]

    def test_empty_summary(self):
        summary = get_diff_summary([])
        assert summary["files_changed"] == 0
        assert summary["additions"] == 0
        assert summary["deletions"] == 0
        assert summary["file_list"] == []
