from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from cli_claw.config.schema import CliClawConfig


_PROVIDER_PRESETS = {
    "iflow": {
        "adapter": "cli_claw.providers.iflow.provider.IflowProvider",
        "command": "iflow",
        "args": ["--experimental-acp", "--stream"],
        "capabilities": {"streaming": True, "sessions": True, "tools": True, "model_switch": True},
    },
    "qwen": {
        "adapter": "cli_claw.providers.qwen.provider.QwenProvider",
        "command": "qwen",
        "args": ["--acp"],
        "capabilities": {"streaming": True, "sessions": True, "tools": True, "model_switch": True},
    },
    "opencode": {
        "adapter": "cli_claw.providers.generic.cli_provider.GenericCliProvider",
        "command": "opencode",
        "args": ["acp"],
        "capabilities": {"streaming": True, "sessions": True, "tools": True, "model_switch": True},
    },
    "claude": {
        "adapter": "cli_claw.providers.generic.cli_provider.GenericCliProvider",
        "command": "claude",
        "args": [],
        "capabilities": {"streaming": True, "sessions": True, "tools": True, "model_switch": True},
    },
    "codex": {
        "adapter": "cli_claw.providers.generic.cli_provider.GenericCliProvider",
        "command": "codex",
        "args": [],
        "capabilities": {"streaming": True, "sessions": True, "tools": True, "model_switch": True},
    },
    "goose": {
        "adapter": "cli_claw.providers.generic.cli_provider.GenericCliProvider",
        "command": "goose",
        "args": ["acp"],
        "capabilities": {"streaming": True, "sessions": True, "tools": True, "model_switch": True},
    },
    "auggie": {
        "adapter": "cli_claw.providers.generic.cli_provider.GenericCliProvider",
        "command": "auggie",
        "args": ["--acp"],
        "capabilities": {"streaming": True, "sessions": True, "tools": True, "model_switch": True},
    },
    "kimi": {
        "adapter": "cli_claw.providers.generic.cli_provider.GenericCliProvider",
        "command": "kimi",
        "args": ["--acp"],
        "capabilities": {"streaming": True, "sessions": True, "tools": True, "model_switch": True},
    },
    "droid": {
        "adapter": "cli_claw.providers.generic.cli_provider.GenericCliProvider",
        "command": "droid",
        "args": ["exec", "--output-format", "acp"],
        "capabilities": {"streaming": True, "sessions": True, "tools": True, "model_switch": True},
    },
    "codebuddy": {
        "adapter": "cli_claw.providers.generic.cli_provider.GenericCliProvider",
        "command": "codebuddy",
        "args": ["--acp"],
        "capabilities": {"streaming": True, "sessions": True, "tools": True, "model_switch": True},
    },
    "copilot": {
        "adapter": "cli_claw.providers.generic.cli_provider.GenericCliProvider",
        "command": "copilot",
        "args": ["--acp", "--stdio"],
        "capabilities": {"streaming": True, "sessions": True, "tools": True, "model_switch": True},
    },
    "qodercli": {
        "adapter": "cli_claw.providers.generic.cli_provider.GenericCliProvider",
        "command": "qodercli",
        "args": ["--acp"],
        "capabilities": {"streaming": True, "sessions": True, "tools": True, "model_switch": True},
    },
    "vibe-acp": {
        "adapter": "cli_claw.providers.generic.cli_provider.GenericCliProvider",
        "command": "vibe-acp",
        "args": [],
        "capabilities": {"streaming": True, "sessions": True, "tools": True, "model_switch": True},
    },
    "nanobot": {
        "adapter": "cli_claw.providers.generic.cli_provider.GenericCliProvider",
        "command": "nanobot",
        "args": [],
        "capabilities": {"streaming": True, "sessions": True, "tools": True, "model_switch": True},
    },
    "openclaw": {
        "adapter": "cli_claw.providers.generic.cli_provider.GenericCliProvider",
        "command": "openclaw",
        "args": ["gateway"],
        "capabilities": {"streaming": True, "sessions": True, "tools": True, "model_switch": True},
    },
}


_DEFAULT_CONFIG = {
    "providers": [
        {
            "id": "iflow",
            "enabled": False,
            "adapter": "cli_claw.providers.iflow.provider.IflowProvider",
            "transport": "stdio",
            "protocol": "acp",
            "command": "iflow",
            "args": ["--experimental-acp", "--stream"],
            "capabilities": {
                "streaming": True,
                "sessions": True,
                "tools": True,
                "model_switch": True,
            },
        },
        {
            "id": "qwen",
            "enabled": False,
            "adapter": "cli_claw.providers.qwen.provider.QwenProvider",
            "transport": "stdio",
            "protocol": "acp",
            "command": "qwen",
            "args": ["--acp"],
            "capabilities": {
                "streaming": True,
                "sessions": True,
                "tools": True,
                "model_switch": True,
            },
        },
        {
            "id": "opencode",
            "enabled": False,
            "adapter": "cli_claw.providers.generic.cli_provider.GenericCliProvider",
            "transport": "stdio",
            "protocol": "acp",
            "command": "opencode",
            "args": ["acp"],
            "capabilities": {
                "streaming": True,
                "sessions": True,
                "tools": True,
                "model_switch": True,
            },
        },
        {
            "id": "claude",
            "enabled": False,
            "adapter": "cli_claw.providers.generic.cli_provider.GenericCliProvider",
            "transport": "stdio",
            "protocol": "acp",
            "command": "claude",
            "args": [],
            "capabilities": {
                "streaming": True,
                "sessions": True,
                "tools": True,
                "model_switch": True,
            },
        },
        {
            "id": "codex",
            "enabled": False,
            "adapter": "cli_claw.providers.generic.cli_provider.GenericCliProvider",
            "transport": "stdio",
            "protocol": "acp",
            "command": "codex",
            "args": [],
            "capabilities": {
                "streaming": True,
                "sessions": True,
                "tools": True,
                "model_switch": True,
            },
        },
        {
            "id": "goose",
            "enabled": False,
            "adapter": "cli_claw.providers.generic.cli_provider.GenericCliProvider",
            "transport": "stdio",
            "protocol": "acp",
            "command": "goose",
            "args": ["acp"],
            "capabilities": {
                "streaming": True,
                "sessions": True,
                "tools": True,
                "model_switch": True,
            },
        },
        {
            "id": "auggie",
            "enabled": False,
            "adapter": "cli_claw.providers.generic.cli_provider.GenericCliProvider",
            "transport": "stdio",
            "protocol": "acp",
            "command": "auggie",
            "args": ["--acp"],
            "capabilities": {
                "streaming": True,
                "sessions": True,
                "tools": True,
                "model_switch": True,
            },
        },
        {
            "id": "kimi",
            "enabled": False,
            "adapter": "cli_claw.providers.generic.cli_provider.GenericCliProvider",
            "transport": "stdio",
            "protocol": "acp",
            "command": "kimi",
            "args": ["--acp"],
            "capabilities": {
                "streaming": True,
                "sessions": True,
                "tools": True,
                "model_switch": True,
            },
        },
        {
            "id": "droid",
            "enabled": False,
            "adapter": "cli_claw.providers.generic.cli_provider.GenericCliProvider",
            "transport": "stdio",
            "protocol": "acp",
            "command": "droid",
            "args": ["exec", "--output-format", "acp"],
            "capabilities": {
                "streaming": True,
                "sessions": True,
                "tools": True,
                "model_switch": True,
            },
        },
        {
            "id": "codebuddy",
            "enabled": False,
            "adapter": "cli_claw.providers.generic.cli_provider.GenericCliProvider",
            "transport": "stdio",
            "protocol": "acp",
            "command": "codebuddy",
            "args": ["--acp"],
            "capabilities": {
                "streaming": True,
                "sessions": True,
                "tools": True,
                "model_switch": True,
            },
        },
        {
            "id": "copilot",
            "enabled": False,
            "adapter": "cli_claw.providers.generic.cli_provider.GenericCliProvider",
            "transport": "stdio",
            "protocol": "acp",
            "command": "copilot",
            "args": ["--acp", "--stdio"],
            "capabilities": {
                "streaming": True,
                "sessions": True,
                "tools": True,
                "model_switch": True,
            },
        },
        {
            "id": "qodercli",
            "enabled": False,
            "adapter": "cli_claw.providers.generic.cli_provider.GenericCliProvider",
            "transport": "stdio",
            "protocol": "acp",
            "command": "qodercli",
            "args": ["--acp"],
            "capabilities": {
                "streaming": True,
                "sessions": True,
                "tools": True,
                "model_switch": True,
            },
        },
        {
            "id": "vibe-acp",
            "enabled": False,
            "adapter": "cli_claw.providers.generic.cli_provider.GenericCliProvider",
            "transport": "stdio",
            "protocol": "acp",
            "command": "vibe-acp",
            "args": [],
            "capabilities": {
                "streaming": True,
                "sessions": True,
                "tools": True,
                "model_switch": True,
            },
        },
        {
            "id": "nanobot",
            "enabled": False,
            "adapter": "cli_claw.providers.generic.cli_provider.GenericCliProvider",
            "transport": "stdio",
            "protocol": "acp",
            "command": "nanobot",
            "args": [],
            "capabilities": {
                "streaming": True,
                "sessions": True,
                "tools": True,
                "model_switch": True,
            },
        },
        {
            "id": "openclaw",
            "enabled": False,
            "adapter": "cli_claw.providers.generic.cli_provider.GenericCliProvider",
            "transport": "stdio",
            "protocol": "acp",
            "command": "openclaw",
            "args": ["gateway"],
            "capabilities": {
                "streaming": True,
                "sessions": True,
                "tools": True,
                "model_switch": True,
            },
        },
    ],
    "channels": [
        {
            "name": "feishu",
            "enabled": False,
            "channel_class": "cli_claw.channels.feishu.FeishuChannel",
            "provider": "iflow",
            "settings": {},
        },
        {
            "name": "telegram",
            "enabled": False,
            "channel_class": "cli_claw.channels.telegram.TelegramChannel",
            "provider": "iflow",
            "settings": {},
        },
        {
            "name": "slack",
            "enabled": False,
            "channel_class": "cli_claw.channels.slack.SlackChannel",
            "provider": "iflow",
            "settings": {},
        },
        {
            "name": "discord",
            "enabled": False,
            "channel_class": "cli_claw.channels.discord.DiscordChannel",
            "provider": "iflow",
            "settings": {},
        },
        {
            "name": "dingtalk",
            "enabled": False,
            "channel_class": "cli_claw.channels.dingtalk.DingTalkChannel",
            "provider": "iflow",
            "settings": {},
        },
        {
            "name": "qq",
            "enabled": False,
            "channel_class": "cli_claw.channels.qq.QQChannel",
            "provider": "iflow",
            "settings": {},
        },
        {
            "name": "whatsapp",
            "enabled": False,
            "channel_class": "cli_claw.channels.whatsapp.WhatsAppChannel",
            "provider": "iflow",
            "settings": {},
        },
        {
            "name": "email",
            "enabled": False,
            "channel_class": "cli_claw.channels.email.EmailChannel",
            "provider": "iflow",
            "settings": {},
        },
        {
            "name": "mochat",
            "enabled": False,
            "channel_class": "cli_claw.channels.mochat.MochatChannel",
            "provider": "iflow",
            "settings": {},
        },
    ],
    "runtime": {
        "default_provider": "iflow",
        "streaming": True,
        "compression_trigger_tokens": 60000,
        "workspace": str(Path.home() / ".cli-claw" / "workspace"),
        "log_dir": str(Path.home() / ".cli-claw" / "logs"),
        "model": None,
        "thinking": False,
        "permission_mode": "yolo",
        "timeout": 600,
        "compression_provider": None,
        "compression_prompt": "Summarize the conversation so far with key decisions and context.",
        "heartbeat_enabled": False,
        "heartbeat_interval_seconds": 60,
        "schedules": [],
    },
}


def get_config_dir() -> Path:
    return Path.home() / ".cli-claw"


def get_config_path() -> Path:
    return get_config_dir() / "config.json"


def _write_default_config(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_DEFAULT_CONFIG, ensure_ascii=False, indent=2))


def load_config(config_path: Path | None = None, auto_create: bool = True) -> CliClawConfig:
    path = config_path or get_config_path()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            data = _apply_provider_presets(data)
            return CliClawConfig(**data)
        except (json.JSONDecodeError, ValidationError):
            pass
    if auto_create:
        _write_default_config(path)
    return CliClawConfig(**_DEFAULT_CONFIG)


def _apply_provider_presets(data: dict[str, Any]) -> dict[str, Any]:
    providers = data.get("providers")
    if not isinstance(providers, list):
        return data

    for provider in providers:
        if not isinstance(provider, dict):
            continue
        provider_id = provider.get("id")
        if not provider_id:
            continue
        preset = _PROVIDER_PRESETS.get(str(provider_id))
        if not preset:
            continue
        if not provider.get("command"):
            provider["command"] = preset.get("command")
        if not provider.get("args"):
            provider["args"] = preset.get("args", [])
        if not provider.get("adapter"):
            provider["adapter"] = preset.get("adapter")
        if not provider.get("transport"):
            provider["transport"] = preset.get("transport", "stdio")
        if not provider.get("protocol"):
            provider["protocol"] = preset.get("protocol", "acp")
        if not provider.get("capabilities"):
            provider["capabilities"] = preset.get("capabilities", {})

    return data


def dump_config(config: CliClawConfig, path: Path | None = None) -> None:
    target = path or get_config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = config.model_dump()
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
