import pytest

from cli_claw.runtime.orchestrator import RuntimeOrchestrator
from cli_claw.schemas.channel import InboundEnvelope


class _FakeProvider:
    def __init__(self):
        self.cleared = False

    async def start(self):
        return None

    async def stop(self):
        return None

    async def health_check(self):
        return True

    async def new_session(self, logical_session_id: str) -> str:
        return f"p:{logical_session_id}"

    async def clear_session(self, logical_session_id: str, provider_session_id: str | None = None) -> bool:
        self.cleared = True
        return True

    async def chat(self, logical_session_id: str, message: str) -> str:
        if message.startswith("Summarize"):
            return "summary"
        return "answer"

    async def chat_stream(self, logical_session_id: str, message: str):
        _ = logical_session_id
        _ = message
        raise NotImplementedError

    async def list_models(self) -> list[str]:
        return []


class _FakeRegistry:
    def __init__(self, provider):
        self._provider = provider

    def get(self, provider_id: str):
        _ = provider_id
        return self._provider


@pytest.mark.asyncio
async def test_runtime_compression_summary_injected():
    provider = _FakeProvider()
    rt = RuntimeOrchestrator()
    rt.providers = _FakeRegistry(provider)
    rt.compression_trigger_tokens = 1

    inbound = InboundEnvelope(channel="cli", chat_id="c1", text="hello")
    out = await rt.handle_inbound("iflow", "s1", inbound)

    assert out.text == "answer"
    state = rt.sessions.get_or_create("s1", "iflow")
    assert state.metadata.get("summary") == "summary"
    assert provider.cleared is True
