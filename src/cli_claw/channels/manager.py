from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from cli_claw.channels.base import BaseChannel, InboundHandler
from cli_claw.schemas.channel import OutboundEnvelope

logger = logging.getLogger(__name__)

ChannelFactory = Callable[[], BaseChannel]
DeliveryHandler = Callable[[OutboundEnvelope], Awaitable[None]]


class ChannelManager:
    def __init__(self) -> None:
        self._registry: dict[str, ChannelFactory] = {}
        self._channels: dict[str, BaseChannel] = {}
        self._queue: asyncio.Queue[OutboundEnvelope] = asyncio.Queue()
        self._dispatch_task: asyncio.Task | None = None
        self._delivery_handler: DeliveryHandler | None = None

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

    def bind_inbound_handler(self, name: str, handler: InboundHandler | None) -> None:
        channel = self._channels.get(name)
        if channel is None:
            raise ValueError(f"Channel '{name}' not found")
        channel.set_inbound_handler(handler)

    def set_delivery_handler(self, handler: DeliveryHandler | None) -> None:
        self._delivery_handler = handler

    async def _emit_delivery(self, envelope: OutboundEnvelope) -> None:
        if self._delivery_handler is None:
            return
        try:
            await self._delivery_handler(envelope)
        except Exception:
            logger.exception("Delivery handler failed")

    async def _dispatch_loop(self) -> None:
        while True:
            try:
                envelope = await self._queue.get()
                try:
                    if (
                        envelope.stream_final
                        and envelope.stream_id
                        and envelope.kind == "text"
                        and (envelope.text is None or envelope.text == "")
                    ):
                        if envelope.delivery_status is None:
                            envelope.delivery_status = "sent"
                        if envelope.receipt_id is None:
                            envelope.receipt_id = envelope.message_id or envelope.reply_to_id
                        await self._emit_delivery(envelope)
                        continue
                    channel = self._channels.get(envelope.channel)
                    if channel is None:
                        raise ValueError(f"Channel '{envelope.channel}' not found")
                    if not channel.is_running:
                        raise ValueError(f"Channel '{envelope.channel}' is not running")
                    if not channel.is_allowed(envelope):
                        raise ValueError(f"Channel '{envelope.channel}' rejected outbound envelope")
                    await channel.send(envelope)
                    if envelope.delivery_status is None:
                        envelope.delivery_status = "sent"
                    if envelope.receipt_id is None:
                        envelope.receipt_id = envelope.message_id or envelope.reply_to_id
                    await self._emit_delivery(envelope)
                except Exception as exc:
                    logger.exception("Error dispatching outbound envelope")
                    failed = envelope.model_copy(
                        update={
                            "delivery_status": "failed",
                            "error_code": "delivery_error",
                            "error_detail": str(exc),
                            "receipt_id": envelope.receipt_id or envelope.message_id or envelope.reply_to_id,
                        }
                    )
                    await self._emit_delivery(failed)
                    if envelope.kind != "error" and not (envelope.metadata or {}).get("streaming"):
                        error_envelope = OutboundEnvelope(
                            channel=envelope.channel,
                            chat_id=envelope.chat_id,
                            kind="error",
                            text=f"delivery error: {exc}",
                            reply_to_id=envelope.reply_to_id or envelope.message_id,
                            receipt_id=envelope.receipt_id or envelope.message_id,
                            delivery_status="failed",
                            error_code="delivery_error",
                            error_detail=str(exc),
                            metadata=envelope.metadata or {},
                        )
                        await self.send(error_envelope)
                finally:
                    self._queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error dispatching outbound envelope")
