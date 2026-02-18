"""Tests for prompt templates."""

from ai_review.models import ModelConfig
from ai_review.prompts import build_agent_response_prompt, build_deliberation_prompt, build_review_prompt, build_verification_prompt


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

    def test_contains_arv_commands(self):
        prompt = build_review_prompt("sess1", _mc(), "http://localhost:3000")
        assert "arv get index" in prompt
        assert "arv get context" in prompt
        assert "arv report" in prompt
        assert "arv summary" in prompt
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

    def test_direct_tool_usage(self):
        prompt = build_review_prompt("sess1", _mc(), "http://localhost:3000")
        assert "Read, Grep, Glob" in prompt
        assert "arv commands only for session data" in prompt

    def test_contains_arv_get_file(self):
        prompt = build_review_prompt("sess1", _mc(), "http://localhost:3000")
        assert "arv get file" in prompt

    def test_contains_arv_get_search(self):
        prompt = build_review_prompt("sess1", _mc(), "http://localhost:3000")
        assert "arv get search" in prompt

    def test_contains_arv_get_tree(self):
        prompt = build_review_prompt("sess1", _mc(), "http://localhost:3000")
        assert "arv get tree" in prompt

    def test_no_curl_commands_in_prompt(self):
        prompt = build_review_prompt("sess1", _mc(), "http://localhost:3000", agent_key="k1")
        # "curl" may appear in "Do NOT use curl" warning, but not as an instruction to run
        assert "curl -" not in prompt
        assert "curl " not in prompt.replace("Do NOT use curl", "")

    def test_includes_implementation_context(self):
        ic = {
            "summary": "Add caching layer",
            "decisions": ["Use Redis for persistence"],
            "tradeoffs": ["Memory overhead"],
            "known_issues": ["No TTL support"],
            "out_of_scope": ["Cache invalidation"],
        }
        prompt = build_review_prompt("sess1", _mc(), "http://localhost:3000", implementation_context=ic)
        assert "## Implementation Context" in prompt
        assert "### 변경 요약" in prompt
        assert "Add caching layer" in prompt
        assert "### 의도적 결정" in prompt
        assert "Use Redis for persistence" in prompt
        assert "### 트레이드오프" in prompt
        assert "Memory overhead" in prompt
        assert "### 알려진 제한" in prompt
        assert "No TTL support" in prompt
        assert "### 의도적 제외" in prompt
        assert "Cache invalidation" in prompt

    def test_no_context_omits_section(self):
        prompt = build_review_prompt("sess1", _mc(), "http://localhost:3000", implementation_context=None)
        assert "## Implementation Context" not in prompt

    def test_respects_decisions_instruction(self):
        prompt = build_review_prompt("sess1", _mc(), "http://localhost:3000")
        assert "Respect the author's stated decisions" in prompt


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

    def test_contains_arv_commands(self):
        prompt = build_deliberation_prompt("sess1", _mc("gpt"), ["iss1"], "http://localhost:3000")
        assert "arv get thread" in prompt
        assert "arv opinion" in prompt

    def test_contains_turn_for_deliberation(self):
        prompt = build_deliberation_prompt("sess1", _mc("gpt"), ["iss1"], "http://localhost:3000", turn=2)
        assert "Current deliberation turn: 2" in prompt

    def test_no_curl_commands_in_deliberation(self):
        prompt = build_deliberation_prompt("sess1", _mc("gpt"), ["iss1"], "http://localhost:3000", turn=1, agent_key="k_test_456")
        assert "curl -" not in prompt

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

    def test_direct_tool_usage(self):
        prompt = build_deliberation_prompt("sess1", _mc("gpt"), ["iss1"], "http://localhost:3000")
        assert "Read, Grep, Glob" in prompt
        assert "arv commands only for session data" in prompt

    def test_file_content_available(self):
        prompt = build_deliberation_prompt("sess1", _mc("gpt"), ["iss1"], "http://localhost:3000")
        assert "arv get file" in prompt

    def test_confidence_guidance_included(self):
        prompt = build_deliberation_prompt("sess1", _mc("gpt"), ["iss1"], "http://localhost:3000")
        assert "confidence" in prompt
        assert "0.0" in prompt or "0.5" in prompt
        assert "uncertain" in prompt.lower() or "speculative" in prompt.lower()


class TestBuildAgentResponsePrompt:
    def test_contains_model_id(self):
        prompt = build_agent_response_prompt("sess1", _mc("coding-agent"), "http://localhost:3000")
        assert "coding-agent" in prompt

    def test_contains_session_id(self):
        prompt = build_agent_response_prompt("sess1", _mc(), "http://localhost:3000")
        assert "sess1" in prompt

    def test_contains_arv_get_confirmed(self):
        prompt = build_agent_response_prompt("sess1", _mc(), "http://localhost:3000")
        assert "arv get confirmed" in prompt

    def test_contains_arv_respond(self):
        prompt = build_agent_response_prompt("sess1", _mc(), "http://localhost:3000")
        assert "arv respond" in prompt

    def test_contains_accept_dispute_partial(self):
        prompt = build_agent_response_prompt("sess1", _mc(), "http://localhost:3000")
        assert "accept" in prompt
        assert "dispute" in prompt
        assert "partial" in prompt

    def test_no_curl_commands_in_agent_response(self):
        prompt = build_agent_response_prompt("sess1", _mc(), "http://localhost:3000", agent_key="k_test")
        assert "curl -" not in prompt

    def test_contains_redeliberation_guidance(self):
        prompt = build_agent_response_prompt("sess1", _mc(), "http://localhost:3000")
        assert "re-deliberation" in prompt

    def test_contains_arv_get_file(self):
        prompt = build_agent_response_prompt("sess1", _mc(), "http://localhost:3000")
        assert "arv get file" in prompt

    def test_contains_arv_get_thread(self):
        prompt = build_agent_response_prompt("sess1", _mc(), "http://localhost:3000")
        assert "arv get thread" in prompt


class TestBuildVerificationPrompt:
    def test_contains_model_id(self):
        prompt = build_verification_prompt("sess1", _mc("opus"), "http://localhost:3000")
        assert "opus" in prompt

    def test_contains_session_id(self):
        prompt = build_verification_prompt("sess1", _mc(), "http://localhost:3000")
        assert "sess1" in prompt

    def test_contains_arv_get_delta(self):
        prompt = build_verification_prompt("sess1", _mc(), "http://localhost:3000")
        assert "arv get delta" in prompt

    def test_contains_arv_opinion(self):
        prompt = build_verification_prompt("sess1", _mc(), "http://localhost:3000")
        assert "arv opinion" in prompt

    def test_contains_arv_report(self):
        prompt = build_verification_prompt("sess1", _mc(), "http://localhost:3000")
        assert "arv report" in prompt

    def test_contains_verification_round(self):
        prompt = build_verification_prompt("sess1", _mc(), "http://localhost:3000", verification_round=3)
        assert "Round 3" in prompt

    def test_no_curl_commands_in_verification(self):
        prompt = build_verification_prompt("sess1", _mc(), "http://localhost:3000", agent_key="k_verify_123")
        assert "curl -" not in prompt

    def test_contains_arv_get_file(self):
        prompt = build_verification_prompt("sess1", _mc(), "http://localhost:3000")
        assert "arv get file" in prompt

    def test_direct_tool_usage(self):
        prompt = build_verification_prompt("sess1", _mc(), "http://localhost:3000")
        assert "Read, Grep, Glob" in prompt
        assert "arv commands only for session data" in prompt

    def test_system_prompt_included(self):
        mc = _mc(system_prompt="Focus on security regressions.")
        prompt = build_verification_prompt("sess1", mc, "http://localhost:3000")
        assert "## System Instructions" in prompt
        assert "security regressions" in prompt

    def test_no_system_prompt_omits_section(self):
        prompt = build_verification_prompt("sess1", _mc(), "http://localhost:3000")
        assert "## System Instructions" not in prompt
