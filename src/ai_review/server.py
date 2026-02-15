"""FastAPI server with MCP integration, REST API, and SSE."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from ai_review.assist import (
    compose_assist_opinion_prompt,
    compose_assist_prompt,
    issue_location_text,
    parse_assist_opinion,
)
from ai_review.fs_utils import (
    list_local_openers,
    open_local_path_with_opener,
    pick_directory_native,
    resolve_local_path,
)
from ai_review.git_diff import list_branches, validate_repo
from ai_review.models import AssistMessage, ModelConfig, SessionStatus
from ai_review.orchestrator import Orchestrator
from ai_review.session_manager import SessionManager
from ai_review.tools import mcp, set_manager
from ai_review.trigger.base import TriggerEngine, TriggerResult
from ai_review.trigger.cc import ClaudeCodeTrigger
from ai_review.trigger.codex import CodexTrigger
from ai_review.trigger.gemini import GeminiTrigger
from ai_review.trigger.opencode import OpenCodeTrigger

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
    app.state.manager = manager

    # ------------------------------------------------------------------
    # Helper: resolve session from current or raise 404
    # ------------------------------------------------------------------
    def _require_current_session() -> str:
        session = manager.current_session
        if not session:
            raise HTTPException(status_code=404, detail="No active session")
        return session.id

    def _extract_agent_key(request: Request, body: dict[str, Any] | None = None) -> str:
        header_key = (request.headers.get("x-agent-key") or "").strip()
        if header_key:
            return header_key
        if isinstance(body, dict):
            return str(body.get("agent_key") or "").strip()
        return ""

    def _try_record_activity(request: Request, session_id: str, action: str, target: str) -> None:
        """Record agent activity if X-Agent-Key header is present."""
        agent_key = (request.headers.get("x-agent-key") or "").strip()
        if not agent_key:
            return
        try:
            model_id = manager.resolve_model_id_from_key(session_id, agent_key)
            if model_id:
                manager.record_activity(session_id, model_id, action, target)
        except KeyError:
            pass

    def _require_model_access_key(
        session_id: str,
        model_id: str,
        request: Request,
        body: dict[str, Any] | None = None,
    ) -> None:
        """Validate write access for model-identified requests."""
        session = manager.get_session(session_id)
        if model_id == "human":
            return

        key = _extract_agent_key(request, body)
        if model_id == "human-assist":
            expected = session.human_assist_access_key or ""
            if not expected or key != expected:
                raise HTTPException(status_code=403, detail="human-assist access key is required")
            return

        configured_ids = {m.id for m in session.config.models}
        if model_id in configured_ids:
            expected = session.agent_access_keys.get(model_id) or ""
            if not expected:
                expected = manager.ensure_agent_access_key(session_id, model_id)
            if key != expected:
                raise HTTPException(status_code=403, detail=f"invalid access key for model '{model_id}'")

    def _require_human_assist_key(session_id: str, request: Request, body: dict[str, Any] | None = None) -> None:
        _require_model_access_key(session_id, "human-assist", request, body)

    supported_connection_clients = {"claude-code", "codex", "gemini", "opencode"}
    pending_connection_tests: dict[str, dict[str, Any]] = {}

    def _create_connection_test_trigger(client_type: str, timeout_seconds: float) -> TriggerEngine:
        if client_type == "codex":
            return CodexTrigger(timeout_seconds=max(10.0, timeout_seconds + 5.0))
        if client_type == "gemini":
            return GeminiTrigger(timeout_seconds=max(10.0, timeout_seconds + 5.0))
        if client_type == "opencode":
            return OpenCodeTrigger(timeout=max(10.0, timeout_seconds + 5.0))
        return ClaudeCodeTrigger()

    def _build_connection_test_prompt(
        callback_url: str,
        test_token: str,
        session_marker: str,
        client_type: str,
        provider: str,
        model_id: str,
    ) -> str:
        payload = {
            "test_token": test_token,
            "session_marker": session_marker,
            "client_type": client_type,
            "provider": provider,
            "model_id": model_id,
            "message": "ai-review connection test callback",
        }
        payload_json = json.dumps(payload, ensure_ascii=False)
        parts = [
            "연결 테스트입니다.",
            "아래 지시를 그대로 수행하세요.",
            "1) 단 한 번만 curl 요청을 실행합니다.",
            "2) 요청 URL/Body를 수정하지 않습니다.",
            "3) 실행 후 간단히 결과만 보고합니다.",
            "",
            f"콜백 URL: {callback_url}",
            f"POST Body(JSON): {payload_json}",
            "",
            "실행 명령:",
            f"curl -sS -X POST '{callback_url}' -H 'Content-Type: application/json' -d '{payload_json}'",
        ]
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Available models
    # ------------------------------------------------------------------

    @app.get("/api/available-models")
    async def api_available_models():
        return JSONResponse({
            "claude-code": [
                {"model_id": "claude-opus-4-1-20250805", "label": "Opus 4.1 (Latest)"},
                {"model_id": "claude-sonnet-4-20250514", "label": "Sonnet 4"},
                {"model_id": "claude-opus-4-20250514", "label": "Opus 4"},
            ],
            "codex": [
                {"model_id": "gpt-5.2-codex", "label": "GPT-5.2 Codex (Latest/Recommended)"},
                {"model_id": "gpt-5.1-codex-max", "label": "GPT-5.1 Codex Max"},
                {"model_id": "gpt-5.1-codex", "label": "GPT-5.1 Codex"},
                {"model_id": "gpt-5.1-codex-mini", "label": "GPT-5.1 Codex mini"},
                {"model_id": "gpt-5-codex", "label": "GPT-5 Codex"},
                {"model_id": "codex-mini-latest", "label": "codex-mini-latest"},
                {"model_id": "o3", "label": "o3 (Legacy)"},
                {"model_id": "o4-mini", "label": "o4-mini (Legacy)"},
            ],
            "gemini": [
                {"model_id": "gemini-3-pro-preview", "label": "Gemini 3 Pro Preview (Latest)"},
                {"model_id": "gemini-3-flash-preview", "label": "Gemini 3 Flash Preview"},
                {"model_id": "gemini-2.5-pro", "label": "Gemini 2.5 Pro"},
                {"model_id": "gemini-2.5-flash", "label": "Gemini 2.5 Flash"},
                {"model_id": "gemini-2.5-flash-lite", "label": "Gemini 2.5 Flash-Lite"},
            ],
            "opencode": [],
        })

    @app.get("/api/agents/connection-targets")
    async def api_agent_connection_targets():
        return JSONResponse({
            "message": "테스트 시 콜백 URL/세션 ID를 서버가 자동 생성합니다.",
            "supported_client_types": sorted(supported_connection_clients),
        })

    @app.post("/api/agents/connection-test/callback/{test_token}")
    async def api_agent_connection_test_callback(test_token: str, request: Request):
        test_state = pending_connection_tests.get(test_token)
        if not test_state:
            raise HTTPException(status_code=404, detail="unknown or expired connection test token")

        body_bytes = await request.body()
        payload: Any
        try:
            payload = json.loads(body_bytes.decode("utf-8")) if body_bytes else {}
        except Exception:
            payload = {"raw": body_bytes.decode("utf-8", errors="ignore")}

        test_state["payload"] = payload
        test_state["received_at"] = time.time()
        test_state["client_host"] = request.client.host if request.client else ""
        test_state["event"].set()

        return JSONResponse({"status": "received", "test_token": test_token})

    @app.post("/api/agents/connection-test")
    async def api_agent_connection_test(request: Request):
        body = await request.json()
        client_type = str(body.get("client_type", "")).strip()
        provider = str(body.get("provider", "")).strip()
        model_id = str(body.get("model_id", "")).strip()
        timeout_raw = body.get("timeout_seconds", 5)
        if client_type not in supported_connection_clients:
            raise HTTPException(
                status_code=400,
                detail=f"client_type must be one of: {', '.join(sorted(supported_connection_clients))}",
            )

        try:
            timeout_seconds = float(timeout_raw)
        except (TypeError, ValueError):
            timeout_seconds = 20.0
        timeout_seconds = max(3.0, min(timeout_seconds, 120.0))

        trigger = _create_connection_test_trigger(client_type, timeout_seconds)
        test_token = uuid.uuid4().hex
        session_marker = uuid.uuid4().hex[:12]
        callback_url = f"{api_base_url}/api/agents/connection-test/callback/{test_token}"
        prompt = _build_connection_test_prompt(
            callback_url=callback_url,
            test_token=test_token,
            session_marker=session_marker,
            client_type=client_type,
            provider=provider,
            model_id=model_id,
        )
        model_config = ModelConfig(
            id=f"connection-test-{test_token[:8]}",
            client_type=client_type,
            provider=provider,
            model_id=model_id,
        )
        callback_state = {
            "event": asyncio.Event(),
            "payload": None,
            "received_at": None,
            "client_host": "",
        }
        pending_connection_tests[test_token] = callback_state

        trigger_task: asyncio.Task | None = None
        started_at = time.monotonic()
        trigger_result: TriggerResult | None = None

        try:
            client_session_id = await trigger.create_session(model_config.id)
            trigger_task = asyncio.create_task(
                trigger.send_prompt(
                    client_session_id,
                    model_config.id,
                    prompt,
                    model_config=model_config,
                )
            )

            while True:
                if callback_state["event"].is_set():
                    break
                if trigger_task.done():
                    try:
                        trigger_result = trigger_task.result()
                    except asyncio.CancelledError:
                        trigger_result = TriggerResult(success=False, error="connection test trigger cancelled")
                    except Exception as exc:
                        trigger_result = TriggerResult(success=False, error=str(exc))
                    if not trigger_result.success:
                        elapsed_ms = int((time.monotonic() - started_at) * 1000)
                        return JSONResponse({
                            "ok": False,
                            "status": "trigger_failed",
                            "reason": trigger_result.error or "trigger failed",
                            "elapsed_ms": elapsed_ms,
                            "test_token": test_token,
                            "session_marker": session_marker,
                        })
                elapsed = time.monotonic() - started_at
                if elapsed >= timeout_seconds:
                    elapsed_ms = int(elapsed * 1000)
                    return JSONResponse({
                        "ok": False,
                        "status": "timeout",
                        "reason": f"callback not received within {timeout_seconds:.1f}s",
                        "elapsed_ms": elapsed_ms,
                        "test_token": test_token,
                        "session_marker": session_marker,
                    })
                await asyncio.sleep(0.05)

            elapsed_ms = int((time.monotonic() - started_at) * 1000)
            callback_payload = {
                "payload": callback_state.get("payload"),
                "received_at": callback_state.get("received_at"),
                "client_host": callback_state.get("client_host"),
            }
            if trigger_result is None and trigger_task is not None and trigger_task.done():
                with suppress(Exception):
                    trigger_result = trigger_task.result()

            return JSONResponse({
                "ok": True,
                "status": "callback_received",
                "elapsed_ms": elapsed_ms,
                "test_token": test_token,
                "session_marker": session_marker,
                "callback": callback_payload,
                "trigger": {
                    "success": trigger_result.success if trigger_result else None,
                    "error": trigger_result.error if trigger_result else "",
                },
            })
        finally:
            pending_connection_tests.pop(test_token, None)
            if trigger_task and not trigger_task.done():
                trigger_task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await asyncio.wait_for(trigger_task, timeout=1.0)
            await trigger.close()

    # ------------------------------------------------------------------
    # Git utilities
    # ------------------------------------------------------------------

    @app.post("/api/git/validate")
    async def api_git_validate(request: Request):
        body = await request.json()
        path = body.get("path", "")
        if not path:
            raise HTTPException(status_code=400, detail="path is required")
        result = await validate_repo(path)
        return JSONResponse(result)

    @app.get("/api/git/branches")
    async def api_git_branches(repo_path: str = ""):
        if not repo_path:
            raise HTTPException(status_code=400, detail="repo_path query parameter is required")
        result = await list_branches(repo_path)
        return JSONResponse(result)

    @app.get("/api/pick-directory")
    @app.get("/api/fs/pick-directory")
    async def api_pick_directory():
        try:
            path = await asyncio.to_thread(pick_directory_native)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"폴더 선택 UI를 열 수 없습니다: {e}")

        if not path:
            return JSONResponse({"ok": False, "cancelled": True, "path": ""})

        return JSONResponse({"ok": True, "cancelled": False, "path": path})

    @app.get("/api/fs/openers")
    async def api_list_local_openers():
        return JSONResponse({"openers": list_local_openers()})

    @app.post("/api/fs/open")
    async def api_open_local_path(request: Request):
        body = await request.json() if await request.body() else {}
        path = str(body.get("path", ""))
        session_id = body.get("session_id")
        opener_id = str(body.get("opener_id", "default"))
        resolved = resolve_local_path(path, manager=manager, session_id=session_id)
        if not resolved.exists():
            raise HTTPException(status_code=404, detail=f"path not found: {resolved}")
        try:
            used_opener = await asyncio.to_thread(open_local_path_with_opener, resolved, opener_id)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"로컬 파일을 열 수 없습니다: {e}")
        return JSONResponse({"ok": True, "path": str(resolved), "opener_id": used_opener})

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    @app.get("/api/sessions")
    async def api_list_sessions():
        return JSONResponse(manager.list_sessions())

    @app.post("/api/sessions")
    async def api_start_review(request: Request):
        body = await request.json() if await request.body() else {}
        base = body.get("base", "main")
        head = body.get("head")
        repo_path_param = body.get("repo_path")
        preset_ids = body.get("preset_ids")
        try:
            result = await manager.start_review(base, head=head, repo_path=repo_path_param, preset_ids=preset_ids)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        # Kick off automated review if models are configured
        session_id = result["session_id"]
        await orchestrator.start(session_id)

        return JSONResponse(result)

    @app.delete("/api/sessions/{session_id}")
    async def api_delete_session(session_id: str):
        await orchestrator.stop_session(session_id)
        try:
            manager.delete_session(session_id)
            return JSONResponse({"status": "deleted", "session_id": session_id})
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.post("/api/sessions/{session_id}/activate")
    async def api_activate_session(session_id: str):
        try:
            manager.set_current_session(session_id)
            return JSONResponse({"status": "activated", "session_id": session_id})
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))

    # ------------------------------------------------------------------
    # Agent presets (global)
    # ------------------------------------------------------------------

    @app.get("/api/agent-presets")
    async def api_list_agent_presets():
        return JSONResponse(manager.list_agent_presets())

    @app.post("/api/agent-presets")
    async def api_add_agent_preset(request: Request):
        body = await request.json()
        try:
            added = manager.add_agent_preset(body)
            manager.broker.publish("agent_preset_changed", {"action": "added", "preset_id": added.get("id", "")})
            return JSONResponse(added, status_code=201)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.put("/api/agent-presets/{preset_id}")
    async def api_update_agent_preset(preset_id: str, request: Request):
        body = await request.json()
        try:
            updated = manager.update_agent_preset(preset_id, body)
            manager.broker.publish("agent_preset_changed", {"action": "updated", "preset_id": preset_id})
            return JSONResponse(updated)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except (ValueError, TypeError) as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.delete("/api/agent-presets/{preset_id}")
    async def api_remove_agent_preset(preset_id: str):
        try:
            result = manager.remove_agent_preset(preset_id)
            manager.broker.publish("agent_preset_changed", {"action": "removed", "preset_id": preset_id})
            return JSONResponse(result)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))

    # ------------------------------------------------------------------
    # Session status
    # ------------------------------------------------------------------

    @app.get("/api/sessions/current/status")
    async def api_get_current_status():
        return await api_get_status(_require_current_session())

    @app.get("/api/sessions/{session_id}/status")
    async def api_get_status(session_id: str):
        try:
            return JSONResponse(manager.get_session_status(session_id))
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.post("/api/sessions/{session_id}/assist/key")
    async def api_issue_human_assist_key(session_id: str):
        try:
            key = manager.issue_human_assist_access_key(session_id)
            return JSONResponse({"status": "issued", "access_key": key})
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))

    # ------------------------------------------------------------------
    # Context & Index
    # ------------------------------------------------------------------

    @app.get("/api/sessions/{session_id}/context")
    async def api_get_context(session_id: str, file: str | None = None, request: Request = None):
        try:
            _try_record_activity(request, session_id, "view_context", f"context:{file or 'all'}")
            return JSONResponse(manager.get_review_context(session_id, file))
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.post("/api/sessions/{session_id}/implementation-context")
    async def api_submit_implementation_context(session_id: str, request: Request):
        body = await request.json()
        try:
            result = manager.submit_implementation_context(session_id, body)
            return JSONResponse(result)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.get("/api/sessions/{session_id}/index")
    async def api_get_context_index(session_id: str, request: Request = None):
        try:
            _try_record_activity(request, session_id, "view_index", "index")
            return JSONResponse(manager.get_context_index(session_id))
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))

    # ------------------------------------------------------------------
    # Reviews
    # ------------------------------------------------------------------

    @app.post("/api/sessions/{session_id}/reviews")
    async def api_submit_review(session_id: str, request: Request):
        body = await request.json()
        try:
            _require_model_access_key(session_id, body["model_id"], request, body)
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

    @app.post("/api/sessions/{session_id}/overall-reviews")
    async def api_submit_overall_review(session_id: str, request: Request):
        body = await request.json()
        try:
            _require_model_access_key(session_id, body["model_id"], request, body)
            result = manager.submit_overall_review(
                session_id=session_id,
                model_id=body["model_id"],
                merge_decision=body.get("merge_decision", "needs_discussion"),
                summary=body.get("summary", ""),
                task_type=body.get("task_type", "review"),
                turn=body.get("turn"),
                highlights=body.get("highlights"),
                blockers=body.get("blockers"),
                recommendations=body.get("recommendations"),
            )
            return JSONResponse(result)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.get("/api/sessions/{session_id}/overall-reviews")
    async def api_get_overall_reviews(session_id: str, turn: int | None = None):
        try:
            return JSONResponse(manager.get_overall_reviews(session_id, turn=turn))
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))

    # ------------------------------------------------------------------
    # Issues (session-scoped)
    # ------------------------------------------------------------------

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
                body.get("line_start"),
                body.get("line_end"),
            )
            return JSONResponse(result, status_code=201)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.get("/api/sessions/{session_id}/issues/{issue_id}/thread")
    async def api_get_thread_by_session(session_id: str, issue_id: str):
        try:
            return JSONResponse(manager.get_issue_thread(session_id, issue_id))
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.post("/api/sessions/{session_id}/issues/{issue_id}/opinions")
    async def api_submit_opinion_by_session(session_id: str, issue_id: str, request: Request):
        body = await request.json()
        try:
            _require_model_access_key(session_id, body["model_id"], request, body)
            result = manager.submit_opinion(
                session_id,
                issue_id,
                body["model_id"],
                body["action"],
                body["reasoning"],
                body.get("suggested_severity"),
                body.get("mentions"),
                confidence=float(body.get("confidence", 1.0)),
            )
            return JSONResponse(result)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    # Legacy issue endpoints (delegate to current session)
    @app.get("/api/issues/{issue_id}/thread")
    async def api_get_thread(issue_id: str):
        return await api_get_thread_by_session(_require_current_session(), issue_id)

    @app.post("/api/issues/{issue_id}/opinions")
    async def api_submit_opinion(issue_id: str, request: Request):
        return await api_submit_opinion_by_session(_require_current_session(), issue_id, request)

    # ------------------------------------------------------------------
    # Pending issues
    # ------------------------------------------------------------------

    @app.get("/api/sessions/{session_id}/pending")
    async def api_get_pending(session_id: str, model_id: str):
        try:
            return JSONResponse(manager.get_pending_issues(session_id, model_id))
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))

    # ------------------------------------------------------------------
    # Issue Responses (Agent Response Protocol)
    # ------------------------------------------------------------------

    @app.get("/api/sessions/{session_id}/confirmed-issues")
    async def api_get_confirmed_issues(session_id: str):
        try:
            return JSONResponse(manager.get_confirmed_issues(session_id))
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.post("/api/sessions/{session_id}/issues/{issue_id}/respond")
    async def api_submit_issue_response(session_id: str, issue_id: str, request: Request):
        body = await request.json()
        try:
            result = manager.submit_issue_response(
                session_id,
                issue_id,
                body["action"],
                body.get("reasoning", ""),
                body.get("proposed_change", ""),
                body.get("submitted_by", ""),
            )
            return JSONResponse(result)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.get("/api/sessions/{session_id}/issue-responses")
    async def api_get_issue_response_status(session_id: str):
        try:
            return JSONResponse(manager.get_issue_response_status(session_id))
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))

    # ------------------------------------------------------------------
    # Fix Complete & Delta Context (Delta Review Protocol)
    # ------------------------------------------------------------------

    @app.post("/api/sessions/{session_id}/fix-complete")
    async def api_fix_complete(session_id: str, request: Request):
        body = await request.json()
        try:
            result = await manager.submit_fix_complete(
                session_id,
                body["commit_hash"],
                issues_addressed=body.get("issues_addressed"),
                submitted_by=body.get("submitted_by", ""),
            )
            return JSONResponse(result)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.get("/api/sessions/{session_id}/delta-context")
    async def api_get_delta_context(session_id: str):
        try:
            return JSONResponse(manager.get_delta_context(session_id))
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))

    # ------------------------------------------------------------------
    # Agents — "current" routes MUST be registered before {session_id}
    # to prevent FastAPI from matching "current" as a session_id param.
    # ------------------------------------------------------------------

    @app.get("/api/sessions/current/agents")
    async def api_list_agents():
        return await api_list_agents_by_session(_require_current_session())

    @app.post("/api/sessions/current/agents")
    async def api_add_agent(request: Request):
        return await api_add_agent_by_session(_require_current_session(), request)

    @app.put("/api/sessions/current/agents/{model_id}")
    async def api_update_agent(model_id: str, request: Request):
        return await api_update_agent_by_session(_require_current_session(), model_id, request)

    @app.delete("/api/sessions/current/agents/{model_id}")
    async def api_remove_agent(model_id: str):
        return await api_remove_agent_by_session(_require_current_session(), model_id)

    @app.get("/api/sessions/current/agents/{model_id}/chat")
    async def api_get_agent_chat(model_id: str):
        return await api_get_agent_chat_by_session(_require_current_session(), model_id)

    @app.post("/api/sessions/current/agents/{model_id}/chat")
    async def api_chat_with_agent(model_id: str, request: Request):
        return await api_chat_with_agent_by_session(_require_current_session(), model_id, request)

    @app.get("/api/sessions/current/agents/{model_id}/runtime")
    async def api_get_agent_runtime(model_id: str):
        return await api_get_agent_runtime_by_session(_require_current_session(), model_id)

    # Session-scoped agent endpoints
    @app.get("/api/sessions/{session_id}/agents")
    async def api_list_agents_by_session(session_id: str):
        try:
            return JSONResponse(manager.list_agents(session_id))
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.post("/api/sessions/{session_id}/agents")
    async def api_add_agent_by_session(session_id: str, request: Request):
        body = await request.json()
        try:
            added = manager.add_agent(session_id, body)
            await orchestrator.add_agent(session_id, body["id"])
            manager.broker.publish("agent_config_changed", {"session_id": session_id})
            return JSONResponse(added, status_code=201)
        except (ValueError, KeyError) as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.put("/api/sessions/{session_id}/agents/{model_id}")
    async def api_update_agent_by_session(session_id: str, model_id: str, request: Request):
        body = await request.json()
        try:
            updated = manager.update_agent(session_id, model_id, body)
            manager.broker.publish("agent_config_changed", {"session_id": session_id})
            return JSONResponse(updated)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except (ValueError, TypeError) as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.delete("/api/sessions/{session_id}/agents/{model_id}")
    async def api_remove_agent_by_session(session_id: str, model_id: str):
        try:
            result = manager.remove_agent(session_id, model_id)
            await orchestrator.remove_agent(session_id, model_id)
            manager.broker.publish("agent_config_changed", {"session_id": session_id})
            return JSONResponse(result)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.get("/api/sessions/{session_id}/agents/{model_id}/runtime")
    async def api_get_agent_runtime_by_session(session_id: str, model_id: str):
        try:
            return JSONResponse(manager.get_agent_runtime(session_id, model_id))
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.get("/api/sessions/{session_id}/agents/{model_id}/chat")
    async def api_get_agent_chat_by_session(session_id: str, model_id: str):
        try:
            session = manager.get_session(session_id)
            return JSONResponse({"messages": manager.get_agent_chat(session_id, model_id)})
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.post("/api/sessions/{session_id}/agents/{model_id}/chat")
    async def api_chat_with_agent_by_session(session_id: str, model_id: str, request: Request):
        body = await request.json()
        message = body.get("message", "").strip()
        if not message:
            raise HTTPException(status_code=400, detail="메시지를 입력해주세요")
        try:
            manager.append_agent_chat(session_id, model_id, "user", message)
            response = await orchestrator.chat_with_agent(session_id, model_id, message)
            manager.append_agent_chat(session_id, model_id, "assistant", response)
            return JSONResponse({
                "response": response,
                "messages": manager.get_agent_chat(session_id, model_id),
            })
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    # ------------------------------------------------------------------
    # Process & Finish
    # ------------------------------------------------------------------

    @app.post("/api/sessions/{session_id}/process")
    async def api_process_reviews(session_id: str):
        """Create issues from reviews, deduplicate, and apply consensus."""
        from ai_review.consensus import apply_consensus
        from ai_review.dedup import deduplicate_issues
        from ai_review.state import can_transition, transition

        try:
            session = manager.get_session(session_id)
            raw_count = sum(len(r.issues) for r in session.reviews)

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
                manager.broker.publish("phase_change", {"status": "deliberating", "session_id": session_id})

            manager.persist()
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

            manager.persist()
            return JSONResponse(manager.get_final_report(session_id))
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.get("/api/sessions/{session_id}/files/{file_path:path}")
    async def api_get_file_content(session_id: str, file_path: str, start: int | None = None, end: int | None = None, request: Request = None):
        """Read source file content with optional line range."""
        try:
            target = f"{file_path}:{start or 1}-{end or 'end'}"
            _try_record_activity(request, session_id, "view_file", target)
            return JSONResponse(manager.read_file(session_id, file_path, start, end))
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=str(e))

    @app.get("/api/sessions/{session_id}/search")
    async def api_search_code(session_id: str, q: str = "", glob: str | None = None, max_results: int = 30, request: Request = None):
        """Search code in the repository."""
        if not q.strip():
            raise HTTPException(status_code=400, detail="q (query) parameter is required")
        try:
            _try_record_activity(request, session_id, "search", f"search:{q.strip()}")
            return JSONResponse(await manager.search_code(session_id, q.strip(), glob, max_results))
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.get("/api/sessions/{session_id}/tree")
    async def api_get_tree(session_id: str, path: str = "", depth: int = 2, request: Request = None):
        """Browse project directory structure."""
        try:
            _try_record_activity(request, session_id, "view_tree", f"tree:{path or '.'}")
            return JSONResponse(manager.get_tree(session_id, path, depth))
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=str(e))

    @app.get("/api/sessions/{session_id}/diff/{file_path:path}")
    async def api_get_file_diff(session_id: str, file_path: str, request: Request = None):
        """Get diff content for a specific file."""
        try:
            _try_record_activity(request, session_id, "view_diff", f"diff:{file_path}")
            session = manager.get_session(session_id)
            for f in session.diff:
                if f.path == file_path:
                    return JSONResponse({"path": f.path, "additions": f.additions, "deletions": f.deletions, "content": f.content})
            raise HTTPException(status_code=404, detail=f"File not found in diff: {file_path}")
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))

    # ------------------------------------------------------------------
    # Assist (issue resolution helper)
    # ------------------------------------------------------------------

    def _find_issue_in_session(session_id: str, issue_id: str):
        """Find an issue within a specific session."""
        session = manager.get_session(session_id)
        for i in session.issues:
            if i.id == issue_id:
                return session, i
        raise HTTPException(status_code=404, detail="이슈를 찾을 수 없습니다")

    @app.post("/api/sessions/{session_id}/issues/{issue_id}/assist")
    async def api_assist_issue_by_session(session_id: str, issue_id: str, request: Request):
        """AI assistant for resolving an issue."""
        body = await request.json()
        user_message = body.get("message", "").strip()
        if not user_message:
            raise HTTPException(status_code=400, detail="메시지를 입력해주세요")

        session, issue = _find_issue_in_session(session_id, issue_id)

        diff_content = ""
        for f in session.diff:
            if f.path == issue.file:
                diff_content = f.content
                break

        issue.assist_messages.append(AssistMessage(role="user", content=user_message))

        prompt = compose_assist_prompt(issue, diff_content, user_message)
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

        issue.assist_messages.append(AssistMessage(role="assistant", content=response))
        manager.persist()

        cli_cmd = f'claude -p "{issue.file} 파일의 이슈를 해결해주세요: {issue.title}. {issue.description}"'

        return JSONResponse({
            "response": response,
            "cli_command": cli_cmd,
            "messages": [m.model_dump(mode="json") for m in issue.assist_messages],
        })

    @app.get("/api/sessions/{session_id}/issues/{issue_id}/assist")
    async def api_get_assist_history_by_session(session_id: str, issue_id: str):
        """Get assist chat history for an issue."""
        _, issue = _find_issue_in_session(session_id, issue_id)
        return JSONResponse({
            "messages": [m.model_dump(mode="json") for m in issue.assist_messages],
        })

    @app.post("/api/sessions/{session_id}/issues/{issue_id}/assist/opinion")
    async def api_submit_assist_opinion_by_session(session_id: str, issue_id: str, request: Request):
        """Generate an AI mediator opinion and submit it to the issue thread."""
        body = await request.json() if await request.body() else {}
        user_message = (body.get("message", "") or "").strip()
        _require_human_assist_key(session_id, request, body)

        session, issue = _find_issue_in_session(session_id, issue_id)

        diff_content = ""
        for f in session.diff:
            if f.path == issue.file:
                diff_content = f.content
                break

        prompt = compose_assist_opinion_prompt(issue, diff_content, user_message)
        try:
            proc = await asyncio.create_subprocess_exec(
                "claude", "--print", "--output-format", "text", "-p", prompt,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=90)
            parsed = parse_assist_opinion(stdout.decode().strip())
            action = (parsed.get("action") or "comment").strip().lower()
            if action not in {"fix_required", "no_fix", "comment"}:
                action = "comment"
            suggested = parsed.get("suggested_severity")
            if suggested in ("", "null", None):
                suggested = None
            reasoning = (parsed.get("reasoning") or "").strip() or "도우미 AI 의견"
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"도우미 의견 생성 실패: {e}")

        try:
            result = manager.submit_opinion(
                session_id,
                issue_id,
                "human-assist",
                action,
                reasoning,
                suggested,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        manager.append_agent_chat(
            session_id,
            "assist-mediator",
            "assistant",
            f"[{action}] {reasoning}",
        )
        manager.persist()
        return JSONResponse({
            "status": "accepted",
            "opinion": {
                "model_id": "human-assist",
                "action": action,
                "reasoning": reasoning,
                "suggested_severity": suggested,
            },
            "result": result,
        })

    # Legacy assist endpoints (delegate to current session)
    @app.post("/api/issues/{issue_id}/assist")
    async def api_assist_issue(issue_id: str, request: Request):
        return await api_assist_issue_by_session(_require_current_session(), issue_id, request)

    @app.get("/api/issues/{issue_id}/assist")
    async def api_get_assist_history(issue_id: str):
        return await api_get_assist_history_by_session(_require_current_session(), issue_id)

    @app.post("/api/issues/{issue_id}/assist/opinion")
    async def api_submit_assist_opinion(issue_id: str, request: Request):
        return await api_submit_assist_opinion_by_session(_require_current_session(), issue_id, request)

    # ------------------------------------------------------------------
    # Actionable Issues
    # ------------------------------------------------------------------

    @app.get("/api/sessions/{session_id}/actionable-issues")
    async def api_get_actionable_issues(session_id: str):
        try:
            return JSONResponse(manager.get_actionable_issues(session_id))
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))

    # ------------------------------------------------------------------
    # Report & SSE
    # ------------------------------------------------------------------

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
