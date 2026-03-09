from __future__ import annotations


class McpKernel:
    def __init__(self) -> None:
        self._servers: dict[str, dict] = {}

    def register(self, name: str, config: dict) -> None:
        self._servers[name] = config

    def list_servers(self) -> dict[str, dict]:
        return dict(self._servers)
