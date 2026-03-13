from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4


@dataclass
class SessionState:
    logical_session_id: str
    provider: str
    provider_session_id: str | None = None
    metadata: dict = field(default_factory=dict)


class SessionKernel:
    def __init__(self) -> None:
        self._states: dict[str, SessionState] = {}
        self._channel_bindings: dict[str, str] = {}

    def resolve_logical_session_id(
        self,
        provider: str,
        channel: str,
        chat_id: str,
        thread_id: str | None = None,
    ) -> str:
        binding_key = self._binding_key(provider=provider, channel=channel, chat_id=chat_id, thread_id=thread_id)
        logical_session_id = self._channel_bindings.get(binding_key)
        if logical_session_id is None:
            logical_session_id = f"{provider}:{channel}:{chat_id}:{thread_id or 'root'}:{uuid4().hex[:8]}"
            self._channel_bindings[binding_key] = logical_session_id
        return logical_session_id

    def get_or_create(self, logical_session_id: str, provider: str) -> SessionState:
        if logical_session_id not in self._states:
            self._states[logical_session_id] = SessionState(logical_session_id=logical_session_id, provider=provider)
        return self._states[logical_session_id]

    def clear(self, logical_session_id: str) -> bool:
        cleared = self._states.pop(logical_session_id, None) is not None
        stale_bindings = [key for key, value in self._channel_bindings.items() if value == logical_session_id]
        for key in stale_bindings:
            self._channel_bindings.pop(key, None)
        return cleared

    def reset_binding(self, provider: str, channel: str, chat_id: str, thread_id: str | None = None) -> None:
        binding_key = self._binding_key(provider=provider, channel=channel, chat_id=chat_id, thread_id=thread_id)
        logical_session_id = self._channel_bindings.pop(binding_key, None)
        if logical_session_id:
            self._states.pop(logical_session_id, None)

    @staticmethod
    def _binding_key(provider: str, channel: str, chat_id: str, thread_id: str | None) -> str:
        return "::".join([provider, channel, chat_id, thread_id or "root"])
