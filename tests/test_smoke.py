import pytest

from cli_claw.runtime.orchestrator import RuntimeOrchestrator
from cli_claw.schemas.events import MessageInput
from cli_claw.schemas.provider import CapabilityMap, ProviderSpec


@pytest.mark.asyncio
async def test_runtime_orchestrator_smoke_iflow():
    rt = RuntimeOrchestrator()
    rt.register_provider(
        ProviderSpec(
            id="iflow",
            command="iflow",
            args=["--experimental-acp"],
            capabilities=CapabilityMap(streaming=True, sessions=True),
        )
    )
    out = await rt.handle_message("iflow", "s1", MessageInput(channel="cli", chat_id="d1", content="hello"))
    assert out.content == "iflow:hello"
    assert rt.providers.list() == ["iflow"]
    assert len(rt.transcript.list_for_session("s1")) == 2


@pytest.mark.asyncio
async def test_runtime_orchestrator_smoke_qwen():
    rt = RuntimeOrchestrator()
    rt.register_provider(
        ProviderSpec(
            id="qwen",
            command="qwen",
            args=["--acp"],
            capabilities=CapabilityMap(streaming=True, sessions=True),
        )
    )
    out = await rt.handle_message("qwen", "s2", MessageInput(channel="cli", chat_id="d2", content="hello"))
    assert out.content == "qwen:hello"
