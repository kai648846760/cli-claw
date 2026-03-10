import json

import pytest

from cli_claw.channels.discord import DiscordChannel
from cli_claw.channels.discord_webhook import process_discord_webhook_payload
from cli_claw.channels.manager import ChannelManager
from cli_claw.channels.simple_channels import EmailChannel
from cli_claw.channels.simple_webhook import process_simple_webhook_payload
from cli_claw.channels.slack import SlackChannel
from cli_claw.channels.slack_webhook import process_slack_webhook_payload
from cli_claw.channels.telegram import TelegramChannel
from cli_claw.channels.telegram_webhook import process_telegram_webhook_payload
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


@pytest.mark.asyncio
async def test_telegram_webhook_routes_to_runtime(monkeypatch):
    channel = TelegramChannel()
    channel.config.bot_token = "token"

    captured = {}

    async def _fake_post(url, payload, headers, timeout):
        captured["url"] = url
        captured["payload"] = payload
        captured["headers"] = headers
        captured["timeout"] = timeout
        return {"ok": True}

    monkeypatch.setattr("cli_claw.channels.telegram.post_json", _fake_post)

    manager = ChannelManager()
    manager.register("telegram", lambda: channel)

    orchestrator = RuntimeOrchestrator()
    orchestrator.providers = _FakeRegistry(_FakeProvider())

    runtime = ChannelRuntime(orchestrator, manager)
    runtime.register_route("telegram", "fake")
    await runtime.start(["telegram"])

    payload = {
        "update_id": 1,
        "message": {
            "message_id": 10,
            "from": {"id": 100},
            "chat": {"id": 200, "type": "private"},
            "text": "hello",
        },
    }
    raw_body = json.dumps(payload).encode("utf-8")
    status, response = await process_telegram_webhook_payload(channel, runtime, payload, raw_body)
    await manager._queue.join()

    assert status == 200
    assert response == {"ok": True}
    assert captured["payload"]["text"] == "echo:hello"

    await runtime.stop()


@pytest.mark.asyncio
async def test_slack_webhook_routes_to_runtime(monkeypatch):
    channel = SlackChannel()
    channel.config.webhook_url = "https://example.com/hook"

    captured = {}

    async def _fake_post(url, payload, headers, timeout):
        captured["url"] = url
        captured["payload"] = payload
        captured["headers"] = headers
        captured["timeout"] = timeout
        return {"ok": True}

    monkeypatch.setattr("cli_claw.channels.slack.post_json", _fake_post)

    manager = ChannelManager()
    manager.register("slack", lambda: channel)

    orchestrator = RuntimeOrchestrator()
    orchestrator.providers = _FakeRegistry(_FakeProvider())

    runtime = ChannelRuntime(orchestrator, manager)
    runtime.register_route("slack", "fake")
    await runtime.start(["slack"])

    payload = {
        "event": {"type": "message", "text": "hello", "channel": "C1", "user": "U1", "ts": "1"},
        "team_id": "T1",
    }
    raw_body = json.dumps(payload).encode("utf-8")
    status, response = await process_slack_webhook_payload(channel, runtime, payload, raw_body)
    await manager._queue.join()

    assert status == 200
    assert response == {"ok": True}
    assert captured["payload"]["text"] == "echo:hello"

    await runtime.stop()


@pytest.mark.asyncio
async def test_discord_webhook_routes_to_runtime(monkeypatch):
    channel = DiscordChannel()
    channel.config.base_url = "https://discord.test/api"

    captured = {}

    async def _fake_post(url, payload, headers, timeout):
        captured["url"] = url
        captured["payload"] = payload
        captured["headers"] = headers
        captured["timeout"] = timeout
        return {"ok": True}

    monkeypatch.setattr("cli_claw.channels.discord.post_json", _fake_post)

    manager = ChannelManager()
    manager.register("discord", lambda: channel)

    orchestrator = RuntimeOrchestrator()
    orchestrator.providers = _FakeRegistry(_FakeProvider())

    runtime = ChannelRuntime(orchestrator, manager)
    runtime.register_route("discord", "fake")
    await runtime.start(["discord"])

    payload = {
        "type": 2,
        "id": "i1",
        "token": "tok",
        "application_id": "app",
        "channel_id": "c1",
        "member": {"user": {"id": "u1"}},
        "data": {"name": "ping"},
    }
    raw_body = json.dumps(payload).encode("utf-8")
    status, response = await process_discord_webhook_payload(channel, runtime, payload, raw_body)
    await manager._queue.join()

    assert status == 200
    assert response == {"type": 5}
    assert captured["url"] == "https://discord.test/api/webhooks/app/tok"
    assert captured["payload"]["content"] == "echo:/ping"

    await runtime.stop()


@pytest.mark.asyncio
async def test_simple_webhook_routes_to_runtime(monkeypatch):
    channel = EmailChannel()
    channel.config.webhook_url = "https://example.com/hook"

    captured = {}

    async def _fake_post(url, payload, headers, timeout):
        captured["url"] = url
        captured["payload"] = payload
        captured["headers"] = headers
        captured["timeout"] = timeout
        return {"ok": True}

    monkeypatch.setattr("cli_claw.channels.simple_channels.post_json", _fake_post)

    manager = ChannelManager()
    manager.register("email", lambda: channel)

    orchestrator = RuntimeOrchestrator()
    orchestrator.providers = _FakeRegistry(_FakeProvider())

    runtime = ChannelRuntime(orchestrator, manager)
    runtime.register_route("email", "fake")
    await runtime.start(["email"])

    payload = {"text": "hello", "chat_id": "c1", "sender_id": "u1"}
    raw_body = json.dumps(payload).encode("utf-8")
    status, response = await process_simple_webhook_payload(channel, runtime, payload, raw_body)
    await manager._queue.join()

    assert status == 200
    assert response == {"ok": True}
    assert captured["payload"]["text"] == "echo:hello"

    await runtime.stop()
