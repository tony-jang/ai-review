"""Unit tests for ai_review.assist module."""

from __future__ import annotations

import pytest

from ai_review.assist import (
    compose_assist_opinion_prompt,
    compose_assist_prompt,
    issue_location_text,
    parse_assist_opinion,
)
from ai_review.models import AssistMessage, Issue, Opinion, OpinionAction, Severity


def _make_issue(**kwargs) -> Issue:
    defaults = {"title": "Bug found", "severity": Severity.HIGH, "file": "app.py", "description": "desc"}
    defaults.update(kwargs)
    return Issue(**defaults)


# --- issue_location_text ---


class TestIssueLocationText:
    def test_file_only(self):
        issue = _make_issue()
        assert issue_location_text(issue) == "app.py"

    def test_single_line(self):
        issue = _make_issue(line=10)
        assert issue_location_text(issue) == "app.py:10"

    def test_line_range(self):
        issue = _make_issue(line_start=5, line_end=10)
        assert issue_location_text(issue) == "app.py:5-10"

    def test_swapped_range(self):
        issue = _make_issue(line_start=20, line_end=5)
        assert issue_location_text(issue) == "app.py:5-20"

    def test_line_start_only(self):
        issue = _make_issue(line_start=7)
        assert issue_location_text(issue) == "app.py:7"

    def test_line_takes_precedence_when_no_line_start(self):
        issue = _make_issue(line=3, line_end=8)
        assert issue_location_text(issue) == "app.py:3-8"


# --- compose_assist_prompt ---


class TestComposeAssistPrompt:
    def test_contains_issue_info(self):
        issue = _make_issue(title="SQL injection", severity=Severity.CRITICAL, description="raw sql")
        prompt = compose_assist_prompt(issue, "", "help")
        assert "SQL injection" in prompt
        assert "심각" in prompt
        assert "app.py" in prompt
        assert "raw sql" in prompt

    def test_includes_diff(self):
        issue = _make_issue()
        prompt = compose_assist_prompt(issue, "+added line\n-removed line", "check")
        assert "```diff" in prompt
        assert "+added line" in prompt

    def test_includes_thread(self):
        op = Opinion(model_id="codex", action=OpinionAction.FIX_REQUIRED, reasoning="confirmed bug")
        issue = _make_issue(thread=[op])
        prompt = compose_assist_prompt(issue, "", "msg")
        assert "리뷰어 토론" in prompt
        assert "codex" in prompt
        assert "수정필요" in prompt
        assert "confirmed bug" in prompt

    def test_includes_previous_messages(self):
        msgs = [
            AssistMessage(role="user", content="이전 질문"),
            AssistMessage(role="assistant", content="이전 답변"),
        ]
        issue = _make_issue(assist_messages=msgs)
        prompt = compose_assist_prompt(issue, "", "새 질문")
        assert "이전 대화" in prompt
        assert "이전 질문" in prompt
        assert "이전 답변" in prompt

    def test_korean_instruction(self):
        issue = _make_issue()
        prompt = compose_assist_prompt(issue, "", "help")
        assert "한국어로 답변해주세요" in prompt

    def test_includes_suggestion(self):
        issue = _make_issue(suggestion="use parameterized queries")
        prompt = compose_assist_prompt(issue, "", "msg")
        assert "수정 제안" in prompt
        assert "use parameterized queries" in prompt


# --- compose_assist_opinion_prompt ---


class TestComposeAssistOpinionPrompt:
    def test_json_format_instruction(self):
        issue = _make_issue()
        prompt = compose_assist_opinion_prompt(issue, "", "")
        assert "JSON" in prompt
        assert "action" in prompt

    def test_no_extra_text_warning(self):
        issue = _make_issue()
        prompt = compose_assist_opinion_prompt(issue, "", "")
        assert "JSON 외 텍스트를 절대 출력하지" in prompt

    def test_includes_user_instruction(self):
        issue = _make_issue()
        prompt = compose_assist_opinion_prompt(issue, "", "중요도 낮춰줘")
        assert "사용자 지시" in prompt
        assert "중요도 낮춰줘" in prompt

    def test_includes_diff(self):
        issue = _make_issue()
        prompt = compose_assist_opinion_prompt(issue, "+new code", "")
        assert "```diff" in prompt
        assert "+new code" in prompt


# --- parse_assist_opinion ---


class TestParseAssistOpinion:
    def test_parses_clean_json(self):
        text = '{"action":"fix_required","reasoning":"bug confirmed","suggested_severity":"high"}'
        result = parse_assist_opinion(text)
        assert result["action"] == "fix_required"
        assert result["reasoning"] == "bug confirmed"

    def test_parses_json_with_surrounding_text(self):
        text = 'Here is my opinion:\n{"action":"no_fix","reasoning":"false positive"}\nDone.'
        result = parse_assist_opinion(text)
        assert result["action"] == "no_fix"

    def test_raises_on_no_json(self):
        with pytest.raises(ValueError, match="assist opinion parse failed"):
            parse_assist_opinion("no json here at all")

    def test_raises_on_empty(self):
        with pytest.raises(ValueError, match="assist opinion parse failed"):
            parse_assist_opinion("")

    def test_parses_json_with_whitespace(self):
        text = '  \n  {"action":"comment","reasoning":"ok"}  \n  '
        result = parse_assist_opinion(text)
        assert result["action"] == "comment"
