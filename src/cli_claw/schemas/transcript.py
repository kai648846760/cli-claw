from __future__ import annotations

from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


class TranscriptRecord(BaseModel):
    record_id: str = Field(default_factory=lambda: str(uuid4()))
    logical_session_id: str
    provider_session_id: str | None = None
    provider: str
    channel: str | None = None
    chat_id: str | None = None
    message_id: str | None = None
    reply_to_id: str | None = None
    role: Literal["user", "assistant", "tool", "system", "thought"]
    kind: Literal["delta", "final", "error", "event"]
    content: str = ""
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    tool: dict[str, Any] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)
