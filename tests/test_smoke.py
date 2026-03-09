import pytest

from cli_claw.runtime.orchestrator import RuntimeOrchestrator
from cli_claw.schemas.channel import InboundEnvelope
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
    out = await rt.handle_inbound("iflow", "s1", InboundEnvelope(channel="cli", chat_id="d1", text="hello"))
    assert out.text == "iflow:hello"
    assert out.reply_to_id is None
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
    out = await rt.handle_inbound("qwen", "s2", InboundEnvelope(channel="cli", chat_id="d2", text="hello"))
    assert out.text == "qwen:hello"
