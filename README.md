# AI Review

Multi-agent code review orchestrator. Multiple LLMs review your code simultaneously, debate each other's findings, and reach consensus through confidence-weighted voting.

## How It Works

```
Create Session ── Start Review ── Collect Issues ── Dedup ── Deliberation ── Consensus ── Done
                   (parallel)       (each model)            (models debate)   (voting)
```

1. **Create a session** with a Git diff (base..head)
2. **Multiple AI agents** review the diff independently in parallel
3. **Deduplication** groups similar issues across models
4. **Deliberation** — agents exchange opinions over multiple turns
5. **Consensus** — confidence-weighted voting determines the final verdict

## Supported Clients

| Client | Engine | Models |
|--------|--------|--------|
| Claude Code | `claude` CLI | Opus 4.6, Sonnet 4.6, Haiku 4.5 |
| Codex | `codex` CLI | GPT-5.3, GPT-5.2, GPT-5 Mini |
| Gemini | `gemini` CLI | 3.1 Pro, 2.5 Pro, 2.5 Flash |
| OpenCode | `opencode` CLI | Big Pickle, MiniMax M2.5, GLM-5, GPT-5 Nano, Trinity Large |

## Quick Start

```bash
# Install
uv sync

# Start server (default: http://localhost:3000)
ai-review

# Or specify port
ai-review --port 8080
```

### Global CLI

```bash
# Install globally (arv, ai-review commands available everywhere)
uv tool install -e .

# Uninstall
uv tool uninstall ai-review
```

## Web UI

The web UI provides:

- **Conversation view** — timeline of reviews, issues, and deliberation threads
- **Changes view** — diff with inline issue annotations
- **Agent panel** — real-time activity tracking per reviewer
- **Session management** — create, configure, and monitor review sessions
- Light/dark theme support

## CLI: `arv`

`arv` wraps the REST API for scripting and CI/CD integration.

```bash
# Session management
arv session list
arv session create --name "Review" --repo . --base main --head feature

# Agent presets
arv preset list

# Within a review session (env vars auto-injected for agents)
arv get file src/main.py
arv get search "TODO"
arv report -n "Missing null check" -s high -f src/api.py -l 42
arv opinion <issue_id> -a fix_required -c 0.9
arv finish
```

## Architecture

```
src/ai_review/
  server.py            # FastAPI server + REST API
  orchestrator.py      # Review lifecycle engine
  session_manager.py   # Session state & persistence
  consensus.py         # Confidence-weighted voting
  dedup.py             # Duplicate issue detection
  prompts.py           # LLM prompt templates
  models.py            # Pydantic data models
  trigger/             # Client engines
    cc.py              #   Claude Code
    codex.py           #   OpenAI Codex
    gemini.py          #   Google Gemini
    opencode.py        #   OpenCode
  static/              # Web UI (vanilla JS + CSS)
```

## Testing

```bash
uv run pytest tests/ -x -q
```

## License

MIT
