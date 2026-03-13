from __future__ import annotations

from typing import Protocol


class SessionCapability(Protocol):
    async def new_session(self, logical_session_id: str) -> str: ...
    async def clear_session(self, logical_session_id: str, provider_session_id: str | None = None) -> bool: ...


class StreamingCapability(Protocol):
    async def chat_stream(self, logical_session_id: str, message: str, attachments=None): ...


class HistoryCapability(Protocol):
    async def export_history(self, provider_session_id: str) -> str: ...


class ToolCapability(Protocol):
    async def list_tools(self) -> list[dict]: ...


class ModelCapability(Protocol):
    async def list_models(self) -> list[str]: ...


class AuthCapability(Protocol):
    async def health_check(self) -> bool: ...
