from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CommandDefinition:
    command: str
    description: str | None = None
    usage: str | None = None
    mode: str = "prompt"  # prompt | reply
    prompt: str | None = None
    response: str | None = None
    aliases: list[str] = field(default_factory=list)
    allow_empty_args: bool = True


class CommandRegistry:
    def __init__(self, skills_dir: Path | None = None) -> None:
        self._commands: dict[str, CommandDefinition] = {}
        self._skills_dir: Path | None = skills_dir

    def set_skills_dir(self, skills_dir: Path | None) -> None:
        self._skills_dir = skills_dir

    def refresh(self) -> None:
        self._commands.clear()
        if not self._skills_dir:
            return
        skills_dir = self._skills_dir.expanduser()
        if not skills_dir.exists():
            return
        for entry in skills_dir.iterdir():
            if not entry.is_dir():
                continue
            self._load_from_skill(entry / "SKILL.md", entry.name)
            self._load_from_file(entry / "command.json")
            self._load_from_file(entry / "commands.json")

    def list_commands(self) -> list[CommandDefinition]:
        unique: dict[str, CommandDefinition] = {}
        for cmd, definition in self._commands.items():
            unique.setdefault(definition.command, definition)
        return sorted(unique.values(), key=lambda item: item.command)

    def get(self, command: str) -> CommandDefinition | None:
        return self._commands.get(command.lower())

    def _load_from_file(self, path: Path) -> None:
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return

        if isinstance(data, dict) and isinstance(data.get("commands"), list):
            items = data.get("commands")
        elif isinstance(data, list):
            items = data
        else:
            items = [data]

        for raw in items:
            if not isinstance(raw, dict):
                continue
            command = raw.get("command") or raw.get("name")
            if not isinstance(command, str) or not command.strip():
                continue
            command = command.strip()
            if not command.startswith("/"):
                command = "/" + command
            aliases = raw.get("aliases") or []
            if isinstance(aliases, str):
                aliases = [aliases]
            if not isinstance(aliases, list):
                aliases = []
            cleaned_aliases = []
            for alias in aliases:
                if not isinstance(alias, str):
                    continue
                alias = alias.strip()
                if not alias:
                    continue
                if not alias.startswith("/"):
                    alias = "/" + alias
                cleaned_aliases.append(alias)

            definition = CommandDefinition(
                command=command,
                description=raw.get("description"),
                usage=raw.get("usage"),
                mode=str(raw.get("mode") or raw.get("action") or ("reply" if raw.get("response") else "prompt")),
                prompt=raw.get("prompt"),
                response=raw.get("response"),
                aliases=cleaned_aliases,
                allow_empty_args=bool(raw.get("allow_empty_args", True)),
            )
            self._register(definition)

    def _register(self, definition: CommandDefinition) -> None:
        self._commands[definition.command.lower()] = definition
        for alias in definition.aliases:
            self._commands[alias.lower()] = definition

    def _load_from_skill(self, path: Path, fallback_name: str) -> None:
        if not path.exists():
            return
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return
        frontmatter = self._parse_frontmatter(text)
        if not frontmatter:
            return
        name = frontmatter.get("name") or fallback_name
        if not isinstance(name, str) or not name.strip():
            return
        name = name.strip()
        description = frontmatter.get("description")
        if not isinstance(description, str):
            description = None
        command = f"/{name}" if not name.startswith("/") else name
        usage = f"{command} <task>"
        prompt = (
            f"Use the `{name}` skill. "
            f"If you can access local files, read `{path}` for instructions. "
            f"Task: {{args}}"
        )
        aliases = []
        if fallback_name and fallback_name != name:
            alias = f"/{fallback_name}" if not fallback_name.startswith("/") else fallback_name
            aliases.append(alias)
        definition = CommandDefinition(
            command=command,
            description=description,
            usage=usage,
            mode="prompt",
            prompt=prompt,
            aliases=aliases,
            allow_empty_args=False,
        )
        self._register(definition)

    @staticmethod
    def _parse_frontmatter(text: str) -> dict[str, str] | None:
        lines = text.splitlines()
        if not lines or lines[0].strip() != "---":
            return None
        data: dict[str, str] = {}
        for line in lines[1:]:
            if line.strip() == "---":
                break
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            if line.startswith(" ") or line.startswith("\t"):
                # ignore nested yaml blocks
                continue
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and value:
                data[key] = value
        return data if data else None
