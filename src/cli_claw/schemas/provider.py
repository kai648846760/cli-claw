from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CapabilityMap(BaseModel):
    streaming: bool = False
    sessions: bool = False
    history_export: bool = False
    tools: bool = False
    model_switch: bool = False
    auth_check: bool = False
    mcp: bool = False
    skills: bool = False


class ProviderSpec(BaseModel):
    id: str
    transport: str = "stdio"
    protocol: str = "acp"
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    capabilities: CapabilityMap = Field(default_factory=CapabilityMap)
    metadata: dict[str, Any] = Field(default_factory=dict)
    adapter: str | None = None
