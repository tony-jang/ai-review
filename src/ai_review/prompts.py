"""Prompt templates for triggering LLM clients."""

from __future__ import annotations


def build_review_prompt(
    session_id: str,
    model_id: str,
    role: str,
    mcp_server_url: str,
) -> str:
    """Build a prompt that instructs an LLM to perform a code review via MCP tools."""
    parts = [
        f"You are a code reviewer (model: {model_id}).",
    ]
    if role:
        parts.append(f"Your review focus: {role}")

    parts.extend([
        "",
        "## Instructions",
        "",
        "You have access to ai-review MCP tools. Follow these steps exactly:",
        "",
        f"1. Call `get_review_context` to retrieve the diff and project knowledge.",
        "2. Review the code changes thoroughly based on your assigned focus area.",
        "3. Call `submit_review` with your findings:",
        f'   - model_id: "{model_id}"',
        "   - issues: list of objects with fields: title, severity (critical/high/medium/low), file, line, description, suggestion",
        "   - summary: brief overall assessment",
        "",
        "## Important",
        "",
        "- Review independently. Do not ask for human input.",
        "- Be specific: include file paths and line numbers.",
        "- Only report real issues. Do not fabricate problems.",
        "- Complete the review in a single turn by calling the MCP tools.",
        f"- Session ID: {session_id}",
    ])
    return "\n".join(parts)


def build_deliberation_prompt(
    session_id: str,
    model_id: str,
    issue_ids: list[str],
    mcp_server_url: str,
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
        "1. Call `get_issue_thread` with the issue_id to read the issue details and discussion.",
        "2. Analyze the issue carefully — consider the code context, severity, and other opinions.",
        "3. Call `submit_opinion` with:",
        f'   - model_id: "{model_id}"',
        "   - issue_id: the issue ID",
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
        f"- Session ID: {session_id}",
    ]
    return "\n".join(parts)
