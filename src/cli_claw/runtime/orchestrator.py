from __future__ import annotations

from cli_claw.kernel.compression.service import CompressionPolicyEngine
from cli_claw.kernel.commands.registry import CommandRegistry
from cli_claw.kernel.observability.service import ObservabilityKernel
from cli_claw.kernel.memory.store import MemoryStore
from cli_claw.kernel.session.service import SessionKernel
from cli_claw.kernel.transcript.service import TranscriptKernel
from cli_claw.registry.providers import ProviderRegistry
from uuid import uuid4
from datetime import date
from typing import Any

from cli_claw.schemas.channel import InboundEnvelope, OutboundEnvelope, ChannelAttachment
from cli_claw.schemas.events import EventType, RuntimeEvent
from cli_claw.schemas.provider import ProviderSpec
from cli_claw.schemas.transcript import TranscriptRecord
from datetime import datetime
import time


class RuntimeOrchestrator:
    def __init__(self) -> None:
        self.sessions = SessionKernel()
        self.transcript = TranscriptKernel()
        self.compression = CompressionPolicyEngine()
        self.observability = ObservabilityKernel()
        self.providers = ProviderRegistry()
        self.memory: MemoryStore | None = None
        self.commands = CommandRegistry()
        self.show_thoughts = False
        self.compression_trigger_tokens = 60000
        self.compression_provider_id: str | None = None
        self.compression_prompt: str | None = None

    def register_provider(self, spec: ProviderSpec) -> None:
        self.providers.register(spec)

    async def handle_inbound(
        self,
        provider_id: str,
        logical_session_id: str,
        inbound: InboundEnvelope,
        *,
        stream_handler=None,
    ) -> OutboundEnvelope:
        state = self.sessions.get_or_create(logical_session_id, provider_id)
        provider = self.providers.get(provider_id)

        if state.provider_session_id is None:
            state.provider_session_id = await provider.new_session(logical_session_id)
            self.observability.emit(
                RuntimeEvent(
                    type=EventType.SESSION_CREATED,
                    provider=provider_id,
                    logical_session_id=logical_session_id,
                    provider_session_id=state.provider_session_id,
                    channel=inbound.channel,
                    chat_id=inbound.chat_id,
                )
            )

        self.transcript.append(
            TranscriptRecord(
                logical_session_id=logical_session_id,
                provider_session_id=state.provider_session_id,
                provider=provider_id,
                channel=inbound.channel,
                chat_id=inbound.chat_id,
                message_id=inbound.message_id,
                reply_to_id=inbound.reply_to_id,
                role="user",
                kind="final",
                content=inbound.text,
                attachments=[a.model_dump() for a in inbound.attachments],
                meta=inbound.metadata,
            )
        )

        history_size = sum(len(r.content) for r in self.transcript.list_for_session(logical_session_id)) // 4
        if history_size >= self.compression_trigger_tokens and self.compression_trigger_tokens > 0:
            await self._compress_session(provider_id, logical_session_id, reason="auto")
        decision = self.compression.decide(history_size, self.compression_trigger_tokens)
        self.observability.emit(
            RuntimeEvent(
                type=EventType.COMPRESSION_TRIGGERED if decision.action != "noop" else EventType.COMPRESSION_SKIPPED,
                provider=provider_id,
                logical_session_id=logical_session_id,
                provider_session_id=state.provider_session_id,
                channel=inbound.channel,
                chat_id=inbound.chat_id,
                payload={"action": decision.action, "reason": decision.reason},
            )
        )

        content = ""
        stream_id = None
        stream_seq = 0
        use_stream = bool(
            getattr(provider, "spec", None)
            and provider.spec.capabilities.streaming
            and stream_handler is not None
        )

        if use_stream:
            stream_id = uuid4().hex
            stream_input = self._inject_summary(
                logical_session_id,
                inbound.text,
                provider_id=provider_id,
                channel=inbound.channel,
                chat_id=inbound.chat_id,
                thread_id=(inbound.metadata or {}).get("thread_id"),
                attachments=inbound.attachments,
            )
            async for event in provider.chat_stream(
                logical_session_id,
                stream_input,
                attachments=inbound.attachments,
            ):
                if event.type == EventType.MESSAGE_DELTA:
                    raw = str(event.payload.get("text") or "")
                    if not raw:
                        continue
                    delta, content, is_full = self._merge_stream_delta(content, raw)
                    if not delta:
                        continue
                    stream_seq += 1
                    await stream_handler(
                        OutboundEnvelope(
                            channel=inbound.channel,
                            chat_id=inbound.chat_id,
                            text=delta,
                            kind="text",
                            reply_to_id=inbound.message_id,
                            stream_id=stream_id,
                            stream_seq=stream_seq,
                            stream_final=False,
                            metadata={
                                "provider": provider_id,
                                "logical_session_id": logical_session_id,
                                "streaming": True,
                                "stream_full": bool(is_full),
                            },
                        )
                    )
                elif event.type == EventType.MESSAGE_FINAL:
                    final = str(event.payload.get("text") or "")
                    if final:
                        content = final
                        if stream_handler is not None and stream_seq > 0:
                            stream_seq += 1
                            await stream_handler(
                                OutboundEnvelope(
                                    channel=inbound.channel,
                                    chat_id=inbound.chat_id,
                                    text=final,
                                    kind="text",
                                    reply_to_id=inbound.message_id,
                                    stream_id=stream_id,
                                    stream_seq=stream_seq,
                                    stream_final=True,
                                    metadata={
                                        "provider": provider_id,
                                        "logical_session_id": logical_session_id,
                                        "streaming": True,
                                        "stream_full": True,
                                    },
                                )
                            )
                elif event.type == EventType.MESSAGE_ERROR:
                    raise RuntimeError(str(event.payload.get("error") or "stream error"))
                elif event.type == EventType.THOUGHT_DELTA and self.show_thoughts:
                    delta = str(event.payload.get("text") or "")
                    if not delta:
                        continue
                    await stream_handler(
                        OutboundEnvelope(
                            channel=inbound.channel,
                            chat_id=inbound.chat_id,
                            text=delta,
                            kind="notice",
                            reply_to_id=inbound.message_id,
                            stream_id=stream_id,
                            stream_seq=stream_seq,
                            stream_final=False,
                            metadata={
                                "provider": provider_id,
                                "logical_session_id": logical_session_id,
                                "streaming": True,
                                "thought": True,
                            },
                        )
                    )
                elif event.type in {EventType.TOOL_CALL_BEGIN, EventType.TOOL_CALL_UPDATE, EventType.TOOL_CALL_END}:
                    self.transcript.append(
                        TranscriptRecord(
                            logical_session_id=logical_session_id,
                            provider_session_id=state.provider_session_id,
                            provider=provider_id,
                            channel=inbound.channel,
                            chat_id=inbound.chat_id,
                            role="tool",
                            kind="event",
                            content="",
                            meta={"event": event.type.value, "payload": event.payload},
                        )
                    )
        else:
            content = await provider.chat(
                logical_session_id,
                self._inject_summary(
                    logical_session_id,
                    inbound.text,
                    provider_id=provider_id,
                    channel=inbound.channel,
                    chat_id=inbound.chat_id,
                    thread_id=(inbound.metadata or {}).get("thread_id"),
                    attachments=inbound.attachments,
                ),
                attachments=inbound.attachments,
            )

        outbound = OutboundEnvelope(
            channel=inbound.channel,
            chat_id=inbound.chat_id,
            text=content if not stream_id or stream_seq == 0 else "",
            kind="text",
            reply_to_id=inbound.message_id,
            metadata={"provider": provider_id, "logical_session_id": logical_session_id},
        )
        if stream_id:
            outbound.stream_id = stream_id
            outbound.stream_seq = stream_seq
            outbound.stream_final = True

        self.transcript.append(
            TranscriptRecord(
                logical_session_id=logical_session_id,
                provider_session_id=state.provider_session_id,
                provider=provider_id,
                channel=outbound.channel,
                chat_id=outbound.chat_id,
                message_id=outbound.message_id,
                reply_to_id=outbound.reply_to_id,
                role="assistant",
                kind="final",
                content=content,
                attachments=[a.model_dump() for a in outbound.attachments],
                meta=outbound.metadata,
            )
        )

        self._persist_memory(
            logical_session_id=logical_session_id,
            provider_id=provider_id,
            inbound=inbound,
            assistant_content=content,
        )

        self.observability.emit(
            RuntimeEvent(
                type=EventType.MESSAGE_FINAL,
                provider=provider_id,
                logical_session_id=logical_session_id,
                provider_session_id=state.provider_session_id,
                channel=outbound.channel,
                chat_id=outbound.chat_id,
                message_id=outbound.message_id,
                payload={"text": content, "reply_to_id": outbound.reply_to_id},
            )
        )
        return outbound

    def _inject_summary(
        self,
        logical_session_id: str,
        message: str,
        *,
        provider_id: str,
        channel: str | None,
        chat_id: str | None,
        thread_id: str | None,
        attachments: list[ChannelAttachment] | None = None,
    ) -> str:
        state = self.sessions.get_or_create(logical_session_id, provider_id)
        summary = state.metadata.get("summary")
        summary_type = state.metadata.get("summary_type")
        if not summary and self.memory and channel and chat_id:
            today = date.today().isoformat()
            key = self.memory.make_key(channel=channel, chat_id=chat_id, date=today, thread_id=thread_id)
            record = self.memory.get(key) or self.memory.get_latest(
                channel=channel,
                chat_id=chat_id,
                thread_id=thread_id,
            )
            if record:
                summary = record.get("summary")
                metadata = record.get("metadata") or {}
                if summary:
                    state.metadata["summary"] = summary
                    state.metadata["summary_source"] = "memory"
                if isinstance(metadata, dict):
                    summary_type = metadata.get("summary_type")
                    if summary_type:
                        state.metadata["summary_type"] = summary_type
        enriched_message = self._append_attachments(message, attachments)
        recent_lines: list[str] = []
        if hasattr(self.memory, "get_recent_messages") and self.memory and channel and chat_id:
            try:
                today = date.today().isoformat()
                key = self.memory.make_key(channel=channel, chat_id=chat_id, date=today, thread_id=thread_id)
                recent = self.memory.get_recent_messages(key, limit=12)
                if recent:
                    for item in recent:
                        content = str(item.get("content") or "")
                        if not content:
                            continue
                        attachments = item.get("attachments") or []
                        attachment_text = self._format_attachment_summary(attachments) if attachments else ""
                        if attachment_text:
                            content = f"{content} [attachments: {attachment_text}]"
                        recent_lines.append(f"{item.get('role')}: {content}")
            except Exception:
                recent_lines = []

        if summary_type == "rolling":
            recent_lines = []

        if not summary and not recent_lines:
            return enriched_message
        sections: list[str] = []
        if summary:
            sections.append(f"Conversation summary:\\n{summary}")
        if recent_lines:
            sections.append("Recent messages:\\n" + "\n".join(recent_lines))
        prefix = "\\n\\n".join(sections) + "\\n\\nUser message:\\n"
        return prefix + enriched_message

    @staticmethod
    def _merge_stream_delta(content: str, raw: str) -> tuple[str, str, bool]:
        if not content:
            return raw, raw, True
        if raw == content:
            return "", content, False
        if raw.startswith(content):
            return raw, raw, True
        if content.startswith(raw):
            return "", content, False
        if raw in content:
            return "", content, False
        if content in raw:
            return raw, raw, True
        # Overlap check: treat as delta if raw shares a long prefix with content suffix.
        max_overlap = min(len(content), len(raw))
        overlap = 0
        for size in range(max_overlap, 0, -1):
            if content[-size:] == raw[:size]:
                overlap = size
                break
        if overlap >= max(2, int(max_overlap * 0.5)):
            delta = raw[overlap:]
            return delta, content + delta, False
        # Fallback: if raw is longer, treat as full refresh to avoid duplication.
        if len(raw) >= len(content):
            return raw, raw, True
        return raw, content + raw, False

    @staticmethod
    def _append_attachments(message: str, attachments: list[ChannelAttachment] | None) -> str:
        if not attachments:
            return message
        lines = ["", "Attachments:"]
        for attachment in attachments:
            kind = getattr(attachment, "kind", None) or "file"
            name = getattr(attachment, "name", None)
            path = getattr(attachment, "path", None)
            url = getattr(attachment, "url", None)
            meta = getattr(attachment, "metadata", None)
            parts = []
            if name:
                parts.append(f"name={name}")
            if path:
                parts.append(f"path={path}")
            if url:
                parts.append(f"url={url}")
            if isinstance(meta, dict):
                mime = meta.get("mime_type")
                if mime:
                    parts.append(f"mime={mime}")
            detail = ", ".join(parts) if parts else "no metadata"
            lines.append(f"- [{kind}] {detail}")
        lines.append("If you can access local files, you may open attachment paths for analysis.")
        return message + "\n" + "\n".join(lines)

    async def compress_session(self, provider_id: str, logical_session_id: str, reason: str = "manual") -> str | None:
        return await self._compress_session(provider_id, logical_session_id, reason=reason)

    def estimate_tokens(self, logical_session_id: str) -> int:
        return sum(len(r.content or "") for r in self.transcript.list_for_session(logical_session_id)) // 4

    def get_session_status(self, provider_id: str, logical_session_id: str) -> dict[str, object]:
        state = self.sessions.get_or_create(logical_session_id, provider_id)
        token_estimate = self.estimate_tokens(logical_session_id)
        compression_count = int(state.metadata.get("compression_count", 0))
        last_compressed_at = state.metadata.get("last_compressed_at")
        last_compressed_at_iso = None
        if isinstance(last_compressed_at, (int, float)):
            last_compressed_at_iso = datetime.fromtimestamp(last_compressed_at).astimezone().isoformat()
        return {
            "provider": provider_id,
            "logical_session_id": logical_session_id,
            "provider_session_id": state.provider_session_id,
            "history_messages": len(self.transcript.list_for_session(logical_session_id)),
            "history_tokens": token_estimate,
            "compression_trigger_tokens": int(self.compression_trigger_tokens),
            "compression_count": compression_count,
            "compression_provider": self.compression_provider_id or provider_id,
            "last_compressed_at": last_compressed_at_iso,
            "summary_present": bool(state.metadata.get("compressed_summary")),
            "language": state.metadata.get("language"),
        }

    async def _compress_session(self, provider_id: str, logical_session_id: str, reason: str = "auto") -> str | None:
        provider_key = self.compression_provider_id or provider_id
        provider = self.providers.get(provider_key)
        records = self.transcript.list_for_session(logical_session_id)
        if not records:
            return None
        text_parts: list[str] = []
        for rec in records[-40:]:
            content = rec.content
            if not content:
                continue
            text_parts.append(f"{rec.role}: {content}")
        history_text = "\n".join(text_parts).strip()
        if not history_text:
            return None
        prompt = self.compression_prompt or "Summarize the conversation so far with key decisions and context."
        summary_input = f"{prompt}\n\n{history_text}"
        summary = ""
        try:
            summary = await provider.chat(logical_session_id, summary_input)
        except Exception:
            # Fallback: keep a trimmed recent history as summary.
            summary = history_text[-2000:]
        if not summary:
            return None

        state = self.sessions.get_or_create(logical_session_id, provider_id)
        state.metadata["summary"] = summary
        state.metadata["summary_source"] = "compression"
        state.metadata["compressed_summary"] = summary
        state.metadata["summary_type"] = "compressed"
        state.metadata["compression_count"] = int(state.metadata.get("compression_count", 0)) + 1
        state.metadata["last_compressed_at"] = time.time()
        state.metadata["last_compressed_reason"] = reason
        if self.memory:
            record = next(
                (r for r in reversed(records) if r.channel and r.chat_id),
                None,
            )
            if record:
                thread_id = None
                if record.meta:
                    thread_id = record.meta.get("thread_id")
                today = date.today().isoformat()
                key = self.memory.make_key(
                    channel=str(record.channel),
                    chat_id=str(record.chat_id),
                    date=today,
                    thread_id=str(thread_id) if thread_id else None,
                )
                self.memory.set(
                    key=key,
                    summary=summary,
                    provider=provider_key,
                    metadata={
                        "logical_session_id": logical_session_id,
                        "language": state.metadata.get("language"),
                        "summary_type": "compressed",
                    },
                )

        try:
            await provider.clear_session(logical_session_id, state.provider_session_id)
        except Exception:
            pass
        state.provider_session_id = None

        self.transcript.append(
            TranscriptRecord(
                logical_session_id=logical_session_id,
                provider_session_id=None,
                provider=provider_key,
                channel=None,
                chat_id=None,
                role="system",
                kind="event",
                content=summary,
                meta={"summary": True},
            )
        )
        return summary

    def _persist_memory(
        self,
        *,
        logical_session_id: str,
        provider_id: str,
        inbound: InboundEnvelope,
        assistant_content: str,
    ) -> None:
        if not self.memory:
            return
        channel = inbound.channel
        chat_id = inbound.chat_id
        thread_id = None
        if inbound.metadata:
            thread_id = inbound.metadata.get("thread_id")
        today = date.today().isoformat()
        key = self.memory.make_key(channel=channel, chat_id=chat_id, date=today, thread_id=thread_id)
        if hasattr(self.memory, "append_message"):
            try:
                self.memory.append_message(
                    key=key,
                    role="user",
                    content=inbound.text or "",
                    attachments=[a.model_dump() for a in inbound.attachments] if inbound.attachments else [],
                    provider=provider_id,
                )
                if assistant_content:
                    self.memory.append_message(
                        key=key,
                        role="assistant",
                        content=assistant_content,
                        attachments=[],
                        provider=provider_id,
                    )
            except Exception:
                pass
        state = self.sessions.get_or_create(logical_session_id, provider_id)
        compressed_summary = state.metadata.get("compressed_summary")
        existing = self.memory.get(key)
        existing_summary = existing.get("summary") if existing else None
        existing_meta = existing.get("metadata") if existing else None
        summary_type = existing_meta.get("summary_type") if isinstance(existing_meta, dict) else None
        if summary_type == "compressed":
            if state.metadata.get("language"):
                try:
                    self.memory.update_metadata(key, {"language": state.metadata.get("language")})
                except Exception:
                    pass
            return
        existing_lines = [line for line in (existing_summary or "").splitlines() if line.strip()]

        summary_line = f"summary: {compressed_summary}" if compressed_summary else None
        if existing_lines and existing_lines[0].startswith("summary: "):
            existing_lines = existing_lines[1:]

        recent_lines = self._build_recent_lines(logical_session_id, max_items=200, max_chars=8000)
        if not recent_lines:
            fallback_lines: list[str] = []
            if inbound.text:
                fallback_lines.append(f"user: {inbound.text}")
            if assistant_content:
                fallback_lines.append(f"assistant: {assistant_content}")
            recent_lines = fallback_lines
        if not recent_lines and not existing_lines:
            return

        merged_lines = existing_lines[:]
        if recent_lines:
            overlap = 0
            append_start = 0
            max_k = min(len(existing_lines), len(recent_lines))
            for k in range(max_k, 0, -1):
                suffix = existing_lines[-k:]
                for i in range(0, len(recent_lines) - k + 1):
                    if recent_lines[i : i + k] == suffix:
                        overlap = k
                        append_start = i + k
                        break
                if overlap:
                    break
            if overlap:
                merged_lines.extend(recent_lines[append_start:])
            else:
                merged_lines.extend(recent_lines)

        if not merged_lines:
            return

        if summary_line:
            merged_lines = [summary_line] + merged_lines
        summary = "\n".join(merged_lines).strip()
        max_chars = 8000
        if len(summary) > max_chars:
            trimmed_lines = merged_lines[:]
            keep_summary = None
            if trimmed_lines and trimmed_lines[0].startswith("summary: "):
                keep_summary = trimmed_lines.pop(0)
            while trimmed_lines and len("\n".join(trimmed_lines)) > (max_chars - (len(keep_summary or "") + 1)):
                trimmed_lines.pop(0)
            if keep_summary:
                trimmed_lines.insert(0, keep_summary)
            summary = "\n".join(trimmed_lines).strip()
        if existing_summary and existing_summary == summary:
            return
        metadata = {"logical_session_id": logical_session_id}
        if state.metadata.get("language"):
            metadata["language"] = state.metadata.get("language")
        metadata["summary_type"] = "rolling"
        self.memory.set(
            key=key,
            summary=summary,
            provider=provider_id,
            metadata=metadata,
        )

    def _build_recent_lines(self, logical_session_id: str, max_items: int = 200, max_chars: int = 8000) -> list[str]:
        records = self.transcript.list_for_session(logical_session_id)
        lines: list[str] = []
        for rec in records:
            if rec.role not in {"user", "assistant"}:
                continue
            content = rec.content or ""
            attachments = rec.attachments or []
            attachment_text = self._format_attachment_summary(attachments)
            if attachment_text:
                if content:
                    content = f"{content} [attachments: {attachment_text}]"
                else:
                    content = f"[attachments: {attachment_text}]"
            if not content:
                continue
            lines.append(f"{rec.role}: {content}")
        if not lines:
            return []
        if len(lines) > max_items:
            lines = lines[-max_items:]
        while lines and len("\n".join(lines)) > max_chars:
            lines.pop(0)
        return lines

    @staticmethod
    def _format_attachment_summary(attachments: list[dict[str, Any]]) -> str:
        if not attachments:
            return ""
        bits: list[str] = []
        for attachment in attachments:
            if not isinstance(attachment, dict):
                continue
            kind = attachment.get("kind") or "file"
            name = attachment.get("name")
            path = attachment.get("path")
            url = attachment.get("url")
            detail_parts = []
            if name:
                detail_parts.append(f"name={name}")
            if path:
                detail_parts.append(f"path={path}")
            if url:
                detail_parts.append(f"url={url}")
            detail = " ".join(detail_parts) if detail_parts else "no-metadata"
            bits.append(f"{kind}({detail})")
        return "; ".join(bits)
