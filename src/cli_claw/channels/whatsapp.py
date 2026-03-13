from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

from cli_claw.channels.base import BaseChannel
from cli_claw.channels.simple_webhook_utils import parse_simple_inbound, send_simple_webhook
from cli_claw.channels.policy import sender_allowed
from cli_claw.schemas.channel import InboundEnvelope, OutboundEnvelope

logger = logging.getLogger(__name__)


@dataclass
class WhatsAppConfig:
    webhook_url: str | None = None
    verification_token: str | None = None
    request_timeout: float = 10.0
    bridge_url: str | None = None
    bridge_token: str | None = None
    allow_from: list[str] = field(default_factory=list)


def _load_config() -> WhatsAppConfig:
    return WhatsAppConfig(
        webhook_url=os.getenv("WHATSAPP_WEBHOOK_URL"),
        verification_token=os.getenv("WHATSAPP_VERIFICATION_TOKEN"),
        bridge_url=os.getenv("WHATSAPP_BRIDGE_URL"),
        bridge_token=os.getenv("WHATSAPP_BRIDGE_TOKEN"),
    )


class WhatsAppChannel(BaseChannel):
    name = "whatsapp"

    def __init__(self, config: WhatsAppConfig | None = None) -> None:
        super().__init__()
        self.config = config or _load_config()
        self._ws: Any = None
        self._connected = False

    async def start(self) -> None:
        import websockets

        if not self.config.bridge_url:
            logger.error("WhatsApp bridge_url not configured")
            return

        self._running = True

        while self._running:
            try:
                async with websockets.connect(self.config.bridge_url) as ws:
                    self._ws = ws
                    if self.config.bridge_token:
                        await ws.send(
                            json.dumps({"type": "auth", "token": self.config.bridge_token})
                        )

                    self._connected = True
                    logger.info("[whatsapp] Connected to bridge")

                    async for message in ws:
                        try:
                            await self._handle_bridge_message(message)
                        except Exception as exc:
                            logger.error("[whatsapp] Error handling bridge message: %s", exc)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._connected = False
                self._ws = None
                logger.warning("[whatsapp] Bridge error: %s", exc)
                if self._running:
                    await asyncio.sleep(5)

        logger.info("[whatsapp] channel stopped")

    async def stop(self) -> None:
        self._running = False
        self._connected = False
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

    def is_allowed(self, envelope: OutboundEnvelope) -> bool:
        return envelope.kind in ("text", "error")

    def parse_inbound_event(self, payload: dict[str, Any]) -> InboundEnvelope | None:
        return parse_simple_inbound(self.name, payload, allow_from=self.config.allow_from)

    async def send(self, envelope: OutboundEnvelope) -> None:
        if self.config.webhook_url:
            await send_simple_webhook(
                webhook_url=self.config.webhook_url,
                envelope=envelope,
                timeout=self.config.request_timeout,
                missing_message="WhatsApp webhook missing; set WHATSAPP_WEBHOOK_URL",
            )
            return

        if not self._ws or not self._connected:
            raise RuntimeError("WhatsApp bridge not connected")
        payload = {
            "type": "send",
            "to": envelope.chat_id,
            "text": self._build_content(envelope),
        }
        await self._ws.send(json.dumps(payload, ensure_ascii=False))

    def _build_content(self, envelope: OutboundEnvelope) -> str:
        content = envelope.text or ""
        if not envelope.attachments:
            return content

        lines = [content] if content else []
        for attachment in envelope.attachments:
            label = attachment.name or attachment.path or attachment.url or "attachment"
            target = attachment.url or attachment.path or ""
            if target:
                lines.append(f"[{attachment.kind}] {label}: {target}")
            else:
                lines.append(f"[{attachment.kind}] {label}")
        return "\n".join(lines)

    async def _handle_bridge_message(self, raw: str) -> None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("[whatsapp] Invalid JSON from bridge: %s", raw[:100])
            return

        msg_type = data.get("type")
        if msg_type == "message":
            pn = data.get("pn", "")
            sender = data.get("sender", "")
            content = data.get("content", "")

            user_id = pn if pn else sender
            sender_id = user_id.split("@")[0] if "@" in user_id else user_id

            if not sender_allowed(sender_id, self.config.allow_from):
                return

            await self.emit_inbound(
                InboundEnvelope(
                    channel=self.name,
                    chat_id=sender,
                    sender_id=sender_id,
                    message_id=str(data.get("id") or "") or None,
                    text=content,
                    metadata={
                        "timestamp": data.get("timestamp"),
                        "is_group": data.get("isGroup", False),
                    },
                )
            )
        elif msg_type == "status":
            status = data.get("status")
            if status == "connected":
                self._connected = True
            elif status == "disconnected":
                self._connected = False
        elif msg_type == "error":
            logger.error("[whatsapp] bridge error: %s", data.get("error"))
