from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from cli_claw.channels.base import BaseChannel
from cli_claw.channels.http_client import post_json
from cli_claw.schemas.channel import InboundEnvelope, OutboundEnvelope


@dataclass
class SlackConfig:
    webhook_url: str | None = None
    signing_secret: str | None = None
    request_timeout: float = 10.0


def _load_config() -> SlackConfig:
    return SlackConfig(
        webhook_url=os.getenv("SLACK_WEBHOOK_URL"),
        signing_secret=os.getenv("SLACK_SIGNING_SECRET"),
    )


class SlackChannel(BaseChannel):
    name = "slack"

    def __init__(self, config: SlackConfig | None = None) -> None:
        super().__init__()
        self.config = config or _load_config()

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    def is_allowed(self, envelope: OutboundEnvelope) -> bool:
        return envelope.kind in ("text", "error")

    def parse_inbound_event(self, payload: dict[str, Any]) -> InboundEnvelope | None:
        event = payload.get("event") or {}
        if not isinstance(event, dict):
            return None
        if event.get("type") != "message":
            return None
        if event.get("subtype") == "bot_message":
            return None

        text = event.get("text") or ""
        metadata: dict[str, Any] = {
            "event_type": event.get("type"),
            "team_id": payload.get("team_id"),
            "thread_id": event.get("thread_ts"),
        }

        return InboundEnvelope(
            channel=self.name,
            chat_id=str(event.get("channel") or ""),
            sender_id=str(event.get("user") or "") or None,
            message_id=str(event.get("ts") or "") or None,
            reply_to_id=str(event.get("thread_ts") or "") or None,
            text=str(text),
            metadata={k: v for k, v in metadata.items() if v is not None},
        )

    async def send(self, envelope: OutboundEnvelope) -> None:
        if not self.is_allowed(envelope):
            raise ValueError("SlackChannel only supports outbound text without attachments")
        if not self.config.webhook_url:
            raise RuntimeError("Slack webhook missing; set SLACK_WEBHOOK_URL")
        payload = {"text": envelope.text}
        await post_json(self.config.webhook_url, payload, headers={}, timeout=self.config.request_timeout)
