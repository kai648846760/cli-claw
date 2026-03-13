from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from cli_claw.channels.base import BaseChannel
from cli_claw.channels.http_client import post_json, post_multipart
from cli_claw.channels.policy import sender_allowed
from cli_claw.schemas.channel import ChannelAttachment, InboundEnvelope, OutboundEnvelope


@dataclass
class SlackConfig:
    webhook_url: str | None = None
    signing_secret: str | None = None
    bot_token: str | None = None
    base_url: str = "https://slack.com/api"
    request_timeout: float = 10.0
    allow_from: list[str] = field(default_factory=list)
    group_policy: str = "mention"
    group_allow_from: list[str] = field(default_factory=list)


def _load_config() -> SlackConfig:
    return SlackConfig(
        webhook_url=os.getenv("SLACK_WEBHOOK_URL"),
        signing_secret=os.getenv("SLACK_SIGNING_SECRET"),
        bot_token=os.getenv("SLACK_BOT_TOKEN"),
        base_url=os.getenv("SLACK_BASE_URL", "https://slack.com/api"),
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

        event_type = event.get("type")
        event_kind = None
        if event_type == "reaction_added":
            item = event.get("item") or {}
            channel_id = item.get("channel")
            sender_id = str(event.get("user") or "") or None
            if not sender_allowed(sender_id, self.config.allow_from):
                return None
            event_kind = "reaction"
            return InboundEnvelope(
                channel=self.name,
                chat_id=str(channel_id or ""),
                sender_id=sender_id,
                message_id=str(item.get("ts") or "") or None,
                reply_to_id=None,
                text=f"reaction_added :{event.get('reaction')}:",
                metadata={
                    "event_type": "reaction_added",
                    "event_kind": event_kind,
                    "item_type": item.get("type"),
                    "item_user": event.get("item_user"),
                },
            )
        if event_type == "reaction_removed":
            item = event.get("item") or {}
            channel_id = item.get("channel")
            sender_id = str(event.get("user") or "") or None
            if not sender_allowed(sender_id, self.config.allow_from):
                return None
            event_kind = "reaction"
            return InboundEnvelope(
                channel=self.name,
                chat_id=str(channel_id or ""),
                sender_id=sender_id,
                message_id=str(item.get("ts") or "") or None,
                reply_to_id=None,
                text=f"reaction_removed :{event.get('reaction')}:",
                metadata={
                    "event_type": "reaction_removed",
                    "event_kind": event_kind,
                    "item_type": item.get("type"),
                    "item_user": event.get("item_user"),
                },
            )

        if event_type in {
            "file_shared",
            "file_created",
            "file_public",
            "file_change",
            "file_deleted",
            "file_comment_added",
        }:
            sender_id = str(event.get("user") or "") or None
            if not sender_allowed(sender_id, self.config.allow_from):
                return None
            file_info = event.get("file") or {}
            file_id = event.get("file_id") or file_info.get("id")
            attachments: list[ChannelAttachment] = []
            if isinstance(file_info, dict) and file_info:
                mimetype = file_info.get("mimetype") or ""
                kind = "file"
                if isinstance(mimetype, str):
                    if mimetype.startswith("image/"):
                        kind = "image"
                    elif mimetype.startswith("audio/"):
                        kind = "audio"
                    elif mimetype.startswith("video/"):
                        kind = "video"
                attachments.append(
                    ChannelAttachment(
                        kind=kind,
                        name=file_info.get("name"),
                        url=file_info.get("url_private") or file_info.get("url_private_download"),
                        mime_type=file_info.get("mimetype"),
                        size_bytes=file_info.get("size"),
                        metadata={"file_id": file_info.get("id")},
                    )
                )
            text = f"{event_type} {file_id}" if file_id else event_type
            return InboundEnvelope(
                channel=self.name,
                chat_id=str(event.get("channel_id") or event.get("channel") or ""),
                sender_id=sender_id,
                message_id=str(event.get("event_ts") or "") or None,
                reply_to_id=None,
                text=text,
                attachments=attachments,
                metadata={
                    "event_type": event_type,
                    "event_kind": "file",
                    "file_id": file_id,
                    "team_id": payload.get("team_id"),
                },
            )

        if event_type not in ("message", "app_mention"):
            return None
        if event.get("subtype") == "bot_message":
            return None

        if event.get("subtype") == "message_changed":
            inner = (event.get("message") or {}) if isinstance(event.get("message"), dict) else {}
            if inner:
                inner["channel"] = event.get("channel") or inner.get("channel")
                inner["thread_ts"] = inner.get("thread_ts") or (event.get("message") or {}).get("thread_ts")
                event = inner
        if event.get("subtype") == "message_deleted":
            previous = event.get("previous_message") or {}
            if isinstance(previous, dict):
                event = {
                    "channel": event.get("channel"),
                    "thread_ts": previous.get("thread_ts"),
                    "ts": event.get("deleted_ts") or previous.get("ts"),
                    "text": previous.get("text") or "",
                    "user": previous.get("user") or event.get("user"),
                    "subtype": "message_deleted",
                }

        text = event.get("text") or ""
        attachments: list[ChannelAttachment] = []

        if event.get("files"):
            for item in event.get("files") or []:
                if not isinstance(item, dict):
                    continue
                kind = "file"
                mimetype = item.get("mimetype") or ""
                if isinstance(mimetype, str):
                    if mimetype.startswith("image/"):
                        kind = "image"
                    elif mimetype.startswith("audio/"):
                        kind = "audio"
                    elif mimetype.startswith("video/"):
                        kind = "video"
                attachments.append(
                    ChannelAttachment(
                        kind=kind,
                        name=item.get("name"),
                        url=item.get("url_private"),
                        mime_type=item.get("mimetype"),
                        size_bytes=item.get("size"),
                        metadata={"file_id": item.get("id")},
                    )
                )

        channel_type = event.get("channel_type")
        is_group = channel_type in {"channel", "group", "mpim"}

        if is_group and self.config.group_policy == "mention" and event_type != "app_mention":
            return None
        if is_group and self.config.group_policy == "allowlist":
            if event.get("channel") not in set(self.config.group_allow_from or []):
                return None

        sender_id = str(event.get("user") or "") or None
        if not sender_allowed(sender_id, self.config.allow_from):
            return None

        metadata: dict[str, Any] = {
            "event_type": event.get("type"),
            "event_subtype": event.get("subtype"),
            "team_id": payload.get("team_id"),
            "thread_id": event.get("thread_ts"),
            "channel_type": channel_type,
            "event_kind": "message",
        }

        return InboundEnvelope(
            channel=self.name,
            chat_id=str(event.get("channel") or ""),
            sender_id=sender_id,
            message_id=str(event.get("ts") or "") or None,
            reply_to_id=str(event.get("thread_ts") or "") or None,
            text=str(text),
            attachments=attachments,
            metadata={k: v for k, v in metadata.items() if v is not None},
        )

    async def send(self, envelope: OutboundEnvelope) -> None:
        if not self.is_allowed(envelope):
            raise ValueError("SlackChannel only supports outbound text messages")

        if envelope.attachments:
            await self._send_attachments(envelope)
            return

        if self.config.bot_token:
            await self._send_text_bot(envelope)
            return

        if not self.config.webhook_url:
            raise RuntimeError("Slack webhook missing; set SLACK_WEBHOOK_URL")
        payload = {"text": envelope.text}
        await post_json(self.config.webhook_url, payload, headers={}, timeout=self.config.request_timeout)

    async def _send_attachments(self, envelope: OutboundEnvelope) -> None:
        if not envelope.attachments:
            return
        for attachment in envelope.attachments:
            if attachment.path:
                if not self.config.bot_token:
                    raise RuntimeError("Slack bot token missing; set SLACK_BOT_TOKEN")
                await self._send_file(envelope, attachment)
                continue
            if attachment.url:
                if not self.config.webhook_url:
                    raise RuntimeError("Slack webhook missing; set SLACK_WEBHOOK_URL")
                text = envelope.text or ""
                content = f"{text}\n{attachment.url}" if text else attachment.url
                payload = {"text": content}
                await post_json(self.config.webhook_url, payload, headers={}, timeout=self.config.request_timeout)
                continue
            raise ValueError("SlackChannel attachment missing path/url")

    async def _send_text_bot(self, envelope: OutboundEnvelope) -> None:
        if not self.config.bot_token:
            raise RuntimeError("Slack bot token missing; set SLACK_BOT_TOKEN")

        url = f"{self.config.base_url}/chat.postMessage"
        payload: dict[str, Any] = {"channel": envelope.chat_id, "text": envelope.text}
        if envelope.reply_to_id:
            payload["thread_ts"] = envelope.reply_to_id
        headers = {"Authorization": f"Bearer {self.config.bot_token}"}
        resp = await post_json(url, payload, headers=headers, timeout=self.config.request_timeout)
        if isinstance(resp, dict):
            ts = resp.get("ts")
            if ts:
                envelope.message_id = str(ts)
                envelope.receipt_id = str(ts)

    async def _send_file(self, envelope: OutboundEnvelope, attachment: ChannelAttachment) -> None:
        if not attachment.path:
            raise ValueError("SlackChannel attachment missing path")

        url = f"{self.config.base_url}/files.upload"
        fields: dict[str, Any] = {
            "channels": envelope.chat_id,
            "initial_comment": envelope.text or "",
        }
        if envelope.reply_to_id:
            fields["thread_ts"] = envelope.reply_to_id

        files = {"file": {"path": attachment.path, "filename": attachment.name or attachment.path}}
        headers = {"Authorization": f"Bearer {self.config.bot_token}"}
        resp = await post_multipart(url, fields, files, headers=headers, timeout=self.config.request_timeout)
        if isinstance(resp, dict):
            ts = _extract_file_ts(resp)
            if ts:
                envelope.message_id = str(ts)
                envelope.receipt_id = str(ts)
                return
            file_id = (resp.get("file") or {}).get("id")
            if file_id:
                envelope.receipt_id = str(file_id)


def _extract_file_ts(resp: dict[str, Any]) -> str | None:
    file_info = resp.get("file")
    if not isinstance(file_info, dict):
        return None
    shares = file_info.get("shares")
    if not isinstance(shares, dict):
        return None
    for share_scope in ("public", "private"):
        scope = shares.get(share_scope)
        if not isinstance(scope, dict):
            continue
        for _, items in scope.items():
            if isinstance(items, list) and items:
                item = items[0]
                if isinstance(item, dict):
                    ts = item.get("ts")
                    if ts:
                        return str(ts)
    return None
