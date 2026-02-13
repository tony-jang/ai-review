"""Prompt templates for triggering LLM clients."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ai_review.models import ModelConfig


def build_review_prompt(
    session_id: str,
    model_config: ModelConfig,
    api_base_url: str,
) -> str:
    """Build a prompt that instructs an LLM to perform a code review via REST API."""
    model_id = model_config.id
    role = model_config.role
    review_focus = model_config.review_focus
    system_prompt = model_config.system_prompt

    parts = [
        f"You are a code reviewer (model: {model_id}).",
    ]

    if system_prompt:
        parts.extend(["", "## System Instructions", "", system_prompt])

    if role:
        parts.append(f"Your review focus: {role}")
    if review_focus:
        parts.append(f"Focus areas: {', '.join(review_focus)}")

    parts.extend([
        "",
        "## Instructions",
        "",
        "Follow these steps exactly:",
        "",
        f"1. Retrieve the context index first:",
        f"   curl {api_base_url}/api/sessions/{session_id}/index",
        "2. Use local tools (git/sed/rg) to inspect only the necessary files and line ranges.",
        "3. If needed, retrieve per-file server context:",
        f"   curl \"{api_base_url}/api/sessions/{session_id}/context?file=<path>\"",
        "4. Review the code changes thoroughly based on your assigned focus area.",
        f"5. Submit your review:",
        f"   curl -X POST {api_base_url}/api/sessions/{session_id}/reviews \\",
        f'     -H "Content-Type: application/json" \\',
        f'     -d \'{{"model_id": "{model_id}", "issues": [...], "summary": "..."}}\'',
        "   - issues: list of objects with fields: title, severity (critical/high/medium/low), file, line, description, suggestion",
        "   - summary: brief overall assessment",
        "",
        "## Important",
        "",
        "- Review independently. Do not ask for human input.",
        "- Be specific: include file paths and line numbers.",
        "- Only report real issues. Do not fabricate problems.",
        "- If you find no issues, you MUST still submit a review with an empty issues list and a summary.",
        "- Complete the review in a single turn.",
        "- Avoid loading full repository context. Inspect only relevant files from the index.",
        "- Write all title, description, suggestion, and summary fields in Korean.",
        f"- Session ID: {session_id}",
    ])
    return "\n".join(parts)


def build_deliberation_prompt(
    session_id: str,
    model_config: ModelConfig,
    issue_ids: list[str],
    api_base_url: str,
) -> str:
    """Build a prompt that instructs an LLM to deliberate on pending issues."""
    model_id = model_config.id
    system_prompt = model_config.system_prompt

    issue_list = "\n".join(f"  - {iid}" for iid in issue_ids)
    parts = [
        f"You are a code reviewer (model: {model_id}) participating in a deliberation round.",
    ]

    if system_prompt:
        parts.extend(["", "## System Instructions", "", system_prompt])

    parts.extend([
        "",
        "## Instructions",
        "",
        "Other reviewers have raised issues. You must review each one and share your opinion.",
        "",
        "For each issue ID listed below:",
        "",
        f"1. Retrieve the issue thread:",
        f"   curl {api_base_url}/api/issues/{{issue_id}}/thread",
        "2. Analyze the issue carefully — consider the code context, severity, and other opinions.",
        "   - IMPORTANT: Judge the issue itself, not whether you personally like another reviewer's wording.",
        "   - If you think the issue should be dismissed, choose action=no_fix explicitly.",
        f"3. Submit your opinion:",
        f"   curl -X POST {api_base_url}/api/issues/{{issue_id}}/opinions \\",
        f'     -H "Content-Type: application/json" \\',
        f'     -d \'{{"model_id": "{model_id}", "action": "...", "reasoning": "...", "suggested_severity": "..."}}\'',
        "   - action: one of fix_required/no_fix/comment",
        "   - reasoning: your analysis (be specific)",
        "   - suggested_severity: use only when action=fix_required (critical/high/medium/low). Leave null/omit otherwise.",
        "",
        "Decision rules:",
        "- fix_required: You judge this issue as valid and code change is needed.",
        "- no_fix: You judge this issue as invalid / should be dismissed.",
        "- comment: You have an opinion or question but are not ready to decide yet.",
        "- Do NOT use fix_required just to align with a person. If your final stance is dismiss, use no_fix.",
        "",
        "## Pending issue IDs",
        "",
        issue_list,
        "",
        "## Important",
        "",
        "- Process ALL listed issues.",
        "- Deliberate independently. Do not ask for human input.",
        "- Be concise but substantive in your reasoning.",
        "- You may mention other reviewers using @model_id (e.g., @codex) when asking follow-up in your reasoning.",
        "- Write all reasoning in Korean.",
        f"- Session ID: {session_id}",
    ])
    return "\n".join(parts)
