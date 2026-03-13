from pathlib import Path

from cli_claw.config.loader import load_config


def _write_config(path: Path, payload: dict) -> None:
    path.write_text(__import__("json").dumps(payload), encoding="utf-8")


def test_provider_presets_fill_command(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    _write_config(
        config_path,
        {
            "providers": [
                {"id": "iflow", "enabled": True},
                {"id": "qwen", "enabled": True},
                {"id": "opencode", "enabled": True},
            ],
            "channels": [],
            "runtime": {},
        },
    )

    cfg = load_config(config_path, auto_create=False)
    providers = {item.id: item for item in cfg.providers}

    assert providers["iflow"].command == "iflow"
    assert "--experimental-acp" in providers["iflow"].args
    assert providers["qwen"].command == "qwen"
    assert providers["opencode"].command == "opencode"


def test_provider_presets_do_not_override_explicit_command(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    _write_config(
        config_path,
        {
            "providers": [
                {"id": "iflow", "enabled": True, "command": "iflow-custom", "args": ["--x"]},
            ],
            "channels": [],
            "runtime": {},
        },
    )

    cfg = load_config(config_path, auto_create=False)
    provider = cfg.providers[0]
    assert provider.command == "iflow-custom"
    assert provider.args == ["--x"]
