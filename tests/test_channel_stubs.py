from cli_claw.channels.stubs import (
    DiscordChannel,
    DingtalkChannel,
    EmailChannel,
    MochatChannel,
    QQChannel,
    SlackChannel,
    TelegramChannel,
    WhatsappChannel,
)
from cli_claw.schemas.channel import OutboundEnvelope


async def _send_roundtrip(channel_cls):
    channel = channel_cls()
    await channel.start()
    await channel.send(OutboundEnvelope(channel=channel.name, chat_id="c1", text="hi"))
    await channel.stop()


def test_stub_channels_send():
    for cls in [
        SlackChannel,
        DiscordChannel,
        TelegramChannel,
        EmailChannel,
        DingtalkChannel,
        MochatChannel,
        QQChannel,
        WhatsappChannel,
    ]:
        import asyncio

        asyncio.run(_send_roundtrip(cls))
