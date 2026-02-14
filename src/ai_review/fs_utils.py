"""Filesystem and local opener utilities."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from shutil import which
from typing import Any

from fastapi import HTTPException

from ai_review.session_manager import SessionManager


def pick_directory_native() -> str:
    """Open a native directory picker and return selected path."""
    tk_error: Exception | None = None

    # 1) Try tkinter first (works cross-platform when Tk is installed).
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        try:
            try:
                root.attributes("-topmost", True)
            except Exception:
                pass
            selected = filedialog.askdirectory()
        finally:
            root.destroy()
        return selected or ""
    except Exception as e:
        tk_error = e

    # 2) macOS fallback: use native AppleScript picker when tkinter is unavailable.
    try:
        if sys.platform == "darwin":
            script = (
                'try\n'
                'POSIX path of (choose folder with prompt "리뷰할 폴더를 선택하세요")\n'
                'on error number -128\n'
                'return ""\n'
                'end try'
            )
            proc = subprocess.run(
                ["osascript", "-e", script],
                check=False,
                capture_output=True,
                text=True,
            )
            if proc.returncode == 0:
                return proc.stdout.strip()
            stderr = (proc.stderr or "").strip()
            if "-128" in stderr:
                return ""
            raise RuntimeError(stderr or "osascript picker failed")
    except Exception as e:
        raise RuntimeError(f"native directory picker unavailable: {e}") from e

    raise RuntimeError(f"native directory picker unavailable: {tk_error}")


def resolve_local_path(
    raw_path: str,
    *,
    manager: SessionManager,
    session_id: str | None = None,
) -> Path:
    """Resolve a local path from session/workspace context."""
    target = (raw_path or "").strip()
    if not target:
        raise HTTPException(status_code=400, detail="path is required")

    repo_root = ""
    if session_id:
        try:
            repo_root = manager.get_session(session_id).repo_path or ""
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
    if not repo_root:
        current = manager.current_session
        repo_root = (current.repo_path if current else "") or (manager.repo_path or "")

    p = Path(target).expanduser()
    root = Path(repo_root).expanduser().resolve() if repo_root else Path.cwd().resolve()
    resolved = p.resolve() if p.is_absolute() else (root / p).resolve()
    if repo_root:
        try:
            resolved.relative_to(root)
        except ValueError as e:
            raise HTTPException(status_code=400, detail="path must be within repository") from e
    return resolved


def open_local_path_native(path: Path) -> None:
    """Open a local path using the OS default handler."""
    target = str(path)
    if sys.platform == "darwin":
        proc = subprocess.run(["open", target], check=False, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or "").strip() or "open failed")
        return

    if sys.platform == "win32":
        try:
            os.startfile(target)  # type: ignore[attr-defined]
        except Exception as e:
            raise RuntimeError(str(e)) from e
        return

    proc = subprocess.run(["xdg-open", target], check=False, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or "").strip() or "xdg-open failed")


def _command_exists(cmd: str) -> bool:
    return which(cmd) is not None


def _mac_app_exists(app_name: str) -> bool:
    if sys.platform != "darwin":
        return False
    proc = subprocess.run(["open", "-Ra", app_name], check=False, capture_output=True, text=True)
    return proc.returncode == 0


def _open_with_mac_app(path: Path, app_names: list[str]) -> bool:
    if sys.platform != "darwin":
        return False
    target = str(path)
    for app in app_names:
        if not _mac_app_exists(app):
            continue
        proc = subprocess.run(["open", "-a", app, target], check=False, capture_output=True, text=True)
        if proc.returncode == 0:
            return True
    return False


def list_local_openers() -> list[dict[str, Any]]:
    """Return supported local opener tools and availability."""
    vscode_available = _command_exists("code") or _mac_app_exists("Visual Studio Code")
    idea_available = _command_exists("idea") or _command_exists("idea64.exe") or _mac_app_exists("IntelliJ IDEA") or _mac_app_exists("IntelliJ IDEA CE")
    rider_available = _command_exists("rider") or _command_exists("rider64.exe") or _mac_app_exists("Rider")
    return [
        {"id": "auto", "label": "자동 (파일유형 기반)", "available": vscode_available or idea_available or rider_available},
        {"id": "default", "label": "기본 앱", "available": True},
        {"id": "vscode", "label": "VS Code", "available": vscode_available},
        {"id": "idea", "label": "IntelliJ IDEA", "available": idea_available},
        {"id": "rider", "label": "Rider", "available": rider_available},
    ]


def _pick_auto_opener(path: Path) -> str:
    """Pick best opener by file type and available tools."""
    ext = path.suffix.lower()
    name = path.name.lower()
    vscode_available = _command_exists("code") or _mac_app_exists("Visual Studio Code")
    idea_available = _command_exists("idea") or _command_exists("idea64.exe") or _mac_app_exists("IntelliJ IDEA") or _mac_app_exists("IntelliJ IDEA CE")
    rider_available = _command_exists("rider") or _command_exists("rider64.exe") or _mac_app_exists("Rider")

    if ext in {".cs", ".csproj", ".sln"} and rider_available:
        return "rider"
    if ext in {".kt", ".kts", ".java", ".gradle", ".groovy"} and idea_available:
        return "idea"
    if name in {"pom.xml", "build.gradle", "build.gradle.kts"} and idea_available:
        return "idea"
    if vscode_available:
        return "vscode"
    if idea_available:
        return "idea"
    if rider_available:
        return "rider"
    return "default"


def open_local_path_with_opener(path: Path, opener_id: str | None = None) -> str:
    """Open local path with requested opener. Returns resolved opener id."""
    opener = (opener_id or "default").strip().lower()
    if opener == "auto":
        opener = _pick_auto_opener(path)
    if opener in {"", "default"}:
        open_local_path_native(path)
        return "default"

    target = str(path)
    if opener == "vscode":
        if _command_exists("code"):
            args = ["code", "-g", target] if path.is_file() else ["code", target]
            proc = subprocess.run(args, check=False, capture_output=True, text=True)
            if proc.returncode == 0:
                return opener
            raise RuntimeError((proc.stderr or "").strip() or "VS Code command failed")
        if _open_with_mac_app(path, ["Visual Studio Code"]):
            return opener
        raise RuntimeError("VS Code를 찾을 수 없습니다")

    if opener == "idea":
        for cmd in ("idea", "idea64.exe"):
            if not _command_exists(cmd):
                continue
            proc = subprocess.run([cmd, target], check=False, capture_output=True, text=True)
            if proc.returncode == 0:
                return opener
        if _open_with_mac_app(path, ["IntelliJ IDEA", "IntelliJ IDEA CE"]):
            return opener
        raise RuntimeError("IntelliJ IDEA를 찾을 수 없습니다")

    if opener == "rider":
        for cmd in ("rider", "rider64.exe"):
            if not _command_exists(cmd):
                continue
            proc = subprocess.run([cmd, target], check=False, capture_output=True, text=True)
            if proc.returncode == 0:
                return opener
        if _open_with_mac_app(path, ["Rider"]):
            return opener
        raise RuntimeError("Rider를 찾을 수 없습니다")

    raise RuntimeError(f"지원하지 않는 opener_id입니다: {opener}")
