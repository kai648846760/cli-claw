from __future__ import annotations

from cli_claw.kernel.compression.service import CompressionPolicyEngine
from cli_claw.kernel.observability.service import ObservabilityKernel
from cli_claw.kernel.session.service import SessionKernel
from cli_claw.kernel.transcript.service import TranscriptKernel
from cli_claw.registry.providers import ProviderRegistry
from cli_claw.schemas.channel import InboundEnvelope, OutboundEnvelope
from cli_claw.schemas.events import EventType, RuntimeEvent
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

    async def handle_inbound(
        self,
        provider_id: str,
        logical_session_id: str,
        inbound: InboundEnvelope,
    ) -> OutboundEnvelope:
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
                    channel=inbound.channel,
                    chat_id=inbound.chat_id,
                )
            )

        self.transcript.append(
            TranscriptRecord(
                logical_session_id=logical_session_id,
                provider_session_id=state.provider_session_id,
                provider=provider_id,
                channel=inbound.channel,
                chat_id=inbound.chat_id,
                message_id=inbound.message_id,
                reply_to_id=inbound.reply_to_id,
                role="user",
                kind="final",
                content=inbound.text,
                attachments=[a.model_dump() for a in inbound.attachments],
                meta=inbound.metadata,
            )
        )

        history_size = sum(len(r.content) for r in self.transcript.list_for_session(logical_session_id)) // 4
        decision = self.compression.decide(history_size)
        self.observability.emit(
            RuntimeEvent(
                type=EventType.COMPRESSION_TRIGGERED if decision.action != "noop" else EventType.COMPRESSION_SKIPPED,
                provider=provider_id,
                logical_session_id=logical_session_id,
                provider_session_id=state.provider_session_id,
                channel=inbound.channel,
                chat_id=inbound.chat_id,
                payload={"action": decision.action, "reason": decision.reason},
            )
        )

        content = await provider.chat(logical_session_id, inbound.text)

        outbound = OutboundEnvelope(
            channel=inbound.channel,
            chat_id=inbound.chat_id,
            text=content,
            kind="text",
            reply_to_id=inbound.message_id,
            metadata={"provider": provider_id, "logical_session_id": logical_session_id},
        )

        self.transcript.append(
            TranscriptRecord(
                logical_session_id=logical_session_id,
                provider_session_id=state.provider_session_id,
                provider=provider_id,
                channel=outbound.channel,
                chat_id=outbound.chat_id,
                message_id=outbound.message_id,
                reply_to_id=outbound.reply_to_id,
                role="assistant",
                kind="final",
                content=outbound.text,
                attachments=[a.model_dump() for a in outbound.attachments],
                meta=outbound.metadata,
            )
        )

        self.observability.emit(
            RuntimeEvent(
                type=EventType.MESSAGE_FINAL,
                provider=provider_id,
                logical_session_id=logical_session_id,
                provider_session_id=state.provider_session_id,
                channel=outbound.channel,
                chat_id=outbound.chat_id,
                message_id=outbound.message_id,
                payload={"text": outbound.text, "reply_to_id": outbound.reply_to_id},
            )
        )
        return outbound
