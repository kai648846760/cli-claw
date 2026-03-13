import pytest

from cli_claw.runtime.orchestrator import RuntimeOrchestrator
from cli_claw.schemas.channel import InboundEnvelope


class _FakeProvider:
    def __init__(self, prefix: str):
        self.prefix = prefix

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
        _ = logical_session_id
        return f"{self.prefix}:{message}"

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
async def test_runtime_orchestrator_smoke_iflow():
    rt = RuntimeOrchestrator()
    rt.providers = _FakeRegistry(_FakeProvider("iflow"))
    out = await rt.handle_inbound("iflow", "s1", InboundEnvelope(channel="cli", chat_id="d1", text="hello"))
    assert out.text == "iflow:hello"
    assert out.reply_to_id is None
    assert len(rt.transcript.list_for_session("s1")) == 2


@pytest.mark.asyncio
async def test_runtime_orchestrator_smoke_qwen():
    rt = RuntimeOrchestrator()
    rt.providers = _FakeRegistry(_FakeProvider("qwen"))
    out = await rt.handle_inbound("qwen", "s2", InboundEnvelope(channel="cli", chat_id="d2", text="hello"))
    assert out.text == "qwen:hello"
