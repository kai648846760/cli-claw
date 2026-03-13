from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from cli_claw.schemas.provider import CapabilityMap


class ProviderConfig(BaseModel):
    id: str
    enabled: bool = True
    adapter: str | None = None
    transport: str = "stdio"
    protocol: str = "acp"
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    capabilities: CapabilityMap = Field(default_factory=CapabilityMap)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChannelConfig(BaseModel):
    name: str
    enabled: bool = True
    channel_class: str | None = None
    settings: dict[str, Any] = Field(default_factory=dict)
    provider: str | None = None


class RuntimeConfig(BaseModel):
    default_provider: str | None = None
    streaming: bool = True
    compression_trigger_tokens: int = 60000
    workspace: str | None = None
    log_dir: str | None = None
    model: str | None = None
    thinking: bool = False
    permission_mode: str | None = None
    timeout: int = 600
    compression_provider: str | None = None
    compression_prompt: str | None = None
    heartbeat_enabled: bool = False
    heartbeat_interval_seconds: int = 60
    schedules: list[dict[str, Any]] = Field(default_factory=list)


class CliClawConfig(BaseModel):
    providers: list[ProviderConfig] = Field(default_factory=list)
    channels: list[ChannelConfig] = Field(default_factory=list)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
