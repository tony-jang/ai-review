"""TOON response helper for LLM-facing endpoints."""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse, PlainTextResponse
from toon import encode as toon_encode


def is_agent_request(request: Request | None) -> bool:
    """Return True when the request carries an X-Agent-Key header."""
    if request is None:
        return False
    return bool((request.headers.get("x-agent-key") or "").strip())


def toon_or_json(request: Request | None, data) -> JSONResponse | PlainTextResponse:
    """Return TOON for agent requests, JSON otherwise."""
    if is_agent_request(request):
        return PlainTextResponse(toon_encode(data), media_type="text/toon; charset=utf-8")
    return JSONResponse(data)
