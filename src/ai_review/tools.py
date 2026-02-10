"""FastMCP tool definitions for AI Review."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastmcp import FastMCP

if TYPE_CHECKING:
    from ai_review.session_manager import SessionManager

mcp = FastMCP("ai-review", instructions="Multi-model AI code review orchestrator")

# Will be set by server.py at startup
_manager: SessionManager | None = None


def set_manager(manager: SessionManager) -> None:
    global _manager
    _manager = manager


def _get_manager() -> SessionManager:
    if _manager is None:
        raise RuntimeError("SessionManager not initialized")
    return _manager


@mcp.tool()
async def start_review(base: str = "main") -> dict:
    """코드 리뷰 세션을 시작합니다. git diff를 수집하고 프로젝트 knowledge를 로딩합니다."""
    return await _get_manager().start_review(base)


@mcp.tool()
async def get_review_context(file: str | None = None) -> dict:
    """현재 세션의 diff와 프로젝트 knowledge를 반환합니다. 리뷰 전에 반드시 호출하세요."""
    mgr = _get_manager()
    session = mgr.current_session
    if not session:
        return {"error": "No active session"}
    return mgr.get_review_context(session.id, file)


@mcp.tool()
async def submit_review(model_id: str, issues: list[dict], summary: str = "") -> dict:
    """독립 리뷰 결과를 제출합니다. 발견한 이슈를 구조화하여 전달하세요."""
    mgr = _get_manager()
    session = mgr.current_session
    if not session:
        return {"error": "No active session"}
    return mgr.submit_review(session.id, model_id, issues, summary)


@mcp.tool()
async def get_all_reviews() -> list[dict]:
    """제출된 모든 리뷰를 조회합니다."""
    mgr = _get_manager()
    session = mgr.current_session
    if not session:
        return [{"error": "No active session"}]
    return mgr.get_all_reviews(session.id)


@mcp.tool()
async def get_issues() -> list[dict]:
    """중복 제거된 이슈 목록을 반환합니다."""
    mgr = _get_manager()
    session = mgr.current_session
    if not session:
        return [{"error": "No active session"}]
    return mgr.get_issues(session.id)


@mcp.tool()
async def get_issue_thread(issue_id: str) -> dict:
    """특정 이슈의 토론 스레드를 반환합니다."""
    mgr = _get_manager()
    session = mgr.current_session
    if not session:
        return {"error": "No active session"}
    return mgr.get_issue_thread(session.id, issue_id)


@mcp.tool()
async def submit_opinion(
    issue_id: str,
    model_id: str,
    action: str,
    reasoning: str,
    suggested_severity: str | None = None,
) -> dict:
    """특정 이슈에 대한 토론 의견을 제출합니다. action: agree/disagree/clarify"""
    mgr = _get_manager()
    session = mgr.current_session
    if not session:
        return {"error": "No active session"}
    return mgr.submit_opinion(
        session.id, issue_id, model_id, action, reasoning, suggested_severity
    )


@mcp.tool()
async def get_pending_issues(model_id: str) -> list[dict]:
    """아직 본인이 의견을 제출하지 않은 이슈 목록을 반환합니다."""
    mgr = _get_manager()
    session = mgr.current_session
    if not session:
        return [{"error": "No active session"}]
    return mgr.get_pending_issues(session.id, model_id)


@mcp.tool()
async def get_session_status() -> dict:
    """현재 세션 상태를 조회합니다."""
    mgr = _get_manager()
    session = mgr.current_session
    if not session:
        return {"error": "No active session"}
    return mgr.get_session_status(session.id)


@mcp.tool()
async def get_final_report() -> dict:
    """합의가 완료된 최종 리뷰 리포트를 반환합니다."""
    mgr = _get_manager()
    session = mgr.current_session
    if not session:
        return {"error": "No active session"}
    return mgr.get_final_report(session.id)
