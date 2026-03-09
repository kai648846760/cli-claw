from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ChannelAttachment(BaseModel):
    kind: Literal["file", "image", "audio", "video", "link"] = "file"
    name: str | None = None
    url: str | None = None
    path: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class InboundEnvelope(BaseModel):
    channel: str
    chat_id: str
    sender_id: str | None = None
    message_id: str | None = None
    reply_to_id: str | None = None
    text: str = ""
    attachments: list[ChannelAttachment] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class OutboundEnvelope(BaseModel):
    channel: str
    chat_id: str
    text: str = ""
    kind: Literal["text", "error", "notice"] = "text"
    message_id: str | None = None
    reply_to_id: str | None = None
    attachments: list[ChannelAttachment] = Field(default_factory=list)
    stream_id: str | None = None
    stream_seq: int | None = None
    stream_final: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)
