from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from cli_claw.schemas.events import RuntimeEvent


class ProtocolBridge(ABC):
    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def send(self, payload: dict) -> None: ...

    @abstractmethod
    async def receive(self) -> AsyncIterator[RuntimeEvent]: ...
