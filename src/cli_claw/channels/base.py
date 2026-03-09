from __future__ import annotations

from abc import ABC, abstractmethod

from cli_claw.schemas.channel import OutboundEnvelope


class BaseChannel(ABC):
    name: str = "base"

    def __init__(self) -> None:
        self._running = False

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
