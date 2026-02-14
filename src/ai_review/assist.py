"""Assist prompt composition and parsing utilities."""

from __future__ import annotations

import json


def issue_location_text(issue) -> str:
    """Format issue location as 'file:line' or 'file:start-end'."""
    line_start = getattr(issue, "line_start", None)
    line_end = getattr(issue, "line_end", None)
    line = getattr(issue, "line", None)
    start = line_start if line_start is not None else line
    end = line_end if line_end is not None else start
    if start is None:
        return issue.file
    if end is not None and end < start:
        start, end = end, start
    if end is not None and end != start:
        return f"{issue.file}:{start}-{end}"
    return f"{issue.file}:{start}"


def compose_assist_prompt(issue, diff_content: str, user_message: str) -> str:
    """Build the system prompt for the assist chat."""
    severity_kr = {"critical": "심각", "high": "높음", "medium": "보통", "low": "낮음", "dismissed": "기각"}
    action_kr = {"raise": "제기", "fix_required": "수정필요", "no_fix": "수정불필요", "comment": "의견"}
    parts = [
        "당신은 시니어 개발자입니다. 코드 리뷰에서 발견된 이슈를 해결하는 것을 도와주세요.",
        "",
        "## 이슈 정보",
        f"- 제목: {issue.title}",
        f"- 심각도: {severity_kr.get(issue.severity.value, issue.severity.value)}",
        f"- 파일: {issue_location_text(issue)}",
        f"- 설명: {issue.description}",
    ]
    if issue.suggestion:
        parts.append(f"- 수정 제안: {issue.suggestion}")

    if issue.thread:
        parts.append("")
        parts.append("## 리뷰어 토론")
        for op in issue.thread:
            act = action_kr.get(op.action.value, op.action.value)
            parts.append(f"- {op.model_id} ({act}): {op.reasoning}")

    if diff_content:
        parts.append("")
        parts.append("## 관련 코드 변경 (diff)")
        parts.append("```diff")
        parts.append(diff_content)
        parts.append("```")

    if issue.assist_messages:
        parts.append("")
        parts.append("## 이전 대화")
        for msg in issue.assist_messages:
            role = "사용자" if msg.role == "user" else "도우미"
            parts.append(f"**{role}**: {msg.content}")

    parts.append("")
    parts.append(f"**사용자**: {user_message}")
    parts.append("")
    parts.append("한국어로 답변해주세요. 코드 수정이 필요하면 구체적인 코드를 제공하세요.")
    parts.append("수정 범위가 크거나 여러 파일에 걸치면, CLI에서 직접 수정할 수 있도록 명령어를 제안하세요.")
    return "\n".join(parts)


def compose_assist_opinion_prompt(issue, diff_content: str, user_message: str) -> str:
    """Build the prompt for generating a mediator opinion."""
    parts = [
        "당신은 코드 리뷰 조정자입니다.",
        "아래 이슈를 보고 토론에 제출할 의견을 JSON 하나로만 작성하세요.",
        "",
        "출력 형식(JSON only):",
        '{"action":"fix_required|no_fix|comment","reasoning":"...","suggested_severity":"critical|high|medium|low|dismissed|null"}',
        "",
        f"- 제목: {issue.title}",
        f"- 파일: {issue_location_text(issue)}",
        f"- 설명: {issue.description}",
    ]
    if issue.thread:
        parts.append("")
        parts.append("기존 토론:")
        for op in issue.thread:
            parts.append(f"- {op.model_id} ({op.action.value}): {op.reasoning}")
    if diff_content:
        parts.append("")
        parts.append("관련 diff:")
        parts.append("```diff")
        parts.append(diff_content)
        parts.append("```")
    if user_message:
        parts.append("")
        parts.append(f"사용자 지시: {user_message}")
    parts.append("")
    parts.append("주의: JSON 외 텍스트를 절대 출력하지 마세요.")
    return "\n".join(parts)


def parse_assist_opinion(text: str) -> dict:
    """Parse a JSON opinion from potentially noisy LLM output."""
    raw = (text or "").strip()
    try:
        return json.loads(raw)
    except Exception:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            return json.loads(raw[start:end + 1])
        raise ValueError("assist opinion parse failed")
