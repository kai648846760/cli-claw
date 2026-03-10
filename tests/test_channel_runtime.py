import pytest

from cli_claw.channels.local import LocalChannel
from cli_claw.channels.manager import ChannelManager
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


class _FakeRegistry:
    def __init__(self, provider: _FakeProvider) -> None:
        self._provider = provider

    def get(self, provider_id: str):
        _ = provider_id
        return self._provider


@pytest.mark.asyncio
async def test_channel_runtime_routes_inbound_to_outbound():
    channel = LocalChannel()
    manager = ChannelManager()
    manager.register("local", lambda: channel)

    orchestrator = RuntimeOrchestrator()
    orchestrator.providers = _FakeRegistry(_FakeProvider())

    runtime = ChannelRuntime(orchestrator, manager)
    runtime.register_route("local", "fake")

    await runtime.start(["local"])
    await channel.inject(InboundEnvelope(channel="local", chat_id="c1", text="hi", message_id="m1"))
    await manager._queue.join()

    assert channel.sent
    assert channel.sent[0].text == "echo:hi"
    assert channel.sent[0].reply_to_id == "m1"

    await runtime.stop()
