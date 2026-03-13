from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from cli_claw.channels.base import BaseChannel
from cli_claw.channels.http_client import post_json
from cli_claw.channels.simple_webhook_utils import parse_simple_inbound
from cli_claw.channels.email import EmailChannel
from cli_claw.channels.dingtalk import DingTalkChannel
from cli_claw.channels.mochat import MochatChannel
from cli_claw.channels.qq import QQChannel
from cli_claw.channels.whatsapp import WhatsAppChannel
from cli_claw.schemas.channel import InboundEnvelope, OutboundEnvelope


@dataclass
class SimpleWebhookConfig:
    webhook_url: str | None = None
    verification_token: str | None = None
    request_timeout: float = 10.0
    allow_from: list[str] = field(default_factory=list)


def _load_config(prefix: str) -> SimpleWebhookConfig:
    allow_from = [item for item in os.getenv(f"{prefix}_ALLOW_FROM", "").split(",") if item.strip()]
    return SimpleWebhookConfig(
        webhook_url=os.getenv(f"{prefix}_WEBHOOK_URL"),
        verification_token=os.getenv(f"{prefix}_VERIFICATION_TOKEN"),
        allow_from=allow_from,
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
        return envelope.kind in ("text", "error")

    def parse_inbound_event(self, payload: dict[str, Any]) -> InboundEnvelope | None:
        return parse_simple_inbound(self.name, payload, allow_from=self.config.allow_from)

    async def send(self, envelope: OutboundEnvelope) -> None:
        if not self.is_allowed(envelope):
            raise ValueError(f"{self.__class__.__name__} only supports outbound text without attachments")
        if not self.config.webhook_url:
            raise RuntimeError(f"{self.__class__.__name__} webhook missing; set {self.env_prefix}_WEBHOOK_URL")
        payload = {"text": envelope.text, "chat_id": envelope.chat_id}
        await post_json(self.config.webhook_url, payload, headers={}, timeout=self.config.request_timeout)


SimpleEmailChannel = EmailChannel
SimpleDingtalkChannel = DingTalkChannel
SimpleMochatChannel = MochatChannel
SimpleQQChannel = QQChannel
SimpleWhatsappChannel = WhatsAppChannel
