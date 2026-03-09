from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SessionState:
    logical_session_id: str
    provider: str
    provider_session_id: str | None = None
    metadata: dict = field(default_factory=dict)


class SessionKernel:
    def __init__(self) -> None:
        self._states: dict[str, SessionState] = {}

    def get_or_create(self, logical_session_id: str, provider: str) -> SessionState:
        if logical_session_id not in self._states:
            self._states[logical_session_id] = SessionState(logical_session_id=logical_session_id, provider=provider)
        return self._states[logical_session_id]

    def clear(self, logical_session_id: str) -> bool:
        return self._states.pop(logical_session_id, None) is not None
