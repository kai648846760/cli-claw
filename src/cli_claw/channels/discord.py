from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from cli_claw.channels.base import BaseChannel
from cli_claw.channels.http_client import post_json, post_multipart
from cli_claw.channels.policy import sender_allowed
from cli_claw.schemas.channel import ChannelAttachment, InboundEnvelope, OutboundEnvelope


@dataclass
class DiscordConfig:
    application_id: str | None = None
    public_key: str | None = None
    webhook_url: str | None = None
    base_url: str = "https://discord.com/api/v10"
    request_timeout: float = 10.0
    allow_from: list[str] = field(default_factory=list)


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
        return envelope.kind in ("text", "error")

    def parse_inbound_event(self, payload: dict[str, Any]) -> InboundEnvelope | None:
        if payload.get("type") == 2:
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

            sender_id = str(user.get("id") or "") or None
            if not sender_allowed(sender_id, self.config.allow_from):
                return None

            metadata = {
                "interaction_id": payload.get("id"),
                "interaction_token": payload.get("token"),
                "application_id": payload.get("application_id"),
            }

            return InboundEnvelope(
                channel=self.name,
                chat_id=str(channel_id or ""),
                sender_id=sender_id,
                message_id=None,
                reply_to_id=None,
                text=text,
                metadata={k: v for k, v in metadata.items() if v is not None},
            )

        if payload.get("type") in {3, 5}:
            data = payload.get("data") or {}
            custom_id = data.get("custom_id") or "component"
            values = data.get("values") or []
            text = f"/{custom_id}"
            if values:
                text += " " + " ".join(str(v) for v in values)

            member = payload.get("member") or {}
            user = member.get("user") or payload.get("user") or {}
            channel_id = payload.get("channel_id")
            sender_id = str(user.get("id") or "") or None
            if not sender_allowed(sender_id, self.config.allow_from):
                return None

            metadata = {
                "interaction_id": payload.get("id"),
                "interaction_token": payload.get("token"),
                "application_id": payload.get("application_id"),
                "custom_id": custom_id,
                "component_type": data.get("component_type"),
                "interaction_type": payload.get("type"),
                "event_kind": "component",
            }

            return InboundEnvelope(
                channel=self.name,
                chat_id=str(channel_id or ""),
                sender_id=sender_id,
                message_id=None,
                reply_to_id=None,
                text=text,
                metadata={k: v for k, v in metadata.items() if v is not None},
            )

        if payload.get("type") != 0:
            return None

        event = payload.get("event") or {}
        if not isinstance(event, dict):
            return None
        if event.get("type") not in {"MESSAGE_CREATE", "MESSAGE_UPDATE", "MESSAGE_DELETE", "MESSAGE_DELETE_BULK"}:
            return None

        if event.get("type") in {"MESSAGE_DELETE", "MESSAGE_DELETE_BULK"}:
            sender_id = str((event.get("author") or {}).get("id") or "") or None
            if sender_id and not sender_allowed(sender_id, self.config.allow_from):
                return None
            return InboundEnvelope(
                channel=self.name,
                chat_id=str(event.get("channel_id") or ""),
                sender_id=sender_id,
                message_id=str(event.get("id") or "") or None,
                reply_to_id=None,
                text="message_deleted",
                metadata={
                    "event_type": event.get("type"),
                    "event_kind": "message_deleted",
                },
            )

        author = event.get("author") or {}
        attachments = []
        for item in event.get("attachments") or []:
            if not isinstance(item, dict):
                continue
            kind = "file"
            content_type = item.get("content_type") or ""
            if isinstance(content_type, str):
                if content_type.startswith("image/"):
                    kind = "image"
                elif content_type.startswith("audio/"):
                    kind = "audio"
                elif content_type.startswith("video/"):
                    kind = "video"
            attachments.append(
                ChannelAttachment(
                    kind=kind,
                    name=item.get("filename"),
                    url=item.get("url"),
                    size_bytes=item.get("size"),
                )
            )

        for item in event.get("embeds") or []:
            if not isinstance(item, dict):
                continue
            if item.get("image") and isinstance(item.get("image"), dict):
                attachments.append(
                    ChannelAttachment(
                        kind="image",
                        url=item["image"].get("url"),
                    )
                )

        sender_id = str(author.get("id") or "") or None
        if not sender_allowed(sender_id, self.config.allow_from):
            return None

        metadata = {
            "event_type": event.get("type"),
            "event_kind": "message",
        }

        referenced = event.get("referenced_message")
        reply_to_id = None
        if isinstance(referenced, dict):
            reply_to_id = referenced.get("id")
        elif referenced:
            reply_to_id = referenced
        if not reply_to_id:
            message_ref = event.get("message_reference") or {}
            if isinstance(message_ref, dict):
                reply_to_id = message_ref.get("message_id")

        return InboundEnvelope(
            channel=self.name,
            chat_id=str(event.get("channel_id") or ""),
            sender_id=sender_id,
            message_id=str(event.get("id") or "") or None,
            reply_to_id=str(reply_to_id) if reply_to_id else None,
            text=str(event.get("content") or ""),
            attachments=attachments,
            metadata={k: v for k, v in metadata.items() if v is not None},
        )

    async def send(self, envelope: OutboundEnvelope) -> None:
        metadata = envelope.metadata or {}
        token = metadata.get("interaction_token")
        app_id = metadata.get("application_id")

        url = None
        if token and app_id:
            url = f"{self.config.base_url}/webhooks/{app_id}/{token}"
        elif self.config.webhook_url:
            url = self.config.webhook_url

        if not url:
            raise RuntimeError("Discord webhook missing; set DISCORD_WEBHOOK_URL or provide interaction token")

        if envelope.attachments:
            await self._send_attachments(_with_wait(url), envelope)
            return

        payload = {"content": envelope.text}
        resp = await post_json(_with_wait(url), payload, headers={}, timeout=self.config.request_timeout)
        _apply_receipt(envelope, resp)

    async def _send_attachments(self, url: str, envelope: OutboundEnvelope) -> None:
        for attachment in envelope.attachments:
            if attachment.path:
                await self._send_file(url, envelope, attachment, attachment.path)
                continue
            if attachment.url:
                await self._send_url(url, envelope, attachment, attachment.url)
                continue
            raise ValueError("DiscordChannel attachment missing path/url")

    async def _send_file(self, url: str, envelope: OutboundEnvelope, attachment: ChannelAttachment, path: str) -> None:
        fields: dict[str, Any] = {"payload_json": {"content": envelope.text or ""}}
        files = {"files[0]": {"path": path, "filename": attachment.name or path}}
        resp = await post_multipart(url, fields, files, headers={}, timeout=self.config.request_timeout)
        _apply_receipt(envelope, resp)

    async def _send_url(self, url: str, envelope: OutboundEnvelope, attachment: ChannelAttachment, link: str) -> None:
        content = envelope.text or link
        payload = {"content": content}
        resp = await post_json(url, payload, headers={}, timeout=self.config.request_timeout)
        _apply_receipt(envelope, resp)


def _with_wait(url: str) -> str:
    if "wait=" in url:
        return url
    if "?" in url:
        return f"{url}&wait=true"
    return f"{url}?wait=true"


def _apply_receipt(envelope: OutboundEnvelope, resp: dict[str, Any] | None) -> None:
    if not isinstance(resp, dict):
        return
    message_id = resp.get("id")
    if message_id:
        envelope.message_id = str(message_id)
        envelope.receipt_id = str(message_id)
