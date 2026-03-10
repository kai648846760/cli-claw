# Changelog

## 0.1.2 - 2026-03-10

- channels: add `FeishuChannel` MVP with inbound event parsing and minimal text outbound via webhook or tenant API
- channels: export `FeishuChannel` in channel module
- tests: add unit coverage for Feishu inbound parsing and outbound webhook dispatch

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
