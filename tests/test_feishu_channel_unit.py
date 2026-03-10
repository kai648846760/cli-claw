import pytest

from cli_claw.channels.feishu import FeishuChannel
from cli_claw.schemas.channel import OutboundEnvelope


def test_feishu_parse_inbound_text_event():
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
                "root_id": "om_root",
                "parent_id": "om_parent",
            },
        },
    }

    channel = FeishuChannel()
    inbound = channel.parse_inbound_event(payload)

    assert inbound is not None
    assert inbound.channel == "feishu"
    assert inbound.chat_id == "oc_9"
    assert inbound.sender_id == "ou_123"
    assert inbound.message_id == "om_1"
    assert inbound.reply_to_id == "om_parent"
    assert inbound.text == "hello"
    assert inbound.metadata["event_type"] == "im.message.receive_v1"
    assert inbound.metadata["thread_id"] == "om_root"


@pytest.mark.asyncio
async def test_feishu_send_text_via_webhook():
    captured = {}

    async def _fake_post(url, payload, headers):
        captured["url"] = url
        captured["payload"] = payload
        captured["headers"] = headers
        return {"ok": True}

    channel = FeishuChannel()
    channel.config.bot_webhook_url = "https://example.com/hook"
    channel._post_json = _fake_post  # type: ignore[assignment]

    await channel.start()
    await channel.send(OutboundEnvelope(channel="feishu", chat_id="c1", text="hi"))
    await channel.stop()

    assert captured["url"] == "https://example.com/hook"
    assert captured["payload"]["msg_type"] == "text"
    assert captured["payload"]["content"]["text"] == "hi"
