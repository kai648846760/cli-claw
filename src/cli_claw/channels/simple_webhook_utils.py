from __future__ import annotations

from typing import Any

from cli_claw.channels.http_client import post_json
from cli_claw.channels.policy import sender_allowed
from cli_claw.schemas.channel import ChannelAttachment, InboundEnvelope, OutboundEnvelope


def _parse_attachments(payload: dict[str, Any]) -> list[ChannelAttachment]:
    raw = payload.get("attachments")
    if not isinstance(raw, list):
        return []
    attachments: list[ChannelAttachment] = []
    for item in raw:
        if isinstance(item, ChannelAttachment):
            attachments.append(item)
            continue
        if isinstance(item, dict):
            attachments.append(ChannelAttachment(**item))
    return attachments


def parse_simple_inbound(
    channel: str,
    payload: dict[str, Any],
    *,
    allow_from: list[str] | None = None,
) -> InboundEnvelope | None:
    text = payload.get("text")
    if not isinstance(text, str):
        return None

    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}

    sender_id = str(payload.get("sender_id") or "") or None
    if allow_from is not None and sender_id and not sender_allowed(sender_id, allow_from):
        return None

    return InboundEnvelope(
        channel=channel,
        chat_id=str(payload.get("chat_id") or ""),
        sender_id=sender_id,
        message_id=str(payload.get("message_id") or "") or None,
        reply_to_id=str(payload.get("reply_to_id") or "") or None,
        text=text,
        attachments=_parse_attachments(payload),
        metadata=metadata,
    )


async def send_simple_webhook(
    *,
    webhook_url: str | None,
    envelope: OutboundEnvelope,
    timeout: float,
    missing_message: str,
) -> None:
    if not webhook_url:
        raise RuntimeError(missing_message)

    payload = {
        "text": envelope.text,
        "chat_id": envelope.chat_id,
        "attachments": [attachment.model_dump() for attachment in envelope.attachments],
    }
    await post_json(webhook_url, payload, headers={}, timeout=timeout)
