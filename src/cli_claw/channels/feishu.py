from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cli_claw.channels.base import BaseChannel
from cli_claw.channels.http_client import post_multipart
from cli_claw.channels.policy import sender_allowed
from cli_claw.schemas.channel import ChannelAttachment, InboundEnvelope, OutboundEnvelope

logger = logging.getLogger(__name__)


@dataclass
class FeishuConfig:
    app_id: str | None = None
    app_secret: str | None = None
    bot_webhook_url: str | None = None
    verification_token: str | None = None
    encrypt_key: str | None = None
    base_url: str = "https://open.feishu.cn"
    request_timeout: float = 10.0
    allow_from: list[str] = field(default_factory=list)


def _load_config() -> FeishuConfig:
    return FeishuConfig(
        app_id=os.getenv("FEISHU_APP_ID"),
        app_secret=os.getenv("FEISHU_APP_SECRET"),
        bot_webhook_url=os.getenv("FEISHU_BOT_WEBHOOK_URL"),
        verification_token=os.getenv("FEISHU_VERIFICATION_TOKEN"),
        encrypt_key=os.getenv("FEISHU_ENCRYPT_KEY"),
        base_url=os.getenv("FEISHU_BASE_URL", "https://open.feishu.cn"),
    )


def _request_json(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: float) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8") if exc.fp else ""
        raise RuntimeError(f"Feishu API error {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Feishu API request failed: {exc}") from exc

    if not body:
        return {}
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {"raw": body}


class FeishuChannel(BaseChannel):
    name = "feishu"

    def __init__(self, config: FeishuConfig | None = None) -> None:
        super().__init__()
        self.config = config or _load_config()
        self._tenant_token: str | None = None
        self._tenant_token_expire_at: float | None = None

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    def is_allowed(self, envelope: OutboundEnvelope) -> bool:
        return envelope.kind in ("text", "error")

    def parse_inbound_event(self, payload: dict[str, Any]) -> InboundEnvelope | None:
        if "challenge" in payload:
            return None

        event = payload.get("event") or {}
        message = event.get("message") or {}
        event_type = (payload.get("header") or {}).get("event_type")
        if not message:
            if event_type and str(event_type).startswith("im.message.reaction"):
                reaction = event.get("reaction") or {}
                operator = event.get("operator") or {}
                sender_id_raw = operator.get("sender_id") or {}
                sender_id = (
                    sender_id_raw.get("open_id")
                    or sender_id_raw.get("user_id")
                    or sender_id_raw.get("union_id")
                )
                if not sender_allowed(sender_id, self.config.allow_from):
                    return None
                message_id = reaction.get("message_id") or event.get("message_id")
                chat_id = event.get("chat_id") or reaction.get("chat_id") or ""
                emoji = reaction.get("emoji_type") or reaction.get("reaction_type") or reaction.get("emoji") or ""
                return InboundEnvelope(
                    channel=self.name,
                    chat_id=chat_id,
                    sender_id=sender_id,
                    message_id=message_id,
                    reply_to_id=message_id,
                    text=f"reaction: {emoji}" if emoji else "reaction",
                    metadata={
                        "event_type": event_type,
                        "event_kind": "reaction",
                        "reaction": reaction,
                    },
                )
            return None

        content_raw = message.get("content") or "{}"
        text = _extract_text(content_raw)
        attachments = _extract_attachments(message, self.config, text)

        sender = event.get("sender") or {}
        sender_id_raw = sender.get("sender_id") or {}
        sender_id = (
            sender_id_raw.get("open_id")
            or sender_id_raw.get("user_id")
            or sender_id_raw.get("union_id")
        )

        message_id = message.get("message_id") or message.get("messageId")
        parent_id = message.get("parent_id")
        root_id = message.get("root_id")

        metadata = {
            "event_type": event_type,
            "message_type": message.get("message_type") or message.get("msg_type"),
            "chat_type": message.get("chat_type"),
            "tenant_key": (payload.get("header") or {}).get("tenant_key"),
            "event_kind": "message",
        }
        if root_id:
            metadata["thread_id"] = root_id

        if not sender_allowed(sender_id, self.config.allow_from):
            return None

        return InboundEnvelope(
            channel=self.name,
            chat_id=message.get("chat_id") or event.get("chat_id") or "",
            sender_id=sender_id,
            message_id=message_id,
            reply_to_id=parent_id or root_id,
            text=text,
            attachments=attachments,
            metadata={k: v for k, v in metadata.items() if v is not None},
        )

    async def send(self, envelope: OutboundEnvelope) -> None:
        if not self.is_allowed(envelope):
            raise ValueError("FeishuChannel only supports outbound text messages")

        if envelope.attachments:
            await self._send_attachments(envelope)
            return

        if self.config.bot_webhook_url:
            payload = {"msg_type": "text", "content": {"text": envelope.text}}
            resp = await self._post_json(self.config.bot_webhook_url, payload, headers={})
            _apply_receipt(envelope, resp)
            return

        token = await self._get_tenant_token()
        headers = {"Authorization": f"Bearer {token}"}
        content = json.dumps({"text": envelope.text}, ensure_ascii=False)

        if envelope.reply_to_id:
            url = f"{self.config.base_url}/open-apis/im/v1/messages/{envelope.reply_to_id}/reply"
            payload = {"msg_type": "text", "content": content}
        else:
            url = f"{self.config.base_url}/open-apis/im/v1/messages?receive_id_type=chat_id"
            payload = {"receive_id": envelope.chat_id, "msg_type": "text", "content": content}

        resp = await self._post_json(url, payload, headers=headers)
        _apply_receipt(envelope, resp)

    async def _send_attachments(self, envelope: OutboundEnvelope) -> None:
        token = await self._get_tenant_token()
        headers = {"Authorization": f"Bearer {token}"}

        for attachment in envelope.attachments:
            if attachment.path:
                file_key = await self._upload_media(attachment)
                msg_type, content = _build_attachment_message(attachment, file_key)
            elif attachment.url:
                msg_type, content = _build_attachment_message(attachment, attachment.url)
            else:
                raise ValueError("FeishuChannel attachment missing path/url")

            if envelope.reply_to_id:
                url = f"{self.config.base_url}/open-apis/im/v1/messages/{envelope.reply_to_id}/reply"
                payload = {"msg_type": msg_type, "content": json.dumps(content, ensure_ascii=False)}
            else:
                url = f"{self.config.base_url}/open-apis/im/v1/messages?receive_id_type=chat_id"
                payload = {
                    "receive_id": envelope.chat_id,
                    "msg_type": msg_type,
                    "content": json.dumps(content, ensure_ascii=False),
                }

            resp = await self._post_json(url, payload, headers=headers)
            _apply_receipt(envelope, resp)

    async def _upload_media(self, attachment: ChannelAttachment) -> str:
        if not attachment.path:
            raise ValueError("Feishu upload requires path")
        token = await self._get_tenant_token()
        headers = {"Authorization": f"Bearer {token}"}
        url = f"{self.config.base_url}/open-apis/im/v1/files"
        fields = {"file_type": _attachment_file_type(attachment)}
        files = {"file": {"path": attachment.path, "filename": attachment.name or attachment.path}}
        resp = await post_multipart(url, fields, files, headers=headers, timeout=self.config.request_timeout)
        file_key = (resp.get("data") or {}).get("file_key")
        if not file_key:
            raise RuntimeError(f"Feishu upload failed: {resp}")
        return str(file_key)

    async def _post_json(self, url: str, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        return await asyncio.to_thread(
            _request_json,
            url,
            payload,
            headers,
            self.config.request_timeout,
        )

    async def _get_tenant_token(self) -> str:
        if self.config.app_id is None or self.config.app_secret is None:
            raise RuntimeError("Feishu app credentials missing; set FEISHU_APP_ID/FEISHU_APP_SECRET")

        now = time.time()
        if self._tenant_token and self._tenant_token_expire_at:
            if now < self._tenant_token_expire_at - 60:
                return self._tenant_token

        url = f"{self.config.base_url}/open-apis/auth/v3/tenant_access_token/internal"
        payload = {"app_id": self.config.app_id, "app_secret": self.config.app_secret}
        resp = await self._post_json(url, payload, headers={})
        if resp.get("code") not in (0, None):
            raise RuntimeError(f"Feishu auth failed: {resp}")

        token = resp.get("tenant_access_token")
        if not token:
            raise RuntimeError(f"Feishu auth missing token: {resp}")
        expire = resp.get("expire", 7200)

        self._tenant_token = token
        self._tenant_token_expire_at = now + float(expire)
        return token


def _extract_text(content_raw: str | dict[str, Any]) -> str:
    if isinstance(content_raw, dict):
        return str(content_raw.get("text") or "")

    if not content_raw:
        return ""

    try:
        parsed = json.loads(content_raw)
    except json.JSONDecodeError:
        return str(content_raw)

    if isinstance(parsed, dict):
        if "text" in parsed:
            return str(parsed.get("text") or "")
        if parsed.get("title") or parsed.get("content"):
            return _extract_post_text(parsed)
        if "card" in parsed:
            return json.dumps(parsed, ensure_ascii=False)
    return str(parsed)


def _extract_post_text(parsed: dict[str, Any]) -> str:
    title = parsed.get("title") or ""
    parts: list[str] = []
    if title:
        parts.append(str(title))

    content = parsed.get("content") or []
    for row in content:
        if not isinstance(row, list):
            continue
        for item in row:
            if not isinstance(item, dict):
                continue
            tag = item.get("tag")
            if tag == "text":
                parts.append(str(item.get("text") or ""))
            elif tag in {"a", "link"}:
                parts.append(str(item.get("text") or item.get("href") or ""))
    return "\n".join([p for p in parts if p]).strip()


def _extract_attachments(
    message: dict[str, Any],
    config: FeishuConfig,
    fallback_text: str,
) -> list[ChannelAttachment]:
    attachments: list[ChannelAttachment] = []
    message_type = message.get("message_type") or message.get("msg_type")

    if message_type in {"image", "file", "audio", "media", "sticker"}:
        file_key = message.get("file_key") or message.get("fileKey")
        if file_key:
            attachments.append(
                ChannelAttachment(
                    kind=_map_message_type(message_type),
                    name=message.get("file_name"),
                    path=_download_feishu_file(file_key, config) or None,
                    metadata={"file_key": file_key},
                )
            )
        return attachments

    if message_type == "post":
        content_raw = message.get("content") or "{}"
        try:
            parsed = content_raw if isinstance(content_raw, dict) else json.loads(content_raw)
        except json.JSONDecodeError:
            parsed = {}
        for file_key, name, kind in _extract_post_files(parsed):
            attachments.append(
                ChannelAttachment(
                    kind=kind,
                    name=name,
                    path=_download_feishu_file(file_key, config) or None,
                    metadata={"file_key": file_key},
                )
            )

    if message_type == "text" and fallback_text:
        return attachments

    if message_type in {"share_chat", "share_user"}:
        attachments.append(
            ChannelAttachment(
                kind="link",
                name=message_type,
                metadata={"shared": message},
            )
        )

    return attachments


def _extract_post_files(parsed: dict[str, Any]) -> list[tuple[str, str | None, str]]:
    content = parsed.get("content") or []
    results: list[tuple[str, str | None, str]] = []
    for row in content:
        if not isinstance(row, list):
            continue
        for item in row:
            if not isinstance(item, dict):
                continue
            tag = item.get("tag")
            if tag == "img":
                file_key = item.get("image_key") or item.get("imageKey")
                if file_key:
                    results.append((str(file_key), None, "image"))
            if tag in {"file", "media"}:
                file_key = item.get("file_key") or item.get("fileKey")
                if file_key:
                    results.append((str(file_key), item.get("file_name"), "file"))
    return results


def _map_message_type(message_type: str | None) -> str:
    if message_type == "image":
        return "image"
    if message_type in {"audio", "media"}:
        return "audio" if message_type == "audio" else "video"
    if message_type == "sticker":
        return "image"
    return "file"


def _attachment_file_type(attachment: ChannelAttachment) -> str:
    if attachment.kind == "image":
        return "image"
    if attachment.kind == "audio":
        return "audio"
    if attachment.kind == "video":
        return "video"
    return "file"


def _build_attachment_message(attachment: ChannelAttachment, file_key: str) -> tuple[str, dict[str, Any]]:
    if attachment.kind == "image":
        return "image", {"image_key": file_key}
    if attachment.kind == "audio":
        return "audio", {"file_key": file_key}
    if attachment.kind == "video":
        return "media", {"file_key": file_key}
    return "file", {"file_key": file_key}


def _download_feishu_file(file_key: str, config: FeishuConfig) -> str | None:
    if not file_key:
        return None
    if not config.app_id or not config.app_secret:
        return None
    media_dir = Path(os.path.expanduser("~/.cli-claw/media"))
    media_dir.mkdir(parents=True, exist_ok=True)
    dest = media_dir / f"feishu_{file_key}"

    url = f"{config.base_url}/open-apis/im/v1/files/{urllib.parse.quote(file_key)}"
    try:
        token = _get_token_blocking(config)
    except Exception:
        return None
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=config.request_timeout) as resp:
            dest.write_bytes(resp.read())
    except Exception:
        return None
    return str(dest)


def _get_token_blocking(config: FeishuConfig) -> str:
    if not config.app_id or not config.app_secret:
        raise RuntimeError("Feishu app credentials missing; set FEISHU_APP_ID/FEISHU_APP_SECRET")
    url = f"{config.base_url}/open-apis/auth/v3/tenant_access_token/internal"
    payload = {"app_id": config.app_id, "app_secret": config.app_secret}
    resp = _request_json(url, payload, headers={}, timeout=config.request_timeout)
    if resp.get("code") not in (0, None):
        raise RuntimeError(f"Feishu auth failed: {resp}")
    token = resp.get("tenant_access_token")
    if not token:
        raise RuntimeError(f"Feishu auth missing token: {resp}")
    return str(token)


def _apply_receipt(envelope: OutboundEnvelope, resp: dict[str, Any] | None) -> None:
    if not isinstance(resp, dict):
        return
    data = resp.get("data")
    if not isinstance(data, dict):
        return
    message_id = data.get("message_id") or data.get("messageId")
    if message_id:
        envelope.message_id = str(message_id)
        envelope.receipt_id = str(message_id)
