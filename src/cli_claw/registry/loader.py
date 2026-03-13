from __future__ import annotations

import importlib
from typing import Any, Callable

from cli_claw.config.schema import ChannelConfig, CliClawConfig, ProviderConfig
from cli_claw.registry.channels import DEFAULT_CHANNEL_CLASSES
from cli_claw.schemas.provider import ProviderSpec


ChannelFactory = Callable[[], Any]


def import_symbol(path: str) -> Any:
    if ":" in path:
        module_path, symbol = path.split(":", 1)
    else:
        module_path, symbol = path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, symbol)


def apply_settings(target: Any, settings: dict[str, Any]) -> None:
    if not settings:
        return
    for key, value in settings.items():
        if hasattr(target, key):
            current = getattr(target, key)
            if isinstance(current, dict) and isinstance(value, dict):
                current.update(value)
            else:
                setattr(target, key, value)


def resolve_channel_class(name: str, channel_cfg: ChannelConfig) -> Any:
    class_path = channel_cfg.channel_class or DEFAULT_CHANNEL_CLASSES.get(name)
    if not class_path:
        raise ValueError(f"No channel class configured for {name}")
    return import_symbol(class_path)


def build_channel_factory(name: str, channel_cfg: ChannelConfig) -> ChannelFactory:
    cls = resolve_channel_class(name, channel_cfg)

    def _factory() -> Any:
        instance = cls()
        if hasattr(instance, "config"):
            apply_settings(instance.config, channel_cfg.settings)
        else:
            apply_settings(instance, channel_cfg.settings)
        return instance

    return _factory


def build_provider_spec(provider_cfg: ProviderConfig) -> ProviderSpec:
    if not provider_cfg.command:
        raise ValueError(f"Provider '{provider_cfg.id}' missing command; set command/args or use a preset id")
    return ProviderSpec(
        id=provider_cfg.id,
        transport=provider_cfg.transport,
        protocol=provider_cfg.protocol,
        command=provider_cfg.command,
        args=provider_cfg.args,
        env=provider_cfg.env,
        capabilities=provider_cfg.capabilities,
        metadata=provider_cfg.metadata,
        adapter=provider_cfg.adapter,
    )


def enabled_channels(config: CliClawConfig) -> list[ChannelConfig]:
    return [item for item in config.channels if item.enabled]


def enabled_providers(config: CliClawConfig) -> list[ProviderConfig]:
    return [item for item in config.providers if item.enabled]
