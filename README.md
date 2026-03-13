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

Phase 1 focuses on ACP-compatible providers only. Default presets include common ACP agent CLIs.

ACP CLI presets (disabled by default, enable in `~/.cli-claw/config.json`):

| Provider id | Command | Args |
| --- | --- | --- |
| `iflow` | `iflow` | `["--experimental-acp", "--stream"]` |
| `qwen` | `qwen` | `["--acp"]` |
| `opencode` | `opencode` | `["acp"]` |
| `claude` | `claude` | `[]` |
| `codex` | `codex` | `[]` |
| `goose` | `goose` | `["acp"]` |
| `auggie` | `auggie` | `["--acp"]` |
| `kimi` | `kimi` | `["--acp"]` |
| `droid` | `droid` | `["exec", "--output-format", "acp"]` |
| `codebuddy` | `codebuddy` | `["--acp"]` |
| `copilot` | `copilot` | `["--acp", "--stdio"]` |
| `qodercli` | `qodercli` | `["--acp"]` |
| `vibe-acp` | `vibe-acp` | `[]` |
| `nanobot` | `nanobot` | `[]` |
| `openclaw` | `openclaw` | `["gateway"]` |

### Minimal provider config

You can now configure a provider by name only, and `cli-claw` will fill the ACP command/args from presets:

```json
{
  "providers": [
    {"id": "iflow", "enabled": true},
    {"id": "qwen", "enabled": true}
  ],
  "runtime": {"default_provider": "iflow"}
}
```

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

## Configuration

`cli-claw` reads `~/.cli-claw/config.json` by default (created on first run).

### Project prompt files

`cli-claw` will auto-load the following prompt files from the workspace directory `~/.cli-claw/workspace/templates/` and inject them as the `system_prompt` for every CLI provider:

- `~/.cli-claw/workspace/templates/AGENTS.md`
- `~/.cli-claw/workspace/templates/BOOT.md`
- `~/.cli-claw/workspace/templates/BOOTSTRAP.md`
- `~/.cli-claw/workspace/templates/HEARTBEAT.md`
- `~/.cli-claw/workspace/templates/IDENTITY.md`
- `~/.cli-claw/workspace/templates/SOUL.md`
- `~/.cli-claw/workspace/templates/TOOLS.md`
- `~/.cli-claw/workspace/templates/USER.md`

On first run, `cli-claw` will copy defaults from the repository `templates/` folder into the workspace if they do not exist. Edit the workspace copies to customize behavior across CLIs.

### Memory storage

Cross-CLI memory and summaries are persisted under:

- `~/.cli-claw/workspace/memory/memory.db` (SQLite, authoritative)
- `~/.cli-claw/workspace/memory/memory.jsonl` (append-only mirror)

The runtime injects the latest summary plus recent messages into each provider prompt to keep context across CLI switches.

### Built-in chat commands

- `/status` - show provider + context stats
- `/new` - start a new session (clears today memory)
- `/compact` - compress current session
- `/help` - show help
- `/language` - set language (`en-US`, `zh-CN`)
- `/cron` - schedule management (`list`, `add`, `delete`)
- `/cli` - switch channel CLI (`/cli set <provider>`)
- `/skills` - skills management (`list`, `find`, `add`, `update`)

### Heartbeat + scheduled tasks

You can enable a lightweight heartbeat and simple scheduled tasks in `runtime`:

```json
{
  "runtime": {
    "heartbeat_enabled": true,
    "heartbeat_interval_seconds": 60,
    "schedules": [
      {
        "name": "daily-report",
        "enabled": true,
        "channel": "telegram",
        "chat_id": "123456789",
        "message": "生成日报并总结关键事项",
        "daily_at": "09:00"
      },
      {
        "name": "soak-ping",
        "enabled": true,
        "channel": "telegram",
        "chat_id": "123456789",
        "message": "ping",
        "interval_seconds": 600
      }
    ]
  }
}
```

Scheduled tasks are executed as inbound messages routed through the configured provider for that channel.

Runtime edits via `/cron add|delete` are stored in `~/.cli-claw/workspace/schedules.json`.

Example:

```json
{
  "providers": [
    {
      "id": "iflow",
      "enabled": true,
      "adapter": "cli_claw.providers.iflow.provider.IflowProvider",
      "command": "iflow",
      "args": ["--experimental-acp", "--stream"],
      "capabilities": {
        "streaming": true,
        "sessions": true,
        "tools": true,
        "model_switch": true
      }
    }
  ],
  "channels": [
    {
      "name": "feishu",
      "enabled": true,
      "channel_class": "cli_claw.channels.feishu.FeishuChannel",
      "provider": "iflow",
      "settings": {}
    }
  ],
  "runtime": {
    "default_provider": "iflow",
    "streaming": true,
    "compression_trigger_tokens": 60000,
    "compression_provider": "iflow",
    "compression_prompt": "Summarize the conversation so far with key decisions and context.",
    "model": "glm-5",
    "thinking": false,
    "permission_mode": "yolo",
    "timeout": 600
  }
}
```

OpenCode via ACP: set the provider command to `opencode` with args `["acp"]`. You can also add ACP flags in `args` if needed.

Use `config-check` to validate required/recommended environment variables per channel:

```bash
uv run python -m cli_claw.cli.main config-check --channel all
```

Common env vars:

- Feishu: `FEISHU_APP_ID`, `FEISHU_APP_SECRET` (or `FEISHU_BOT_WEBHOOK_URL`), optional `FEISHU_VERIFICATION_TOKEN`, `FEISHU_ENCRYPT_KEY`
- Telegram: `TELEGRAM_BOT_TOKEN`, optional `TELEGRAM_WEBHOOK_SECRET`
- Slack: `SLACK_WEBHOOK_URL`, optional `SLACK_SIGNING_SECRET`, `SLACK_BOT_TOKEN`
- Discord: `DISCORD_WEBHOOK_URL`, optional `DISCORD_PUBLIC_KEY`
- Email: `EMAIL_IMAP_HOST/USERNAME/PASSWORD`, `EMAIL_SMTP_HOST/USERNAME/PASSWORD`, optional `EMAIL_FROM_ADDRESS`, requires `EMAIL_CONSENT_GRANTED=true`
- Dingtalk: `DINGTALK_CLIENT_ID`, `DINGTALK_CLIENT_SECRET`
- Mochat: `MOCHAT_CLAW_TOKEN`, and one of `MOCHAT_SOCKET_URL` or `MOCHAT_BASE_URL`
- QQ: `QQ_APP_ID`, `QQ_SECRET`
- WhatsApp: `WHATSAPP_BRIDGE_URL`, optional `WHATSAPP_BRIDGE_TOKEN`

## CLI

### Runtime management

```bash
# start a webhook runtime in the background
uv run python -m cli_claw.cli.main start --channel feishu --provider iflow --port 8000

# check status
uv run python -m cli_claw.cli.main status --channel all

# stop
uv run python -m cli_claw.cli.main stop --channel feishu
```

State and logs are stored under `~/.cli-claw/`:

- `~/.cli-claw/state/<channel>/` for pid/meta
- `~/.cli-claw/logs/<channel>.jsonl` for transcripts

### Logs and replay

```bash
# tail logs
uv run python -m cli_claw.cli.main logs --channel feishu --lines 50

# replay a JSONL transcript file
uv run python -m cli_claw.cli.main replay --channel feishu --provider iflow --source /path/to/log.jsonl
```

### Self test

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q
# or
uv run python -m cli_claw.cli.main self-test
```

## Webhook entrypoints

Direct webhook entrypoints are available for Feishu/Telegram/Slack/Discord, plus a `simple-webhook` entrypoint for Email/Dingtalk/Mochat/QQ/WhatsApp:

```bash
uv run python -m cli_claw.cli.main telegram-webhook --provider iflow --port 8001
uv run python -m cli_claw.cli.main slack-webhook --provider iflow --port 8002
uv run python -m cli_claw.cli.main discord-webhook --provider iflow --port 8003
uv run python -m cli_claw.cli.main simple-webhook --channel email --provider iflow --port 8004
```

## Status

Multi-channel runtime wiring and CLI management commands are available. See `PROMPT.md` for the remaining parity work.

## Delivery receipts

When a channel API returns a message id, `cli-claw` will populate `OutboundEnvelope.message_id` and `receipt_id` and persist a delivery event in the transcript. Current coverage:

- Feishu: `message_id` from send/reply responses
- Discord: webhook send with `wait=true` (enabled automatically)
- Slack: `chat.postMessage` when `SLACK_BOT_TOKEN` is configured (incoming webhook has no message id)

## Gateway mode

```bash
# run gateway in foreground
uv run python -m cli_claw.cli.main gateway run

# start in background
uv run python -m cli_claw.cli.main gateway start

# stop
uv run python -m cli_claw.cli.main gateway stop
```

## Model & thinking

```bash
# set default model
uv run python -m cli_claw.cli.main model glm-5 --provider default

# toggle thinking mode
uv run python -m cli_claw.cli.main thinking on
```
