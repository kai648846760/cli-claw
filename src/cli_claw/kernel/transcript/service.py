from __future__ import annotations

import json
from pathlib import Path

from cli_claw.schemas.transcript import TranscriptRecord


class TranscriptKernel:
    def __init__(self) -> None:
        self._records: list[TranscriptRecord] = []
        self._log_path: Path | None = None

    def set_log_path(self, path: str | Path | None) -> None:
        self._log_path = Path(path).expanduser() if path else None
        if self._log_path:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, record: TranscriptRecord) -> None:
        self._records.append(record)
        if self._log_path:
            with self._log_path.open("a", encoding="utf-8") as handle:
                handle.write(f"{json.dumps(record.model_dump(), ensure_ascii=False)}\n")

    def list_for_session(self, logical_session_id: str) -> list[TranscriptRecord]:
        return [r for r in self._records if r.logical_session_id == logical_session_id]

    def clear(self, logical_session_id: str) -> int:
        before = len(self._records)
        self._records = [r for r in self._records if r.logical_session_id != logical_session_id]
        return before - len(self._records)
