import pytest

from cli_claw.channels.discord import DiscordChannel
from cli_claw.channels.simple_channels import (
    DingtalkChannel,
    EmailChannel,
    MochatChannel,
    QQChannel,
    WhatsappChannel,
)
from cli_claw.channels.slack import SlackChannel
from cli_claw.channels.telegram import TelegramChannel
from cli_claw.schemas.channel import OutboundEnvelope


@pytest.mark.asyncio
async def test_telegram_parse_and_send(monkeypatch):
    channel = TelegramChannel()
    channel.config.bot_token = "token"

    sent = {}

    async def _fake_post(url, payload, headers, timeout):
        sent["url"] = url
        sent["payload"] = payload
        sent["headers"] = headers
        sent["timeout"] = timeout
        return {"ok": True}

    monkeypatch.setattr("cli_claw.channels.telegram.post_json", _fake_post)

    payload = {
        "update_id": 1,
        "message": {
            "message_id": 10,
            "from": {"id": 100},
            "chat": {"id": 200, "type": "private"},
            "text": "hi",
        },
    }
    inbound = channel.parse_inbound_event(payload)
    assert inbound is not None
    assert inbound.chat_id == "200"
    assert inbound.sender_id == "100"
    assert inbound.text == "hi"

    await channel.send(OutboundEnvelope(channel="telegram", chat_id="200", text="ok"))
    assert "/bottoken/sendMessage" in sent["url"]
    assert sent["payload"]["text"] == "ok"


@pytest.mark.asyncio
async def test_slack_parse_and_send(monkeypatch):
    channel = SlackChannel()
    channel.config.webhook_url = "https://example.com/hook"

    sent = {}

    async def _fake_post(url, payload, headers, timeout):
        sent["url"] = url
        sent["payload"] = payload
        sent["headers"] = headers
        sent["timeout"] = timeout
        return {"ok": True}

    monkeypatch.setattr("cli_claw.channels.slack.post_json", _fake_post)

    payload = {
        "event": {"type": "message", "text": "hello", "channel": "C1", "user": "U1", "ts": "1"},
        "team_id": "T1",
    }
    inbound = channel.parse_inbound_event(payload)
    assert inbound is not None
    assert inbound.chat_id == "C1"
    assert inbound.sender_id == "U1"
    assert inbound.text == "hello"

    await channel.send(OutboundEnvelope(channel="slack", chat_id="C1", text="ok"))
    assert sent["url"] == "https://example.com/hook"
    assert sent["payload"]["text"] == "ok"


@pytest.mark.asyncio
async def test_discord_parse_and_send(monkeypatch):
    channel = DiscordChannel()
    channel.config.base_url = "https://discord.test/api"

    sent = {}

    async def _fake_post(url, payload, headers, timeout):
        sent["url"] = url
        sent["payload"] = payload
        sent["headers"] = headers
        sent["timeout"] = timeout
        return {"ok": True}

    monkeypatch.setattr("cli_claw.channels.discord.post_json", _fake_post)

    payload = {
        "type": 2,
        "id": "i1",
        "token": "tok",
        "application_id": "app",
        "channel_id": "c1",
        "member": {"user": {"id": "u1"}},
        "data": {"name": "ping"},
    }
    inbound = channel.parse_inbound_event(payload)
    assert inbound is not None
    assert inbound.chat_id == "c1"
    assert inbound.sender_id == "u1"
    assert inbound.text == "/ping"

    await channel.send(
        OutboundEnvelope(
            channel="discord",
            chat_id="c1",
            text="ok",
            metadata={"interaction_token": "tok", "application_id": "app"},
        )
    )
    assert sent["url"] == "https://discord.test/api/webhooks/app/tok"
    assert sent["payload"]["content"] == "ok"


@pytest.mark.asyncio
async def test_simple_channels_parse_and_send(monkeypatch):
    classes = [EmailChannel, DingtalkChannel, MochatChannel, QQChannel, WhatsappChannel]
    for cls in classes:
        channel = cls()
        channel.config.webhook_url = "https://example.com/hook"

        sent = {}

        async def _fake_post(url, payload, headers, timeout):
            sent["url"] = url
            sent["payload"] = payload
            sent["headers"] = headers
            sent["timeout"] = timeout
            return {"ok": True}

        monkeypatch.setattr("cli_claw.channels.simple_channels.post_json", _fake_post)

        payload = {"text": "hi", "chat_id": "c1", "sender_id": "u1"}
        inbound = channel.parse_inbound_event(payload)
        assert inbound is not None
        assert inbound.chat_id == "c1"
        assert inbound.sender_id == "u1"
        assert inbound.text == "hi"

        await channel.send(OutboundEnvelope(channel=channel.name, chat_id="c1", text="ok"))
        assert sent["url"] == "https://example.com/hook"
        assert sent["payload"]["text"] == "ok"
