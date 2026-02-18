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
        "## Response Format",
        "",
        "- arv get commands return TOON format (structured plain text, not JSON).",
        "- arv post commands (report, summary, opinion, respond) handle JSON internally.",
        "- Do NOT use curl, python3, or other tools for API calls. Use ONLY arv commands.",
        "",
        "## Instructions",
        "",
        "Follow these steps exactly:",
        "",
        "1. Retrieve the context index:",
        "   arv get index",
        "2. Read source files to understand context around changed hunks:",
        "   arv get file {path} -r {start}:{end}",
        "3. Search for symbols or usages if needed:",
        "   arv get search {keyword} -g {pattern}",
        "4. Browse project structure if needed:",
        "   arv get tree {dir} -d {n}",
        "5. Retrieve per-file diff context if needed:",
        "   arv get context {file}",
        "6. Review the code changes thoroughly based on your assigned focus area.",
        "7. Submit each issue individually:",
        '   arv report -n "이슈 제목" -s severity --file path --lines start:end -b "설명"',
        "   For long descriptions, use Write tool to create a file, then: arv report ... -f /tmp/desc.md",
        "   For suggestions: --sb \"제안 텍스트\" or --sf /tmp/sugg.md",
        "   severity: critical/high/medium/low",
        "   - description and suggestion MUST be valid Markdown (not plain single-line text).",
        "   - description format (recommended):",
        "     ### 문제",
        "     ...",
        "     ### 근거",
        "     - file:line 근거",
        "     ### 영향",
        "     ...",
        "   - suggestion format (recommended): describe what to change AND include before/after code blocks.",
        "     Example suggestion:",
        "     ```",
        "     - 아래 시나리오를 테스트로 추가해 주세요.",
        "     ",
        "     ### 수정 전",
        "     ```kotlin",
        "     fun getFile(path: String) = storage.get(path)",
        "     ```",
        "     ",
        "     ### 수정 후",
        "     ```kotlin",
        "     fun getFile(path: String, returnAsUri: Boolean = false): Any {",
        "         val file = storage.get(path)",
        "         return if (returnAsUri) file.toUri() else file",
        "     }",
        "     ```",
        "     ```",
        "   - When suggesting code changes, ALWAYS include concrete before/after code blocks.",
        "   - For test additions, show example test code in the suggestion.",
        "   - Prefer explicit ranges: set line_start/line_end. For single-line issues, set line_start == line_end.",
        '8. Finalize your review with a summary:',
        '   arv summary "전체 리뷰 요약"',
        "   If you found no issues, you MUST still finalize: arv summary \"이슈 없음\"",
        "",
        "## Important",
        "",
        "- Use Read, Grep, Glob tools directly to read files and search code.",
        "- Use arv commands only for session data (index, context, thread) and review submission.",
        "- Review independently. Do not ask for human input.",
        "- Be specific: include file paths and line numbers.",
        "- Only report real issues. Do not fabricate problems.",
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
        "## Response Format",
        "",
        "- arv get commands return TOON format (structured plain text, not JSON).",
        "- arv post commands (report, summary, opinion, respond) handle JSON internally.",
        "- Do NOT use curl, python3, or other tools for API calls. Use ONLY arv commands.",
        "",
        "## Instructions",
        "",
        "Other reviewers have raised issues. You must review each one and share your opinion.",
        f"- Current deliberation turn: {turn}",
        "",
        "For each issue ID listed below:",
        "",
        "1. Retrieve the issue thread:",
        "   arv get thread {issue_id}",
        "2. If you need to inspect the code, use the file content API:",
        "   arv get file {path} -r {start}:{end}",
        "3. Analyze the issue carefully — consider the code context, severity, and other opinions.",
        "   - IMPORTANT: Judge the issue itself, not whether you personally like another reviewer's wording.",
        "   - If you think the issue should be dismissed, choose action=no_fix explicitly.",
        "4. Submit your opinion:",
        '   arv opinion {issue_id} -a {action} -b "reasoning" -s {severity} -c {confidence}',
        "   For long reasoning, use Write tool to create a file, then: arv opinion {issue_id} -a {action} -f /tmp/reason.md",
        "   - action: one of fix_required/no_fix/false_positive/comment",
        "   - -b: your analysis (be specific)",
        "   - -s: suggested severity, use only when action=fix_required (critical/high/medium/low). Omit otherwise.",
        "   - -c: confidence 0.0–1.0. How certain you are (default 1.0). Use lower values when uncertain.",
        "",
        "Decision rules:",
        "- fix_required: You judge this issue as valid and code change is needed.",
        "- no_fix: You judge this issue as invalid / should be dismissed.",
        "- false_positive: The reported issue is not a real problem (false alarm). Only non-raisers can use this.",
        "- comment: You have an opinion or question but are not ready to decide yet.",
        "- Do NOT use fix_required just to align with a person. If your final stance is dismiss, use no_fix.",
        "- Set confidence=1.0 when you are certain, 0.5 when somewhat uncertain, 0.3 or lower when speculative.",
        "- If unsure, prefer comment with low confidence over a decisive action you might regret.",
        "",
        "## Pending issue IDs",
        "",
        issue_list,
        "",
        "## Important",
        "",
        "- Use Read, Grep, Glob tools directly if you need to verify code.",
        "- Use arv commands only for session data (thread, issues) and opinion submission.",
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


def build_agent_response_prompt(
    session_id: str,
    model_config: ModelConfig,
    api_base_url: str,
    agent_key: str = "",
) -> str:
    """Build a prompt that instructs a coding agent to respond to confirmed issues."""
    model_id = model_config.id
    base = api_base_url

    parts = [
        f"You are a coding agent (model: {model_id}) responding to code review findings.",
        "",
        "## Response Format",
        "",
        "- arv get commands return TOON format (structured plain text, not JSON).",
        "- arv post commands (report, summary, opinion, respond) handle JSON internally.",
        "- Do NOT use curl, python3, or other tools for API calls. Use ONLY arv commands.",
        "",
        "## Instructions",
        "",
        "Reviewers have identified issues that require fixes. You must review each one and decide whether to accept, dispute, or partially accept.",
        "",
        "1. Retrieve confirmed issues:",
        "   arv get confirmed",
        "2. For each issue, examine the issue thread and relevant source code:",
        "   arv get thread {issue_id}",
        "   arv get file {path} -r {start}:{end}",
        "3. Decide your response for each issue: accept, dispute, or partial.",
        "4. Submit your response for each issue:",
        '   arv respond {issue_id} -a accept -b "reasoning"',
        "   For long reasoning, use Write tool to create a file, then: arv respond {issue_id} -a accept -f /tmp/reason.md",
        "",
        "## Response Guidelines",
        "",
        "- **accept**: You agree with the finding and will implement the fix.",
        "- **dispute**: You disagree with the finding. Provide strong evidence why this is not a real issue.",
        "  - A dispute triggers re-deliberation: all reviewers will re-examine the issue with your reasoning.",
        "- **partial**: You partially agree. Describe what you will fix and what you won't.",
        "",
        "## Important",
        "",
        "- You MUST respond to ALL confirmed issues.",
        "- Use dispute only when you have strong technical justification.",
        "- Write all reasoning in Korean.",
        f"- Session ID: {session_id}",
    ]
    return "\n".join(parts)


def build_verification_prompt(
    session_id: str,
    model_config: ModelConfig,
    api_base_url: str,
    verification_round: int = 1,
    agent_key: str = "",
    issue_ids: list[str] | None = None,
) -> str:
    """Build a prompt that instructs an LLM to verify fixes via delta diff review."""
    model_id = model_config.id
    system_prompt = model_config.system_prompt
    base = api_base_url

    parts = [
        f"You are a verification reviewer (model: {model_id}). Round {verification_round}.",
    ]

    if system_prompt:
        parts.extend(["", "## System Instructions", "", system_prompt])

    strictness = getattr(model_config, "strictness", "balanced") or "balanced"
    if strictness in STRICTNESS_INSTRUCTIONS:
        parts.extend(["", STRICTNESS_INSTRUCTIONS[strictness]])

    parts.extend([
        "",
        "## Response Format",
        "",
        "- arv get commands return TOON format (structured plain text, not JSON).",
        "- arv post commands (report, summary, opinion, respond) handle JSON internally.",
        "- Do NOT use curl, python3, or other tools for API calls. Use ONLY arv commands.",
        "",
        "## Instructions",
        "",
        "A coding agent has submitted fixes for previously identified issues.",
        "Your task is to verify whether each original issue has been resolved by reviewing the delta diff.",
        "",
        "1. Retrieve the delta context (changed files and original issues):",
        "   arv get delta",
        "2. Inspect delta diff details per file:",
        "   arv get file {path} -r {start}:{end}",
        "3. Review the original issue thread for context:",
        "   arv get thread {issue_id}",
        "4. Submit your opinion on each original issue:",
        '   arv opinion {issue_id} -a {action} -b "reasoning" -s {severity} -c {confidence}',
        "   - action: one of fix_required/no_fix/comment",
        "",
        "   **IMPORTANT — action meaning in verification context:**",
        "   - **no_fix**: The original issue has been **resolved** by the fix. No further change needed.",
        "   - **fix_required**: The original issue is **still NOT resolved**. Further fix is needed.",
        "   - **comment**: You have observations but are not ready to make a definitive judgment.",
        "   - This is different from deliberation where no_fix means 'dismiss the issue'.",
        "     Here, no_fix means 'the fix successfully addressed this issue'.",
        "",
        "5. If you discover NEW issues introduced by the fix, submit them individually:",
        '   arv report -n "새 이슈 제목" -s severity --file path --lines start:end -b "설명"',
        "   Then finalize: arv summary \"검증 결과 요약\"",
        "   - Only report issues that are NEW — caused by the fix itself.",
        "",
        "## Verification Rules",
        "",])

    if issue_ids:
        parts.extend([
            "**Scoped Verification**: You are the original reporter. Only verify these issue IDs:",
            *[f"- `{iid}`" for iid in issue_ids],
            "- Skip any issues not listed above.",
            "",
        ])

    parts.extend([
        "- Review ONLY the delta diff. Do NOT re-review the entire codebase.",
        "- For each original issue, verify whether the fix actually addresses it.",
        "- You MAY report new issues introduced by the fix (Step 5), but do not re-raise original issues that are already resolved.",
        "- If all original issues are resolved and no new issues found, submit a clean verdict.",
        "",
        "## Important",
        "",
        "- Use Read, Grep, Glob tools directly if you need to verify code.",
        "- Use arv commands only for session data (thread, issues) and opinion submission.",
        "- Verify independently. Do not ask for human input.",
        "- Be specific: include file paths and line numbers.",
        "- Only report real issues. Do not fabricate problems.",
        "- Complete the verification in a single turn.",
        "- Write all reasoning, description, suggestion, and summary fields in Korean.",
        f"- Session ID: {session_id}",
    ])
    return "\n".join(parts)


def build_false_positive_review_prompt(
    session_id: str,
    model_config: ModelConfig,
    issue_id: str,
    fp_submitter: str,
    api_base_url: str,
    agent_key: str = "",
) -> str:
    """Build a prompt asking the original raiser to re-evaluate after a FALSE_POSITIVE opinion."""
    model_id = model_config.id
    base = api_base_url

    parts = [
        f"You are a code reviewer (model: {model_id}). Another reviewer has flagged one of your reported issues as a false positive.",
        "",
        "## Response Format",
        "",
        "- arv get commands return TOON format (structured plain text, not JSON).",
        "- arv post commands (report, summary, opinion, respond) handle JSON internally.",
        "- Do NOT use curl, python3, or other tools for API calls. Use ONLY arv commands.",
        "",
        "## Context",
        "",
        f"- Reviewer **{fp_submitter}** has marked your issue `{issue_id}` as a **false positive** (not a real problem).",
        "- You must re-evaluate the issue and decide whether to withdraw it or maintain your position.",
        "",
        "## Instructions",
        "",
        "1. Retrieve the issue thread to see all opinions including the false_positive judgment:",
        f"   arv get thread {issue_id}",
        "2. If needed, re-examine the code:",
        "   arv get file {path} -r {start}:{end}",
        "3. Submit your decision:",
        f'   arv opinion {issue_id} -a {{action}} -b "reasoning" -c 0.8',
        "",
        "## Decision Options",
        "",
        "- **withdraw**: You agree this was a false positive and withdraw your issue. The issue will be immediately closed.",
        "- **fix_required**: You disagree — the issue is real and still needs fixing. Deliberation will continue normally.",
        "",
        "Choose only one of: `withdraw` or `fix_required`.",
        "",
        "## Important",
        "",
        "- Use Read, Grep, Glob tools directly if you need to verify code.",
        "- Use arv commands only for session data (thread, issues) and opinion submission.",
        "- Make your decision independently based on the code and arguments.",
        "- Be concise but substantive in your reasoning.",
        "- Write all reasoning in Korean.",
        f"- Session ID: {session_id}",
    ]
    return "\n".join(parts)
