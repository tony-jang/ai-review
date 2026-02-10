"""Tests for knowledge loading."""

import textwrap
from pathlib import Path

import pytest

from ai_review.knowledge import load_config, load_knowledge


@pytest.fixture
def repo_with_knowledge(tmp_path: Path) -> Path:
    """Create a temp repo with .ai-review/ knowledge files."""
    ai_dir = tmp_path / ".ai-review"
    knowledge_dir = ai_dir / "knowledge"
    knowledge_dir.mkdir(parents=True)

    (knowledge_dir / "conventions.md").write_text("Use snake_case.\n")
    (knowledge_dir / "decisions.md").write_text("Use raw SQL.\n")
    (knowledge_dir / "ignore-rules.md").write_text("Skip generated files.\n")
    (knowledge_dir / "architecture.md").write_text("Microservices arch.\n")

    return tmp_path


@pytest.fixture
def repo_with_config(tmp_path: Path) -> Path:
    """Create a temp repo with .ai-review/config.yaml."""
    ai_dir = tmp_path / ".ai-review"
    ai_dir.mkdir(parents=True)

    config_content = textwrap.dedent("""\
        models:
          - id: opus
            client_type: claude-code
            provider: anthropic
            model_id: claude-opus-4-6
            role: "security"

          - id: gpt
            client_type: opencode
            provider: openai
            model_id: gpt-5.3
            role: "performance"

        deliberation:
          max_turns: 5
          consensus_threshold: 3
    """)
    (ai_dir / "config.yaml").write_text(config_content)

    return tmp_path


class TestLoadKnowledge:
    def test_loads_all_files(self, repo_with_knowledge):
        k = load_knowledge(repo_with_knowledge)
        assert k.conventions == "Use snake_case."
        assert k.decisions == "Use raw SQL."
        assert k.ignore_rules == "Skip generated files."

    def test_extra_knowledge(self, repo_with_knowledge):
        k = load_knowledge(repo_with_knowledge)
        assert "architecture" in k.extra
        assert k.extra["architecture"] == "Microservices arch."

    def test_no_ai_review_dir(self, tmp_path):
        k = load_knowledge(tmp_path)
        assert k.conventions == ""
        assert k.decisions == ""
        assert k.extra == {}

    def test_empty_knowledge_dir(self, tmp_path):
        (tmp_path / ".ai-review" / "knowledge").mkdir(parents=True)
        k = load_knowledge(tmp_path)
        assert k.conventions == ""

    def test_uses_example_dir(self):
        """Verify the example directory can be loaded."""
        example_path = Path(__file__).parent.parent / "example"
        k = load_knowledge(example_path)
        assert "snake_case" in k.conventions
        assert "raw SQL" in k.decisions


class TestLoadConfig:
    def test_loads_models(self, repo_with_config):
        config = load_config(repo_with_config)
        assert len(config.models) == 2
        assert config.models[0].id == "opus"
        assert config.models[0].client_type == "claude-code"
        assert config.models[1].id == "gpt"

    def test_loads_deliberation(self, repo_with_config):
        config = load_config(repo_with_config)
        assert config.max_turns == 5
        assert config.consensus_threshold == 3

    def test_no_config_file(self, tmp_path):
        config = load_config(tmp_path)
        assert config.models == []
        assert config.max_turns == 3

    def test_empty_config(self, tmp_path):
        ai_dir = tmp_path / ".ai-review"
        ai_dir.mkdir()
        (ai_dir / "config.yaml").write_text("")
        config = load_config(tmp_path)
        assert config.models == []

    def test_uses_example_config(self):
        """Verify the example config can be loaded."""
        example_path = Path(__file__).parent.parent / "example"
        config = load_config(example_path)
        assert len(config.models) == 3
        assert config.max_turns == 3
        assert config.consensus_threshold == 2
