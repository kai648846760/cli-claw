from __future__ import annotations

import json
import os
from typing import Any

from cli_claw.channels.feishu import FeishuChannel
from cli_claw.channels.feishu_webhook import process_feishu_webhook_payload
from cli_claw.channels.manager import ChannelManager
from cli_claw.runtime.channel_runtime import ChannelRuntime
from cli_claw.runtime.orchestrator import RuntimeOrchestrator


class _FakeProvider:
    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def health_check(self) -> bool:
        return True

    async def new_session(self, logical_session_id: str) -> str:
        return f"p:{logical_session_id}"

    async def clear_session(self, logical_session_id: str, provider_session_id: str | None = None) -> bool:
        _ = provider_session_id
        return True

    async def chat(self, logical_session_id: str, message: str) -> str:
        return f"echo:{message}"

    async def chat_stream(self, logical_session_id: str, message: str):
        _ = logical_session_id
        _ = message
        raise NotImplementedError

    async def list_models(self) -> list[str]:
        return ["fake"]


class _FakeRegistry:
    def __init__(self, provider: _FakeProvider) -> None:
        self._provider = provider

    def get(self, provider_id: str):
        _ = provider_id
        return self._provider


async def main() -> None:
    channel = FeishuChannel()
    channel.config.bot_webhook_url = os.getenv("FEISHU_BOT_WEBHOOK_URL", "https://example.com/hook")

    captured: dict[str, Any] = {}

    async def _fake_post(url, payload, headers):
        captured["url"] = url
        captured["payload"] = payload
        captured["headers"] = headers
        return {"ok": True}

    channel._post_json = _fake_post  # type: ignore[assignment]

    manager = ChannelManager()
    manager.register("feishu", lambda: channel)

    orchestrator = RuntimeOrchestrator()
    orchestrator.providers = _FakeRegistry(_FakeProvider())

    runtime = ChannelRuntime(orchestrator, manager)
    runtime.register_route("feishu", "fake")

    await runtime.start(["feishu"])

    payload = {
        "schema": "2.0",
        "header": {"event_type": "im.message.receive_v1", "tenant_key": "t1"},
        "event": {
            "sender": {"sender_id": {"open_id": "ou_123"}},
            "message": {
                "message_id": "om_1",
                "chat_id": "oc_9",
                "chat_type": "p2p",
                "message_type": "text",
                "content": "{\"text\":\"hello\"}",
            },
        },
    }

    raw_body = json.dumps(payload).encode("utf-8")
    status, response = await process_feishu_webhook_payload(channel, runtime, payload, raw_body, {})
    await manager._queue.join()

    print("status=", status)
    print("response=", response)
    print("outbound=", captured)

    await runtime.stop()


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
