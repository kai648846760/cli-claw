from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from cli_claw.channels.base import BaseChannel
from cli_claw.channels.http_client import post_json
from cli_claw.schemas.channel import InboundEnvelope, OutboundEnvelope


@dataclass
class SimpleWebhookConfig:
    webhook_url: str | None = None
    verification_token: str | None = None
    request_timeout: float = 10.0


def _load_config(prefix: str) -> SimpleWebhookConfig:
    return SimpleWebhookConfig(
        webhook_url=os.getenv(f"{prefix}_WEBHOOK_URL"),
        verification_token=os.getenv(f"{prefix}_VERIFICATION_TOKEN"),
    )


class SimpleWebhookChannel(BaseChannel):
    name = "simple"
    env_prefix = "SIMPLE"

    def __init__(self, config: SimpleWebhookConfig | None = None) -> None:
        super().__init__()
        self.config = config or _load_config(self.env_prefix)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    def is_allowed(self, envelope: OutboundEnvelope) -> bool:
        return envelope.kind == "text" and not envelope.attachments

    def parse_inbound_event(self, payload: dict[str, Any]) -> InboundEnvelope | None:
        text = payload.get("text")
        if not isinstance(text, str):
            return None

        metadata = payload.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}

        return InboundEnvelope(
            channel=self.name,
            chat_id=str(payload.get("chat_id") or ""),
            sender_id=str(payload.get("sender_id") or "") or None,
            message_id=str(payload.get("message_id") or "") or None,
            reply_to_id=str(payload.get("reply_to_id") or "") or None,
            text=text,
            metadata=metadata,
        )

    async def send(self, envelope: OutboundEnvelope) -> None:
        if not self.is_allowed(envelope):
            raise ValueError(f"{self.__class__.__name__} only supports outbound text without attachments")
        if not self.config.webhook_url:
            raise RuntimeError(f"{self.__class__.__name__} webhook missing; set {self.env_prefix}_WEBHOOK_URL")
        payload = {"text": envelope.text, "chat_id": envelope.chat_id}
        await post_json(self.config.webhook_url, payload, headers={}, timeout=self.config.request_timeout)


class EmailChannel(SimpleWebhookChannel):
    name = "email"
    env_prefix = "EMAIL"


class DingtalkChannel(SimpleWebhookChannel):
    name = "dingtalk"
    env_prefix = "DINGTALK"


class MochatChannel(SimpleWebhookChannel):
    name = "mochat"
    env_prefix = "MOCHAT"


class QQChannel(SimpleWebhookChannel):
    name = "qq"
    env_prefix = "QQ"


class WhatsappChannel(SimpleWebhookChannel):
    name = "whatsapp"
    env_prefix = "WHATSAPP"
