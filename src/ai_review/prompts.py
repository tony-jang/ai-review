"""Prompt templates for triggering LLM clients."""

from __future__ import annotations


def build_review_prompt(
    session_id: str,
    model_id: str,
    role: str,
    api_base_url: str,
) -> str:
    """Build a prompt that instructs an LLM to perform a code review via REST API."""
    parts = [
        f"You are a code reviewer (model: {model_id}).",
    ]
    if role:
        parts.append(f"Your review focus: {role}")

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
    model_id: str,
    issue_ids: list[str],
    api_base_url: str,
) -> str:
    """Build a prompt that instructs an LLM to deliberate on pending issues."""
    issue_list = "\n".join(f"  - {iid}" for iid in issue_ids)
    parts = [
        f"You are a code reviewer (model: {model_id}) participating in a deliberation round.",
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
        f"3. Submit your opinion:",
        f"   curl -X POST {api_base_url}/api/issues/{{issue_id}}/opinions \\",
        f'     -H "Content-Type: application/json" \\',
        f'     -d \'{{"model_id": "{model_id}", "action": "...", "reasoning": "...", "suggested_severity": "..."}}\'',
        "   - action: one of agree/disagree/clarify",
        "   - reasoning: your analysis (be specific)",
        "   - suggested_severity: your recommended severity (critical/high/medium/low) if you agree",
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
        "- Write all reasoning in Korean.",
        f"- Session ID: {session_id}",
    ]
    return "\n".join(parts)
