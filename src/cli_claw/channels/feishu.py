from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from cli_claw.channels.base import BaseChannel
from cli_claw.schemas.channel import InboundEnvelope, OutboundEnvelope

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
        if envelope.kind != "text":
            return False
        if envelope.attachments:
            return False
        return True

    def parse_inbound_event(self, payload: dict[str, Any]) -> InboundEnvelope | None:
        if "challenge" in payload:
            return None

        event = payload.get("event") or {}
        message = event.get("message") or {}
        if not message:
            return None

        content_raw = message.get("content") or "{}"
        text = _extract_text(content_raw)

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
            "event_type": (payload.get("header") or {}).get("event_type"),
            "message_type": message.get("message_type") or message.get("msg_type"),
            "chat_type": message.get("chat_type"),
            "tenant_key": (payload.get("header") or {}).get("tenant_key"),
        }
        if root_id:
            metadata["thread_id"] = root_id

        return InboundEnvelope(
            channel=self.name,
            chat_id=message.get("chat_id") or event.get("chat_id") or "",
            sender_id=sender_id,
            message_id=message_id,
            reply_to_id=parent_id or root_id,
            text=text,
            metadata={k: v for k, v in metadata.items() if v is not None},
        )

    async def send(self, envelope: OutboundEnvelope) -> None:
        if not self.is_allowed(envelope):
            raise ValueError("FeishuChannel only supports outbound text without attachments")

        if self.config.bot_webhook_url:
            payload = {"msg_type": "text", "content": {"text": envelope.text}}
            await self._post_json(self.config.bot_webhook_url, payload, headers={})
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

        await self._post_json(url, payload, headers=headers)

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
        return str(parsed.get("text") or "")
    return str(parsed)
