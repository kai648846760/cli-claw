from __future__ import annotations

from cli_claw.channels.base import BaseChannel
from cli_claw.schemas.channel import InboundEnvelope, OutboundEnvelope


class LocalChannel(BaseChannel):
    """In-memory channel for local testing and development."""

    name = "local"

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[OutboundEnvelope] = []

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def send(self, envelope: OutboundEnvelope) -> None:
        self.sent.append(envelope)

    async def inject(self, inbound: InboundEnvelope) -> None:
        await self.emit_inbound(inbound)
