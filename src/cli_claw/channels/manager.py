from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from cli_claw.channels.base import BaseChannel
from cli_claw.schemas.channel import OutboundEnvelope

logger = logging.getLogger(__name__)

ChannelFactory = Callable[[], BaseChannel]


class ChannelManager:
    def __init__(self) -> None:
        self._registry: dict[str, ChannelFactory] = {}
        self._channels: dict[str, BaseChannel] = {}
        self._queue: asyncio.Queue[OutboundEnvelope] = asyncio.Queue()
        self._dispatch_task: asyncio.Task | None = None

    def register(self, name: str, factory: ChannelFactory) -> None:
        self._registry[name] = factory

    def registered(self) -> list[str]:
        return sorted(self._registry.keys())

    def get(self, name: str) -> BaseChannel | None:
        return self._channels.get(name)

    @property
    def channels(self) -> dict[str, BaseChannel]:
        return self._channels.copy()

    async def start_enabled(self, enabled_channels: list[str]) -> None:
        tasks: list[asyncio.Task] = []
        for name in enabled_channels:
            factory = self._registry.get(name)
            if factory is None:
                logger.warning("Channel '%s' is not registered", name)
                continue
            channel = factory()
            self._channels[name] = channel
            tasks.append(asyncio.create_task(channel.start()))
        if tasks:
            await asyncio.gather(*tasks)
            self._dispatch_task = asyncio.create_task(self._dispatch_loop())

    async def stop_all(self) -> None:
        if self._dispatch_task is not None:
            self._dispatch_task.cancel()
            try:
                await self._dispatch_task
            except asyncio.CancelledError:
                pass
            self._dispatch_task = None

        for name, channel in list(self._channels.items()):
            try:
                await channel.stop()
            except Exception:
                logger.exception("Failed stopping channel '%s'", name)
        self._channels.clear()

    async def enqueue(self, envelope: OutboundEnvelope) -> None:
        await self._queue.put(envelope)

    async def send(self, envelope: OutboundEnvelope) -> None:
        channel = self._channels.get(envelope.channel)
        if channel is None:
            raise ValueError(f"Channel '{envelope.channel}' not found")
        if not channel.is_running:
            raise ValueError(f"Channel '{envelope.channel}' is not running")
        if not channel.is_allowed(envelope):
            raise ValueError(f"Channel '{envelope.channel}' rejected outbound envelope")
        await channel.send(envelope)

    async def _dispatch_loop(self) -> None:
        while True:
            try:
                envelope = await self._queue.get()
                try:
                    await self.send(envelope)
                finally:
                    self._queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error dispatching outbound envelope")
