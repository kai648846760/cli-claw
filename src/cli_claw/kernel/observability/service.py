from __future__ import annotations

from cli_claw.schemas.events import RuntimeEvent


class ObservabilityKernel:
    def __init__(self) -> None:
        self._events: list[RuntimeEvent] = []

    def emit(self, event: RuntimeEvent) -> None:
        self._events.append(event)

    def list_events(self) -> list[RuntimeEvent]:
        return list(self._events)
