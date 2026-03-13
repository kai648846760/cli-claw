from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx
from dingtalk_stream import AckMessage, CallbackHandler, CallbackMessage, Credential, DingTalkStreamClient
from dingtalk_stream.chatbot import ChatbotMessage

from cli_claw.channels.base import BaseChannel
from cli_claw.channels.simple_webhook_utils import parse_simple_inbound, send_simple_webhook
from cli_claw.channels.policy import sender_allowed
from cli_claw.schemas.channel import InboundEnvelope, OutboundEnvelope

logger = logging.getLogger(__name__)


@dataclass
class DingTalkConfig:
    webhook_url: str | None = None
    verification_token: str | None = None
    request_timeout: float = 10.0
    client_id: str | None = None
    client_secret: str | None = None
    card_template_id: str | None = None
    card_template_key: str = "content"
    robot_code: str | None = None
    allow_from: list[str] = field(default_factory=list)


def _load_config() -> DingTalkConfig:
    return DingTalkConfig(
        webhook_url=os.getenv("DINGTALK_WEBHOOK_URL"),
        verification_token=os.getenv("DINGTALK_VERIFICATION_TOKEN"),
        client_id=os.getenv("DINGTALK_CLIENT_ID"),
        client_secret=os.getenv("DINGTALK_CLIENT_SECRET"),
        card_template_id=os.getenv("DINGTALK_CARD_TEMPLATE_ID"),
        card_template_key=os.getenv("DINGTALK_CARD_TEMPLATE_KEY", "content"),
        robot_code=os.getenv("DINGTALK_ROBOT_CODE"),
    )


class _AICardStatus:
    PROCESSING = "1"
    INPUTING = "2"
    FINISHED = "3"
    FAILED = "5"


class _AICardInstance:
    def __init__(
        self,
        card_instance_id: str,
        access_token: str,
        conversation_id: str,
        config: DingTalkConfig,
    ) -> None:
        self.card_instance_id = card_instance_id
        self.access_token = access_token
        self.conversation_id = conversation_id
        self.config = config
        self.created_at = time.time()
        self.last_updated = time.time()
        self.state = _AICardStatus.PROCESSING


class DingTalkHandler(CallbackHandler):
    def __init__(self, channel: "DingTalkChannel"):
        super().__init__()
        self.channel = channel

    async def process(self, message: CallbackMessage):
        try:
            chatbot_msg = ChatbotMessage.from_dict(message.data)

            content = ""
            if chatbot_msg.text:
                content = chatbot_msg.text.content.strip()
            if not content:
                content = message.data.get("text", {}).get("content", "").strip()

            if not content:
                return AckMessage.STATUS_OK, "OK"

            sender_id = chatbot_msg.sender_staff_id or chatbot_msg.sender_id
            sender_name = chatbot_msg.sender_nick or "Unknown"
            is_group = chatbot_msg.conversation_type == "2"
            chat_id = chatbot_msg.conversation_id if is_group else sender_id

            if not sender_allowed(str(sender_id or ""), self.channel.config.allow_from):
                return AckMessage.STATUS_OK, "OK"

            await self.channel.emit_inbound(
                InboundEnvelope(
                    channel=self.channel.name,
                    chat_id=chat_id,
                    sender_id=sender_id,
                    text=content,
                    metadata={
                        "sender_name": sender_name,
                        "is_group": is_group,
                    },
                )
            )
            return AckMessage.STATUS_OK, "OK"
        except Exception as exc:
            logger.error("[dingtalk] Error processing message: %s", exc)
            return AckMessage.STATUS_OK, "Error"


class DingTalkChannel(BaseChannel):
    name = "dingtalk"

    def __init__(self, config: DingTalkConfig | None = None) -> None:
        super().__init__()
        self.config = config or _load_config()
        self._client: DingTalkStreamClient | None = None
        self._http: httpx.AsyncClient | None = None
        self._access_token: str | None = None
        self._token_expiry: float = 0
        self._ai_cards: dict[str, _AICardInstance] = {}
        self._active_cards_by_target: dict[str, str] = {}
        self._streaming_last_sent_content: dict[str, str] = {}
        self._streaming_last_sent_at: dict[str, float] = {}
        self._stream_state: dict[str, dict[str, Any]] = {}

    async def start(self) -> None:
        if not self.config.client_id or not self.config.client_secret:
            logger.error("[dingtalk] client_id and client_secret not configured")
            return

        self._running = True
        self._http = httpx.AsyncClient()

        credential = Credential(self.config.client_id, self.config.client_secret)
        self._client = DingTalkStreamClient(credential)
        handler = DingTalkHandler(self)
        self._client.register_callback_handler(ChatbotMessage.TOPIC, handler)

        while self._running:
            try:
                await self._client.start()
            except Exception as exc:
                logger.warning("[dingtalk] stream error: %s", exc)
            if self._running:
                await asyncio.sleep(5)

    async def stop(self) -> None:
        self._running = False
        if self._http:
            await self._http.aclose()
            self._http = None
        logger.info("[dingtalk] channel stopped")

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
                missing_message="DingTalk webhook missing; set DINGTALK_WEBHOOK_URL",
            )
            return

        if envelope.stream_id:
            await self._send_stream(envelope)
            return

        is_group = (envelope.metadata or {}).get("is_group", False)
        content = self._build_content(envelope)
        await self._send_markdown(envelope.chat_id, content, is_group)

    def supports_streaming(self) -> bool:
        return True

    async def _send_stream(self, envelope: OutboundEnvelope) -> None:
        stream_id = str(envelope.stream_id)
        is_group = (envelope.metadata or {}).get("is_group", False)
        state = self._stream_state.setdefault(
            stream_id,
            {
                "text": "",
                "card": None,
                "chat_id": envelope.chat_id,
                "is_group": is_group,
                "sent_fallback": False,
            },
        )
        if (envelope.metadata or {}).get("stream_full"):
            state["text"] = envelope.text or ""
        else:
            state["text"] = (state.get("text") or "") + (envelope.text or "")

        card = state.get("card")
        if not card:
            card = await self._create_ai_card(envelope.chat_id, is_group=is_group)
            if card:
                state["card"] = card

        if card:
            await self._stream_ai_card(card, state["text"], finished=bool(envelope.stream_final))
        else:
            if envelope.stream_final or not state.get("sent_fallback"):
                await self._send_markdown(envelope.chat_id, state["text"], is_group)
                state["sent_fallback"] = True

        if envelope.stream_final:
            self._stream_state.pop(stream_id, None)

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

    async def _get_access_token(self) -> str | None:
        if self._access_token and time.time() < self._token_expiry:
            return self._access_token

        url = "https://api.dingtalk.com/v1.0/oauth2/accessToken"
        data = {"appKey": self.config.client_id, "appSecret": self.config.client_secret}

        if not self._http:
            return None

        resp = await self._http.post(url, json=data)
        resp.raise_for_status()
        res_data = resp.json()
        self._access_token = res_data.get("accessToken")
        self._token_expiry = time.time() + int(res_data.get("expireIn", 7200)) - 60
        return self._access_token

    def _get_target_key(self, chat_id: str) -> str:
        return f"{self.config.client_id}:{chat_id}"

    async def _create_ai_card(self, chat_id: str, is_group: bool = False) -> _AICardInstance | None:
        token = await self._get_access_token()
        if not token or not self.config.card_template_id:
            return None

        card_instance_id = f"card_{uuid.uuid4().hex[:24]}"
        if is_group:
            open_space_id = f"dtv1.card//IM_GROUP.{chat_id}"
        else:
            open_space_id = f"dtv1.card//IM_ROBOT.{chat_id}"

        create_body = {
            "cardTemplateId": self.config.card_template_id,
            "outTrackId": card_instance_id,
            "cardData": {"cardParamMap": {}},
            "callbackType": "STREAM",
            "imGroupOpenSpaceModel": {"supportForward": True},
            "imRobotOpenSpaceModel": {"supportForward": True},
            "openSpaceId": open_space_id,
            "userIdType": 1,
        }

        if is_group:
            robot_code = self.config.robot_code or self.config.client_id
            create_body["imGroupOpenDeliverModel"] = {"robotCode": robot_code}
        else:
            create_body["imRobotOpenDeliverModel"] = {"spaceType": "IM_ROBOT"}

        resp = await self._http.post(
            "https://api.dingtalk.com/v1.0/card/instances/createAndDeliver",
            json=create_body,
            headers={
                "x-acs-dingtalk-access-token": token,
                "Content-Type": "application/json",
            },
        )
        resp.raise_for_status()

        card = _AICardInstance(
            card_instance_id=card_instance_id,
            access_token=token,
            conversation_id=chat_id,
            config=self.config,
        )
        self._ai_cards[card_instance_id] = card
        self._active_cards_by_target[self._get_target_key(chat_id)] = card_instance_id
        return card

    async def _stream_ai_card(self, card: _AICardInstance, content: str, finished: bool = False) -> bool:
        if card.state == _AICardStatus.FINISHED:
            return False

        token_age = time.time() - card.created_at
        if token_age > 90 * 60:
            new_token = await self._get_access_token()
            if new_token:
                card.access_token = new_token

        stream_body = {
            "outTrackId": card.card_instance_id,
            "guid": uuid.uuid4().hex,
            "key": self.config.card_template_key,
            "content": content,
            "isFull": True,
            "isFinalize": finished,
            "isError": False,
        }

        resp = await self._http.put(
            "https://api.dingtalk.com/v1.0/card/streaming",
            json=stream_body,
            headers={
                "x-acs-dingtalk-access-token": card.access_token,
                "Content-Type": "application/json",
            },
        )
        if resp.status_code >= 400:
            logger.error("[dingtalk] AI card stream error: %s", resp.text[:500])
        resp.raise_for_status()

        card.last_updated = time.time()
        if finished:
            card.state = _AICardStatus.FINISHED
        elif card.state == _AICardStatus.PROCESSING:
            card.state = _AICardStatus.INPUTING
        return True

    async def _send_markdown(self, chat_id: str, content: str, is_group: bool = False) -> bool:
        token = await self._get_access_token()
        if not token:
            return False

        robot_code = self.config.robot_code or self.config.client_id

        if is_group:
            url = "https://api.dingtalk.com/v1.0/robot/groupMessages/send"
            data = {
                "robotCode": robot_code,
                "openConversationId": chat_id,
                "msgKey": "sampleMarkdown",
                "msgParam": json.dumps({"title": "cli-claw", "text": content}, ensure_ascii=False),
            }
        else:
            url = "https://api.dingtalk.com/v1.0/robot/oToMessages/batchSend"
            data = {
                "robotCode": robot_code,
                "userIds": [chat_id],
                "msgKey": "sampleMarkdown",
                "msgParam": json.dumps({"title": "cli-claw Reply", "text": content}, ensure_ascii=False),
            }

        resp = await self._http.post(
            url,
            json=data,
            headers={"x-acs-dingtalk-access-token": token},
        )
        return resp.status_code == 200
