from __future__ import annotations

from collections.abc import AsyncIterator

from cli_claw.bridges.base.base import ProtocolBridge
from cli_claw.schemas.events import RuntimeEvent


class AcpBridge(ProtocolBridge):
    def __init__(self, command: str, args: list[str] | None = None):
        self.command = command
        self.args = args or []
        self.started = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.started = False

    async def send(self, payload: dict) -> None:
        _ = payload
        if not self.started:
            raise RuntimeError("ACP bridge is not started")

    async def receive(self) -> AsyncIterator[RuntimeEvent]:
        if False:
            yield RuntimeEvent(type="trace.step")  # pragma: no cover
        return
