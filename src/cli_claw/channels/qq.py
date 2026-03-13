from __future__ import annotations

import asyncio
import json
import logging
import os
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import botpy
    from botpy.message import C2CMessage, GroupMessage
    _BOTPY_IMPORT_ERROR: Exception | None = None
except Exception as exc:  # pragma: no cover - exercised when botpy is missing
    botpy = None  # type: ignore[assignment]
    C2CMessage = GroupMessage = Any  # type: ignore[misc,assignment]
    _BOTPY_IMPORT_ERROR = exc

from cli_claw.channels.base import BaseChannel
from cli_claw.channels.simple_webhook_utils import parse_simple_inbound, send_simple_webhook
from cli_claw.channels.policy import sender_allowed
from cli_claw.schemas.channel import InboundEnvelope, OutboundEnvelope

logger = logging.getLogger(__name__)


def _ensure_botpy() -> None:
    if botpy is None:
        raise RuntimeError(
            "botpy is required for QQChannel; install botpy separately to enable QQ support"
        ) from _BOTPY_IMPORT_ERROR


@dataclass
class QQConfig:
    webhook_url: str | None = None
    verification_token: str | None = None
    request_timeout: float = 10.0
    app_id: str | None = None
    secret: str | None = None
    markdown_support: bool = False
    groups: list[str] | None = None
    allow_from: list[str] = field(default_factory=list)


def _load_config() -> QQConfig:
    groups = [g for g in os.getenv("QQ_GROUPS", "").split(",") if g.strip()]
    return QQConfig(
        webhook_url=os.getenv("QQ_WEBHOOK_URL"),
        verification_token=os.getenv("QQ_VERIFICATION_TOKEN"),
        app_id=os.getenv("QQ_APP_ID"),
        secret=os.getenv("QQ_SECRET"),
        markdown_support=os.getenv("QQ_MARKDOWN_SUPPORT", "false").lower() == "true",
        groups=groups or None,
    )


def _make_bot_class(channel: "QQChannel") -> Any:
    _ensure_botpy()
    intents = botpy.Intents(public_messages=True, direct_message=True)

    class _Bot(botpy.Client):
        def __init__(self):
            super().__init__(intents=intents)

        async def on_ready(self):
            logger.info("[qq] bot ready: %s", self.robot.name)

        async def on_c2c_message_create(self, message: C2CMessage):
            await channel._on_c2c_message(message)

        async def on_group_at_message_create(self, message: GroupMessage):
            await channel._on_group_message(message)

    return _Bot


class QQChannel(BaseChannel):
    name = "qq"

    def __init__(self, config: QQConfig | None = None) -> None:
        super().__init__()
        self.config = config or _load_config()
        self._client: Any = None
        self._processed_ids: deque[str] = deque(maxlen=1000)
        self._group_msg_seq: dict[str, int] = {}

    async def start(self) -> None:
        _ensure_botpy()
        if not self.config.app_id or not self.config.secret:
            logger.error("[qq] app_id and secret not configured")
            return

        await self._load_msg_seq_state()
        self._running = True
        BotClass = _make_bot_class(self)
        self._client = BotClass()
        await self._run_bot()

    async def _run_bot(self) -> None:
        while self._running:
            try:
                await self._client.start(appid=self.config.app_id, secret=self.config.secret)
            except Exception as exc:
                logger.warning("[qq] bot error: %s", exc)
            finally:
                await self._save_msg_seq_state()
            if self._running:
                await asyncio.sleep(5)

    async def stop(self) -> None:
        self._running = False
        if self._client:
            try:
                await self._client.close()
            except Exception:
                pass
        await self._save_msg_seq_state()

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
                missing_message="QQ webhook missing; set QQ_WEBHOOK_URL",
            )
            return

        if not self._client:
            raise RuntimeError("QQ client not initialized")

        metadata = envelope.metadata or {}
        content = self._build_content(envelope)

        if metadata.get("is_group"):
            group_id = metadata.get("group_id")
            msg_id = metadata.get("reply_to_id") or metadata.get("message_id")
            seq_key = f"group_{group_id}"
            if seq_key not in self._group_msg_seq:
                self._group_msg_seq[seq_key] = 1
            msg_seq = self._group_msg_seq[seq_key]
            self._group_msg_seq[seq_key] += 1

            if self.config.markdown_support:
                payload = {
                    "msg_type": 2,
                    "msg_id": msg_id,
                    "msg_seq": msg_seq,
                    "markdown": {"content": content},
                }
                from botpy.http import Route

                route = Route("POST", f"/v2/groups/{group_id}/messages", group_openid=group_id)
                await self._client.api._http.request(route, json=payload)
            else:
                await self._client.api.post_group_message(
                    group_openid=group_id,
                    msg_type=0,
                    msg_id=msg_id,
                    msg_seq=msg_seq,
                    content=content,
                )
        else:
            openid = envelope.chat_id
            if self.config.markdown_support:
                payload = {
                    "msg_type": 2,
                    "markdown": {"content": content},
                }
                c2c_msg_id = metadata.get("reply_to_id") or metadata.get("message_id")
                if c2c_msg_id:
                    payload["msg_id"] = c2c_msg_id
                    payload["msg_seq"] = 1

                from botpy.http import Route

                route = Route("POST", f"/v2/users/{openid}/messages")
                await self._client.api._http.request(route, json=payload)
            else:
                await self._client.api.post_c2c_message(
                    openid=openid,
                    msg_type=0,
                    content=content,
                )

    async def _on_c2c_message(self, data: C2CMessage) -> None:
        if data.id in self._processed_ids:
            return
        self._processed_ids.append(data.id)

        author = data.author
        user_id = str(getattr(author, "id", None) or getattr(author, "user_openid", "unknown"))
        if not sender_allowed(user_id, self.config.allow_from):
            return
        content = (data.content or "").strip()
        if not content:
            return

        await self.emit_inbound(
            InboundEnvelope(
                channel=self.name,
                chat_id=user_id,
                sender_id=user_id,
                message_id=data.id,
                text=content,
                metadata={"is_group": False},
            )
        )

    async def _on_group_message(self, data: GroupMessage) -> None:
        if data.id in self._processed_ids:
            return
        self._processed_ids.append(data.id)

        group_id = data.group_openid or ""
        if self.config.groups and group_id not in self.config.groups:
            return

        author = data.author
        user_id = str(getattr(author, "member_openid", None) or getattr(author, "user_openid", "unknown"))
        if not sender_allowed(user_id, self.config.allow_from):
            return
        content = (data.content or "").strip()
        if not content:
            return

        bot_id = getattr(self._client.robot, "id", "") if self._client else ""
        if bot_id:
            content = content.replace(f"<@!{bot_id}>", "").strip()
        if not content:
            return

        chat_id = f"group_{group_id}"
        await self.emit_inbound(
            InboundEnvelope(
                channel=self.name,
                chat_id=chat_id,
                sender_id=user_id,
                message_id=data.id,
                text=content,
                metadata={
                    "is_group": True,
                    "group_id": group_id,
                    "message_id": data.id,
                },
            )
        )

    def _build_content(self, envelope: OutboundEnvelope) -> str:
        content = envelope.text or ""
        if not envelope.attachments:
            return content

        lines = [content] if content else []
        for attachment in envelope.attachments:
            label = attachment.name or attachment.path or attachment.url or "attachment"
            target = attachment.url or attachment.path or ""
            if target:
                lines.append(f"[{attachment.kind}] {label}: {target}")
            else:
                lines.append(f"[{attachment.kind}] {label}")
        return "\n".join(lines)

    async def _load_msg_seq_state(self) -> None:
        state_file = Path.home() / ".cli-claw" / "qq_msg_seq_state.json"
        if not state_file.exists():
            return
        data = json.loads(state_file.read_text())
        if isinstance(data, dict):
            self._group_msg_seq = {k: int(v) for k, v in data.items()}

    async def _save_msg_seq_state(self) -> None:
        state_file = Path.home() / ".cli-claw" / "qq_msg_seq_state.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps(self._group_msg_seq, ensure_ascii=False, indent=2))
