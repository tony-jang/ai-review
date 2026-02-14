"""Tests for prompt templates."""

from ai_review.models import ModelConfig
from ai_review.prompts import build_deliberation_prompt, build_review_prompt


def _mc(id: str = "opus", role: str = "", **kwargs) -> ModelConfig:
    return ModelConfig(id=id, role=role, **kwargs)


class TestBuildReviewPrompt:
    def test_contains_model_id(self):
        prompt = build_review_prompt("sess1", _mc("opus", "security"), "http://localhost:3000")
        assert "opus" in prompt

    def test_contains_role(self):
        prompt = build_review_prompt("sess1", _mc("opus", "security review"), "http://localhost:3000")
        assert "security review" in prompt

    def test_contains_session_id(self):
        prompt = build_review_prompt("sess1", _mc(), "http://localhost:3000")
        assert "sess1" in prompt

    def test_contains_api_endpoints(self):
        prompt = build_review_prompt("sess1", _mc(), "http://localhost:3000")
        assert "/api/sessions/sess1/index" in prompt
        assert "/api/sessions/sess1/context" in prompt
        assert "/api/sessions/sess1/reviews" in prompt
        assert "/api/sessions/sess1/overall-reviews" in prompt
        assert "line_start" in prompt
        assert "line_end" in prompt

    def test_empty_role_omits_focus(self):
        prompt = build_review_prompt("sess1", _mc(), "http://localhost:3000")
        assert "review focus" not in prompt

    def test_nonempty_role_includes_focus(self):
        prompt = build_review_prompt("sess1", _mc("opus", "perf"), "http://localhost:3000")
        assert "review focus" in prompt

    def test_system_prompt_included(self):
        mc = _mc(system_prompt="Always check for SQL injection vulnerabilities.")
        prompt = build_review_prompt("sess1", mc, "http://localhost:3000")
        assert "## System Instructions" in prompt
        assert "SQL injection" in prompt

    def test_no_system_prompt_omits_section(self):
        prompt = build_review_prompt("sess1", _mc(), "http://localhost:3000")
        assert "## System Instructions" not in prompt

    def test_review_focus_tags(self):
        mc = _mc(review_focus=["security", "auth", "injection"])
        prompt = build_review_prompt("sess1", mc, "http://localhost:3000")
        assert "security" in prompt
        assert "auth" in prompt
        assert "injection" in prompt

    def test_role_and_review_focus_together(self):
        mc = _mc(role="Security Reviewer", review_focus=["xss", "csrf"])
        prompt = build_review_prompt("sess1", mc, "http://localhost:3000")
        assert "Security Reviewer" in prompt
        assert "xss" in prompt

    def test_includes_agent_key_header(self):
        prompt = build_review_prompt("sess1", _mc("codex"), "http://localhost:3000", agent_key="k_test_123")
        assert "X-Agent-Key: k_test_123" in prompt

    def test_no_local_tools(self):
        prompt = build_review_prompt("sess1", _mc(), "http://localhost:3000")
        assert "Do NOT use local tools" in prompt
        # Should not instruct to use git/sed/rg directly
        assert "Use local tools" not in prompt

    def test_contains_file_api(self):
        prompt = build_review_prompt("sess1", _mc(), "http://localhost:3000")
        assert "/files/" in prompt

    def test_contains_search_api(self):
        prompt = build_review_prompt("sess1", _mc(), "http://localhost:3000")
        assert "/search?" in prompt

    def test_contains_tree_api(self):
        prompt = build_review_prompt("sess1", _mc(), "http://localhost:3000")
        assert "/tree?" in prompt

    def test_agent_key_for_get_requests(self):
        prompt = build_review_prompt("sess1", _mc(), "http://localhost:3000", agent_key="k1")
        assert "both GET and POST" in prompt


class TestBuildDeliberationPrompt:
    def test_contains_model_id(self):
        prompt = build_deliberation_prompt("sess1", _mc("gpt"), ["iss1"], "http://localhost:3000")
        assert "gpt" in prompt

    def test_contains_issue_ids(self):
        prompt = build_deliberation_prompt("sess1", _mc("gpt"), ["abc123", "def456"], "http://localhost:3000")
        assert "abc123" in prompt
        assert "def456" in prompt

    def test_contains_session_id(self):
        prompt = build_deliberation_prompt("sess1", _mc("gpt"), ["iss1"], "http://localhost:3000")
        assert "sess1" in prompt

    def test_contains_api_endpoints(self):
        prompt = build_deliberation_prompt("sess1", _mc("gpt"), ["iss1"], "http://localhost:3000")
        assert "/api/sessions/sess1/issues/" in prompt
        assert "/thread" in prompt
        assert "/opinions" in prompt
        assert "/api/sessions/sess1/overall-reviews" in prompt

    def test_contains_turn_for_deliberation(self):
        prompt = build_deliberation_prompt("sess1", _mc("gpt"), ["iss1"], "http://localhost:3000", turn=2)
        assert "Current deliberation turn: 2" in prompt
        assert '"turn":2' in prompt

    def test_includes_agent_key_header(self):
        prompt = build_deliberation_prompt("sess1", _mc("gpt"), ["iss1"], "http://localhost:3000", turn=1, agent_key="k_test_456")
        assert "X-Agent-Key: k_test_456" in prompt

    def test_contains_action_options(self):
        prompt = build_deliberation_prompt("sess1", _mc("gpt"), ["iss1"], "http://localhost:3000")
        assert "fix_required" in prompt
        assert "no_fix" in prompt
        assert "comment" in prompt

    def test_contains_decision_rules(self):
        prompt = build_deliberation_prompt("sess1", _mc("gpt"), ["iss1"], "http://localhost:3000")
        assert "Judge the issue itself" in prompt
        assert "Do NOT use fix_required just to align with a person" in prompt

    def test_contains_issue_mention_syntax(self):
        prompt = build_deliberation_prompt("sess1", _mc("gpt"), ["iss1"], "http://localhost:3000")
        assert "@issue_id" in prompt
        assert "@1d9f63acf240" in prompt

    def test_contains_markdown_guidance(self):
        prompt = build_deliberation_prompt("sess1", _mc("gpt"), ["iss1"], "http://localhost:3000")
        assert "**bold**" in prompt
        assert "~~strikethrough~~" in prompt

    def test_system_prompt_included(self):
        mc = _mc("gpt", system_prompt="Focus on performance implications.")
        prompt = build_deliberation_prompt("sess1", mc, ["iss1"], "http://localhost:3000")
        assert "## System Instructions" in prompt
        assert "performance implications" in prompt

    def test_no_system_prompt_omits_section(self):
        prompt = build_deliberation_prompt("sess1", _mc("gpt"), ["iss1"], "http://localhost:3000")
        assert "## System Instructions" not in prompt

    def test_no_local_tools(self):
        prompt = build_deliberation_prompt("sess1", _mc("gpt"), ["iss1"], "http://localhost:3000")
        assert "Do NOT use local tools" in prompt

    def test_uses_session_scoped_thread_url(self):
        prompt = build_deliberation_prompt("sess1", _mc("gpt"), ["iss1"], "http://localhost:3000")
        assert "/api/sessions/sess1/issues/" in prompt

    def test_file_content_api_available(self):
        prompt = build_deliberation_prompt("sess1", _mc("gpt"), ["iss1"], "http://localhost:3000")
        assert "/files/" in prompt
