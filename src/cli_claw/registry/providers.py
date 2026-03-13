from __future__ import annotations

from cli_claw.providers.iflow.provider import IflowProvider
from cli_claw.providers.qwen.provider import QwenProvider
from cli_claw.registry.loader import import_symbol
from cli_claw.schemas.provider import ProviderSpec


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers = {}

    def register(self, spec: ProviderSpec):
        if spec.adapter:
            adapter_cls = import_symbol(spec.adapter)
            self._providers[spec.id] = adapter_cls(spec)
            return
        if spec.id == "iflow":
            self._providers[spec.id] = IflowProvider(spec)
            return
        if spec.id == "qwen":
            self._providers[spec.id] = QwenProvider(spec)
            return
        raise ValueError(f"Unsupported provider: {spec.id}")

    def get(self, provider_id: str):
        return self._providers[provider_id]

    def list(self) -> list[str]:
        return sorted(self._providers.keys())
