"""Git diff collection and parsing."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from ai_review.models import DiffFile

# Pattern to match diff file headers: diff --git a/path b/path
_DIFF_HEADER = re.compile(r"^diff --git a/(.+?) b/(.+?)$", re.MULTILINE)

# Pattern to match stat lines like: 10 insertions(+), 3 deletions(-)
_STAT_LINE = re.compile(
    r"^\s*(\d+)\s+file.+?(\d+)\s+insertion.+?(\d+)\s+deletion", re.MULTILINE
)

# Per-file numstat: additions deletions filename
_NUMSTAT_LINE = re.compile(r"^(\d+|-)\t(\d+|-)\t(.+)$", re.MULTILINE)


async def get_current_branch(repo_path: str | Path | None = None) -> str:
    """Return the current branch name (or 'HEAD' on detached head)."""
    cwd = str(repo_path) if repo_path else None
    proc = await asyncio.create_subprocess_exec(
        "git", "rev-parse", "--abbrev-ref", "HEAD",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    stdout, _ = await proc.communicate()
    return stdout.decode().strip() or "HEAD"


async def validate_repo(repo_path: str | Path) -> dict:
    """Validate a git repository path.

    Returns {'valid': bool, 'root': str, 'current_branch': str, 'error'?: str}.
    """
    cwd = str(repo_path)
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "rev-parse", "--show-toplevel",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            return {"valid": False, "root": "", "current_branch": "", "error": stderr.decode().strip()}
        root = stdout.decode().strip()
        branch = await get_current_branch(root)
        return {"valid": True, "root": root, "current_branch": branch}
    except (FileNotFoundError, OSError) as e:
        return {"valid": False, "root": "", "current_branch": "", "error": str(e)}


async def list_branches(repo_path: str | Path | None = None) -> list[dict]:
    """List local and remote branches.

    Returns [{'name': 'main', 'type': 'local'}, {'name': 'origin/main', 'type': 'remote'}, ...].
    """
    cwd = str(repo_path) if repo_path else None
    proc = await asyncio.create_subprocess_exec(
        "git", "branch", "-a", "--format=%(refname:short)",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    stdout, _ = await proc.communicate()
    branches: list[dict] = []
    for line in stdout.decode().strip().splitlines():
        name = line.strip()
        if not name:
            continue
        if name.endswith("/HEAD"):
            continue
        if "/" in name and not name.startswith("refs/"):
            branches.append({"name": name, "type": "remote"})
        else:
            branches.append({"name": name, "type": "local"})
    return branches


async def collect_diff(
    base: str = "main", repo_path: str | Path | None = None, *, head: str = "HEAD",
) -> list[DiffFile]:
    """Run git diff and parse into DiffFile list."""
    cwd = str(repo_path) if repo_path else None
    diff_range = f"{base}...{head}"

    # Get numstat for per-file additions/deletions
    numstat_proc = await asyncio.create_subprocess_exec(
        "git", "diff", diff_range, "--numstat",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    numstat_out, _ = await numstat_proc.communicate()

    # Get full diff for content
    diff_proc = await asyncio.create_subprocess_exec(
        "git", "diff", diff_range,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    diff_out, _ = await diff_proc.communicate()

    numstat_text = numstat_out.decode()
    diff_text = diff_out.decode()

    return parse_diff(numstat_text, diff_text)


def parse_diff(numstat_text: str, diff_text: str) -> list[DiffFile]:
    """Parse git diff output into DiffFile list."""
    # Parse numstat
    stats: dict[str, tuple[int, int]] = {}
    for m in _NUMSTAT_LINE.finditer(numstat_text):
        adds = int(m.group(1)) if m.group(1) != "-" else 0
        dels = int(m.group(2)) if m.group(2) != "-" else 0
        path = m.group(3)
        stats[path] = (adds, dels)

    # Split diff by file
    file_diffs = _split_diff_by_file(diff_text)

    files: list[DiffFile] = []
    for path, content in file_diffs.items():
        adds, dels = stats.get(path, (0, 0))
        files.append(DiffFile(path=path, additions=adds, deletions=dels, content=content))

    # Include files from numstat that didn't appear in diff (binary, etc.)
    for path, (adds, dels) in stats.items():
        if path not in file_diffs:
            files.append(DiffFile(path=path, additions=adds, deletions=dels, content=""))

    return files


def _split_diff_by_file(diff_text: str) -> dict[str, str]:
    """Split a unified diff into per-file sections."""
    result: dict[str, str] = {}
    positions = list(_DIFF_HEADER.finditer(diff_text))

    for i, m in enumerate(positions):
        path = m.group(2)
        start = m.start()
        end = positions[i + 1].start() if i + 1 < len(positions) else len(diff_text)
        result[path] = diff_text[start:end].strip()

    return result


def get_diff_summary(files: list[DiffFile]) -> dict:
    """Create a summary of the diff."""
    total_adds = sum(f.additions for f in files)
    total_dels = sum(f.deletions for f in files)
    return {
        "files_changed": len(files),
        "additions": total_adds,
        "deletions": total_dels,
        "file_list": [f.path for f in files],
    }
