from __future__ import annotations

from collections.abc import AsyncIterator

from cli_claw.bridges.acp.bridge import AcpBridge
from cli_claw.providers.base.provider import ProviderAdapter
from cli_claw.schemas.events import EventType, RuntimeEvent
from cli_claw.schemas.provider import ProviderSpec


class QwenProvider(ProviderAdapter):
    def __init__(self, spec: ProviderSpec):
        super().__init__(spec)
        self.bridge = AcpBridge(spec.command, spec.args)
        self._sessions: dict[str, str] = {}

    async def start(self) -> None:
        await self.bridge.start()

    async def stop(self) -> None:
        await self.bridge.stop()

    async def health_check(self) -> bool:
        return True

    async def new_session(self, logical_session_id: str) -> str:
        provider_session_id = f"qwen:{logical_session_id}"
        self._sessions[logical_session_id] = provider_session_id
        return provider_session_id

    async def clear_session(self, logical_session_id: str, provider_session_id: str | None = None) -> bool:
        _ = provider_session_id
        return self._sessions.pop(logical_session_id, None) is not None

    async def chat(self, logical_session_id: str, message: str) -> str:
        _ = logical_session_id
        return f"qwen:{message}"

    async def chat_stream(self, logical_session_id: str, message: str) -> AsyncIterator[RuntimeEvent]:
        yield RuntimeEvent(
            type=EventType.MESSAGE_FINAL,
            provider=self.spec.id,
            logical_session_id=logical_session_id,
            payload={"content": f"qwen:{message}"},
        )

    async def list_models(self) -> list[str]:
        return ["qwen/default"]
