from __future__ import annotations

from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


class EventType(str, Enum):
    MESSAGE_DELTA = "message.delta"
    MESSAGE_FINAL = "message.final"
    MESSAGE_ERROR = "message.error"
    THOUGHT_DELTA = "thought.delta"
    TOOL_CALL_BEGIN = "tool.call.begin"
    TOOL_CALL_UPDATE = "tool.call.update"
    TOOL_CALL_END = "tool.call.end"
    SESSION_CREATED = "session.created"
    SESSION_CLEARED = "session.cleared"
    SESSION_INVALIDATED = "session.invalidated"
    SESSION_ROTATED = "session.rotated"
    RECOVERY_RETRY = "recovery.retry"
    RECOVERY_COMPACT_RETRY = "recovery.compact_retry"
    RECOVERY_FALLBACK = "recovery.fallback"
    COMPRESSION_TRIGGERED = "compression.triggered"
    COMPRESSION_APPLIED = "compression.applied"
    COMPRESSION_SKIPPED = "compression.skipped"
    METRIC_COUNTER = "metric.counter"
    METRIC_LATENCY = "metric.latency"
    TRACE_STEP = "trace.step"


class RuntimeEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    type: EventType
    provider: str | None = None
    logical_session_id: str | None = None
    provider_session_id: str | None = None
    channel: str | None = None
    chat_id: str | None = None
    message_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class MessageInput(BaseModel):
    channel: str
    chat_id: str
    sender_id: str | None = None
    content: str
    attachments: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MessageOutput(BaseModel):
    content: str
    kind: Literal["text", "error", "notice"] = "text"
    attachments: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
