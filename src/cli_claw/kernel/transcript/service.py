from __future__ import annotations

from cli_claw.schemas.transcript import TranscriptRecord


class TranscriptKernel:
    def __init__(self) -> None:
        self._records: list[TranscriptRecord] = []

    def append(self, record: TranscriptRecord) -> None:
        self._records.append(record)

    def list_for_session(self, logical_session_id: str) -> list[TranscriptRecord]:
        return [r for r in self._records if r.logical_session_id == logical_session_id]
