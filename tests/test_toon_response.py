"""Unit tests for TOON response helper."""

from __future__ import annotations

import json

import pytest
from fastapi import Request
from fastapi.responses import JSONResponse, PlainTextResponse
from toon import decode as toon_decode

from ai_review.toon_response import is_agent_request, toon_or_json


# ---------------------------------------------------------------------------
# Helpers: build a minimal Request with optional headers
# ---------------------------------------------------------------------------

def _make_request(headers: dict[str, str] | None = None) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/test",
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
    }
    return Request(scope)


# ---------------------------------------------------------------------------
# is_agent_request
# ---------------------------------------------------------------------------

class TestIsAgentRequest:
    def test_none_request(self):
        assert is_agent_request(None) is False

    def test_no_header(self):
        assert is_agent_request(_make_request()) is False

    def test_empty_header(self):
        assert is_agent_request(_make_request({"x-agent-key": ""})) is False

    def test_whitespace_only(self):
        assert is_agent_request(_make_request({"x-agent-key": "   "})) is False

    def test_valid_key(self):
        assert is_agent_request(_make_request({"x-agent-key": "abc123"})) is True

    def test_key_with_surrounding_spaces(self):
        assert is_agent_request(_make_request({"x-agent-key": "  key  "})) is True


# ---------------------------------------------------------------------------
# toon_or_json
# ---------------------------------------------------------------------------

class TestToonOrJson:
    def test_returns_json_without_agent_key(self):
        data = {"hello": "world"}
        resp = toon_or_json(_make_request(), data)
        assert isinstance(resp, JSONResponse)

    def test_returns_json_for_none_request(self):
        resp = toon_or_json(None, {"a": 1})
        assert isinstance(resp, JSONResponse)

    def test_returns_toon_with_agent_key(self):
        data = {"items": [{"name": "a", "val": 1}, {"name": "b", "val": 2}]}
        resp = toon_or_json(_make_request({"x-agent-key": "k"}), data)
        assert isinstance(resp, PlainTextResponse)
        assert resp.media_type == "text/toon; charset=utf-8"

    def test_toon_roundtrip_simple(self):
        data = {"items": [{"x": 1}, {"x": 2}]}
        resp = toon_or_json(_make_request({"x-agent-key": "k"}), data)
        decoded = toon_decode(resp.body.decode())
        assert decoded == data

    def test_toon_roundtrip_nested(self):
        data = {
            "session_id": "abc",
            "files": [
                {"path": "a.py", "additions": 3, "deletions": 1},
                {"path": "b.py", "additions": 0, "deletions": 5},
            ],
        }
        resp = toon_or_json(_make_request({"x-agent-key": "k"}), data)
        decoded = toon_decode(resp.body.decode())
        assert decoded == data

    def test_toon_roundtrip_flat_dict(self):
        """Flat dict (no arrays) should still roundtrip."""
        data = {"status": "ok", "count": 42}
        resp = toon_or_json(_make_request({"x-agent-key": "k"}), data)
        decoded = toon_decode(resp.body.decode())
        assert decoded == data

    def test_json_content_type(self):
        resp = toon_or_json(_make_request(), {"a": 1})
        assert resp.media_type == "application/json"
