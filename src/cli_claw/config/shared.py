from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SharedSkillsConfig:
    skills_dir: Path
    paths: list[Path] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SharedMcpConfig:
    servers: list[dict[str, Any]] = field(default_factory=list)


def load_shared_skills(root: Path, auto_create: bool = True) -> SharedSkillsConfig:
    root = root.expanduser()
    skills_dir = (root / "skills").expanduser()
    skills_dir.mkdir(parents=True, exist_ok=True)
    _ = auto_create
    return SharedSkillsConfig(skills_dir=skills_dir, paths=[skills_dir], env={})


def _read_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except json.JSONDecodeError:
        return None


def load_shared_mcp(root: Path, auto_create: bool = True) -> SharedMcpConfig:
    root = root.expanduser()
    cfg_path = root / "mcp.json"
    if not cfg_path.exists() and auto_create:
        cfg_path.write_text(json.dumps({"servers": []}, ensure_ascii=False, indent=2))

    data = _read_json(cfg_path)
    servers: list[dict[str, Any]] = []

    if isinstance(data, dict):
        raw = data.get("servers") or []
        if isinstance(raw, list):
            servers = [item for item in raw if isinstance(item, dict)]
    elif isinstance(data, list):
        servers = [item for item in data if isinstance(item, dict)]

    return SharedMcpConfig(servers=servers)
