# cli-claw

A new multi-CLI agent runtime platform designed from day one for provider abstraction.

## Design goals

- Provider-agnostic core runtime
- Capability-driven architecture
- Unified event model
- Transcript as source of truth
- ACP-first provider support
- Clear separation between:
  - runtime orchestration
  - capability kernels
  - provider plugins
  - protocol bridges

## Planned provider strategy

Phase 1 focuses on ACP-compatible providers only:

- iflow
- qwen

## High-level architecture

```text
Channels / UI / API
        ↓
Core Runtime (loop / router / retry / approval)
        ↓
Capability Kernels
(session / transcript / compression / skills / mcp / observability)
        ↓
Provider Registry
        ↓
Providers
(iflow / qwen / ...)
        ↓
Protocol Bridges
(acp / openclaw / stdout-json / ...)
```

## Quickstart (Feishu webhook)

```bash
# 1) create venv & install dev deps
uv venv
uv pip install -e .
uv pip install -e .[dev]

# 2) export Feishu secrets (optional but recommended)
export FEISHU_VERIFICATION_TOKEN=your_token
export FEISHU_ENCRYPT_KEY=your_encrypt_key
export FEISHU_APP_ID=cli_xxx
export FEISHU_APP_SECRET=xxx
export FEISHU_BOT_WEBHOOK_URL=https://example.com/hook  # optional outbound shortcut

# 3) run webhook server
uv run python -m cli_claw.cli.main feishu-webhook --provider iflow --port 8000
```

### Local E2E simulation

```bash
uv run python scripts/feishu_e2e_sim.py
```

## Status

Feishu inbound webhook -> runtime -> outbound chain is wired and test-covered.
Next: full Feishu live callback verification + Telegram/Discord migration.
