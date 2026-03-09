# cli-claw Architecture

## Design intent

cli-claw is not an iflow-specific bot. It is intended to become a provider-agnostic multi-CLI runtime.

## Layers

1. Core Runtime
2. Capability Kernels
3. Provider Registry
4. Provider Plugins
5. Protocol Bridges
6. Unified Event / Transcript schemas

## Current bootstrap scope

This first scaffold intentionally keeps implementation light while locking in the right architectural boundaries.

### Already separated
- session lifecycle
- transcript truth store
- compression policy engine
- observability event store
- provider registry
- protocol bridge abstraction

### Next phase
- replace scaffold providers with real ACP-backed provider implementations
- enrich canonical transcript/event schema
- add provider-native history and error adapters
- add MCP / skills integration against kernels rather than provider-specific code
