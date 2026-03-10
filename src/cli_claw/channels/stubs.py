from __future__ import annotations

from cli_claw.channels.base import BaseChannel
from cli_claw.schemas.channel import OutboundEnvelope


class _StubChannel(BaseChannel):
    name = "stub"

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def send(self, envelope: OutboundEnvelope) -> None:
        _ = envelope
        return None


class SlackChannel(_StubChannel):
    name = "slack"


class DiscordChannel(_StubChannel):
    name = "discord"


class TelegramChannel(_StubChannel):
    name = "telegram"


class EmailChannel(_StubChannel):
    name = "email"


class DingtalkChannel(_StubChannel):
    name = "dingtalk"


class MochatChannel(_StubChannel):
    name = "mochat"


class QQChannel(_StubChannel):
    name = "qq"


class WhatsappChannel(_StubChannel):
    name = "whatsapp"
