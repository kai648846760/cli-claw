from __future__ import annotations

import logging
from dataclasses import dataclass

from cli_claw.channels.manager import ChannelManager
from cli_claw.runtime.orchestrator import RuntimeOrchestrator
from cli_claw.schemas.channel import InboundEnvelope, OutboundEnvelope

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChannelRoute:
    channel: str
    provider: str


class ChannelRuntime:
    def __init__(
        self,
        orchestrator: RuntimeOrchestrator,
        channel_manager: ChannelManager | None = None,
    ) -> None:
        self.orchestrator = orchestrator
        self.channels = channel_manager or ChannelManager()
        self._routes: dict[str, str] = {}
        self._running = False

    def register_route(self, channel: str, provider: str) -> None:
        self._routes[channel] = provider

    def routes(self) -> list[ChannelRoute]:
        return [ChannelRoute(channel=key, provider=value) for key, value in sorted(self._routes.items())]

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self, enabled_channels: list[str]) -> None:
        if self._running:
            return
        await self.channels.start_enabled(enabled_channels)
        for name in self.channels.channels.keys():
            self.channels.bind_inbound_handler(name, self._make_inbound_handler(name))
        self._running = True

    async def stop(self) -> None:
        if not self._running:
            return
        await self.channels.stop_all()
        self._running = False

    async def _handle_inbound(self, channel_name: str, inbound: InboundEnvelope) -> None:
        provider_id = self._routes.get(channel_name)
        if provider_id is None:
            raise ValueError(f"Channel '{channel_name}' has no provider route")

        thread_id = None
        if inbound.metadata:
            thread_id = inbound.metadata.get("thread_id")

        logical_session_id = self.orchestrator.sessions.resolve_logical_session_id(
            provider=provider_id,
            channel=inbound.channel,
            chat_id=inbound.chat_id,
            thread_id=thread_id,
        )

        try:
            outbound = await self.orchestrator.handle_inbound(provider_id, logical_session_id, inbound)
        except Exception as exc:
            logger.exception("Inbound handling failed for channel '%s'", channel_name)
            outbound = OutboundEnvelope(
                channel=inbound.channel,
                chat_id=inbound.chat_id,
                kind="error",
                text=f"runtime error: {exc}",
                reply_to_id=inbound.message_id,
                metadata={"provider": provider_id, "logical_session_id": logical_session_id},
            )

        await self.channels.enqueue(outbound)

    async def handle_inbound(self, channel_name: str, inbound: InboundEnvelope) -> None:
        await self._handle_inbound(channel_name, inbound)

    def _make_inbound_handler(self, channel_name: str):
        async def _handler(inbound: InboundEnvelope) -> None:
            await self._handle_inbound(channel_name, inbound)

        return _handler
