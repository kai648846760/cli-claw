from cli_claw.schemas.channel import ChannelAttachment, InboundEnvelope, OutboundEnvelope


def test_inbound_envelope_defaults():
    inbound = InboundEnvelope(channel="telegram", chat_id="chat-1", text="hello")
    assert inbound.sender_id is None
    assert inbound.message_id is None
    assert inbound.reply_to_id is None
    assert inbound.attachments == []
    assert inbound.metadata == {}


def test_outbound_envelope_streaming_fields():
    outbound = OutboundEnvelope(
        channel="feishu",
        chat_id="chat-2",
        text="partial",
        stream_id="stream-1",
        stream_seq=2,
        stream_final=False,
    )
    assert outbound.kind == "text"
    assert outbound.stream_id == "stream-1"
    assert outbound.stream_seq == 2
    assert outbound.stream_final is False


def test_attachment_typed_object():
    att = ChannelAttachment(kind="image", url="https://example.com/a.png", mime_type="image/png")
    inbound = InboundEnvelope(channel="discord", chat_id="c1", text="see", attachments=[att])
    outbound = OutboundEnvelope(channel="discord", chat_id="c1", text="got it", attachments=[att])
    assert inbound.attachments[0].kind == "image"
    assert outbound.attachments[0].mime_type == "image/png"
