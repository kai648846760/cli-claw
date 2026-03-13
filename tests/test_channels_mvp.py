import time

import pytest

from cli_claw.channels.discord import DiscordChannel
from cli_claw.channels.dingtalk import DingTalkChannel
from cli_claw.channels.email import EmailChannel
from cli_claw.channels.mochat import MochatChannel
from cli_claw.channels.qq import QQChannel
from cli_claw.channels.whatsapp import WhatsAppChannel
from cli_claw.channels.slack import SlackChannel
from cli_claw.channels.telegram import TelegramChannel
from cli_claw.channels.feishu import FeishuChannel
from cli_claw.channels.base import BaseChannel
from cli_claw.schemas.channel import OutboundEnvelope


@pytest.mark.asyncio
async def test_feishu_parse_and_send(monkeypatch):
    channel = FeishuChannel()
    channel.config.app_id = "app"
    channel.config.app_secret = "secret"
    channel.config.base_url = "https://feishu.test"

    async def _fake_post(*args, **kwargs):
        return {"ok": True, "data": {"file_key": "fk"}}

    monkeypatch.setattr("cli_claw.channels.feishu.FeishuChannel._post_json", _fake_post)
    monkeypatch.setattr("cli_claw.channels.feishu.post_multipart", _fake_post)

    payload = {
        "event": {
            "sender": {"sender_id": {"open_id": "u1"}},
            "message": {
                "message_id": "m1",
                "chat_id": "c1",
                "message_type": "text",
                "content": "{\"text\":\"hello\"}",
            },
        }
    }
    inbound = channel.parse_inbound_event(payload)
    assert inbound is not None
    assert inbound.chat_id == "c1"
    assert inbound.sender_id == "u1"

    channel.config.bot_webhook_url = "https://example.com/hook"
    await channel.send(OutboundEnvelope(channel="feishu", chat_id="c1", text="ok"))

    channel.config.bot_webhook_url = None
    channel._tenant_token = "token"
    channel._tenant_token_expire_at = time.time() + 10000
    await channel.send(
        OutboundEnvelope(
            channel="feishu",
            chat_id="c1",
            text="file",
            attachments=[{"kind": "file", "name": "demo.txt", "path": "/tmp/demo.txt"}],
        )
    )


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

    async def _fake_multipart(url, fields, files, headers, timeout):
        sent["url"] = url
        sent["fields"] = fields
        sent["files"] = files
        sent["headers"] = headers
        sent["timeout"] = timeout
        return {"ok": True}

    monkeypatch.setattr("cli_claw.channels.telegram.post_json", _fake_post)
    monkeypatch.setattr("cli_claw.channels.telegram.post_multipart", _fake_multipart)

    payload = {
        "update_id": 1,
        "message": {
            "message_id": 10,
            "from": {"id": 100},
            "chat": {"id": 200, "type": "private"},
            "text": "/start",
        },
    }
    inbound = channel.parse_inbound_event(payload)
    assert inbound is not None
    assert inbound.chat_id == "200"
    assert inbound.sender_id == "100"
    assert inbound.text == "/start"
    assert inbound.metadata.get("is_command") is True

    callback_payload = {
        "update_id": 2,
        "callback_query": {
            "id": "cb1",
            "from": {"id": 101},
            "message": {
                "message_id": 11,
                "chat": {"id": 201, "type": "private"},
            },
            "data": "btn:ok",
        },
    }
    inbound_cb = channel.parse_inbound_event(callback_payload)
    assert inbound_cb is not None
    assert inbound_cb.chat_id == "201"
    assert inbound_cb.sender_id == "101"
    assert inbound_cb.text == "btn:ok"
    assert inbound_cb.metadata.get("callback_query_data") == "btn:ok"

    await channel.send(OutboundEnvelope(channel="telegram", chat_id="200", text="ok"))
    assert "/bottoken/sendMessage" in sent["url"]
    assert sent["payload"]["text"] == "ok"

    attachment = OutboundEnvelope(
        channel="telegram",
        chat_id="200",
        text="file",
        attachments=[
            {
                "kind": "file",
                "name": "demo.txt",
                "path": "/tmp/demo.txt",
            }
        ],
    )
    await channel.send(attachment)
    assert "/bottoken/sendDocument" in sent["url"]
    assert sent["fields"]["caption"] == "file"
    assert "document" in sent["files"]


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

    async def _fake_multipart(url, fields, files, headers, timeout):
        sent["url"] = url
        sent["fields"] = fields
        sent["files"] = files
        sent["headers"] = headers
        sent["timeout"] = timeout
        return {"ok": True}

    monkeypatch.setattr("cli_claw.channels.slack.post_json", _fake_post)
    monkeypatch.setattr("cli_claw.channels.slack.post_multipart", _fake_multipart)

    payload = {
        "event": {
            "type": "message",
            "text": "hello",
            "channel": "C1",
            "user": "U1",
            "ts": "1",
            "thread_ts": "0.9",
            "files": [
                {
                    "id": "F1",
                    "name": "demo.txt",
                    "url_private": "https://files",
                    "mimetype": "text/plain",
                    "size": 10,
                }
            ],
        },
        "team_id": "T1",
    }
    inbound = channel.parse_inbound_event(payload)
    assert inbound is not None
    assert inbound.chat_id == "C1"
    assert inbound.sender_id == "U1"
    assert inbound.text == "hello"
    assert inbound.reply_to_id == "0.9"

    app_mention_payload = {
        "event": {
            "type": "app_mention",
            "text": "<@U2> ping",
            "channel": "C2",
            "user": "U2",
            "ts": "2",
        },
        "team_id": "T1",
    }
    inbound2 = channel.parse_inbound_event(app_mention_payload)
    assert inbound2 is not None
    assert inbound2.chat_id == "C2"
    assert inbound2.sender_id == "U2"

    reaction_payload = {
        "event": {
            "type": "reaction_added",
            "user": "U3",
            "reaction": "+1",
            "item": {"type": "message", "channel": "C3", "ts": "9"},
        }
    }
    inbound3 = channel.parse_inbound_event(reaction_payload)
    assert inbound3 is not None
    assert inbound3.chat_id == "C3"
    assert "reaction_added" in inbound3.text

    await channel.send(OutboundEnvelope(channel="slack", chat_id="C1", text="ok"))
    assert sent["url"] == "https://example.com/hook"
    assert sent["payload"]["text"] == "ok"

    channel.config.bot_token = "xoxb-test"
    await channel.send(
        OutboundEnvelope(
            channel="slack",
            chat_id="C1",
            text="file",
            attachments=[{"kind": "file", "name": "demo.txt", "path": "/tmp/demo.txt"}],
        )
    )
    assert sent["url"] == "https://slack.com/api/files.upload"
    assert sent["fields"]["channels"] == "C1"
    assert "file" in sent["files"]


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
    assert sent["url"] == "https://discord.test/api/webhooks/app/tok?wait=true"
    assert sent["payload"]["content"] == "ok"

    event_payload = {
        "type": 0,
        "event": {
            "type": "MESSAGE_CREATE",
            "id": "m1",
            "channel_id": "c2",
            "author": {"id": "u2"},
            "content": "hello",
            "referenced_message": {"id": "m0"},
            "attachments": [{"id": "a1", "filename": "a.txt", "url": "https://x/a.txt", "size": 3}],
        },
    }
    inbound2 = channel.parse_inbound_event(event_payload)
    assert inbound2 is not None
    assert inbound2.chat_id == "c2"
    assert inbound2.sender_id == "u2"
    assert inbound2.text == "hello"
    assert inbound2.reply_to_id == "m0"
    assert inbound2.attachments

    component_payload = {
        "type": 3,
        "id": "i2",
        "token": "tok2",
        "application_id": "app",
        "channel_id": "c3",
        "member": {"user": {"id": "u3"}},
        "data": {"custom_id": "btn_ok", "component_type": 2, "values": []},
    }
    inbound3 = channel.parse_inbound_event(component_payload)
    assert inbound3 is not None
    assert inbound3.text.startswith("/btn_ok")

    async def _fake_multipart(url, fields, files, headers, timeout):
        sent["url"] = url
        sent["fields"] = fields
        sent["files"] = files
        sent["headers"] = headers
        sent["timeout"] = timeout
        return {"ok": True}

    monkeypatch.setattr("cli_claw.channels.discord.post_multipart", _fake_multipart)
    await channel.send(
        OutboundEnvelope(
            channel="discord",
            chat_id="c1",
            text="file",
            attachments=[{"kind": "file", "name": "demo.txt", "path": "/tmp/demo.txt"}],
            metadata={"interaction_token": "tok", "application_id": "app"},
        )
    )
    assert "/webhooks/app/tok?wait=true" in sent["url"]
    assert "files[0]" in sent["files"]


@pytest.mark.asyncio
async def test_telegram_streaming(monkeypatch):
    channel = TelegramChannel()
    channel.config.bot_token = "token"
    channel.config.stream_update_interval = 0

    calls: list[tuple[str, dict]] = []

    async def _fake_post(url, payload, headers, timeout):
        calls.append((url, payload))
        if url.endswith("/sendMessage"):
            return {"ok": True, "result": {"message_id": 123}}
        return {"ok": True, "result": {}}

    monkeypatch.setattr("cli_claw.channels.telegram.post_json", _fake_post)

    await channel.send(
        OutboundEnvelope(
            channel="telegram",
            chat_id="c1",
            text="hi",
            stream_id="s1",
            stream_seq=1,
            stream_final=False,
        )
    )
    await channel.send(
        OutboundEnvelope(
            channel="telegram",
            chat_id="c1",
            text=" there",
            stream_id="s1",
            stream_seq=2,
            stream_final=True,
        )
    )

    assert calls[0][0].endswith("/sendMessage")
    assert calls[0][1]["text"] == "hi"
    assert calls[1][0].endswith("/editMessageText")
    assert calls[1][1]["text"] == "hi there"


@pytest.mark.asyncio
async def test_dingtalk_streaming(monkeypatch):
    channel = DingTalkChannel()

    class _Card:
        pass

    card = _Card()
    seen: list[tuple[str, bool]] = []

    async def _fake_create(chat_id, is_group=False):
        _ = chat_id
        _ = is_group
        return card

    async def _fake_stream(card_instance, content, finished=False):
        _ = card_instance
        seen.append((content, finished))
        return True

    monkeypatch.setattr(channel, "_create_ai_card", _fake_create)
    monkeypatch.setattr(channel, "_stream_ai_card", _fake_stream)

    await channel.send(
        OutboundEnvelope(
            channel="dingtalk",
            chat_id="c1",
            text="hi",
            stream_id="s1",
            stream_seq=1,
            stream_final=False,
        )
    )
    await channel.send(
        OutboundEnvelope(
            channel="dingtalk",
            chat_id="c1",
            text=" there",
            stream_id="s1",
            stream_seq=2,
            stream_final=True,
        )
    )

    assert seen[-1][0] == "hi there"
    assert seen[-1][1] is True


@pytest.mark.asyncio
async def test_simple_channels_parse_and_send(monkeypatch):
    classes = [EmailChannel, DingTalkChannel, MochatChannel, QQChannel, WhatsAppChannel]
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

        monkeypatch.setattr("cli_claw.channels.simple_webhook_utils.post_json", _fake_post)

        payload = {
            "text": "hi",
            "chat_id": "c1",
            "sender_id": "u1",
            "attachments": [{"kind": "file", "name": "demo.txt", "url": "https://x"}],
        }
        inbound = channel.parse_inbound_event(payload)
        assert inbound is not None
        assert inbound.chat_id == "c1"
        assert inbound.sender_id == "u1"
        assert inbound.text == "hi"
        assert inbound.attachments

        await channel.send(OutboundEnvelope(channel=channel.name, chat_id="c1", text="ok"))
        assert sent["url"] == "https://example.com/hook"
        assert sent["payload"]["text"] == "ok"

        await channel.send(
            OutboundEnvelope(
                channel=channel.name,
                chat_id="c1",
                text="file",
                attachments=[{"kind": "file", "name": "demo.txt", "url": "https://x"}],
            )
        )
        assert sent["payload"]["attachments"][0]["name"] == "demo.txt"


class _FailChannel(BaseChannel):
    name = "fail"

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def send(self, envelope: OutboundEnvelope) -> None:
        _ = envelope
        raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_manager_failure_downgrade(monkeypatch):
    from cli_claw.channels.manager import ChannelManager

    manager = ChannelManager()
    manager.register("fail", lambda: _FailChannel())
    await manager.start_enabled(["fail"])

    sent = {}

    async def _fake_send(self, envelope):
        sent["envelope"] = envelope

    monkeypatch.setattr(ChannelManager, "send", _fake_send)

    await manager.enqueue(
        OutboundEnvelope(channel="fail", chat_id="c1", text="hi", message_id="m1", receipt_id="r1")
    )
    await manager._queue.join()

    assert sent["envelope"].kind == "error"
    assert sent["envelope"].receipt_id == "r1"
    assert sent["envelope"].delivery_status == "failed"
    assert sent["envelope"].error_code == "delivery_error"

    await manager.stop_all()


@pytest.mark.asyncio
async def test_telegram_failure_downgrade(monkeypatch):
    from cli_claw.channels.manager import ChannelManager

    manager = ChannelManager()
    channel = TelegramChannel()
    channel.config.bot_token = "token"
    manager.register("telegram", lambda: channel)
    await manager.start_enabled(["telegram"])

    sent = {}

    async def _fake_send(self, envelope):
        if envelope.kind != "error":
            raise RuntimeError("boom")
        sent["envelope"] = envelope

    monkeypatch.setattr(TelegramChannel, "send", _fake_send)

    await manager.enqueue(
        OutboundEnvelope(channel="telegram", chat_id="c1", text="hi", message_id="m1", receipt_id="r1")
    )
    await manager._queue.join()

    assert sent["envelope"].kind == "error"
    assert sent["envelope"].receipt_id == "r1"
    assert sent["envelope"].delivery_status == "failed"
    assert sent["envelope"].error_code == "delivery_error"

    await manager.stop_all()


@pytest.mark.asyncio
async def test_slack_failure_downgrade(monkeypatch):
    from cli_claw.channels.manager import ChannelManager

    manager = ChannelManager()
    channel = SlackChannel()
    channel.config.webhook_url = "https://example.com/hook"
    manager.register("slack", lambda: channel)
    await manager.start_enabled(["slack"])

    sent = {}

    async def _fake_send(self, envelope):
        if envelope.kind != "error":
            raise RuntimeError("boom")
        sent["envelope"] = envelope

    monkeypatch.setattr(SlackChannel, "send", _fake_send)

    await manager.enqueue(
        OutboundEnvelope(channel="slack", chat_id="c1", text="hi", message_id="m1", receipt_id="r1")
    )
    await manager._queue.join()

    assert sent["envelope"].kind == "error"
    assert sent["envelope"].receipt_id == "r1"
    assert sent["envelope"].delivery_status == "failed"
    assert sent["envelope"].error_code == "delivery_error"

    await manager.stop_all()


@pytest.mark.asyncio
async def test_discord_failure_downgrade(monkeypatch):
    from cli_claw.channels.manager import ChannelManager

    manager = ChannelManager()
    channel = DiscordChannel()
    channel.config.webhook_url = "https://example.com/hook"
    manager.register("discord", lambda: channel)
    await manager.start_enabled(["discord"])

    sent = {}

    async def _fake_send(self, envelope):
        if envelope.kind != "error":
            raise RuntimeError("boom")
        sent["envelope"] = envelope

    monkeypatch.setattr(DiscordChannel, "send", _fake_send)

    await manager.enqueue(
        OutboundEnvelope(channel="discord", chat_id="c1", text="hi", message_id="m1", receipt_id="r1")
    )
    await manager._queue.join()

    assert sent["envelope"].kind == "error"
    assert sent["envelope"].receipt_id == "r1"
    assert sent["envelope"].delivery_status == "failed"
    assert sent["envelope"].error_code == "delivery_error"

    await manager.stop_all()
