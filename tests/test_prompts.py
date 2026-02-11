"""Tests for prompt templates."""

from ai_review.prompts import build_deliberation_prompt, build_review_prompt


class TestBuildReviewPrompt:
    def test_contains_model_id(self):
        prompt = build_review_prompt("sess1", "opus", "security", "http://localhost:3000")
        assert "opus" in prompt

    def test_contains_role(self):
        prompt = build_review_prompt("sess1", "opus", "security review", "http://localhost:3000")
        assert "security review" in prompt

    def test_contains_session_id(self):
        prompt = build_review_prompt("sess1", "opus", "", "http://localhost:3000")
        assert "sess1" in prompt

    def test_contains_api_endpoints(self):
        prompt = build_review_prompt("sess1", "opus", "", "http://localhost:3000")
        assert "/api/sessions/sess1/context" in prompt
        assert "/api/sessions/sess1/reviews" in prompt

    def test_empty_role_omits_focus(self):
        prompt = build_review_prompt("sess1", "opus", "", "http://localhost:3000")
        assert "review focus" not in prompt

    def test_nonempty_role_includes_focus(self):
        prompt = build_review_prompt("sess1", "opus", "perf", "http://localhost:3000")
        assert "review focus" in prompt


class TestBuildDeliberationPrompt:
    def test_contains_model_id(self):
        prompt = build_deliberation_prompt("sess1", "gpt", ["iss1"], "http://localhost:3000")
        assert "gpt" in prompt

    def test_contains_issue_ids(self):
        prompt = build_deliberation_prompt("sess1", "gpt", ["abc123", "def456"], "http://localhost:3000")
        assert "abc123" in prompt
        assert "def456" in prompt

    def test_contains_session_id(self):
        prompt = build_deliberation_prompt("sess1", "gpt", ["iss1"], "http://localhost:3000")
        assert "sess1" in prompt

    def test_contains_api_endpoints(self):
        prompt = build_deliberation_prompt("sess1", "gpt", ["iss1"], "http://localhost:3000")
        assert "/api/issues/" in prompt
        assert "/thread" in prompt
        assert "/opinions" in prompt

    def test_contains_action_options(self):
        prompt = build_deliberation_prompt("sess1", "gpt", ["iss1"], "http://localhost:3000")
        assert "agree" in prompt
        assert "disagree" in prompt
        assert "clarify" in prompt
