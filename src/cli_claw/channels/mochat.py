from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import socketio

from cli_claw.channels.base import BaseChannel
from cli_claw.channels.simple_webhook_utils import parse_simple_inbound, send_simple_webhook
from cli_claw.channels.policy import sender_allowed
from cli_claw.schemas.channel import InboundEnvelope, OutboundEnvelope

logger = logging.getLogger(__name__)

MAX_SEEN_MESSAGE_IDS = 2000
CURSOR_SAVE_DEBOUNCE_S = 0.5


@dataclass
class MochatConfig:
    webhook_url: str | None = None
    verification_token: str | None = None
    request_timeout: float = 10.0
    claw_token: str | None = None
    agent_user_id: str | None = None
    base_url: str | None = None
    socket_url: str | None = None
    socket_path: str | None = None
    sessions: list[str] = field(default_factory=list)
    panels: list[str] = field(default_factory=list)
    reply_delay_mode: str = "non-mention"
    reply_delay_ms: int = 120000
    allow_from: list[str] = field(default_factory=list)


def _load_config() -> MochatConfig:
    sessions = [s for s in os.getenv("MOCHAT_SESSIONS", "").split(",") if s.strip()]
    panels = [s for s in os.getenv("MOCHAT_PANELS", "").split(",") if s.strip()]
    return MochatConfig(
        webhook_url=os.getenv("MOCHAT_WEBHOOK_URL"),
        verification_token=os.getenv("MOCHAT_VERIFICATION_TOKEN"),
        claw_token=os.getenv("MOCHAT_CLAW_TOKEN"),
        agent_user_id=os.getenv("MOCHAT_AGENT_USER_ID"),
        base_url=os.getenv("MOCHAT_BASE_URL"),
        socket_url=os.getenv("MOCHAT_SOCKET_URL"),
        socket_path=os.getenv("MOCHAT_SOCKET_PATH"),
        sessions=sessions,
        panels=panels,
        reply_delay_mode=os.getenv("MOCHAT_REPLY_DELAY_MODE", "non-mention"),
        reply_delay_ms=int(os.getenv("MOCHAT_REPLY_DELAY_MS", "120000")),
    )


@dataclass
class _BufferedEntry:
    raw_body: str
    author: str
    sender_name: str = ""
    sender_username: str = ""
    timestamp: int | None = None
    message_id: str = ""
    group_id: str = ""


@dataclass
class _DelayState:
    entries: list[_BufferedEntry] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    timer: asyncio.Task | None = None


@dataclass
class _MochatTarget:
    id: str
    is_panel: bool


def _safe_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _str_field(src: dict, *keys: str) -> str:
    for key in keys:
        value = src.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _normalize_content(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if content is None:
        return ""
    try:
        return json.dumps(content, ensure_ascii=False)
    except TypeError:
        return str(content)


def _resolve_target(raw: str) -> _MochatTarget:
    trimmed = (raw or "").strip()
    if not trimmed:
        return _MochatTarget(id="", is_panel=False)

    lowered = trimmed.lower()
    cleaned, forced_panel = trimmed, False

    for prefix in ("mochat:", "group:", "channel:", "panel:"):
        if lowered.startswith(prefix):
            cleaned = trimmed[len(prefix) :].strip()
            forced_panel = prefix in {"group:", "channel:", "panel:"}
            break

    if not cleaned:
        return _MochatTarget(id="", is_panel=False)

    return _MochatTarget(id=cleaned, is_panel=forced_panel or not cleaned.startswith("session_"))


def _extract_mention_ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    ids: list[str] = []
    for item in value:
        if isinstance(item, str):
            if item.strip():
                ids.append(item.strip())
        elif isinstance(item, dict):
            for key in ("id", "userId", "_id"):
                candidate = item.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    ids.append(candidate.strip())
                    break
    return ids


def _resolve_was_mentioned(payload: dict, agent_user_id: str) -> bool:
    meta = payload.get("meta")
    if isinstance(meta, dict):
        if meta.get("mentioned") is True or meta.get("wasMentioned") is True:
            return True
        for key in ("mentions", "mentionIds", "mentionedUserIds", "mentionedUsers"):
            if agent_user_id and agent_user_id in _extract_mention_ids(meta.get(key)):
                return True

    if not agent_user_id:
        return False

    content = payload.get("content")
    if not isinstance(content, str) or not content:
        return False

    return f"<@{agent_user_id}>" in content or f"@{agent_user_id}" in content


def _build_buffer(entries: list[_BufferedEntry], is_group: bool) -> str:
    if not entries:
        return ""
    if len(entries) == 1:
        return entries[0].raw_body

    lines: list[str] = []
    for entry in entries:
        if not entry.raw_body:
            continue
        if is_group:
            label = entry.sender_name.strip() or entry.sender_username.strip() or entry.author
            if label:
                lines.append(f"{label}: {entry.raw_body}")
                continue
        lines.append(entry.raw_body)

    return "\n".join(lines).strip()


def _parse_timestamp(value: Any) -> int | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


class MochatChannel(BaseChannel):
    name = "mochat"

    def __init__(self, config: MochatConfig | None = None) -> None:
        super().__init__()
        self.config = config or _load_config()
        self._http: httpx.AsyncClient | None = None
        self._socket: socketio.AsyncClient | None = None
        self._ws_connected = False
        self._ws_ready = False
        self._state_dir = Path.home() / ".cli-claw" / "mochat"
        self._cursor_path = self._state_dir / "session_cursors.json"
        self._session_cursor: dict[str, int] = {}
        self._cursor_save_task: asyncio.Task | None = None
        self._session_set: set[str] = set()
        self._panel_set: set[str] = set()
        self._auto_discover_sessions = False
        self._auto_discover_panels = False
        self._cold_sessions: set[str] = set()
        self._session_by_converse: dict[str, str] = {}
        self._seen_set: dict[str, set[str]] = {}
        self._seen_queue: dict[str, list[str]] = {}
        self._delay_states: dict[str, _DelayState] = {}
        self._fallback_mode = False
        self._session_fallback_tasks: dict[str, asyncio.Task] = {}
        self._panel_fallback_tasks: dict[str, asyncio.Task] = {}
        self._refresh_task: asyncio.Task | None = None
        self._target_locks: dict[str, asyncio.Lock] = {}

    async def start(self) -> None:
        if not self.config.claw_token:
            logger.error("[mochat] claw_token not configured")
            return

        self._running = True
        self._http = httpx.AsyncClient(timeout=30.0)
        self._state_dir.mkdir(parents=True, exist_ok=True)
        await self._load_session_cursors()
        self._seed_targets_from_config()
        await self._refresh_targets(subscribe_new=False)

        if not await self._start_socket_client():
            await self._ensure_fallback_workers()

        self._refresh_task = asyncio.create_task(self._refresh_loop())

        while self._running:
            await asyncio.sleep(1)

    async def stop(self) -> None:
        self._running = False
        if self._refresh_task:
            self._refresh_task.cancel()
            self._refresh_task = None
        await self._stop_fallback_workers()
        await self._cancel_delay_timers()
        if self._socket:
            try:
                await self._socket.disconnect()
            except Exception:
                pass
            self._socket = None
        if self._cursor_save_task:
            self._cursor_save_task.cancel()
            self._cursor_save_task = None
        await self._save_session_cursors()
        if self._http:
            await self._http.aclose()
            self._http = None
        self._ws_connected = False
        self._ws_ready = False

    def is_allowed(self, envelope: OutboundEnvelope) -> bool:
        return envelope.kind in ("text", "error")

    def parse_inbound_event(self, payload: dict[str, Any]) -> InboundEnvelope | None:
        return parse_simple_inbound(self.name, payload, allow_from=self.config.allow_from)

    async def send(self, envelope: OutboundEnvelope) -> None:
        if self.config.webhook_url:
            await send_simple_webhook(
                webhook_url=self.config.webhook_url,
                envelope=envelope,
                timeout=self.config.request_timeout,
                missing_message="Mochat webhook missing; set MOCHAT_WEBHOOK_URL",
            )
            return

        if not self.config.claw_token:
            raise RuntimeError("[mochat] claw_token missing")

        content = self._build_content(envelope)
        if not content:
            return

        target = _resolve_target(envelope.chat_id)
        if not target.id:
            raise RuntimeError("[mochat] outbound target empty")

        is_panel = (target.is_panel or target.id in self._panel_set) and not target.id.startswith(
            "session_"
        )

        if is_panel:
            await self._api_send_panel(
                target.id,
                content,
                envelope.reply_to_id,
                self._read_group_id(envelope.metadata),
            )
        else:
            await self._api_send_session(target.id, content, envelope.reply_to_id)

    async def _start_socket_client(self) -> bool:
        client = socketio.AsyncClient(
            reconnection=True,
            reconnection_attempts=5,
            reconnection_delay=1.0,
            reconnection_delay_max=5.0,
            logger=False,
            engineio_logger=False,
        )

        @client.event
        async def connect() -> None:
            self._ws_connected = True
            self._ws_ready = False
            subscribed = await self._subscribe_all()
            self._ws_ready = subscribed
            if subscribed:
                await self._stop_fallback_workers()
            else:
                await self._ensure_fallback_workers()

        @client.event
        async def disconnect() -> None:
            if not self._running:
                return
            self._ws_connected = False
            self._ws_ready = False
            await self._ensure_fallback_workers()

        @client.on("claw.session.events")
        async def on_session_events(payload: dict) -> None:
            await self._handle_watch_payload(payload, "session")

        @client.on("claw.panel.events")
        async def on_panel_events(payload: dict) -> None:
            await self._handle_watch_payload(payload, "panel")

        socket_url = (self.config.socket_url or self.config.base_url or "").strip().rstrip("/")
        socket_path = (self.config.socket_path or "/socket.io").strip().lstrip("/")
        if not socket_url:
            logger.error("[mochat] socket_url/base_url not configured")
            return False

        try:
            self._socket = client
            await client.connect(
                socket_url,
                transports=["websocket"],
                socketio_path=socket_path,
                auth={"token": self.config.claw_token},
                wait_timeout=10.0,
            )
            return True
        except Exception as exc:
            logger.error("[mochat] websocket connect failed: %s", exc)
            try:
                await client.disconnect()
            except Exception:
                pass
            self._socket = None
            return False

    def _seed_targets_from_config(self) -> None:
        sessions, self._auto_discover_sessions = self._normalize_id_list(self.config.sessions)
        panels, self._auto_discover_panels = self._normalize_id_list(self.config.panels)
        self._session_set.update(sessions)
        self._panel_set.update(panels)
        for sid in sessions:
            if sid not in self._session_cursor:
                self._cold_sessions.add(sid)

    @staticmethod
    def _normalize_id_list(values: list[str]) -> tuple[list[str], bool]:
        cleaned = [str(v).strip() for v in values if str(v).strip()]
        return sorted({v for v in cleaned if v != "*"}), "*" in cleaned

    async def _refresh_loop(self) -> None:
        interval_s = 60.0
        while self._running:
            await asyncio.sleep(interval_s)
            try:
                await self._refresh_targets(subscribe_new=self._ws_ready)
            except Exception as exc:
                logger.warning("[mochat] refresh failed: %s", exc)

            if self._fallback_mode:
                await self._ensure_fallback_workers()

    async def _refresh_targets(self, subscribe_new: bool) -> None:
        if self._auto_discover_sessions:
            await self._refresh_sessions_directory(subscribe_new)
        if self._auto_discover_panels:
            await self._refresh_panels(subscribe_new)

    async def _refresh_sessions_directory(self, subscribe_new: bool) -> None:
        response = await self._post_json("/api/claw/sessions/list", {})
        sessions = response.get("sessions")
        if not isinstance(sessions, list):
            return

        new_ids: list[str] = []
        for session in sessions:
            if not isinstance(session, dict):
                continue
            sid = _str_field(session, "sessionId")
            if not sid:
                continue
            if sid not in self._session_set:
                self._session_set.add(sid)
                new_ids.append(sid)
                if sid not in self._session_cursor:
                    self._cold_sessions.add(sid)
            cid = _str_field(session, "converseId")
            if cid:
                self._session_by_converse[cid] = sid

        if new_ids:
            if self._ws_ready and subscribe_new:
                await self._subscribe_sessions(new_ids)
            if self._fallback_mode:
                await self._ensure_fallback_workers()

    async def _refresh_panels(self, subscribe_new: bool) -> None:
        response = await self._post_json("/api/claw/groups/get", {})
        raw_panels = response.get("panels")
        if not isinstance(raw_panels, list):
            return

        new_ids: list[str] = []
        for panel in raw_panels:
            if not isinstance(panel, dict):
                continue
            pid = _str_field(panel, "id", "_id")
            if pid and pid not in self._panel_set:
                self._panel_set.add(pid)
                new_ids.append(pid)

        if new_ids:
            if self._ws_ready and subscribe_new:
                await self._subscribe_panels(new_ids)
            if self._fallback_mode:
                await self._ensure_fallback_workers()

    async def _ensure_fallback_workers(self) -> None:
        if not self._running:
            return
        self._fallback_mode = True

        for sid in sorted(self._session_set):
            task = self._session_fallback_tasks.get(sid)
            if not task or task.done():
                self._session_fallback_tasks[sid] = asyncio.create_task(
                    self._session_watch_worker(sid)
                )

        for pid in sorted(self._panel_set):
            task = self._panel_fallback_tasks.get(pid)
            if not task or task.done():
                self._panel_fallback_tasks[pid] = asyncio.create_task(
                    self._panel_poll_worker(pid)
                )

    async def _stop_fallback_workers(self) -> None:
        self._fallback_mode = False
        tasks = [
            *self._session_fallback_tasks.values(),
            *self._panel_fallback_tasks.values(),
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._session_fallback_tasks.clear()
        self._panel_fallback_tasks.clear()

    async def _session_watch_worker(self, session_id: str) -> None:
        while self._running and self._fallback_mode:
            try:
                payload = await self._post_json(
                    "/api/claw/sessions/watch",
                    {
                        "sessionId": session_id,
                        "cursor": self._session_cursor.get(session_id, 0),
                        "timeoutMs": 30000,
                        "limit": 50,
                    },
                )
                await self._handle_watch_payload(payload, "session")
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("[mochat] watch fallback error (%s): %s", session_id, exc)
                await asyncio.sleep(5)

    async def _panel_poll_worker(self, panel_id: str) -> None:
        sleep_s = 60.0
        while self._running and self._fallback_mode:
            try:
                resp = await self._post_json(
                    "/api/claw/groups/panels/messages",
                    {"panelId": panel_id, "limit": 50},
                )
                msgs = resp.get("messages")
                if isinstance(msgs, list):
                    for message in reversed(msgs):
                        if not isinstance(message, dict):
                            continue
                        event = {
                            "type": "message.add",
                            "payload": {
                                "messageId": str(message.get("messageId") or ""),
                                "author": str(message.get("author") or ""),
                                "content": message.get("content"),
                                "meta": message.get("meta"),
                                "groupId": str(resp.get("groupId") or ""),
                            },
                        }
                        await self._process_inbound_event(panel_id, event, "panel")
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("[mochat] panel polling error (%s): %s", panel_id, exc)
            await asyncio.sleep(sleep_s)

    async def _handle_watch_payload(self, payload: dict, target_kind: str) -> None:
        if not isinstance(payload, dict):
            return

        target_id = _str_field(payload, "sessionId")
        if not target_id:
            return

        lock = self._target_locks.setdefault(f"{target_kind}:{target_id}", asyncio.Lock())
        async with lock:
            pc = payload.get("cursor")
            if target_kind == "session" and isinstance(pc, int) and pc >= 0:
                self._mark_session_cursor(target_id, pc)

            raw_events = payload.get("events")
            if not isinstance(raw_events, list):
                return

            if target_kind == "session" and target_id in self._cold_sessions:
                self._cold_sessions.discard(target_id)
                return

            for event in raw_events:
                if not isinstance(event, dict):
                    continue
                if event.get("type") == "message.add":
                    await self._process_inbound_event(target_id, event, target_kind)

    async def _process_inbound_event(self, target_id: str, event: dict, target_kind: str) -> None:
        payload = event.get("payload")
        if not isinstance(payload, dict):
            return

        author = _str_field(payload, "author")
        if not author or (self.config.agent_user_id and author == self.config.agent_user_id):
            return

        if not sender_allowed(author, self.config.allow_from):
            return

        message_id = _str_field(payload, "messageId")
        seen_key = f"{target_kind}:{target_id}"
        if message_id and self._remember_message_id(seen_key, message_id):
            return

        raw_body = _normalize_content(payload.get("content")) or "[empty message]"
        ai = _safe_dict(payload.get("authorInfo"))
        sender_name = _str_field(ai, "nickname", "email")
        sender_username = _str_field(ai, "agentId")

        group_id = _str_field(payload, "groupId")
        is_group = bool(group_id)
        was_mentioned = _resolve_was_mentioned(payload, self.config.agent_user_id or "")

        use_delay = target_kind == "panel" and self.config.reply_delay_mode == "non-mention"
        entry = _BufferedEntry(
            raw_body=raw_body,
            author=author,
            sender_name=sender_name,
            sender_username=sender_username,
            timestamp=_parse_timestamp(event.get("timestamp")),
            message_id=message_id,
            group_id=group_id,
        )

        if use_delay:
            delay_key = seen_key
            if was_mentioned:
                await self._flush_delayed_entries(delay_key, target_id, target_kind, "mention", entry)
            else:
                await self._enqueue_delayed_entry(delay_key, target_id, target_kind, entry)
            return

        await self._dispatch_entries(target_id, target_kind, [entry], was_mentioned)

    def _remember_message_id(self, key: str, message_id: str) -> bool:
        seen_set = self._seen_set.setdefault(key, set())
        seen_queue = self._seen_queue.setdefault(key, [])
        if message_id in seen_set:
            return True
        seen_set.add(message_id)
        seen_queue.append(message_id)
        while len(seen_queue) > MAX_SEEN_MESSAGE_IDS:
            removed = seen_queue.pop(0)
            seen_set.discard(removed)
        return False

    async def _enqueue_delayed_entry(
        self, key: str, target_id: str, target_kind: str, entry: _BufferedEntry
    ) -> None:
        state = self._delay_states.setdefault(key, _DelayState())
        async with state.lock:
            state.entries.append(entry)
            if state.timer:
                state.timer.cancel()
            state.timer = asyncio.create_task(self._delay_flush_after(key, target_id, target_kind))

    async def _delay_flush_after(self, key: str, target_id: str, target_kind: str) -> None:
        await asyncio.sleep(max(0, self.config.reply_delay_ms) / 1000.0)
        await self._flush_delayed_entries(key, target_id, target_kind, "timer", None)

    async def _flush_delayed_entries(
        self,
        key: str,
        target_id: str,
        target_kind: str,
        reason: str,
        entry: _BufferedEntry | None,
    ) -> None:
        state = self._delay_states.setdefault(key, _DelayState())
        async with state.lock:
            if entry:
                state.entries.append(entry)
            current = asyncio.current_task()
            if state.timer and state.timer is not current:
                state.timer.cancel()
            state.timer = None
            entries = state.entries[:]
            state.entries.clear()

        if entries:
            await self._dispatch_entries(target_id, target_kind, entries, reason == "mention")

    async def _dispatch_entries(
        self,
        target_id: str,
        target_kind: str,
        entries: list[_BufferedEntry],
        was_mentioned: bool,
    ) -> None:
        if not entries:
            return

        last = entries[-1]
        is_group = bool(last.group_id)
        body = _build_buffer(entries, is_group) or "[empty message]"

        await self.emit_inbound(
            InboundEnvelope(
                channel=self.name,
                chat_id=target_id,
                sender_id=last.author,
                message_id=last.message_id or None,
                reply_to_id=None,
                text=body,
                metadata={
                    "timestamp": last.timestamp,
                    "is_group": is_group,
                    "group_id": last.group_id,
                    "was_mentioned": was_mentioned,
                    "target_kind": target_kind,
                },
            )
        )

    async def _cancel_delay_timers(self) -> None:
        for state in self._delay_states.values():
            if state.timer and not state.timer.done():
                state.timer.cancel()

    async def _api_send_session(self, session_id: str, content: str, reply_to: str | None) -> None:
        payload = {"sessionId": session_id, "content": content}
        if reply_to:
            payload["replyTo"] = reply_to
        await self._post_json("/api/claw/sessions/send", payload)

    async def _api_send_panel(
        self, panel_id: str, content: str, reply_to: str | None, group_id: str | None
    ) -> None:
        payload = {"panelId": panel_id, "content": content}
        if reply_to:
            payload["replyTo"] = reply_to
        if group_id:
            payload["groupId"] = group_id
        await self._post_json("/api/claw/groups/panels/send", payload)

    async def _post_json(self, path: str, data: dict) -> dict:
        if not self._http:
            return {}
        base = (self.config.base_url or "").rstrip("/")
        if not base:
            return {}
        url = f"{base}{path}"
        headers = {"Authorization": f"Bearer {self.config.claw_token}"}
        resp = await self._http.post(url, json=data, headers=headers)
        resp.raise_for_status()
        return resp.json()

    def _read_group_id(self, metadata: dict | None) -> str | None:
        if not metadata:
            return None
        return metadata.get("group_id")

    def _build_content(self, envelope: OutboundEnvelope) -> str:
        parts = [envelope.text.strip()] if envelope.text and envelope.text.strip() else []
        for attachment in envelope.attachments:
            label = attachment.name or attachment.path or attachment.url or "attachment"
            target = attachment.url or attachment.path or ""
            if target:
                parts.append(f"[{attachment.kind}] {label}: {target}")
            else:
                parts.append(f"[{attachment.kind}] {label}")
        return "\n".join([p for p in parts if p]).strip()

    def _mark_session_cursor(self, session_id: str, cursor: int) -> None:
        self._session_cursor[session_id] = cursor
        self._schedule_cursor_save()

    def _schedule_cursor_save(self) -> None:
        if self._cursor_save_task and not self._cursor_save_task.done():
            return
        self._cursor_save_task = asyncio.create_task(self._delayed_cursor_save())

    async def _delayed_cursor_save(self) -> None:
        await asyncio.sleep(CURSOR_SAVE_DEBOUNCE_S)
        await self._save_session_cursors()

    async def _load_session_cursors(self) -> None:
        if not self._cursor_path.exists():
            return
        data = json.loads(self._cursor_path.read_text())
        if isinstance(data, dict):
            self._session_cursor = {k: int(v) for k, v in data.items()}

    async def _save_session_cursors(self) -> None:
        self._cursor_path.write_text(json.dumps(self._session_cursor, ensure_ascii=False, indent=2))
