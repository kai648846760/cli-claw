from __future__ import annotations

import asyncio
import json
import logging
import shlex
import shutil
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from cli_claw.channels.manager import ChannelManager
from cli_claw.config.loader import load_config, dump_config, get_config_path
from cli_claw.kernel.commands.registry import CommandRegistry
from cli_claw.runtime.orchestrator import RuntimeOrchestrator
from cli_claw.schemas.channel import InboundEnvelope, OutboundEnvelope
from cli_claw.schemas.events import EventType, RuntimeEvent
from cli_claw.schemas.transcript import TranscriptRecord
from datetime import date

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChannelRoute:
    channel: str
    provider: str


class ChannelRuntime:
    _DEFAULT_LANGUAGE = "zh-CN"
    _LANGUAGE_LABELS = {
        "en-US": "English",
        "zh-CN": "简体中文",
    }
    _I18N = {
        "en-US": {
            "help.title": "📌 Built-in commands",
            "help.skills": "🧰 Skill commands",
            "help.status": "• /status - show current provider + context stats",
            "help.new": "• /new - start a new session (clears today memory)",
            "help.compact": "• /compact - compress current session",
            "help.help": "• /help - show this help",
            "help.language": "• /language - set language (en-US, zh-CN)",
            "status.title": "✅ Status",
            "status.provider": "CLI",
            "status.command": "Command",
            "status.logical_session_id": "Session",
            "status.provider_session_id": "CLI Session",
            "status.history_messages": "Messages",
            "status.history_tokens": "Tokens",
            "status.compression_trigger_tokens": "Compression threshold",
            "status.compression_count": "Compression count",
            "status.compression_provider": "Compression CLI",
            "status.last_compressed_at": "Last compressed",
            "status.summary_present": "Summary present",
            "status.memory_key": "Memory key",
            "status.memory_present": "Memory present",
            "status.language": "Language",
            "compact.skipped": "🧹 Compression skipped (no content)",
            "compact.done": "🧹 Compressed (count={count})\n{summary}",
            "new.started": "🆕 New session started",
            "new.cleared": "memory cleared",
            "language.current": "🌐 Current language: {code} ({label})",
            "language.usage": "Usage: /language <code>",
            "language.available": "Available languages:",
            "language.set": "🌐 Language set: {code} ({label})",
            "language.invalid": "Unsupported language: {code}",
            "language.unknown": "⚠️ Unknown command: {command} (try /help)",
        },
        "zh-CN": {
            "help.title": "📌 内置命令",
            "help.skills": "🧰 技能命令",
            "help.status": "• /status - 查看当前 CLI 与上下文状态",
            "help.new": "• /new - 新建会话（清理当天记忆）",
            "help.compact": "• /compact - 压缩当前会话",
            "help.help": "• /help - 帮助",
            "help.language": "• /language - 设置语言（en-US, zh-CN）",
            "status.title": "✅ 状态",
            "status.provider": "当前 CLI",
            "status.command": "命令",
            "status.logical_session_id": "会话ID",
            "status.provider_session_id": "CLI会话ID",
            "status.history_messages": "历史消息数",
            "status.history_tokens": "上下文长度",
            "status.compression_trigger_tokens": "压缩阈值",
            "status.compression_count": "压缩次数",
            "status.compression_provider": "压缩CLI",
            "status.last_compressed_at": "最近压缩时间",
            "status.summary_present": "摘要存在",
            "status.memory_key": "记忆Key",
            "status.memory_present": "记忆存在",
            "status.language": "语言",
            "compact.skipped": "🧹 压缩跳过（无内容）",
            "compact.done": "🧹 已压缩（次数={count}）\n{summary}",
            "new.started": "🆕 已新建会话",
            "new.cleared": "记忆已清理",
            "language.current": "🌐 当前语言：{code}（{label}）",
            "language.usage": "用法：/language <code>",
            "language.available": "可用语言：",
            "language.set": "🌐 语言已设置：{code}（{label}）",
            "language.invalid": "不支持的语言：{code}",
            "language.unknown": "⚠️ 未知命令：{command}（试试 /help）",
        },
    }

    def __init__(
        self,
        orchestrator: RuntimeOrchestrator,
        channel_manager: ChannelManager | None = None,
        *,
        heartbeat_enabled: bool = False,
        heartbeat_interval_seconds: int = 60,
        schedules: list[dict[str, Any]] | None = None,
        schedule_store_path: Path | None = None,
    ) -> None:
        self.orchestrator = orchestrator
        self.channels = channel_manager or ChannelManager()
        self._routes: dict[str, str] = {}
        self._running = False
        self.channels.set_delivery_handler(self._record_delivery)
        self._heartbeat_enabled = heartbeat_enabled
        self._heartbeat_interval_seconds = max(5, int(heartbeat_interval_seconds))
        self._schedules = schedules or []
        self._heartbeat_task: asyncio.Task | None = None
        self._schedule_task: asyncio.Task | None = None
        self._schedule_store_path = schedule_store_path
        self._scheduled_tasks: dict[str, ScheduledTask] = {}

    def register_route(self, channel: str, provider: str) -> None:
        self._routes[channel] = provider

    def routes(self) -> list[ChannelRoute]:
        return [ChannelRoute(channel=key, provider=value) for key, value in sorted(self._routes.items())]

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self, enabled_channels: list[str]) -> None:
        if self._running:
            return
        await self.channels.start_enabled(enabled_channels)
        for name in self.channels.channels.keys():
            self.channels.bind_inbound_handler(name, self._make_inbound_handler(name))
        providers = {self._routes.get(name) for name in enabled_channels if self._routes.get(name)}
        tasks = []
        for provider_id in providers:
            provider = self.orchestrator.providers.get(provider_id)
            tasks.append(asyncio.create_task(provider.start()))
        if tasks:
            await asyncio.gather(*tasks)
        self._load_schedule_store()
        self._merge_schedule_sources()
        if self._heartbeat_enabled:
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        if self._scheduled_tasks:
            self._schedule_task = asyncio.create_task(self._schedule_loop())
        self._running = True

    async def stop(self) -> None:
        if not self._running:
            return
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None
        if self._schedule_task:
            self._schedule_task.cancel()
            try:
                await self._schedule_task
            except asyncio.CancelledError:
                pass
            self._schedule_task = None
        await self.channels.stop_all()
        providers = {provider_id for provider_id in self._routes.values() if provider_id}
        tasks = []
        for provider_id in providers:
            provider = self.orchestrator.providers.get(provider_id)
            tasks.append(asyncio.create_task(provider.stop()))
        if tasks:
            await asyncio.gather(*tasks)
        self._running = False

    async def _handle_inbound(self, channel_name: str, inbound: InboundEnvelope) -> None:
        provider_id = self._routes.get(channel_name)
        if provider_id is None:
            raise ValueError(f"Channel '{channel_name}' has no provider route")
        await self._handle_inbound_with_provider(channel_name, provider_id, inbound)

    async def _handle_inbound_with_provider(
        self,
        channel_name: str,
        provider_id: str,
        inbound: InboundEnvelope,
    ) -> None:

        thread_id = None
        if inbound.metadata:
            thread_id = inbound.metadata.get("thread_id")

        logical_session_id = self.orchestrator.sessions.resolve_logical_session_id(
            provider=provider_id,
            channel=inbound.channel,
            chat_id=inbound.chat_id,
            thread_id=thread_id,
        )

        channel = self.channels.get(channel_name)
        command_result = await self._maybe_handle_command(
            channel=channel,
            provider_id=provider_id,
            logical_session_id=logical_session_id,
            inbound=inbound,
        )
        if isinstance(command_result, OutboundEnvelope):
            await self.channels.enqueue(command_result)
            return
        if isinstance(command_result, InboundEnvelope):
            inbound = command_result
            logical_session_id = self.orchestrator.sessions.resolve_logical_session_id(
                provider=provider_id,
                channel=inbound.channel,
                chat_id=inbound.chat_id,
                thread_id=thread_id,
            )

        typing_task: asyncio.Task | None = None
        if channel is not None:
            send_typing = getattr(channel, "send_typing", None)
            if callable(send_typing):
                try:
                    await send_typing(inbound.chat_id)
                except Exception:
                    pass
                async def _typing_loop() -> None:
                    try:
                        interval = 4.0
                        config = getattr(channel, "config", None)
                        if config is not None:
                            interval = float(getattr(config, "typing_interval", interval))
                        while True:
                            await send_typing(inbound.chat_id)
                            await asyncio.sleep(interval)
                    except asyncio.CancelledError:
                        return
                typing_task = asyncio.create_task(_typing_loop())
        stream_handler = None
        if channel and channel.supports_streaming():
            stream_buffers: dict[str, dict[str, float | str]] = {}
            stream_interval = 0.8
            config = getattr(channel, "config", None)
            if config is not None:
                stream_interval = float(getattr(config, "stream_update_interval", stream_interval))

            async def _stream_send(envelope: OutboundEnvelope) -> None:
                stream_id = envelope.stream_id or "default"
                state = stream_buffers.setdefault(stream_id, {"full": "", "last_sent": 0.0})
                incoming = envelope.text or ""
                if incoming:
                    if (envelope.metadata or {}).get("stream_full"):
                        state["full"] = incoming
                    else:
                        state["full"] = str(state["full"]) + incoming
                now = asyncio.get_running_loop().time()
                should_flush = envelope.stream_final or (now - float(state["last_sent"]) >= stream_interval)
                if should_flush and state["full"]:
                    send_env = envelope.model_copy(
                        update={
                            "text": state["full"],
                            "metadata": {**(envelope.metadata or {}), "stream_full": True},
                        }
                    )
                    await self.channels.enqueue(send_env)
                    state["last_sent"] = now
                if envelope.stream_final:
                    stream_buffers.pop(stream_id, None)

            stream_handler = _stream_send

        start_time = time.perf_counter()
        self.orchestrator.observability.emit(
            RuntimeEvent(
                type=EventType.METRIC_COUNTER,
                provider=provider_id,
                logical_session_id=logical_session_id,
                channel=inbound.channel,
                chat_id=inbound.chat_id,
                payload={"metric": "inbound_total", "delta": 1},
            )
        )

        try:
            outbound = await self.orchestrator.handle_inbound(
                provider_id,
                logical_session_id,
                inbound,
                stream_handler=stream_handler,
            )
        except Exception as exc:
            logger.exception("Inbound handling failed for channel '%s'", channel_name)
            outbound = OutboundEnvelope(
                channel=inbound.channel,
                chat_id=inbound.chat_id,
                kind="error",
                text=f"runtime error: {exc}",
                reply_to_id=inbound.message_id,
                receipt_id=inbound.message_id or inbound.reply_to_id,
                delivery_status="failed",
                error_code="runtime_error",
                error_detail=str(exc),
                metadata={"provider": provider_id, "logical_session_id": logical_session_id},
            )
        finally:
            if typing_task:
                typing_task.cancel()
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            self.orchestrator.observability.emit(
                RuntimeEvent(
                    type=EventType.METRIC_LATENCY,
                    provider=provider_id,
                    logical_session_id=logical_session_id,
                    channel=inbound.channel,
                    chat_id=inbound.chat_id,
                    payload={"metric": "inbound_latency_ms", "value": round(elapsed_ms, 2)},
                )
            )

        if inbound.metadata:
            outbound.metadata = {**inbound.metadata, **(outbound.metadata or {})}

        await self.channels.enqueue(outbound)

    async def _heartbeat_loop(self) -> None:
        while True:
            try:
                providers: dict[str, bool] = {}
                for provider_id in set(self._routes.values()):
                    try:
                        provider = self.orchestrator.providers.get(provider_id)
                        health = await provider.health_check()
                    except Exception:
                        health = False
                    providers[provider_id] = bool(health)
                channels = {name: ch.is_running for name, ch in self.channels.channels.items()}
                self.orchestrator.observability.emit(
                    RuntimeEvent(
                        type=EventType.TRACE_STEP,
                        payload={
                            "heartbeat": True,
                            "providers": providers,
                            "channels": channels,
                        },
                    )
                )
                await asyncio.sleep(self._heartbeat_interval_seconds)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Heartbeat loop failed")
                await asyncio.sleep(self._heartbeat_interval_seconds)

    async def _schedule_loop(self) -> None:
        if not self._scheduled_tasks:
            return
        while True:
            try:
                now = datetime.now().astimezone()
                next_due = None
                for task in list(self._scheduled_tasks.values()):
                    if not task.enabled:
                        continue
                    if task.next_run is None:
                        task.next_run = task.compute_next_run(now)
                    if task.next_run and now >= task.next_run:
                        await self._run_scheduled_task(task)
                        task.last_run = now
                        task.next_run = task.compute_next_run(now)
                    if task.next_run:
                        next_due = task.next_run if next_due is None else min(next_due, task.next_run)
                if next_due is None:
                    await asyncio.sleep(5)
                else:
                    sleep_for = max(1.0, (next_due - datetime.now().astimezone()).total_seconds())
                    await asyncio.sleep(min(sleep_for, 5.0))
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Schedule loop failed")
                await asyncio.sleep(5)

    async def _run_scheduled_task(self, task: "ScheduledTask") -> None:
        channel = self.channels.get(task.channel)
        if channel is None or not channel.is_running:
            logger.warning("Scheduled task '%s' skipped (channel not running)", task.name)
            return
        provider_id = task.provider or self._routes.get(task.channel)
        if not provider_id:
            logger.warning("Scheduled task '%s' skipped (provider missing)", task.name)
            return
        inbound = InboundEnvelope(
            channel=task.channel,
            chat_id=task.chat_id,
            text=task.message,
            metadata={"scheduled_task": task.name},
        )
        await self._handle_inbound_with_provider(task.channel, provider_id, inbound)

    @staticmethod
    def _build_schedule_tasks(raw: list[dict[str, Any]]) -> list["ScheduledTask"]:
        tasks: list[ScheduledTask] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            seed = f"{name}:{item.get('channel') or ''}:{item.get('chat_id') or ''}:{item.get('message') or ''}"
            task_id = str(item.get("id") or "").strip() or uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex[:8]
            channel = str(item.get("channel") or "").strip()
            chat_id = str(item.get("chat_id") or "").strip()
            message = str(item.get("message") or "").strip()
            if not channel or not chat_id or not message:
                continue
            tasks.append(
                ScheduledTask(
                    task_id=task_id,
                    name=name,
                    channel=channel,
                    chat_id=chat_id,
                    message=message,
                    provider=str(item.get("provider") or "").strip() or None,
                    enabled=bool(item.get("enabled", True)),
                    interval_seconds=item.get("interval_seconds"),
                    daily_at=str(item.get("daily_at") or "").strip() or None,
                )
            )
        return tasks
    async def _record_delivery(self, envelope: OutboundEnvelope) -> None:
        metadata = envelope.metadata or {}
        logical_session_id = metadata.get("logical_session_id")
        provider_id = metadata.get("provider")
        if not logical_session_id or not provider_id:
            return
        status = envelope.delivery_status or "unknown"
        self.orchestrator.observability.emit(
            RuntimeEvent(
                type=EventType.METRIC_COUNTER,
                provider=str(provider_id),
                logical_session_id=str(logical_session_id),
                channel=envelope.channel,
                chat_id=envelope.chat_id,
                payload={"metric": f"delivery_{status}", "delta": 1},
            )
        )
        self.orchestrator.transcript.append(
            TranscriptRecord(
                logical_session_id=str(logical_session_id),
                provider_session_id=None,
                provider=str(provider_id),
                channel=envelope.channel,
                chat_id=envelope.chat_id,
                message_id=envelope.message_id,
                reply_to_id=envelope.reply_to_id,
                role="system",
                kind="event",
                content="",
                meta={
                    "delivery_status": envelope.delivery_status,
                    "receipt_id": envelope.receipt_id,
                    "error_code": envelope.error_code,
                    "error_detail": envelope.error_detail,
                },
            )
        )

    async def handle_inbound(self, channel_name: str, inbound: InboundEnvelope) -> None:
        await self._handle_inbound(channel_name, inbound)

    def _make_inbound_handler(self, channel_name: str):
        async def _handler(inbound: InboundEnvelope) -> None:
            await self._handle_inbound(channel_name, inbound)

        return _handler

    async def _maybe_handle_command(
        self,
        *,
        channel,
        provider_id: str,
        logical_session_id: str,
        inbound: InboundEnvelope,
    ) -> OutboundEnvelope | InboundEnvelope | None:
        text = (inbound.text or "").strip()
        if not text.startswith("/"):
            return None
        language = self._resolve_language(provider_id, logical_session_id, inbound)
        command, _, args = text.partition(" ")
        command = command.strip().lower()
        args = args.strip()

        if command in {"/help", "/status", "/new", "/compact", "/language", "/cron", "/cli", "/skills"}:
            if command == "/help":
                return OutboundEnvelope(
                    channel=inbound.channel,
                    chat_id=inbound.chat_id,
                    kind="text",
                    text=self._render_help(language),
                    reply_to_id=inbound.message_id,
                    metadata={"provider": provider_id, "logical_session_id": logical_session_id},
                )
            if command == "/status":
                status = self.orchestrator.get_session_status(provider_id, logical_session_id)
                provider = self.orchestrator.providers.get(provider_id)
                command_line = None
                try:
                    spec = getattr(provider, "spec", None)
                    if spec is not None:
                        command_line = " ".join([spec.command, *spec.args])
                except Exception:
                    command_line = None
                memory_present = False
                memory_key = None
                if self.orchestrator.memory:
                    today = date.today().isoformat()
                    thread_id = None
                    if inbound.metadata:
                        thread_id = inbound.metadata.get("thread_id")
                    key = self.orchestrator.memory.make_key(
                        channel=inbound.channel,
                        chat_id=inbound.chat_id,
                        date=today,
                        thread_id=thread_id,
                    )
                    memory_key = key.to_string()
                    memory_present = bool(self.orchestrator.memory.get(key))
                lines = [
                    self._t(language, "status.title"),
                    f"• {self._t(language, 'status.provider')}: {status['provider']}",
                    f"• {self._t(language, 'status.command')}: {command_line or 'unknown'}",
                    f"• {self._t(language, 'status.logical_session_id')}: {status['logical_session_id']}",
                    f"• {self._t(language, 'status.provider_session_id')}: {status['provider_session_id'] or 'pending'}",
                    f"• {self._t(language, 'status.history_messages')}: {status['history_messages']}",
                    f"• {self._t(language, 'status.history_tokens')}: {status['history_tokens']}",
                    f"• {self._t(language, 'status.compression_trigger_tokens')}: {status['compression_trigger_tokens']}",
                    f"• {self._t(language, 'status.compression_count')}: {status['compression_count']}",
                    f"• {self._t(language, 'status.compression_provider')}: {status['compression_provider']}",
                    f"• {self._t(language, 'status.last_compressed_at')}: {status['last_compressed_at'] or 'n/a'}",
                    f"• {self._t(language, 'status.summary_present')}: {status['summary_present']}",
                    f"• {self._t(language, 'status.language')}: {language}",
                ]
                if memory_key:
                    lines.append(f"• {self._t(language, 'status.memory_key')}: {memory_key}")
                    lines.append(f"• {self._t(language, 'status.memory_present')}: {memory_present}")
                return OutboundEnvelope(
                    channel=inbound.channel,
                    chat_id=inbound.chat_id,
                    kind="text",
                    text="\n".join(lines),
                    reply_to_id=inbound.message_id,
                    metadata={"provider": provider_id, "logical_session_id": logical_session_id},
                )
            if command == "/new":
                if inbound.metadata:
                    thread_id = inbound.metadata.get("thread_id")
                else:
                    thread_id = None
                self.orchestrator.sessions.reset_binding(
                    provider=provider_id,
                    channel=inbound.channel,
                    chat_id=inbound.chat_id,
                    thread_id=thread_id,
                )
                self.orchestrator.transcript.clear(logical_session_id)
                memory_cleared = False
                if self.orchestrator.memory:
                    today = date.today().isoformat()
                    key = self.orchestrator.memory.make_key(
                        channel=inbound.channel,
                        chat_id=inbound.chat_id,
                        date=today,
                        thread_id=thread_id,
                    )
                    memory_cleared = self.orchestrator.memory.delete(key)
                new_session_id = self.orchestrator.sessions.resolve_logical_session_id(
                    provider=provider_id,
                    channel=inbound.channel,
                    chat_id=inbound.chat_id,
                    thread_id=thread_id,
                )
                text = self._t(language, "new.started")
                if memory_cleared:
                    text += f" ({self._t(language, 'new.cleared')})"
                text += f": {new_session_id}"
                return OutboundEnvelope(
                    channel=inbound.channel,
                    chat_id=inbound.chat_id,
                    kind="text",
                    text=text,
                    reply_to_id=inbound.message_id,
                    metadata={"provider": provider_id, "logical_session_id": new_session_id},
                )
            if command == "/compact":
                send_typing = getattr(channel, "send_typing", None) if channel else None
                typing_task = None
                if callable(send_typing):
                    async def _typing_loop() -> None:
                        try:
                            interval = 4.0
                            cfg = getattr(channel, "config", None)
                            if cfg is not None:
                                interval = float(getattr(cfg, "typing_interval", interval))
                            while True:
                                await send_typing(inbound.chat_id)
                                await asyncio.sleep(interval)
                        except asyncio.CancelledError:
                            return
                    typing_task = asyncio.create_task(_typing_loop())
                summary = await self.orchestrator.compress_session(provider_id, logical_session_id, reason="manual")
                if typing_task:
                    typing_task.cancel()
                if not summary:
                    text = self._t(language, "compact.skipped")
                else:
                    count = self.orchestrator.sessions.get_or_create(logical_session_id, provider_id).metadata.get(
                        "compression_count",
                        0,
                    )
                    text = self._t(language, "compact.done").format(count=count, summary=summary)
                return OutboundEnvelope(
                    channel=inbound.channel,
                    chat_id=inbound.chat_id,
                    kind="text",
                    text=text,
                    reply_to_id=inbound.message_id,
                    metadata={"provider": provider_id, "logical_session_id": logical_session_id},
                )
            if command == "/language":
                code = args.split()[0] if args else ""
                if not code or code.lower() in {"list", "ls"}:
                    current_label = self._LANGUAGE_LABELS.get(language, language)
                    lines = [
                        self._t(language, "language.current").format(code=language, label=current_label),
                        self._t(language, "language.available"),
                    ]
                    for key, label in self._LANGUAGE_LABELS.items():
                        lines.append(f"{key}  {label}")
                    lines.append(self._t(language, "language.usage"))
                    return OutboundEnvelope(
                        channel=inbound.channel,
                        chat_id=inbound.chat_id,
                        kind="text",
                        text="\n".join(lines),
                        reply_to_id=inbound.message_id,
                        metadata={"provider": provider_id, "logical_session_id": logical_session_id},
                    )
                if code not in self._LANGUAGE_LABELS:
                    return OutboundEnvelope(
                        channel=inbound.channel,
                        chat_id=inbound.chat_id,
                        kind="text",
                        text=self._t(language, "language.invalid").format(code=code),
                        reply_to_id=inbound.message_id,
                        metadata={"provider": provider_id, "logical_session_id": logical_session_id},
                    )
                state = self.orchestrator.sessions.get_or_create(logical_session_id, provider_id)
                state.metadata["language"] = code
                if self.orchestrator.memory:
                    today = date.today().isoformat()
                    thread_id = None
                    if inbound.metadata:
                        thread_id = inbound.metadata.get("thread_id")
                    key = self.orchestrator.memory.make_key(
                        channel=inbound.channel,
                        chat_id=inbound.chat_id,
                        date=today,
                        thread_id=thread_id,
                    )
                    self.orchestrator.memory.update_metadata(key, {"language": code})
                return OutboundEnvelope(
                    channel=inbound.channel,
                    chat_id=inbound.chat_id,
                    kind="text",
                    text=self._t(code, "language.set").format(code=code, label=self._LANGUAGE_LABELS.get(code, code)),
                    reply_to_id=inbound.message_id,
                    metadata={"provider": provider_id, "logical_session_id": logical_session_id},
                )
            if command == "/cron":
                return self._handle_cron_command(
                    language=language,
                    provider_id=provider_id,
                    inbound=inbound,
                    args=args,
                )
            if command == "/cli":
                return self._handle_cli_command(
                    language=language,
                    provider_id=provider_id,
                    logical_session_id=logical_session_id,
                    inbound=inbound,
                    args=args,
                )
            if command == "/skills":
                return self._handle_skills_command(
                    language=language,
                    inbound=inbound,
                    args=args,
                )

        self.orchestrator.commands.refresh()
        definition = self.orchestrator.commands.get(command)
        if not definition:
            return OutboundEnvelope(
                channel=inbound.channel,
                chat_id=inbound.chat_id,
                kind="text",
                text=self._t(language, "language.unknown").format(command=command),
                reply_to_id=inbound.message_id,
                metadata={"provider": provider_id, "logical_session_id": logical_session_id},
            )

        if not args and not definition.allow_empty_args:
            usage = definition.usage or f"{definition.command} <args>"
            return OutboundEnvelope(
                channel=inbound.channel,
                chat_id=inbound.chat_id,
                kind="text",
                text=f"usage: {usage}",
                reply_to_id=inbound.message_id,
                metadata={"provider": provider_id, "logical_session_id": logical_session_id},
            )

        if definition.mode == "reply":
            response = definition.response or ""
            response = response.replace("{args}", args).replace("{command}", definition.command)
            return OutboundEnvelope(
                channel=inbound.channel,
                chat_id=inbound.chat_id,
                kind="text",
                text=response.strip() or "ok",
                reply_to_id=inbound.message_id,
                metadata={"provider": provider_id, "logical_session_id": logical_session_id},
            )

        prompt = definition.prompt or ""
        prompt = prompt.replace("{args}", args).replace("{command}", definition.command)
        rewritten = inbound.model_copy(update={"text": prompt.strip()})
        return rewritten

    def _render_help(self, language: str) -> str:
        lines = [
            self._t(language, "help.title"),
            self._t(language, "help.status"),
            self._t(language, "help.new"),
            self._t(language, "help.compact"),
            self._t(language, "help.help"),
            self._t(language, "help.language"),
            "• /cron - 定时任务管理",
            "• /cli - 切换当前渠道 CLI",
            "• /skills - 技能管理",
        ]
        self.orchestrator.commands.refresh()
        extensions = self.orchestrator.commands.list_commands()
        if extensions:
            lines.append("")
            lines.append(self._t(language, "help.skills"))
            for definition in extensions:
                desc = definition.description or "custom command"
                usage = definition.usage or definition.command
                lines.append(f"{usage} - {desc}")
        return "\n".join(lines)

    def _resolve_language(self, provider_id: str, logical_session_id: str, inbound: InboundEnvelope) -> str:
        state = self.orchestrator.sessions.get_or_create(logical_session_id, provider_id)
        language = state.metadata.get("language")
        if not language and self.orchestrator.memory:
            thread_id = None
            if inbound.metadata:
                thread_id = inbound.metadata.get("thread_id")
            record = self.orchestrator.memory.get_latest(
                channel=inbound.channel,
                chat_id=inbound.chat_id,
                thread_id=thread_id,
            )
            if record:
                metadata = record.get("metadata") or {}
                if isinstance(metadata, dict):
                    language = metadata.get("language")
                    if language:
                        state.metadata["language"] = language
        return language or self._DEFAULT_LANGUAGE

    def _parse_args(self, args: str) -> tuple[list[str], dict[str, str]]:
        tokens = shlex.split(args)
        positional: list[str] = []
        kv: dict[str, str] = {}
        for token in tokens:
            if "=" in token:
                key, value = token.split("=", 1)
                key = key.strip()
                value = value.strip()
                if key:
                    kv[key] = value
            else:
                positional.append(token)
        return positional, kv

    def _handle_cron_command(
        self,
        *,
        language: str,
        provider_id: str,
        inbound: InboundEnvelope,
        args: str,
    ) -> OutboundEnvelope:
        positional, kv = self._parse_args(args)
        action = (positional[0] if positional else "list").lower()
        if action in {"list", "ls"}:
            items = list(self._scheduled_tasks.values())
            if not items:
                text = "🗓️ 没有定时任务。"
            else:
                lines = [f"🗓️ 定时任务 ({len(items)})"]
                for task in items:
                    schedule = task.describe()
                    lines.append(
                        f"- id={task.task_id} name={task.name} channel={task.channel} chat_id={task.chat_id} "
                        f"provider={task.provider or self._routes.get(task.channel)} schedule={schedule} "
                        f"enabled={task.enabled}"
                    )
                text = "\n".join(lines)
            return OutboundEnvelope(
                channel=inbound.channel,
                chat_id=inbound.chat_id,
                kind="text",
                text=text,
                reply_to_id=inbound.message_id,
                metadata={"provider": provider_id},
            )
        if action == "add":
            name = kv.get("name") or (positional[1] if len(positional) > 1 else "")
            message = kv.get("message") or kv.get("msg")
            if not message:
                message = " ".join(positional[2:]) if len(positional) > 2 else ""
            channel = kv.get("channel") or inbound.channel
            chat_id = kv.get("chat_id") or inbound.chat_id
            provider = kv.get("provider") or None
            interval = kv.get("interval") or kv.get("interval_seconds") or kv.get("every")
            daily_at = kv.get("daily_at") or kv.get("at")
            if not name:
                name = f"task-{datetime.now().strftime('%H%M%S')}"
            if not message:
                return OutboundEnvelope(
                    channel=inbound.channel,
                    chat_id=inbound.chat_id,
                    kind="text",
                    text="用法：/cron add name=<name> message=\"...\" (interval=600 | daily_at=09:00) [channel=telegram chat_id=123]",
                    reply_to_id=inbound.message_id,
                    metadata={"provider": provider_id},
                )
            if not interval and not daily_at:
                return OutboundEnvelope(
                    channel=inbound.channel,
                    chat_id=inbound.chat_id,
                    kind="text",
                    text="请提供 interval=秒 或 daily_at=HH:MM。",
                    reply_to_id=inbound.message_id,
                    metadata={"provider": provider_id},
                )
            task_id = uuid.uuid4().hex[:8]
            interval_seconds = int(interval) if interval else None
            task = ScheduledTask(
                task_id=task_id,
                name=name,
                channel=channel,
                chat_id=chat_id,
                message=message,
                provider=provider,
                enabled=True,
                interval_seconds=interval_seconds,
                daily_at=daily_at,
            )
            self._scheduled_tasks[task_id] = task
            self._save_schedule_store()
            text = f"✅ 已添加任务 id={task_id} name={name} schedule={task.describe()}"
            return OutboundEnvelope(
                channel=inbound.channel,
                chat_id=inbound.chat_id,
                kind="text",
                text=text,
                reply_to_id=inbound.message_id,
                metadata={"provider": provider_id},
            )
        if action in {"delete", "del", "rm"}:
            task_id = kv.get("id") or (positional[1] if len(positional) > 1 else "")
            if not task_id:
                return OutboundEnvelope(
                    channel=inbound.channel,
                    chat_id=inbound.chat_id,
                    kind="text",
                    text="用法：/cron delete <id>",
                    reply_to_id=inbound.message_id,
                    metadata={"provider": provider_id},
                )
            removed = self._scheduled_tasks.pop(task_id, None)
            if removed:
                self._save_schedule_store()
                text = f"🗑️ 已删除任务 id={task_id} name={removed.name}"
            else:
                text = f"⚠️ 未找到任务 id={task_id}"
            return OutboundEnvelope(
                channel=inbound.channel,
                chat_id=inbound.chat_id,
                kind="text",
                text=text,
                reply_to_id=inbound.message_id,
                metadata={"provider": provider_id},
            )
        return OutboundEnvelope(
            channel=inbound.channel,
            chat_id=inbound.chat_id,
            kind="text",
            text="用法：/cron list | /cron add | /cron delete <id>",
            reply_to_id=inbound.message_id,
            metadata={"provider": provider_id},
        )

    def _handle_cli_command(
        self,
        *,
        language: str,
        provider_id: str,
        logical_session_id: str,
        inbound: InboundEnvelope,
        args: str,
    ) -> OutboundEnvelope:
        positional, kv = self._parse_args(args)
        action = (positional[0] if positional else "").lower()
        if action != "set":
            current = self._routes.get(inbound.channel) or provider_id
            return OutboundEnvelope(
                channel=inbound.channel,
                chat_id=inbound.chat_id,
                kind="text",
                text=f"当前 CLI: {current}\n用法：/cli set <provider> [channel=telegram]",
                reply_to_id=inbound.message_id,
                metadata={"provider": provider_id},
            )
        target_provider = kv.get("provider") or kv.get("id") or (positional[1] if len(positional) > 1 else "")
        target_channel = kv.get("channel") or inbound.channel
        if not target_provider:
            return OutboundEnvelope(
                channel=inbound.channel,
                chat_id=inbound.chat_id,
                kind="text",
                text="用法：/cli set <provider> [channel=telegram]",
                reply_to_id=inbound.message_id,
                metadata={"provider": provider_id},
            )
        if target_provider not in self.orchestrator.providers.list():
            return OutboundEnvelope(
                channel=inbound.channel,
                chat_id=inbound.chat_id,
                kind="text",
                text=f"未启用的 CLI: {target_provider}",
                reply_to_id=inbound.message_id,
                metadata={"provider": provider_id},
            )
        self._routes[target_channel] = target_provider
        try:
            self.orchestrator.sessions.reset_binding(
                provider=target_provider,
                channel=inbound.channel,
                chat_id=inbound.chat_id,
                thread_id=inbound.metadata.get("thread_id") if inbound.metadata else None,
            )
        except Exception:
            pass
        try:
            cfg = load_config(get_config_path())
            for chan in cfg.channels:
                if chan.name == target_channel:
                    chan.provider = target_provider
            dump_config(cfg)
        except Exception:
            pass
        return OutboundEnvelope(
            channel=inbound.channel,
            chat_id=inbound.chat_id,
            kind="text",
            text=f"✅ 已切换 {target_channel} -> {target_provider}",
            reply_to_id=inbound.message_id,
            metadata={"provider": target_provider, "logical_session_id": logical_session_id},
        )

    def _handle_skills_command(
        self,
        *,
        language: str,
        inbound: InboundEnvelope,
        args: str,
    ) -> OutboundEnvelope:
        positional, kv = self._parse_args(args)
        action = (positional[0] if positional else "list").lower()
        skills_dir = getattr(self.orchestrator.commands, "_skills_dir", None)
        if not skills_dir:
            return OutboundEnvelope(
                channel=inbound.channel,
                chat_id=inbound.chat_id,
                kind="text",
                text="⚠️ 未配置 skills 目录。",
                reply_to_id=inbound.message_id,
            )
        skills_dir = Path(skills_dir).expanduser()
        if action in {"list", "ls"}:
            items = []
            for entry in skills_dir.iterdir():
                if not entry.is_dir():
                    continue
                desc = None
                skill_path = entry / "SKILL.md"
                if skill_path.exists():
                    try:
                        front = CommandRegistry._parse_frontmatter(skill_path.read_text(encoding="utf-8"))
                        desc = front.get("description") if front else None
                    except Exception:
                        desc = None
                items.append((entry.name, desc))
            if not items:
                text = "🧰 当前没有已安装技能。"
            else:
                lines = [f"🧰 已安装技能 ({len(items)})"]
                for name, desc in sorted(items):
                    if desc:
                        lines.append(f"- {name}: {desc}")
                    else:
                        lines.append(f"- {name}")
                text = "\n".join(lines)
            return OutboundEnvelope(
                channel=inbound.channel,
                chat_id=inbound.chat_id,
                kind="text",
                text=text,
                reply_to_id=inbound.message_id,
            )
        if action == "find":
            keyword = kv.get("q") or (positional[1] if len(positional) > 1 else "")
            if not keyword:
                return OutboundEnvelope(
                    channel=inbound.channel,
                    chat_id=inbound.chat_id,
                    kind="text",
                    text="用法：/skills find <keyword>",
                    reply_to_id=inbound.message_id,
                )
            matches = []
            for entry in skills_dir.iterdir():
                if not entry.is_dir():
                    continue
                name = entry.name
                desc = ""
                skill_path = entry / "SKILL.md"
                if skill_path.exists():
                    try:
                        front = CommandRegistry._parse_frontmatter(skill_path.read_text(encoding="utf-8"))
                        desc = front.get("description") if front else ""
                    except Exception:
                        desc = ""
                hay = f"{name} {desc}".lower()
                if keyword.lower() in hay:
                    matches.append((name, desc))
            if not matches:
                text = f"未找到包含 “{keyword}” 的技能。"
            else:
                lines = [f"🔍 匹配技能 ({len(matches)})"]
                for name, desc in sorted(matches):
                    lines.append(f"- {name}: {desc}" if desc else f"- {name}")
                text = "\n".join(lines)
            return OutboundEnvelope(
                channel=inbound.channel,
                chat_id=inbound.chat_id,
                kind="text",
                text=text,
                reply_to_id=inbound.message_id,
            )
        if action in {"add", "update"}:
            name = kv.get("name") or (positional[1] if len(positional) > 1 else "")
            source = kv.get("path")
            if not name:
                return OutboundEnvelope(
                    channel=inbound.channel,
                    chat_id=inbound.chat_id,
                    kind="text",
                    text="用法：/skills add name=<name> [path=/path/to/skill] 或 /skills update name=<name> path=/path/to/skill",
                    reply_to_id=inbound.message_id,
                )
            target_dir = skills_dir / name
            target_dir.mkdir(parents=True, exist_ok=True)
            if source:
                src_path = Path(source).expanduser()
                if src_path.is_dir():
                    for item in src_path.iterdir():
                        if item.is_file():
                            shutil.copyfile(item, target_dir / item.name)
                elif src_path.is_file():
                    shutil.copyfile(src_path, target_dir / src_path.name)
            else:
                skill_file = target_dir / "SKILL.md"
                if not skill_file.exists():
                    skill_file.write_text(
                        "---\nname: {name}\ndescription: TODO\n---\n".format(name=name),
                        encoding="utf-8",
                    )
            self.orchestrator.commands.refresh()
            text = f"✅ 已{('更新' if action == 'update' else '添加')}技能：{name}"
            return OutboundEnvelope(
                channel=inbound.channel,
                chat_id=inbound.chat_id,
                kind="text",
                text=text,
                reply_to_id=inbound.message_id,
            )
        return OutboundEnvelope(
            channel=inbound.channel,
            chat_id=inbound.chat_id,
            kind="text",
            text="用法：/skills list | /skills find <keyword> | /skills add | /skills update",
            reply_to_id=inbound.message_id,
        )

    def _load_schedule_store(self) -> None:
        if not self._schedule_store_path:
            return
        path = Path(self._schedule_store_path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text("[]", encoding="utf-8")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = []
        if not isinstance(data, list):
            data = []
        for task in self._build_schedule_tasks(data):
            self._scheduled_tasks[task.task_id] = task

    def _save_schedule_store(self) -> None:
        if not self._schedule_store_path:
            return
        payload = []
        for task in self._scheduled_tasks.values():
            payload.append(task.to_dict())
        path = Path(self._schedule_store_path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _merge_schedule_sources(self) -> None:
        for task in self._build_schedule_tasks(self._schedules):
            self._scheduled_tasks.setdefault(task.task_id, task)
        if self._scheduled_tasks:
            self._save_schedule_store()

    def _t(self, language: str, key: str) -> str:
        table = self._I18N.get(language) or self._I18N.get(self._DEFAULT_LANGUAGE, {})
        return table.get(key, key)


@dataclass
class ScheduledTask:
    task_id: str
    name: str
    channel: str
    chat_id: str
    message: str
    provider: str | None = None
    enabled: bool = True
    interval_seconds: int | None = None
    daily_at: str | None = None
    last_run: datetime | None = None
    next_run: datetime | None = None

    def compute_next_run(self, now: datetime) -> datetime | None:
        if self.interval_seconds:
            return now + timedelta(seconds=int(self.interval_seconds))
        if self.daily_at:
            try:
                hour, minute = self.daily_at.split(":", 1)
                hour_i = int(hour)
                minute_i = int(minute)
            except Exception:
                return None
            local_now = now.astimezone()
            target = local_now.replace(hour=hour_i, minute=minute_i, second=0, microsecond=0)
            if target <= now:
                target = target + timedelta(days=1)
            return target
        return None

    def describe(self) -> str:
        if self.interval_seconds:
            return f"every {self.interval_seconds}s"
        if self.daily_at:
            return f"daily {self.daily_at}"
        return "unspecified"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.task_id,
            "name": self.name,
            "channel": self.channel,
            "chat_id": self.chat_id,
            "message": self.message,
            "provider": self.provider,
            "enabled": self.enabled,
            "interval_seconds": self.interval_seconds,
            "daily_at": self.daily_at,
        }
