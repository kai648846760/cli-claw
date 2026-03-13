from __future__ import annotations

from cli_claw.providers.generic.cli_provider import GenericCliProvider
from cli_claw.schemas.provider import ProviderSpec


class IflowProvider(GenericCliProvider):
    def __init__(self, spec: ProviderSpec):
        super().__init__(spec)
