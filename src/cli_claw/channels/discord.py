from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from cli_claw.channels.base import BaseChannel
from cli_claw.channels.http_client import post_json
from cli_claw.schemas.channel import InboundEnvelope, OutboundEnvelope


@dataclass
class DiscordConfig:
    application_id: str | None = None
    public_key: str | None = None
    webhook_url: str | None = None
    base_url: str = "https://discord.com/api/v10"
    request_timeout: float = 10.0


def _load_config() -> DiscordConfig:
    return DiscordConfig(
        application_id=os.getenv("DISCORD_APPLICATION_ID"),
        public_key=os.getenv("DISCORD_PUBLIC_KEY"),
        webhook_url=os.getenv("DISCORD_WEBHOOK_URL"),
        base_url=os.getenv("DISCORD_BASE_URL", "https://discord.com/api/v10"),
    )


class DiscordChannel(BaseChannel):
    name = "discord"

    def __init__(self, config: DiscordConfig | None = None) -> None:
        super().__init__()
        self.config = config or _load_config()

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    def is_allowed(self, envelope: OutboundEnvelope) -> bool:
        return envelope.kind == "text" and not envelope.attachments

    def parse_inbound_event(self, payload: dict[str, Any]) -> InboundEnvelope | None:
        if payload.get("type") != 2:
            return None

        data = payload.get("data") or {}
        name = data.get("name") or "command"
        options = data.get("options") or []
        text = f"/{name}"
        if options:
            text += " " + " ".join(
                str(opt.get("value")) for opt in options if isinstance(opt, dict)
            )

        member = payload.get("member") or {}
        user = member.get("user") or payload.get("user") or {}
        channel_id = payload.get("channel_id")

        metadata = {
            "interaction_id": payload.get("id"),
            "interaction_token": payload.get("token"),
            "application_id": payload.get("application_id"),
        }

        return InboundEnvelope(
            channel=self.name,
            chat_id=str(channel_id or ""),
            sender_id=str(user.get("id") or "") or None,
            message_id=None,
            reply_to_id=None,
            text=text,
            metadata={k: v for k, v in metadata.items() if v is not None},
        )

    async def send(self, envelope: OutboundEnvelope) -> None:
        if not self.is_allowed(envelope):
            raise ValueError("DiscordChannel only supports outbound text without attachments")

        metadata = envelope.metadata or {}
        token = metadata.get("interaction_token")
        app_id = metadata.get("application_id")

        if token and app_id:
            url = f"{self.config.base_url}/webhooks/{app_id}/{token}"
            payload = {"content": envelope.text}
            await post_json(url, payload, headers={}, timeout=self.config.request_timeout)
            return

        if self.config.webhook_url:
            payload = {"content": envelope.text}
            await post_json(self.config.webhook_url, payload, headers={}, timeout=self.config.request_timeout)
            return

        raise RuntimeError("Discord webhook missing; set DISCORD_WEBHOOK_URL or provide interaction token")
