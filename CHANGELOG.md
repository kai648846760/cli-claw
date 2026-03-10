# Changelog

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
