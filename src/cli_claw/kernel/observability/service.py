from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path
import time

from cli_claw.schemas.events import RuntimeEvent


class ObservabilityKernel:
    def __init__(self) -> None:
        self._events: list[RuntimeEvent] = []
        self._counters: dict[str, int] = defaultdict(int)
        self._log_path: Path | None = None

    def set_log_path(self, path: Path | None) -> None:
        if not path:
            self._log_path = None
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        self._log_path = path

    def emit(self, event: RuntimeEvent) -> None:
        self._events.append(event)
        self._counters["events_total"] += 1
        self._counters[f"type:{event.type.value}"] += 1
        if event.channel:
            self._counters[f"channel:{event.channel}"] += 1
        if event.provider:
            self._counters[f"provider:{event.provider}"] += 1
        if self._log_path:
            payload = event.model_dump()
            payload["ts"] = time.time()
            with self._log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def list_events(self) -> list[RuntimeEvent]:
        return list(self._events)

    def metrics(self) -> dict[str, int]:
        return dict(self._counters)
