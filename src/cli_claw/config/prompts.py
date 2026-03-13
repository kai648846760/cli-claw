from __future__ import annotations

from pathlib import Path
import shutil


_PROMPT_FILES = (
    "AGENTS.md",
    "BOOT.md",
    "BOOTSTRAP.md",
    "HEARTBEAT.md",
    "IDENTITY.md",
    "SOUL.md",
    "TOOLS.md",
    "USER.md",
)


def find_project_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    for parent in [current, *current.parents]:
        if (parent / "pyproject.toml").exists() or (parent / ".git").exists():
            return parent
    return current


def ensure_workspace_templates(workspace_root: Path, project_root: Path | None = None) -> Path:
    workspace_templates = workspace_root.expanduser() / "templates"
    workspace_templates.mkdir(parents=True, exist_ok=True)
    existing = {p.name for p in workspace_templates.iterdir() if p.is_file()}
    if all(name in existing for name in _PROMPT_FILES):
        return workspace_templates
    project_templates = (project_root or find_project_root()) / "templates"
    if project_templates.exists():
        for name in _PROMPT_FILES:
            src = project_templates / name
            dst = workspace_templates / name
            if dst.exists() or not src.exists():
                continue
            try:
                shutil.copyfile(src, dst)
            except OSError:
                continue
    return workspace_templates


def load_project_prompts(root: Path | None = None) -> str | None:
    base = (root or Path.cwd()).expanduser() / "templates"
    parts: list[str] = []
    for name in _PROMPT_FILES:
        path = base / name
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if not text:
            continue
        parts.append(f"# {name}\n{text}")
    if not parts:
        return None
    return "\n\n".join(parts)
