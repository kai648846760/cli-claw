from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from cli_claw.channels.base import BaseChannel
from cli_claw.channels.http_client import post_json, post_multipart
from cli_claw.schemas.channel import ChannelAttachment, InboundEnvelope, OutboundEnvelope


@dataclass
class TelegramConfig:
    bot_token: str | None = None
    webhook_secret: str | None = None
    base_url: str = "https://api.telegram.org"
    request_timeout: float = 10.0


def _load_config() -> TelegramConfig:
    return TelegramConfig(
        bot_token=os.getenv("TELEGRAM_BOT_TOKEN"),
        webhook_secret=os.getenv("TELEGRAM_WEBHOOK_SECRET"),
        base_url=os.getenv("TELEGRAM_BASE_URL", "https://api.telegram.org"),
    )


class TelegramChannel(BaseChannel):
    name = "telegram"

    def __init__(self, config: TelegramConfig | None = None) -> None:
        super().__init__()
        self.config = config or _load_config()

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    def is_allowed(self, envelope: OutboundEnvelope) -> bool:
        return envelope.kind == "text" and not envelope.attachments

    def parse_inbound_event(self, payload: dict[str, Any]) -> InboundEnvelope | None:
        message = (
            payload.get("message")
            or payload.get("edited_message")
            or payload.get("channel_post")
            or payload.get("edited_channel_post")
        )
        if not isinstance(message, dict):
            return None

        chat = message.get("chat") or {}
        sender = message.get("from") or {}

        text = message.get("text") or message.get("caption") or ""
        attachments: list[ChannelAttachment] = []

        photo = message.get("photo")
        if isinstance(photo, list) and photo:
            attachments.append(
                ChannelAttachment(
                    kind="image",
                    name="photo",
                    metadata={"file_id": photo[-1].get("file_id"), "sizes": photo},
                )
            )
        if message.get("document"):
            doc = message["document"]
            attachments.append(
                ChannelAttachment(
                    kind="file",
                    name=doc.get("file_name"),
                    metadata={"file_id": doc.get("file_id"), "mime_type": doc.get("mime_type")},
                )
            )
        if message.get("voice"):
            voice = message["voice"]
            attachments.append(
                ChannelAttachment(
                    kind="audio",
                    name="voice",
                    metadata={"file_id": voice.get("file_id"), "mime_type": voice.get("mime_type")},
                )
            )
        if message.get("video"):
            video = message["video"]
            attachments.append(
                ChannelAttachment(
                    kind="video",
                    name=video.get("file_name") or "video",
                    metadata={"file_id": video.get("file_id"), "mime_type": video.get("mime_type")},
                )
            )

        metadata: dict[str, Any] = {
            "update_id": payload.get("update_id"),
            "chat_type": chat.get("type"),
            "message_type": "text" if message.get("text") else None,
            "thread_id": message.get("message_thread_id"),
        }

        return InboundEnvelope(
            channel=self.name,
            chat_id=str(chat.get("id") or ""),
            sender_id=str(sender.get("id") or "") or None,
            message_id=str(message.get("message_id") or "") or None,
            reply_to_id=str((message.get("reply_to_message") or {}).get("message_id") or "") or None,
            text=str(text),
            attachments=attachments,
            metadata={k: v for k, v in metadata.items() if v is not None},
        )

    async def send(self, envelope: OutboundEnvelope) -> None:
        if not self.config.bot_token:
            raise RuntimeError("Telegram bot token missing; set TELEGRAM_BOT_TOKEN")

        if envelope.attachments:
            await self._send_attachments(envelope)
            return

        url = f"{self.config.base_url}/bot{self.config.bot_token}/sendMessage"
        payload: dict[str, Any] = {"chat_id": envelope.chat_id, "text": envelope.text}
        if envelope.reply_to_id:
            payload["reply_to_message_id"] = envelope.reply_to_id
        await post_json(url, payload, headers={}, timeout=self.config.request_timeout)

    async def _send_attachments(self, envelope: OutboundEnvelope) -> None:
        for attachment in envelope.attachments:
            if attachment.path:
                await self._send_file(envelope, attachment, attachment.path)
                continue
            if attachment.url:
                await self._send_url(envelope, attachment, attachment.url)
                continue
            raise ValueError("TelegramChannel attachment missing path/url")

    async def _send_file(self, envelope: OutboundEnvelope, attachment: ChannelAttachment, path: str) -> None:
        kind = attachment.kind
        if kind in ("image", "photo"):
            method = "sendPhoto"
            file_key = "photo"
        elif kind == "video":
            method = "sendVideo"
            file_key = "video"
        elif kind == "audio":
            method = "sendAudio"
            file_key = "audio"
        else:
            method = "sendDocument"
            file_key = "document"

        url = f"{self.config.base_url}/bot{self.config.bot_token}/{method}"
        fields: dict[str, Any] = {"chat_id": envelope.chat_id}
        if envelope.text:
            fields["caption"] = envelope.text
        if envelope.reply_to_id:
            fields["reply_to_message_id"] = envelope.reply_to_id
        files = {file_key: {"path": path, "filename": attachment.name or path}}
        await post_multipart(url, fields, files, headers={}, timeout=self.config.request_timeout)

    async def _send_url(self, envelope: OutboundEnvelope, attachment: ChannelAttachment, url: str) -> None:
        kind = attachment.kind
        if kind in ("image", "photo"):
            method = "sendPhoto"
            file_key = "photo"
        elif kind == "video":
            method = "sendVideo"
            file_key = "video"
        elif kind == "audio":
            method = "sendAudio"
            file_key = "audio"
        else:
            method = "sendDocument"
            file_key = "document"

        api_url = f"{self.config.base_url}/bot{self.config.bot_token}/{method}"
        payload: dict[str, Any] = {"chat_id": envelope.chat_id, file_key: url}
        if envelope.text:
            payload["caption"] = envelope.text
        if envelope.reply_to_id:
            payload["reply_to_message_id"] = envelope.reply_to_id
        await post_json(api_url, payload, headers={}, timeout=self.config.request_timeout)
