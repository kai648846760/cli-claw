import pytest

from cli_claw.channels.base import BaseChannel
from cli_claw.channels.manager import ChannelManager
from cli_claw.schemas.channel import OutboundEnvelope


class DummyChannel(BaseChannel):
    name = "dummy"

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[OutboundEnvelope] = []
        self.allow = True

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def send(self, envelope: OutboundEnvelope) -> None:
        self.sent.append(envelope)

    def is_allowed(self, envelope: OutboundEnvelope) -> bool:
        _ = envelope
        return self.allow


@pytest.mark.asyncio
async def test_manager_start_send_and_stop():
    mgr = ChannelManager()
    ch = DummyChannel()
    mgr.register("dummy", lambda: ch)

    await mgr.start_enabled(["dummy"])
    assert "dummy" in mgr.channels
    assert ch.is_running

    env = OutboundEnvelope(channel="dummy", chat_id="c1", text="hello")
    await mgr.send(env)
    assert len(ch.sent) == 1
    assert ch.sent[0].text == "hello"

    await mgr.stop_all()
    assert not ch.is_running
    assert mgr.channels == {}


@pytest.mark.asyncio
async def test_manager_queue_dispatch():
    mgr = ChannelManager()
    ch = DummyChannel()
    mgr.register("dummy", lambda: ch)

    await mgr.start_enabled(["dummy"])
    await mgr.enqueue(OutboundEnvelope(channel="dummy", chat_id="c1", text="queued"))
    await mgr._queue.join()

    assert [m.text for m in ch.sent] == ["queued"]
    await mgr.stop_all()


@pytest.mark.asyncio
async def test_manager_rejects_disallowed_envelope():
    mgr = ChannelManager()
    ch = DummyChannel()
    ch.allow = False
    mgr.register("dummy", lambda: ch)

    await mgr.start_enabled(["dummy"])
    with pytest.raises(ValueError, match="rejected outbound envelope"):
        await mgr.send(OutboundEnvelope(channel="dummy", chat_id="c1", text="blocked"))

    await mgr.stop_all()
