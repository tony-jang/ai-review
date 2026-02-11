"""FastAPI server with MCP integration, REST API, and SSE."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from ai_review.models import AssistMessage, SessionStatus
from ai_review.orchestrator import Orchestrator
from ai_review.session_manager import SessionManager
from ai_review.tools import mcp, set_manager

STATIC_DIR = Path(__file__).parent / "static"


def create_app(repo_path: str | None = None, port: int = 3000) -> FastAPI:
    """Create the FastAPI application."""
    manager = SessionManager(repo_path=repo_path)
    set_manager(manager)

    api_base_url = f"http://localhost:{port}"
    orchestrator = Orchestrator(manager, api_base_url=api_base_url)

    mcp_http_app = mcp.http_app(path="")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with mcp_http_app.lifespan(app):
            yield
        await orchestrator.close()

    app = FastAPI(title="AI Review", version="0.1.0", lifespan=lifespan)

    # --- REST API ---

    @app.post("/api/sessions")
    async def api_start_review(request: Request):
        body = await request.json() if await request.body() else {}
        base = body.get("base", "main")
        result = await manager.start_review(base)

        # Kick off automated review if models are configured
        session_id = result["session_id"]
        await orchestrator.start(session_id)

        return JSONResponse(result)

    @app.get("/api/sessions/{session_id}/context")
    async def api_get_context(session_id: str, file: str | None = None):
        try:
            return JSONResponse(manager.get_review_context(session_id, file))
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.get("/api/sessions/{session_id}/index")
    async def api_get_context_index(session_id: str):
        try:
            return JSONResponse(manager.get_context_index(session_id))
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.post("/api/sessions/{session_id}/reviews")
    async def api_submit_review(session_id: str, request: Request):
        body = await request.json()
        try:
            result = manager.submit_review(
                session_id,
                body["model_id"],
                body["issues"],
                body.get("summary", ""),
            )
            return JSONResponse(result)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.get("/api/sessions/{session_id}/reviews")
    async def api_get_reviews(session_id: str):
        try:
            return JSONResponse(manager.get_all_reviews(session_id))
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.get("/api/sessions/{session_id}/issues")
    async def api_get_issues(session_id: str):
        try:
            return JSONResponse(manager.get_issues(session_id))
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.post("/api/sessions/{session_id}/issues")
    async def api_create_issue(session_id: str, request: Request):
        body = await request.json()
        try:
            result = manager.add_manual_issue(
                session_id,
                body["title"],
                body["severity"],
                body["file"],
                body.get("line"),
                body.get("description", ""),
                body.get("suggestion", ""),
            )
            return JSONResponse(result, status_code=201)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.get("/api/issues/{issue_id}/thread")
    async def api_get_thread(issue_id: str):
        session = manager.current_session
        if not session:
            raise HTTPException(status_code=404, detail="No active session")
        try:
            return JSONResponse(manager.get_issue_thread(session.id, issue_id))
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.post("/api/issues/{issue_id}/opinions")
    async def api_submit_opinion(issue_id: str, request: Request):
        session = manager.current_session
        if not session:
            raise HTTPException(status_code=404, detail="No active session")
        body = await request.json()
        try:
            result = manager.submit_opinion(
                session.id,
                issue_id,
                body["model_id"],
                body["action"],
                body["reasoning"],
                body.get("suggested_severity"),
            )
            return JSONResponse(result)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.get("/api/sessions/{session_id}/pending")
    async def api_get_pending(session_id: str, model_id: str):
        try:
            return JSONResponse(manager.get_pending_issues(session_id, model_id))
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.get("/api/sessions/current/status")
    async def api_get_current_status():
        session = manager.current_session
        if not session:
            raise HTTPException(status_code=404, detail="No active session")
        return JSONResponse(manager.get_session_status(session.id))

    @app.get("/api/sessions/{session_id}/status")
    async def api_get_status(session_id: str):
        try:
            return JSONResponse(manager.get_session_status(session_id))
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.post("/api/sessions/{session_id}/process")
    async def api_process_reviews(session_id: str):
        """Create issues from reviews, deduplicate, and apply consensus."""
        from ai_review.consensus import apply_consensus
        from ai_review.dedup import deduplicate_issues
        from ai_review.state import can_transition, transition

        try:
            session = manager.get_session(session_id)
            raw_count = sum(len(r.issues) for r in session.reviews)

            # Transition REVIEWING → DEDUP → DELIBERATING
            if can_transition(session, SessionStatus.DEDUP):
                transition(session, SessionStatus.DEDUP)
                manager.broker.publish("phase_change", {"status": "dedup", "session_id": session_id})

            # Only create+dedup if no issues yet
            if not session.issues:
                issues = manager.create_issues_from_reviews(session_id)
                deduped = deduplicate_issues(issues)
                session.issues = deduped

            apply_consensus(session.issues, session.config.consensus_threshold)

            if can_transition(session, SessionStatus.DELIBERATING):
                transition(session, SessionStatus.DELIBERATING)
                manager.broker.publish("phase_change", {"status": "deliberating", "session_id": session_id})

            return JSONResponse({
                "raw_issues": raw_count,
                "after_dedup": len(session.issues),
                "issues": [
                    {"id": i.id, "title": i.title, "severity": i.severity.value,
                     "consensus": i.consensus, "final_severity": i.final_severity.value if i.final_severity else None}
                    for i in session.issues
                ],
            })
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.post("/api/sessions/{session_id}/finish")
    async def api_finish_session(session_id: str):
        """Finish the review session and generate final report."""
        from ai_review.consensus import apply_consensus
        from ai_review.dedup import deduplicate_issues
        from ai_review.state import can_transition, transition

        try:
            session = manager.get_session(session_id)

            # Process if not yet done
            if can_transition(session, SessionStatus.DEDUP):
                transition(session, SessionStatus.DEDUP)
                manager.broker.publish("phase_change", {"status": "dedup", "session_id": session_id})

            if not session.issues:
                issues = manager.create_issues_from_reviews(session_id)
                deduped = deduplicate_issues(issues)
                session.issues = deduped

            apply_consensus(session.issues, session.config.consensus_threshold)

            if can_transition(session, SessionStatus.DELIBERATING):
                transition(session, SessionStatus.DELIBERATING)

            if can_transition(session, SessionStatus.COMPLETE):
                transition(session, SessionStatus.COMPLETE)
                manager.broker.publish("phase_change", {"status": "complete", "session_id": session_id})

            return JSONResponse(manager.get_final_report(session_id))
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.get("/api/sessions/{session_id}/diff/{file_path:path}")
    async def api_get_file_diff(session_id: str, file_path: str):
        """Get diff content for a specific file."""
        try:
            session = manager.get_session(session_id)
            for f in session.diff:
                if f.path == file_path:
                    return JSONResponse({"path": f.path, "additions": f.additions, "deletions": f.deletions, "content": f.content})
            raise HTTPException(status_code=404, detail=f"File not found in diff: {file_path}")
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))

    # --- Assist (issue resolution helper) ---

    def _compose_assist_prompt(issue, diff_content: str, user_message: str) -> str:
        severity_kr = {"critical": "심각", "high": "높음", "medium": "보통", "low": "낮음", "dismissed": "기각"}
        action_kr = {"raise": "제기", "agree": "동의", "disagree": "반대", "clarify": "의견"}
        parts = [
            "당신은 시니어 개발자입니다. 코드 리뷰에서 발견된 이슈를 해결하는 것을 도와주세요.",
            "",
            "## 이슈 정보",
            f"- 제목: {issue.title}",
            f"- 심각도: {severity_kr.get(issue.severity.value, issue.severity.value)}",
            f"- 파일: {issue.file}" + (f":{issue.line}" if issue.line else ""),
            f"- 설명: {issue.description}",
        ]
        if issue.suggestion:
            parts.append(f"- 수정 제안: {issue.suggestion}")

        if issue.thread:
            parts.append("")
            parts.append("## 리뷰어 토론")
            for op in issue.thread:
                act = action_kr.get(op.action.value, op.action.value)
                parts.append(f"- {op.model_id} ({act}): {op.reasoning}")

        if diff_content:
            parts.append("")
            parts.append("## 관련 코드 변경 (diff)")
            parts.append("```diff")
            parts.append(diff_content)
            parts.append("```")

        if issue.assist_messages:
            parts.append("")
            parts.append("## 이전 대화")
            for msg in issue.assist_messages:
                role = "사용자" if msg.role == "user" else "도우미"
                parts.append(f"**{role}**: {msg.content}")

        parts.append("")
        parts.append(f"**사용자**: {user_message}")
        parts.append("")
        parts.append("한국어로 답변해주세요. 코드 수정이 필요하면 구체적인 코드를 제공하세요.")
        parts.append("수정 범위가 크거나 여러 파일에 걸치면, CLI에서 직접 수정할 수 있도록 명령어를 제안하세요.")
        return "\n".join(parts)

    @app.post("/api/issues/{issue_id}/assist")
    async def api_assist_issue(issue_id: str, request: Request):
        """AI assistant for resolving an issue."""
        session = manager.current_session
        if not session:
            raise HTTPException(status_code=404, detail="활성 세션이 없습니다")

        body = await request.json()
        user_message = body.get("message", "").strip()
        if not user_message:
            raise HTTPException(status_code=400, detail="메시지를 입력해주세요")

        # Find issue
        issue = None
        for i in session.issues:
            if i.id == issue_id:
                issue = i
                break
        if not issue:
            raise HTTPException(status_code=404, detail="이슈를 찾을 수 없습니다")

        # Get diff context
        diff_content = ""
        for f in session.diff:
            if f.path == issue.file:
                diff_content = f.content
                break

        # Store user message
        issue.assist_messages.append(AssistMessage(role="user", content=user_message))

        # Compose prompt and call claude
        prompt = _compose_assist_prompt(issue, diff_content, user_message)
        try:
            proc = await asyncio.create_subprocess_exec(
                "claude", "--print", "--output-format", "text", "-p", prompt,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
            response = stdout.decode().strip()
        except asyncio.TimeoutError:
            response = "응답 시간이 초과되었습니다. CLI에서 직접 해결해보세요:\n\n```\nclaude -p \"" + issue.title + " 이슈를 해결해주세요. 파일: " + issue.file + "\"\n```"
        except Exception as e:
            response = f"오류가 발생했습니다: {e}"

        # Store assistant response
        issue.assist_messages.append(AssistMessage(role="assistant", content=response))

        # Generate CLI command for reference
        cli_cmd = f'claude -p "{issue.file} 파일의 이슈를 해결해주세요: {issue.title}. {issue.description}"'

        return JSONResponse({
            "response": response,
            "cli_command": cli_cmd,
            "messages": [m.model_dump(mode="json") for m in issue.assist_messages],
        })

    @app.get("/api/issues/{issue_id}/assist")
    async def api_get_assist_history(issue_id: str):
        """Get assist chat history for an issue."""
        session = manager.current_session
        if not session:
            raise HTTPException(status_code=404, detail="활성 세션이 없습니다")
        for i in session.issues:
            if i.id == issue_id:
                return JSONResponse({
                    "messages": [m.model_dump(mode="json") for m in i.assist_messages],
                })
        raise HTTPException(status_code=404, detail="이슈를 찾을 수 없습니다")

    @app.get("/api/sessions/{session_id}/report")
    async def api_get_report(session_id: str):
        try:
            return JSONResponse(manager.get_final_report(session_id))
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.get("/api/sessions/{session_id}/stream")
    async def api_sse_stream(session_id: str):
        async def event_generator():
            async for event in manager.broker.subscribe():
                if event.data.get("session_id") == session_id:
                    yield event.format()

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # --- MCP mount ---
    app.mount("/mcp", mcp_http_app)

    # --- Static files ---
    if STATIC_DIR.exists():
        app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

    return app
