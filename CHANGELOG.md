# Changelog

## 0.2.3 - 2026-03-13

- commands: add /cron, /cli, /skills management commands
- schedules: persist tasks to workspace schedules.json for runtime edits

## 0.2.2 - 2026-03-13

- runtime: add heartbeat loop with provider/channel health metrics
- runtime: add scheduled task runner (interval or daily) for automated prompts

## 0.2.1 - 2026-03-13

- memory: switch to SQLite-backed memory store with JSONL append for per-message history
- memory: load recent messages + summary to support cross-CLI continuity
- runtime: improve streaming merge logic to avoid duplicate/partial Telegram responses
- telegram: capture delivery receipt ids on outbound sends; improve photo selection for inbound attachments
- prompts: add workspace-scoped template prompt files with auto-copy on first run
- docs: document workspace templates and memory storage locations

## 0.2.0 - 2026-03-11

- config: add config schema + loader for providers/channels/runtime with compression settings
- registry: enable dynamic provider/channel loading via adapter class paths
- providers: add generic CLI ACP stdio provider; iflow/qwen now use ACP stdio bridge
- runtime: provider lifecycle start/stop, streaming delivery, thought streaming, tool + delivery event persistence, compression summary injection
- channels: add allowlist checks; expand Slack/Discord/Feishu parsing for broader event types; add Telegram/Dingtalk streaming support; add Slack/Discord/Feishu receipt id backfill
- cli: add gateway run/start/stop/status; add model + thinking commands
- tests: add compression test and streaming tests for Telegram/Dingtalk
- docs/config: default OpenCode provider command uses `opencode acp`
- config: add ACP provider presets aligned with common agent CLIs (claude/codex/goose/auggie/kimi/droid/codebuddy/copilot/qodercli/vibe-acp/nanobot/openclaw)


## 0.1.9 - 2026-03-11

- cli: add config check, runtime start/stop/status, logs/replay, and self-test commands
- transcript: persist JSONL logs for replay and tailing
- docs: expand README with configuration and CLI usage
- tests: add CLI command coverage
- tests: add Discord webhook signature rejection coverage
- tests: cover simple webhook attachments
- slack: accept app_mention inbound events
- tests: cover slack app_mention parsing
- tests: cover Discord webhook missing signature rejection
- feishu: allow error downgrade outbound
- tests: cover Feishu error envelope send
- telegram: expose command/callback semantics in inbound parsing
- tests: cover telegram command and callback parsing
- discord: fix reply_to_id parsing for referenced messages
- tests: cover discord reply_to_id parsing
- tests: cover slack thread reply_to_id parsing
- tests: cover simple webhook token rejection
- tests: cover simple webhook payload token acceptance and inbound routing
- tests: avoid email channel polling in webhook routing test
- tests: cover Feishu webhook missing signature rejection
- tests: stabilize Feishu/simple webhook test setup
- telegram: read callback sender id from callback_query

## 0.1.8 - 2026-03-11

- discord: add outbound attachment support with multipart uploads
- tests: cover discord attachment send path

## 0.1.7 - 2026-03-11

- channels: add multipart upload support for attachment delivery
- telegram: parse inbound media metadata and support outbound file/image/audio/video attachments
- tests: cover telegram attachment send path

## 0.1.6 - 2026-03-10

- channels: add stub channel classes for Slack/Discord/Telegram/Email/Dingtalk/Mochat/QQ/Whatsapp to mirror iflow-bot coverage
- tests: add stub channel smoke test

## 0.1.6 - 2026-03-10

- channels: add Telegram/Slack/Discord webhook MVPs with inbound parsing, runtime routing, and outbound text send
- channels: add simple webhook MVPs for Email/Dingtalk/Mochat/QQ/Whatsapp
- cli: add webhook entrypoints for Telegram/Slack/Discord and simple webhook runner for other channels
- tests: cover new channel parsing, outbound send, and webhook -> runtime routing

## 0.1.5 - 2026-03-10

- feishu: add encrypt-key signature verification for webhook callbacks
- feishu: support decrypting encrypted event payloads using AES-CBC + PKCS7 when cryptography is available
- tests: cover signature rejection and encrypted payload processing (skips if cryptography missing)

## 0.1.4 - 2026-03-10

- docs: add Feishu webhook quickstart + local E2E usage

- feishu: add webhook verification token check for real event subscription chain
- feishu: return HTTP status codes from webhook processor and cover invalid token case
- feishu: allow configuration via `FEISHU_VERIFICATION_TOKEN`

## 0.1.4 - 2026-03-10

- scripts: add `scripts/feishu_e2e_sim.py` and example payload to validate webhook -> runtime -> outbound flow

## 0.1.3 - 2026-03-10

- channels: route Feishu webhook inbound events through ChannelRuntime (parse -> runtime) and add challenge test coverage
- runtime: expose `handle_inbound` to allow webhook integrations to bypass channel emit

## 0.1.2 - 2026-03-10

- channels: add `FeishuChannel` MVP with inbound event parsing and minimal text outbound via webhook or tenant API
- channels: add minimal Feishu webhook HTTP receiver and CLI entrypoint that routes inbound events to runtime
- channels: export `FeishuChannel` in channel module
- session: add logical session id resolver for channel runtime routing
- tests: add unit coverage for Feishu inbound parsing and outbound webhook dispatch
- tests: add integration coverage for Feishu webhook -> runtime routing

## 0.1.1 - 2026-03-10

- channels: add inbound handler wiring and in-memory `LocalChannel` for multi-channel runtime tests
- runtime: add `ChannelRuntime` to route inbound envelopes to providers and dispatch outbound replies
- tests: add channel runtime routing test with fake provider registry

## 0.1.0 - 2026-03-09

- bootstrap: initialize cli-claw as a new provider-agnostic multi-CLI runtime scaffold
- architecture: separate runtime, kernels, providers, bridges, schemas, registry, and CLI entrypoint
- providers: add initial scaffold providers for iflow and qwen
- schemas: add canonical event / transcript / provider spec models
- smoke: add initial runtime smoke tests and CLI demo path
- channels: add channel contract v1 schemas (`InboundEnvelope`, `OutboundEnvelope`, `ChannelAttachment`)
- channels: add abstract `BaseChannel` and provider-agnostic `ChannelManager` (registry/lifecycle/queue dispatch)
- runtime: switch orchestrator message handling to inbound/outbound envelope path (`handle_inbound`)
- transcript: persist `message_id` / `reply_to_id` and structured attachment payloads
- tests: add channel contract tests and channel manager behavior tests; migrate runtime smoke tests to envelope API
