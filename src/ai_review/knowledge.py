"""Knowledge loading from .ai-review/ directory."""

from __future__ import annotations

from pathlib import Path

import yaml

from ai_review.models import Knowledge, ModelConfig, SessionConfig


def load_knowledge(repo_path: str | Path) -> Knowledge:
    """Load knowledge from .ai-review/knowledge/ directory."""
    repo = Path(repo_path)
    knowledge_dir = repo / ".ai-review" / "knowledge"

    if not knowledge_dir.exists():
        return Knowledge()

    fields: dict[str, str] = {}
    extra: dict[str, str] = {}

    # Map known filenames to Knowledge fields
    field_map = {
        "conventions": "conventions",
        "decisions": "decisions",
        "ignore-rules": "ignore_rules",
        "ignore_rules": "ignore_rules",
        "review-examples": "review_examples",
        "review_examples": "review_examples",
    }

    for md_file in sorted(knowledge_dir.glob("*.md")):
        stem = md_file.stem
        content = md_file.read_text(encoding="utf-8").strip()
        if stem in field_map:
            fields[field_map[stem]] = content
        else:
            extra[stem] = content

    return Knowledge(
        conventions=fields.get("conventions", ""),
        decisions=fields.get("decisions", ""),
        ignore_rules=fields.get("ignore_rules", ""),
        review_examples=fields.get("review_examples", ""),
        extra=extra,
    )


def load_config(repo_path: str | Path) -> SessionConfig:
    """Load config from .ai-review/config.yaml."""
    repo = Path(repo_path)
    config_file = repo / ".ai-review" / "config.yaml"

    if not config_file.exists():
        return SessionConfig()

    raw = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    if not raw:
        return SessionConfig()

    models = []
    for m in raw.get("models", []):
        models.append(ModelConfig(**m))

    deliberation = raw.get("deliberation", {})

    return SessionConfig(
        models=models,
        max_turns=deliberation.get("max_turns", 3),
        consensus_threshold=deliberation.get("consensus_threshold", 2),
    )
