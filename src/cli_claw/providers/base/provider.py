from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from cli_claw.schemas.channel import ChannelAttachment
from cli_claw.schemas.events import RuntimeEvent
from cli_claw.schemas.provider import ProviderSpec


class ProviderAdapter(ABC):
    def __init__(self, spec: ProviderSpec):
        self.spec = spec

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def health_check(self) -> bool: ...

    @abstractmethod
    async def new_session(self, logical_session_id: str) -> str: ...

    @abstractmethod
    async def clear_session(self, logical_session_id: str, provider_session_id: str | None = None) -> bool: ...

    @abstractmethod
    async def chat(
        self,
        logical_session_id: str,
        message: str,
        attachments: list[ChannelAttachment] | None = None,
    ) -> str: ...

    @abstractmethod
    async def chat_stream(
        self,
        logical_session_id: str,
        message: str,
        attachments: list[ChannelAttachment] | None = None,
    ) -> AsyncIterator[RuntimeEvent]: ...

    @abstractmethod
    async def list_models(self) -> list[str]: ...
