from __future__ import annotations


class SkillKernel:
    def __init__(self) -> None:
        self._enabled: set[str] = set()

    def enable(self, name: str) -> None:
        self._enabled.add(name)

    def list_enabled(self) -> list[str]:
        return sorted(self._enabled)
