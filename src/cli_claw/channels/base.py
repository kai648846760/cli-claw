from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable

from cli_claw.schemas.channel import InboundEnvelope, OutboundEnvelope

InboundHandler = Callable[[InboundEnvelope], Awaitable[None]]


class BaseChannel(ABC):
    name: str = "base"

    def __init__(self) -> None:
        self._running = False
        self._inbound_handler: InboundHandler | None = None

    @property
    def is_running(self) -> bool:
        return self._running

    @abstractmethod
    async def start(self) -> None:
        self._running = True

    @abstractmethod
    async def stop(self) -> None:
        self._running = False

    @abstractmethod
    async def send(self, envelope: OutboundEnvelope) -> None: ...

    def is_allowed(self, envelope: OutboundEnvelope) -> bool:
        _ = envelope
        return True

    def supports_streaming(self) -> bool:
        return False

    async def send_typing(self, chat_id: str) -> None:
        _ = chat_id
        return

    def set_inbound_handler(self, handler: InboundHandler | None) -> None:
        self._inbound_handler = handler

    async def emit_inbound(self, inbound: InboundEnvelope) -> None:
        if self._inbound_handler is None:
            raise RuntimeError("Inbound handler is not set")
        await self._inbound_handler(inbound)
