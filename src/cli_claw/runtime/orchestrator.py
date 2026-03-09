from __future__ import annotations

from cli_claw.kernel.compression.service import CompressionPolicyEngine
from cli_claw.kernel.observability.service import ObservabilityKernel
from cli_claw.kernel.session.service import SessionKernel
from cli_claw.kernel.transcript.service import TranscriptKernel
from cli_claw.registry.providers import ProviderRegistry
from cli_claw.schemas.events import EventType, MessageInput, MessageOutput, RuntimeEvent
from cli_claw.schemas.provider import ProviderSpec
from cli_claw.schemas.transcript import TranscriptRecord


class RuntimeOrchestrator:
    def __init__(self) -> None:
        self.sessions = SessionKernel()
        self.transcript = TranscriptKernel()
        self.compression = CompressionPolicyEngine()
        self.observability = ObservabilityKernel()
        self.providers = ProviderRegistry()

    def register_provider(self, spec: ProviderSpec) -> None:
        self.providers.register(spec)

    async def handle_message(self, provider_id: str, logical_session_id: str, message: MessageInput) -> MessageOutput:
        state = self.sessions.get_or_create(logical_session_id, provider_id)
        provider = self.providers.get(provider_id)
        if state.provider_session_id is None:
            state.provider_session_id = await provider.new_session(logical_session_id)
            self.observability.emit(
                RuntimeEvent(
                    type=EventType.SESSION_CREATED,
                    provider=provider_id,
                    logical_session_id=logical_session_id,
                    provider_session_id=state.provider_session_id,
                )
            )

        self.transcript.append(
            TranscriptRecord(
                logical_session_id=logical_session_id,
                provider_session_id=state.provider_session_id,
                provider=provider_id,
                channel=message.channel,
                chat_id=message.chat_id,
                role="user",
                kind="final",
                content=message.content,
                attachments=message.attachments,
                meta=message.metadata,
            )
        )

        decision = self.compression.decide(sum(len(r.content) for r in self.transcript.list_for_session(logical_session_id)) // 4)
        self.observability.emit(
            RuntimeEvent(
                type=EventType.COMPRESSION_TRIGGERED if decision.action != 'noop' else EventType.COMPRESSION_SKIPPED,
                provider=provider_id,
                logical_session_id=logical_session_id,
                provider_session_id=state.provider_session_id,
                payload={"action": decision.action, "reason": decision.reason},
            )
        )

        content = await provider.chat(logical_session_id, message.content)
        self.transcript.append(
            TranscriptRecord(
                logical_session_id=logical_session_id,
                provider_session_id=state.provider_session_id,
                provider=provider_id,
                channel=message.channel,
                chat_id=message.chat_id,
                role="assistant",
                kind="final",
                content=content,
            )
        )
        self.observability.emit(
            RuntimeEvent(
                type=EventType.MESSAGE_FINAL,
                provider=provider_id,
                logical_session_id=logical_session_id,
                provider_session_id=state.provider_session_id,
                channel=message.channel,
                chat_id=message.chat_id,
                payload={"content": content},
            )
        )
        return MessageOutput(content=content)
