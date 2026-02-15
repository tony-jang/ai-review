"""Prompt templates for triggering LLM clients."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ai_review.models import ModelConfig

STRICTNESS_INSTRUCTIONS: dict[str, str] = {
    "strict": (
        "## Review Strictness: Strict\n\n"
        "- Report every issue you find, no matter how minor.\n"
        "- Include style, naming, documentation, and potential edge-case issues.\n"
        "- Err on the side of reporting — false positives are acceptable."
    ),
    "balanced": (
        "## Review Strictness: Balanced\n\n"
        "- Focus on issues that have real impact on correctness, security, or maintainability.\n"
        "- Skip purely stylistic or trivial nitpicks unless they hurt readability significantly.\n"
        "- Aim for actionable, substantive feedback."
    ),
    "lenient": (
        "## Review Strictness: Lenient\n\n"
        "- Only report critical bugs, security vulnerabilities, or data-loss risks.\n"
        "- Ignore style, naming, minor code smells, and low-impact suggestions.\n"
        "- Keep the review minimal — only flag what truly needs fixing."
    ),
}


def _render_implementation_context(ic: dict) -> str:
    """Render implementation context dict into markdown sections."""
    sections: list[str] = ["## Implementation Context", ""]
    if ic.get("summary"):
        sections.extend(["### 변경 요약", ic["summary"], ""])
    if ic.get("decisions"):
        sections.append("### 의도적 결정")
        for d in ic["decisions"]:
            sections.append(f"- {d}")
        sections.append("")
    if ic.get("tradeoffs"):
        sections.append("### 트레이드오프")
        for t in ic["tradeoffs"]:
            sections.append(f"- {t}")
        sections.append("")
    if ic.get("known_issues"):
        sections.append("### 알려진 제한")
        for k in ic["known_issues"]:
            sections.append(f"- {k}")
        sections.append("")
    if ic.get("out_of_scope"):
        sections.append("### 의도적 제외")
        for o in ic["out_of_scope"]:
            sections.append(f"- {o}")
        sections.append("")
    return "\n".join(sections)


def build_review_prompt(
    session_id: str,
    model_config: ModelConfig,
    api_base_url: str,
    agent_key: str = "",
    implementation_context: dict | None = None,
) -> str:
    """Build a prompt that instructs an LLM to perform a code review via REST API."""
    model_id = model_config.id
    role = model_config.role
    review_focus = model_config.review_focus
    system_prompt = model_config.system_prompt
    base = api_base_url

    parts = [
        f"You are a code reviewer (model: {model_id}).",
    ]

    if system_prompt:
        parts.extend(["", "## System Instructions", "", system_prompt])

    strictness = getattr(model_config, "strictness", "balanced") or "balanced"
    if strictness in STRICTNESS_INSTRUCTIONS:
        parts.extend(["", STRICTNESS_INSTRUCTIONS[strictness]])

    if implementation_context:
        parts.extend(["", _render_implementation_context(implementation_context)])

    if role:
        parts.append(f"Your review focus: {role}")
    if review_focus:
        parts.append(f"Focus areas: {', '.join(review_focus)}")

    parts.extend([
        "",
        "## Authentication",
        "",
        f"- X-Agent-Key: {agent_key}",
        "- Include this header in ALL requests (both GET and POST).",
        "",
        "## Instructions",
        "",
        "Follow these steps exactly:",
        "",
        f"1. Retrieve the context index:",
        f'   curl -H "X-Agent-Key: {agent_key}" {base}/api/sessions/{session_id}/index',
        f"2. Read source files to understand context around changed hunks:",
        f'   curl -H "X-Agent-Key: {agent_key}" "{base}/api/sessions/{session_id}/files/{{path}}?start={{n}}&end={{n}}"',
        f"3. Search for symbols or usages if needed:",
        f'   curl -H "X-Agent-Key: {agent_key}" "{base}/api/sessions/{session_id}/search?q={{keyword}}&glob={{pattern}}"',
        f"4. Browse project structure if needed:",
        f'   curl -H "X-Agent-Key: {agent_key}" "{base}/api/sessions/{session_id}/tree?path={{dir}}&depth={{n}}"',
        f"5. Retrieve per-file diff context if needed:",
        f'   curl -H "X-Agent-Key: {agent_key}" "{base}/api/sessions/{session_id}/context?file={{path}}"',
        "6. Review the code changes thoroughly based on your assigned focus area.",
        f"7. Submit your review:",
        f"   curl -X POST {base}/api/sessions/{session_id}/reviews \\",
        f'     -H "Content-Type: application/json" \\',
        f'     -H "X-Agent-Key: {agent_key}" \\',
        f'     -d \'{{"model_id": "{model_id}", "issues": [...], "summary": "..."}}\'',
        "   - issues: list of objects with fields: title, severity (critical/high/medium/low), file, line_start, line_end, line(optional), description, suggestion",
        "   - description and suggestion MUST be valid Markdown (not plain single-line text).",
        "   - description format (recommended):",
        "     ### 문제",
        "     ...",
        "     ### 근거",
        "     - file:line 근거",
        "     ### 영향",
        "     ...",
        "   - suggestion format (recommended): checklist/bullets and code block when needed.",
        "   - Prefer explicit ranges: set line_start/line_end. For single-line issues, set line_start == line_end.",
        "   - summary: brief overall assessment",
        f"8. Submit your overall verdict for this review turn (turn=0):",
        f"   curl -X POST {base}/api/sessions/{session_id}/overall-reviews \\",
        f'     -H "Content-Type: application/json" \\',
        f'     -H "X-Agent-Key: {agent_key}" \\',
        f'     -d \'{{"model_id":"{model_id}","task_type":"review","turn":0,"merge_decision":"mergeable|not_mergeable|needs_discussion","summary":"...","highlights":["..."],"blockers":["..."],"recommendations":["..."]}}\'',
        "   - merge_decision rules: mergeable(머지 가능), not_mergeable(머지 불가), needs_discussion(추가 토론 필요).",
        "   - blockers에는 머지를 막는 핵심 근거만 간단히 작성.",
        "",
        "## Important",
        "",
        "- Do NOT use local tools (git, sed, rg, cat, etc). Use only the APIs above.",
        "- Review independently. Do not ask for human input.",
        "- Be specific: include file paths and line numbers.",
        "- Only report real issues. Do not fabricate problems.",
        "- If you find no issues, you MUST still submit a review with an empty issues list and a summary.",
        "- Complete the review in a single turn.",
        "- Write all title, description, suggestion, and summary fields in Korean.",
        "- Respect the author's stated decisions, but flag issues if they have concrete negative impact.",
        f"- Session ID: {session_id}",
    ])
    return "\n".join(parts)


def build_deliberation_prompt(
    session_id: str,
    model_config: ModelConfig,
    issue_ids: list[str],
    api_base_url: str,
    turn: int = 0,
    agent_key: str = "",
) -> str:
    """Build a prompt that instructs an LLM to deliberate on pending issues."""
    model_id = model_config.id
    system_prompt = model_config.system_prompt
    base = api_base_url

    issue_list = "\n".join(f"  - {iid}" for iid in issue_ids)
    parts = [
        f"You are a code reviewer (model: {model_id}) participating in a deliberation round.",
    ]

    if system_prompt:
        parts.extend(["", "## System Instructions", "", system_prompt])

    strictness = getattr(model_config, "strictness", "balanced") or "balanced"
    if strictness in STRICTNESS_INSTRUCTIONS:
        parts.extend(["", STRICTNESS_INSTRUCTIONS[strictness]])

    parts.extend([
        "",
        "## Authentication",
        "",
        f"- X-Agent-Key: {agent_key}",
        "- Include this header in ALL requests (both GET and POST).",
        "",
        "## Instructions",
        "",
        "Other reviewers have raised issues. You must review each one and share your opinion.",
        f"- Current deliberation turn: {turn}",
        "",
        "For each issue ID listed below:",
        "",
        f"1. Retrieve the issue thread:",
        f'   curl -H "X-Agent-Key: {agent_key}" {base}/api/sessions/{session_id}/issues/{{issue_id}}/thread',
        "2. If you need to inspect the code, use the file content API:",
        f'   curl -H "X-Agent-Key: {agent_key}" "{base}/api/sessions/{session_id}/files/{{path}}?start={{n}}&end={{n}}"',
        "3. Analyze the issue carefully — consider the code context, severity, and other opinions.",
        "   - IMPORTANT: Judge the issue itself, not whether you personally like another reviewer's wording.",
        "   - If you think the issue should be dismissed, choose action=no_fix explicitly.",
        f"4. Submit your opinion:",
        f"   curl -X POST {base}/api/sessions/{session_id}/issues/{{issue_id}}/opinions \\",
        f'     -H "Content-Type: application/json" \\',
        f'     -H "X-Agent-Key: {agent_key}" \\',
        f'     -d \'{{"model_id": "{model_id}", "action": "...", "reasoning": "...", "suggested_severity": "...", "confidence": 0.8}}\'',
        "   - action: one of fix_required/no_fix/comment",
        "   - reasoning: your analysis (be specific)",
        "   - suggested_severity: use only when action=fix_required (critical/high/medium/low). Leave null/omit otherwise.",
        "   - confidence: 0.0–1.0. How certain you are about your judgment (default 1.0). Use lower values when uncertain.",
        f"5. After processing all issues, submit your overall verdict for this turn:",
        f"   curl -X POST {base}/api/sessions/{session_id}/overall-reviews \\",
        f'     -H "Content-Type: application/json" \\',
        f'     -H "X-Agent-Key: {agent_key}" \\',
        f'     -d \'{{"model_id":"{model_id}","task_type":"deliberation","turn":{turn},"merge_decision":"mergeable|not_mergeable|needs_discussion","summary":"...","highlights":["..."],"blockers":["..."],"recommendations":["..."]}}\'',
        "",
        "Decision rules:",
        "- fix_required: You judge this issue as valid and code change is needed.",
        "- no_fix: You judge this issue as invalid / should be dismissed.",
        "- comment: You have an opinion or question but are not ready to decide yet.",
        "- Do NOT use fix_required just to align with a person. If your final stance is dismiss, use no_fix.",
        "- Set confidence=1.0 when you are certain, 0.5 when somewhat uncertain, 0.3 or lower when speculative.",
        "- If unsure, prefer comment with low confidence over a decisive action you might regret.",
        "- overall merge_decision should reflect your final stance after considering all pending issues in this turn.",
        "",
        "## Pending issue IDs",
        "",
        issue_list,
        "",
        "## Important",
        "",
        "- Do NOT use local tools (git, sed, rg, cat, etc). Use only the APIs above.",
        "- Process ALL listed issues.",
        "- Deliberate independently. Do not ask for human input.",
        "- Be concise but substantive in your reasoning.",
        "- You may mention other reviewers using @model_id (e.g., @codex) when asking follow-up in your reasoning.",
        "- When referencing another issue, use @issue_id (e.g., @1d9f63acf240).",
        "- You may use Markdown in reasoning: **bold**, *italic*, ~~strikethrough~~, `code`.",
        "- Write all reasoning in Korean.",
        f"- Session ID: {session_id}",
    ])
    return "\n".join(parts)
