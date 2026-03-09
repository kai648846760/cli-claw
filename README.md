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

## Status

This project is currently in architecture-first bootstrap stage.
