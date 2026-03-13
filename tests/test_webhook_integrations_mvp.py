import json
import time

import pytest

from cli_claw.channels.discord import DiscordChannel
from cli_claw.channels.discord_webhook import process_discord_webhook_payload
from cli_claw.channels.manager import ChannelManager
from cli_claw.channels.simple_channels import EmailChannel, SimpleWebhookChannel
from cli_claw.channels.simple_webhook import process_simple_webhook_payload
from cli_claw.channels.slack import SlackChannel
from cli_claw.channels.slack_webhook import process_slack_webhook_payload
from cli_claw.channels.telegram import TelegramChannel
from cli_claw.channels.telegram_webhook import process_telegram_webhook_payload
from cli_claw.runtime.channel_runtime import ChannelRuntime
from cli_claw.runtime.orchestrator import RuntimeOrchestrator
from cli_claw.schemas.channel import InboundEnvelope


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


class _RecordingRuntime:
    def __init__(self) -> None:
        self.inbound: InboundEnvelope | None = None

    async def handle_inbound(self, channel_name: str, inbound: InboundEnvelope) -> None:
        _ = channel_name
        self.inbound = inbound


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
async def test_telegram_webhook_rejects_invalid_secret(monkeypatch):
    channel = TelegramChannel()
    channel.config.webhook_secret = "secret"

    def _boom(_payload):
        raise AssertionError("parse_inbound_event should not be called for invalid secret")

    monkeypatch.setattr(channel, "parse_inbound_event", _boom)

    payload = {"update_id": 1}
    raw_body = json.dumps(payload).encode("utf-8")
    status, response = await process_telegram_webhook_payload(
        channel,
        object(),
        payload,
        raw_body,
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
    )

    assert status == 401
    assert response == {"ok": False, "error": "invalid token"}


@pytest.mark.asyncio
async def test_telegram_webhook_rejects_missing_secret(monkeypatch):
    channel = TelegramChannel()
    channel.config.webhook_secret = "secret"

    def _boom(_payload):
        raise AssertionError("parse_inbound_event should not be called for missing secret")

    monkeypatch.setattr(channel, "parse_inbound_event", _boom)

    payload = {"update_id": 1}
    raw_body = json.dumps(payload).encode("utf-8")
    status, response = await process_telegram_webhook_payload(
        channel,
        object(),
        payload,
        raw_body,
        headers=None,
    )

    assert status == 401
    assert response == {"ok": False, "error": "invalid token"}


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
async def test_slack_webhook_rejects_missing_signature(monkeypatch):
    channel = SlackChannel()
    channel.config.signing_secret = "secret"

    def _boom(_payload):
        raise AssertionError("parse_inbound_event should not be called for invalid signature")

    monkeypatch.setattr(channel, "parse_inbound_event", _boom)

    payload = {"event": {"type": "message"}}
    raw_body = json.dumps(payload).encode("utf-8")
    status, response = await process_slack_webhook_payload(
        channel,
        object(),
        payload,
        raw_body,
        headers=None,
    )

    assert status == 401
    assert response == {"ok": False, "error": "invalid signature"}


@pytest.mark.asyncio
async def test_slack_webhook_rejects_invalid_signature(monkeypatch):
    channel = SlackChannel()
    channel.config.signing_secret = "secret"

    def _boom(_payload):
        raise AssertionError("parse_inbound_event should not be called for invalid signature")

    monkeypatch.setattr(channel, "parse_inbound_event", _boom)

    payload = {"event": {"type": "message"}}
    raw_body = json.dumps(payload).encode("utf-8")
    headers = {
        "X-Slack-Request-Timestamp": str(int(time.time())),
        "X-Slack-Signature": "v0=bad",
    }
    status, response = await process_slack_webhook_payload(
        channel,
        object(),
        payload,
        raw_body,
        headers=headers,
    )

    assert status == 401
    assert response == {"ok": False, "error": "invalid signature"}


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
    assert captured["url"] == "https://discord.test/api/webhooks/app/tok?wait=true"
    assert captured["payload"]["content"] == "echo:/ping"

    await runtime.stop()


@pytest.mark.asyncio
async def test_discord_webhook_rejects_invalid_signature():
    channel = DiscordChannel()
    channel.config.public_key = "0" * 64

    payload = {"type": 1}
    raw_body = json.dumps(payload).encode("utf-8")
    headers = {
        "X-Signature-Ed25519": "0" * 128,
        "X-Signature-Timestamp": "1",
    }

    status, response = await process_discord_webhook_payload(channel, object(), payload, raw_body, headers)
    assert status == 401
    assert response == {"error": "invalid signature"}


@pytest.mark.asyncio
async def test_discord_webhook_rejects_missing_signature():
    channel = DiscordChannel()
    channel.config.public_key = "0" * 64

    payload = {"type": 1}
    raw_body = json.dumps(payload).encode("utf-8")

    status, response = await process_discord_webhook_payload(channel, object(), payload, raw_body, headers=None)
    assert status == 401
    assert response == {"error": "invalid signature"}


@pytest.mark.asyncio
async def test_simple_webhook_routes_to_runtime(monkeypatch):
    channel = SimpleWebhookChannel()
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
    manager.register("simple", lambda: channel)

    orchestrator = RuntimeOrchestrator()
    orchestrator.providers = _FakeRegistry(_FakeProvider())

    runtime = ChannelRuntime(orchestrator, manager)
    runtime.register_route("simple", "fake")
    await runtime.start(["simple"])

    payload = {"text": "hello", "chat_id": "c1", "sender_id": "u1"}
    raw_body = json.dumps(payload).encode("utf-8")
    status, response = await process_simple_webhook_payload(channel, runtime, payload, raw_body)
    await manager._queue.join()

    assert status == 200
    assert response == {"ok": True}
    assert captured["payload"]["text"] == "echo:hello"

    await runtime.stop()


@pytest.mark.asyncio
async def test_simple_webhook_rejects_invalid_token(monkeypatch):
    channel = EmailChannel()
    channel.config.webhook_url = "https://example.com/hook"
    channel.config.verification_token = "secret"

    def _boom(_payload):
        raise AssertionError("parse_inbound_event should not be called for invalid token")

    monkeypatch.setattr(channel, "parse_inbound_event", _boom)

    payload = {"text": "hello"}
    raw_body = json.dumps(payload).encode("utf-8")
    status, response = await process_simple_webhook_payload(
        channel,
        object(),
        payload,
        raw_body,
        headers={"X-Webhook-Token": "wrong"},
    )

    assert status == 401
    assert response == {"ok": False, "error": "invalid token"}


@pytest.mark.asyncio
async def test_simple_webhook_accepts_payload_token():
    channel = EmailChannel()
    channel.config.webhook_url = "https://example.com/hook"
    channel.config.verification_token = "secret"

    runtime = _RecordingRuntime()

    payload = {"text": "hello", "chat_id": "c1", "token": "secret"}
    raw_body = json.dumps(payload).encode("utf-8")
    status, response = await process_simple_webhook_payload(
        channel,
        runtime,
        payload,
        raw_body,
    )

    assert status == 200
    assert response == {"ok": True}
    assert runtime.inbound is not None
    assert runtime.inbound.chat_id == "c1"
