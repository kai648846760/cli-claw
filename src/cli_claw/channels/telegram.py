from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cli_claw.channels.base import BaseChannel
from cli_claw.channels.http_client import post_json, post_multipart
from cli_claw.channels.policy import sender_allowed
from cli_claw.schemas.channel import ChannelAttachment, InboundEnvelope, OutboundEnvelope

logger = logging.getLogger(__name__)


def _redact_token(token: str | None) -> str:
    if not token:
        return "<missing>"
    if len(token) <= 8:
        return "***"
    return f"{token[:4]}***{token[-4:]}"


def _is_message_not_modified_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "message is not modified" in text


@dataclass
class TelegramConfig:
    bot_token: str | None = None
    webhook_secret: str | None = None
    base_url: str = "https://api.telegram.org"
    request_timeout: float = 10.0
    allow_from: list[str] = field(default_factory=list)
    stream_update_interval: float = 0.8
    polling: bool = False
    polling_interval: float = 1.0
    polling_timeout: int = 30
    typing_interval: float = 4.0
    allowed_updates: list[str] = field(default_factory=list)
    state_path: str | None = None


def _load_config() -> TelegramConfig:
    return TelegramConfig(
        bot_token=os.getenv("TELEGRAM_BOT_TOKEN"),
        webhook_secret=os.getenv("TELEGRAM_WEBHOOK_SECRET"),
        base_url=os.getenv("TELEGRAM_BASE_URL", "https://api.telegram.org"),
        state_path=os.getenv("TELEGRAM_STATE_PATH"),
    )


class TelegramChannel(BaseChannel):
    name = "telegram"
    _MAX_MESSAGE_LEN = 3800
    _MAX_IMAGE_BYTES = 10 * 1024 * 1024

    def __init__(self, config: TelegramConfig | None = None) -> None:
        super().__init__()
        self.config = config or _load_config()
        self._stream_state: dict[str, dict[str, Any]] = {}
        self._poll_task: asyncio.Task | None = None
        self._inbound_worker: asyncio.Task | None = None
        self._inbound_queue: asyncio.Queue[InboundEnvelope] = asyncio.Queue()
        self._poll_offset: int = 0
        self._acked_offset: int = 0
        self._pending_updates: set[int] = set()
        self._state_path = self._resolve_state_path()
        self._last_poll_error: float = 0.0
        self._load_state()

    async def start(self) -> None:
        self._running = True
        if self.config.polling:
            if self._inbound_worker is None:
                self._inbound_worker = asyncio.create_task(self._inbound_loop())
            self._poll_task = asyncio.create_task(self._poll_loop())
            logger.info(
                "Telegram polling enabled: token=%s offset=%s timeout=%s",
                _redact_token(self.config.bot_token),
                self._acked_offset,
                self.config.polling_timeout,
            )

    async def stop(self) -> None:
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None
        if self._inbound_worker:
            self._inbound_worker.cancel()
            try:
                await self._inbound_worker
            except asyncio.CancelledError:
                pass
            self._inbound_worker = None
        self._save_state()

    def is_allowed(self, envelope: OutboundEnvelope) -> bool:
        return envelope.kind in ("text", "error")

    def parse_inbound_event(self, payload: dict[str, Any]) -> InboundEnvelope | None:
        message = (
            payload.get("message")
            or payload.get("edited_message")
            or payload.get("channel_post")
            or payload.get("edited_channel_post")
        )
        if not isinstance(message, dict):
            message = payload.get("callback_query", {}).get("message")
        if not isinstance(message, dict):
            return None

        chat = message.get("chat") or {}
        sender = message.get("from") or (payload.get("callback_query") or {}).get("from") or {}

        text = message.get("text") or message.get("caption") or ""
        callback = payload.get("callback_query") or {}
        if not text and isinstance(callback, dict):
            text = str(callback.get("data") or "")
        attachments: list[ChannelAttachment] = []

        photo = message.get("photo")
        if isinstance(photo, list) and photo:
            chosen = None
            for item in sorted(photo, key=lambda x: x.get("file_size") or 0, reverse=True):
                size = item.get("file_size")
                if isinstance(size, int) and size <= self._MAX_IMAGE_BYTES:
                    chosen = item
                    break
            if chosen is None:
                chosen = photo[-1]
            attachments.append(
                ChannelAttachment(
                    kind="image",
                    name="photo",
                    metadata={
                        "file_id": chosen.get("file_id"),
                        "sizes": photo,
                        "file_size": chosen.get("file_size"),
                    },
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
        if message.get("animation"):
            animation = message["animation"]
            attachments.append(
                ChannelAttachment(
                    kind="video",
                    name=animation.get("file_name") or "animation",
                    metadata={"file_id": animation.get("file_id"), "mime_type": animation.get("mime_type")},
                )
            )
        if message.get("sticker"):
            sticker = message["sticker"]
            attachments.append(
                ChannelAttachment(
                    kind="image" if sticker.get("is_animated") is False else "video",
                    name=sticker.get("set_name") or "sticker",
                    metadata={"file_id": sticker.get("file_id"), "mime_type": sticker.get("mime_type")},
                )
            )

        metadata: dict[str, Any] = {
            "update_id": payload.get("update_id"),
            "chat_type": chat.get("type"),
            "message_type": "text" if message.get("text") else None,
            "thread_id": message.get("message_thread_id"),
            "callback_query_id": (payload.get("callback_query") or {}).get("id"),
            "callback_query_data": (payload.get("callback_query") or {}).get("data"),
            "is_command": bool(isinstance(message.get("text"), str) and message.get("text", "").startswith("/")),
        }

        if not sender_allowed(str(sender.get("id") or ""), self.config.allow_from):
            return None

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

        if envelope.stream_id:
            await self._send_stream(envelope)
            return

        if envelope.attachments:
            await self._send_attachments(envelope)
            return

        text = envelope.text or ""
        if not text.strip():
            return
        parts = self._split_text(text, self._MAX_MESSAGE_LEN)
        for idx, part in enumerate(parts):
            url = f"{self.config.base_url}/bot{self.config.bot_token}/sendMessage"
            payload: dict[str, Any] = {"chat_id": envelope.chat_id, "text": part}
            if envelope.reply_to_id and idx == 0:
                payload["reply_to_message_id"] = envelope.reply_to_id
            resp = await post_json(url, payload, headers={}, timeout=self.config.request_timeout)
            if idx == 0:
                result = resp.get("result") if isinstance(resp, dict) else None
                if isinstance(result, dict):
                    message_id = result.get("message_id")
                    if message_id:
                        envelope.message_id = str(message_id)
                        envelope.receipt_id = str(message_id)

    def supports_streaming(self) -> bool:
        return True

    async def _send_stream(self, envelope: OutboundEnvelope) -> None:
        stream_id = str(envelope.stream_id)
        state = self._stream_state.setdefault(
            stream_id,
            {
                "text": "",
                "message_id": None,
                "last_sent": 0.0,
                "last_sent_text": "",
                "chat_id": envelope.chat_id,
            },
        )
        if (envelope.metadata or {}).get("stream_full"):
            state["text"] = envelope.text or ""
        else:
            state["text"] = (state.get("text") or "") + (envelope.text or "")
        now = time.time()
        if not state["text"] or not str(state["text"]).strip():
            if envelope.stream_final:
                self._stream_state.pop(stream_id, None)
            return

        display_text = state["text"][: self._MAX_MESSAGE_LEN]
        if state["message_id"] is None:
            url = f"{self.config.base_url}/bot{self.config.bot_token}/sendMessage"
            payload: dict[str, Any] = {"chat_id": envelope.chat_id, "text": display_text}
            resp = await post_json(url, payload, headers={}, timeout=self.config.request_timeout)
            result = resp.get("result") if isinstance(resp, dict) else None
            if isinstance(result, dict):
                state["message_id"] = result.get("message_id")
                if state.get("message_id"):
                    envelope.message_id = str(state["message_id"])
                    envelope.receipt_id = str(state["message_id"])
            state["last_sent_text"] = display_text
            state["last_sent"] = now
        else:
            if now - float(state.get("last_sent") or 0.0) < float(self.config.stream_update_interval):
                if not envelope.stream_final:
                    return
            if display_text != state.get("last_sent_text"):
                url = f"{self.config.base_url}/bot{self.config.bot_token}/editMessageText"
                payload = {
                    "chat_id": envelope.chat_id,
                    "message_id": state["message_id"],
                    "text": display_text,
                }
                try:
                    await post_json(url, payload, headers={}, timeout=self.config.request_timeout)
                except Exception as exc:
                    if _is_message_not_modified_error(exc):
                        state["last_sent_text"] = display_text
                        state["last_sent"] = now
                    else:
                        if envelope.stream_final:
                            try:
                                send_url = f"{self.config.base_url}/bot{self.config.bot_token}/sendMessage"
                                send_payload: dict[str, Any] = {"chat_id": envelope.chat_id, "text": display_text}
                                if envelope.reply_to_id:
                                    send_payload["reply_to_message_id"] = envelope.reply_to_id
                                resp = await post_json(
                                    send_url,
                                    send_payload,
                                    headers={},
                                    timeout=self.config.request_timeout,
                                )
                                result = resp.get("result") if isinstance(resp, dict) else None
                                if isinstance(result, dict):
                                    state["message_id"] = result.get("message_id") or state.get("message_id")
                                state["last_sent_text"] = display_text
                            except Exception:
                                raise
                        else:
                            raise
                else:
                    state["last_sent_text"] = display_text
                state["last_sent"] = now

        if envelope.stream_final:
            if len(state["text"]) > self._MAX_MESSAGE_LEN:
                remaining = state["text"][self._MAX_MESSAGE_LEN :]
                for part in self._split_text(remaining, self._MAX_MESSAGE_LEN):
                    send_url = f"{self.config.base_url}/bot{self.config.bot_token}/sendMessage"
                    payload: dict[str, Any] = {"chat_id": envelope.chat_id, "text": part}
                    if envelope.reply_to_id:
                        payload["reply_to_message_id"] = envelope.reply_to_id
                        envelope.reply_to_id = None
                    await post_json(send_url, payload, headers={}, timeout=self.config.request_timeout)
            self._stream_state.pop(stream_id, None)

    @staticmethod
    def _split_text(text: str, limit: int) -> list[str]:
        if len(text) <= limit:
            return [text]
        parts: list[str] = []
        remaining = text
        while len(remaining) > limit:
            cut = remaining.rfind("\n", 0, limit)
            if cut < int(limit * 0.5):
                cut = limit
            parts.append(remaining[:cut].rstrip())
            remaining = remaining[cut:]
        if remaining.strip():
            parts.append(remaining)
        return [p for p in parts if p]

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
        resp = await post_multipart(url, fields, files, headers={}, timeout=self.config.request_timeout)
        result = resp.get("result") if isinstance(resp, dict) else None
        if isinstance(result, dict):
            message_id = result.get("message_id")
            if message_id:
                envelope.message_id = str(message_id)
                envelope.receipt_id = str(message_id)

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
        resp = await post_json(api_url, payload, headers={}, timeout=self.config.request_timeout)
        result = resp.get("result") if isinstance(resp, dict) else None
        if isinstance(result, dict):
            message_id = result.get("message_id")
            if message_id:
                envelope.message_id = str(message_id)
                envelope.receipt_id = str(message_id)

    async def send_typing(self, chat_id: str) -> None:
        if not self.config.bot_token:
            return
        url = f"{self.config.base_url}/bot{self.config.bot_token}/sendChatAction"
        payload = {"chat_id": chat_id, "action": "typing"}
        try:
            await post_json(url, payload, headers={}, timeout=self.config.request_timeout)
        except Exception:
            return

    async def _poll_loop(self) -> None:
        if not self.config.bot_token:
            raise RuntimeError("Telegram bot token missing; set TELEGRAM_BOT_TOKEN")

        url = f"{self.config.base_url}/bot{self.config.bot_token}/getUpdates"
        allowed = self.config.allowed_updates or [
            "message",
            "edited_message",
            "channel_post",
            "edited_channel_post",
            "callback_query",
        ]
        poll_timeout = max(1, int(self.config.polling_timeout))

        logger.info(
            "Telegram poll loop started: url=%s offset=%s allowed=%s",
            self.config.base_url,
            self._acked_offset,
            allowed,
        )
        while self._running:
            if self._inbound_handler is None:
                await asyncio.sleep(self.config.polling_interval)
                continue
            payload: dict[str, Any] = {"timeout": poll_timeout, "offset": self._acked_offset}
            if allowed:
                payload["allowed_updates"] = allowed
            try:
                resp = await post_json(url, payload, headers={}, timeout=self.config.request_timeout + poll_timeout)
                logger.debug(
                    "Telegram poll response: ok=%s result_len=%s offset=%s pending=%s",
                    isinstance(resp, dict) and resp.get("ok"),
                    len(resp.get("result") or []) if isinstance(resp, dict) else 0,
                    self._acked_offset,
                    len(self._pending_updates),
                )
                if not isinstance(resp, dict) or not resp.get("ok"):
                    await asyncio.sleep(self.config.polling_interval)
                    continue
                results = resp.get("result") or []
                if not isinstance(results, list):
                    await asyncio.sleep(self.config.polling_interval)
                    continue
                for update in results:
                    if not isinstance(update, dict):
                        continue
                    update_id = update.get("update_id")
                    if isinstance(update_id, int):
                        if update_id < self._acked_offset or update_id in self._pending_updates:
                            continue
                    inbound = self.parse_inbound_event(update)
                    if inbound:
                        if isinstance(update_id, int):
                            self._pending_updates.add(update_id)
                        await self._inbound_queue.put(inbound)
                    elif isinstance(update_id, int):
                        # Acknowledge dropped updates so they don't replay on restart.
                        self._acked_offset = max(self._acked_offset, update_id + 1)
                if results:
                    self._save_state()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                now = time.time()
                if now - self._last_poll_error > 30:
                    logger.warning("Telegram polling failed: %s", exc)
                    self._last_poll_error = now
                await asyncio.sleep(self.config.polling_interval)

    def _resolve_state_path(self) -> Path:
        if self.config.state_path:
            return Path(self.config.state_path).expanduser()
        return Path.home() / ".cli-claw" / "workspace" / "telegram_state.json"

    def _load_state(self) -> None:
        path = self._state_path
        try:
            if not path.exists():
                return
            data = json.loads(path.read_text(encoding="utf-8"))
            offset = data.get("poll_offset")
            if isinstance(offset, int):
                self._poll_offset = max(self._poll_offset, offset)
                self._acked_offset = max(self._acked_offset, offset)
        except Exception:
            return

    def _save_state(self) -> None:
        path = self._state_path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"poll_offset": self._acked_offset, "updated_at": time.time()}
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            tmp.replace(path)
        except Exception:
            return

    async def hydrate_inbound(self, inbound: InboundEnvelope) -> None:
        if not inbound.attachments or not self.config.bot_token:
            return
        for attachment in inbound.attachments:
            if attachment.path:
                continue
            file_id = None
            if isinstance(attachment.metadata, dict):
                file_id = attachment.metadata.get("file_id")
            if not file_id:
                continue
            path = await self._download_file(str(file_id))
            if path:
                attachment.path = path
                if isinstance(attachment.metadata, dict):
                    mime = mimetypes.guess_type(path)[0]
                    if mime:
                        attachment.metadata.setdefault("mime_type", mime)

    async def _handle_inbound(self, inbound: InboundEnvelope) -> None:
        try:
            await self.hydrate_inbound(inbound)
            await self.emit_inbound(inbound)
        except Exception as exc:
            now = time.time()
            if now - self._last_poll_error > 30:
                logger.warning("Telegram inbound handling failed: %s", exc)
                self._last_poll_error = now

    async def _inbound_loop(self) -> None:
        while self._running:
            try:
                inbound = await self._inbound_queue.get()
                try:
                    await self._handle_inbound(inbound)
                finally:
                    self._inbound_queue.task_done()
                    update_id = inbound.metadata.get("update_id") if isinstance(inbound.metadata, dict) else None
                    if isinstance(update_id, int):
                        self._pending_updates.discard(update_id)
                        self._acked_offset = max(self._acked_offset, update_id + 1)
                        self._save_state()
            except asyncio.CancelledError:
                break

    async def _download_file(self, file_id: str) -> str | None:
        url = f"{self.config.base_url}/bot{self.config.bot_token}/getFile"
        resp = await post_json(url, {"file_id": file_id}, headers={}, timeout=self.config.request_timeout)
        result = resp.get("result") if isinstance(resp, dict) else None
        if not isinstance(result, dict):
            return None
        file_path = result.get("file_path")
        if not file_path:
            return None
        ext = Path(str(file_path)).suffix
        media_dir = Path.home() / ".cli-claw" / "media"
        media_dir.mkdir(parents=True, exist_ok=True)
        target = media_dir / f"telegram_{file_id[:16]}{ext}"
        download_url = f"{self.config.base_url}/file/bot{self.config.bot_token}/{file_path}"
        try:
            with urllib.request.urlopen(download_url, timeout=self.config.request_timeout) as resp_file:
                target.write_bytes(resp_file.read())
        except (urllib.error.URLError, OSError):
            return None
        return str(target)
