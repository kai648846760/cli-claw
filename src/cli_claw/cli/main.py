from __future__ import annotations

import asyncio
import os
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import typer
from rich import print

from cli_claw.channels.discord import DiscordChannel
from cli_claw.channels.discord_webhook import start_discord_webhook_server
from cli_claw.channels.feishu import FeishuChannel
from cli_claw.channels.feishu_webhook import start_feishu_webhook_server
from cli_claw.channels.manager import ChannelManager
from cli_claw.channels.dingtalk import DingTalkChannel
from cli_claw.channels.email import EmailChannel
from cli_claw.channels.mochat import MochatChannel
from cli_claw.channels.qq import QQChannel
from cli_claw.channels.whatsapp import WhatsAppChannel
from cli_claw.channels.simple_webhook import start_simple_webhook_server
from cli_claw.channels.slack import SlackChannel
from cli_claw.channels.slack_webhook import start_slack_webhook_server
from cli_claw.channels.telegram import TelegramChannel
from cli_claw.channels.telegram_webhook import start_telegram_webhook_server
from cli_claw.config.loader import load_config, dump_config, get_config_dir
from cli_claw.config.shared import load_shared_mcp, load_shared_skills
from cli_claw.config.prompts import load_project_prompts, ensure_workspace_templates
from cli_claw.registry.loader import (
    build_channel_factory,
    build_provider_spec,
    enabled_channels,
    enabled_providers,
)
from cli_claw.kernel.memory.store import MemoryStore
from cli_claw.runtime.orchestrator import RuntimeOrchestrator
from cli_claw.runtime.channel_runtime import ChannelRuntime
from cli_claw.schemas.channel import InboundEnvelope
from cli_claw.schemas.provider import CapabilityMap, ProviderSpec

app = typer.Typer(help="cli-claw")
gateway_app = typer.Typer(help="gateway management")
app.add_typer(gateway_app, name="gateway")

_STATE_DIR = Path("~/.cli-claw").expanduser()
_STATE_DIR.mkdir(parents=True, exist_ok=True)

_CONFIG_REQUIREMENTS: dict[str, dict[str, tuple[tuple[str, ...], ...]]] = {
    "feishu": {
        "required_any": (
            ("FEISHU_BOT_WEBHOOK_URL",),
            ("FEISHU_APP_ID", "FEISHU_APP_SECRET"),
        ),
        "recommended": (
            ("FEISHU_VERIFICATION_TOKEN",),
            ("FEISHU_ENCRYPT_KEY",),
        ),
    },
    "telegram": {
        "required": (("TELEGRAM_BOT_TOKEN",),),
        "recommended": (("TELEGRAM_WEBHOOK_SECRET",),),
    },
    "slack": {
        "required": (("SLACK_WEBHOOK_URL",),),
        "recommended": (
            ("SLACK_SIGNING_SECRET",),
            ("SLACK_BOT_TOKEN",),
        ),
    },
    "discord": {
        "required": (("DISCORD_WEBHOOK_URL",),),
        "recommended": (("DISCORD_PUBLIC_KEY",),),
    },
    "email": {
        "required": (
            ("EMAIL_IMAP_HOST", "EMAIL_IMAP_USERNAME", "EMAIL_IMAP_PASSWORD"),
            ("EMAIL_SMTP_HOST", "EMAIL_SMTP_USERNAME", "EMAIL_SMTP_PASSWORD"),
        ),
        "recommended": (("EMAIL_FROM_ADDRESS",),),
    },
    "dingtalk": {
        "required": (("DINGTALK_CLIENT_ID", "DINGTALK_CLIENT_SECRET"),),
    },
    "mochat": {
        "required": (("MOCHAT_CLAW_TOKEN",),),
        "required_any": (
            ("MOCHAT_SOCKET_URL",),
            ("MOCHAT_BASE_URL",),
        ),
    },
    "qq": {
        "required": (("QQ_APP_ID", "QQ_SECRET"),),
    },
    "whatsapp": {
        "required": (("WHATSAPP_BRIDGE_URL",),),
        "recommended": (("WHATSAPP_BRIDGE_TOKEN",),),
    },
}


def _env_set(name: str) -> bool:
    value = os.getenv(name)
    return bool(value and value.strip())


def _flatten(groups: tuple[tuple[str, ...], ...]) -> list[str]:
    items: list[str] = []
    for group in groups:
        items.extend(group)
    return items


def _build_runtime(provider: str, channel_name: str, channel_factory, *, log_path: Path | None = None):
    rt = RuntimeOrchestrator()
    if provider == "iflow":
        rt.register_provider(
            ProviderSpec(
                id="iflow",
                command="iflow",
                args=["--experimental-acp"],
                capabilities=CapabilityMap(
                    streaming=True, sessions=True, tools=True, model_switch=True
                ),
            )
        )
    elif provider == "qwen":
        rt.register_provider(
            ProviderSpec(
                id="qwen",
                command="qwen",
                args=["--acp"],
                capabilities=CapabilityMap(
                    streaming=True, sessions=True, tools=True, model_switch=True
                ),
            )
        )
    else:
        raise typer.BadParameter(f"unsupported provider: {provider}")

    if log_path:
        rt.transcript.set_log_path(log_path)
        rt.observability.set_log_path(log_path.with_name("observability.jsonl"))
    memory_dir = Path.home() / ".cli-claw" / "workspace" / "memory"
    memory_path = memory_dir / "memory.db"
    rt.memory = MemoryStore(memory_path)
    legacy_path = memory_dir / "memory.jsonl"
    if legacy_path.exists():
        try:
            rt.memory.import_jsonl(legacy_path)
        except Exception:
            pass

    manager = ChannelManager()
    manager.register(channel_name, channel_factory)

    runtime = ChannelRuntime(
        rt,
        manager,
        heartbeat_enabled=False,
        heartbeat_interval_seconds=60,
        schedules=[],
    )
    runtime.register_route(channel_name, provider)
    return runtime, manager


def _build_runtime_from_config(config_path: Path | None = None):
    cfg = load_config(config_path)
    shared_root = get_config_dir()
    if not cfg.runtime.workspace:
        cfg.runtime.workspace = str(shared_root / "workspace")
    if not cfg.runtime.log_dir:
        cfg.runtime.log_dir = str(shared_root / "logs")
    workspace_root = None
    schedule_store_path = None
    if cfg.runtime.workspace:
        workspace_root = Path(cfg.runtime.workspace).expanduser()
        (workspace_root / "memory").mkdir(parents=True, exist_ok=True)
        # Ensure memory.jsonl exists for fresh installs.
        (workspace_root / "memory" / "memory.jsonl").touch(exist_ok=True)
        ensure_workspace_templates(workspace_root)
        schedule_store_path = workspace_root / "schedules.json"
        if not schedule_store_path.exists():
            schedule_store_path.write_text("[]", encoding="utf-8")
    if cfg.runtime.log_dir:
        Path(cfg.runtime.log_dir).expanduser().mkdir(parents=True, exist_ok=True)

    rt = RuntimeOrchestrator()
    shared_skills = load_shared_skills(shared_root, auto_create=True)
    shared_mcp = load_shared_mcp(shared_root, auto_create=True)
    project_prompt = load_project_prompts(workspace_root) if workspace_root else None
    rt.commands.set_skills_dir(shared_skills.skills_dir)
    rt.commands.refresh()

    for provider_cfg in enabled_providers(cfg):
        spec = build_provider_spec(provider_cfg)
        if cfg.runtime.workspace and not spec.metadata.get("workspace"):
            spec.metadata["workspace"] = cfg.runtime.workspace
        if cfg.runtime.model and not spec.metadata.get("model"):
            spec.metadata["model"] = cfg.runtime.model
        if cfg.runtime.permission_mode and not spec.metadata.get("permission_mode"):
            spec.metadata["permission_mode"] = cfg.runtime.permission_mode
        if cfg.runtime.timeout and not spec.metadata.get("timeout"):
            spec.metadata["timeout"] = cfg.runtime.timeout
        if project_prompt and not spec.metadata.get("system_prompt"):
            spec.metadata["system_prompt"] = project_prompt
        if shared_skills.paths:
            spec.metadata.setdefault("skills_paths", [str(p) for p in shared_skills.paths])
            spec.metadata.setdefault("skills_dir", str(shared_skills.skills_dir))
            spec.env.setdefault("CLI_CLAW_SKILLS_DIR", str(shared_skills.skills_dir))
            spec.env.setdefault("CLI_CLAW_SKILLS_PATHS", os.pathsep.join(str(p) for p in shared_skills.paths))
        if shared_skills.env:
            for key, value in shared_skills.env.items():
                spec.env.setdefault(key, value)
        if shared_mcp.servers and not spec.metadata.get("mcp_servers"):
            spec.metadata["mcp_servers"] = shared_mcp.servers
        rt.register_provider(spec)

    if cfg.runtime.log_dir:
        log_path = Path(cfg.runtime.log_dir).expanduser() / "runtime.jsonl"
        rt.transcript.set_log_path(log_path)
        rt.observability.set_log_path(log_path.with_name("observability.jsonl"))

    if cfg.runtime.workspace:
        memory_dir = Path(cfg.runtime.workspace).expanduser() / "memory"
        memory_path = memory_dir / "memory.db"
        rt.memory = MemoryStore(memory_path)
        legacy_path = memory_dir / "memory.jsonl"
        if legacy_path.exists():
            try:
                rt.memory.import_jsonl(legacy_path)
            except Exception:
                pass

    rt.show_thoughts = bool(cfg.runtime.thinking)
    rt.compression_trigger_tokens = int(cfg.runtime.compression_trigger_tokens)
    rt.compression_provider_id = cfg.runtime.compression_provider
    rt.compression_prompt = cfg.runtime.compression_prompt

    manager = ChannelManager()
    channels_cfg = enabled_channels(cfg)
    for channel_cfg in channels_cfg:
        manager.register(channel_cfg.name, build_channel_factory(channel_cfg.name, channel_cfg))

    runtime = ChannelRuntime(
        rt,
        manager,
        heartbeat_enabled=bool(cfg.runtime.heartbeat_enabled),
        heartbeat_interval_seconds=int(cfg.runtime.heartbeat_interval_seconds),
        schedules=cfg.runtime.schedules,
        schedule_store_path=schedule_store_path,
    )
    default_provider = cfg.runtime.default_provider
    for channel_cfg in channels_cfg:
        provider_id = channel_cfg.provider or default_provider
        if not provider_id:
            raise typer.BadParameter(f"channel {channel_cfg.name} missing provider mapping")
        runtime.register_route(channel_cfg.name, provider_id)

    return runtime, manager, [c.name for c in channels_cfg]


@app.command()
def config_check(
    channel: str = typer.Option(
        "all",
        help="Channel name to check or 'all' to validate every configured channel.",
    )
) -> None:
    if channel != "all" and channel not in _CONFIG_REQUIREMENTS:
        raise typer.BadParameter(
            f"unsupported channel: {channel}. Available: {', '.join(sorted(_CONFIG_REQUIREMENTS))}"
        )

    targets = sorted(_CONFIG_REQUIREMENTS) if channel == "all" else [channel]
    missing_required = False

    for name in targets:
        spec = _CONFIG_REQUIREMENTS[name]
        required_groups = spec.get("required", ())
        required_any_groups = spec.get("required_any", ())
        recommended_groups = spec.get("recommended", ())

        missing: list[str] = []
        for group in required_groups:
            if not all(_env_set(key) for key in group):
                missing.append(" + ".join(group))

        any_ok = True
        missing_any_line = ""
        if required_any_groups:
            any_ok = any(all(_env_set(key) for key in group) for group in required_any_groups)
            if not any_ok:
                missing_any_line = " | ".join(" + ".join(group) for group in required_any_groups)

        recommended_missing = [key for key in _flatten(recommended_groups) if not _env_set(key)]

        print(f"[bold]{name}[/bold]")
        if missing or not any_ok:
            missing_required = True
            if missing:
                print(f"[red]missing required[/red]: {', '.join(missing)}")
            if missing_any_line:
                print(f"[red]missing required[/red]: one of {missing_any_line}")
        else:
            print("[green]required ok[/green]")

        if name == "email" and os.getenv("EMAIL_CONSENT_GRANTED", "false").lower() != "true":
            print("[yellow]warning[/yellow]: EMAIL_CONSENT_GRANTED is false (email channel disabled)")

        if recommended_missing:
            print(f"[yellow]missing recommended[/yellow]: {', '.join(recommended_missing)}")

    if missing_required:
        raise typer.Exit(code=1)


def _state_paths(channel: str) -> dict[str, Path]:
    state_dir = _STATE_DIR / "state" / channel
    log_dir = _STATE_DIR / "logs"
    return {
        "state_dir": state_dir,
        "pid": state_dir / "pid",
        "meta": state_dir / "meta.json",
        "log": log_dir / f"{channel}.jsonl",
    }


def _write_meta(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def _read_meta(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}


def _pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _resolve_command(channel: str, provider: str, host: str, port: int, path: str) -> list[str]:
    command = [sys.executable, "-m", "cli_claw.cli.main"]
    if channel == "feishu":
        command += ["feishu-webhook", "--provider", provider, "--host", host, "--port", str(port), "--path", path]
    elif channel == "telegram":
        command += ["telegram-webhook", "--provider", provider, "--host", host, "--port", str(port), "--path", path]
    elif channel == "slack":
        command += ["slack-webhook", "--provider", provider, "--host", host, "--port", str(port), "--path", path]
    elif channel == "discord":
        command += ["discord-webhook", "--provider", provider, "--host", host, "--port", str(port), "--path", path]
    else:
        command += ["simple-webhook", "--channel", channel, "--provider", provider, "--host", host, "--port", str(port), "--path", path]
    return command


@app.command()
def start(
    channel: str = typer.Option("feishu", help="Channel name to start"),
    provider: str = typer.Option("iflow", help="Provider id (iflow/qwen)"),
    host: str = typer.Option("0.0.0.0", help="Webhook server host"),
    port: int = typer.Option(8000, help="Webhook server port"),
    path: str = typer.Option("", help="Webhook path (defaults per channel)"),
) -> None:
    if channel not in _CONFIG_REQUIREMENTS:
        raise typer.BadParameter(
            f"unsupported channel: {channel}. Available: {', '.join(sorted(_CONFIG_REQUIREMENTS))}"
        )

    default_paths = {
        "feishu": "/feishu/webhook",
        "telegram": "/telegram/webhook",
        "slack": "/slack/webhook",
        "discord": "/discord/webhook",
        "email": "/simple/webhook",
        "dingtalk": "/simple/webhook",
        "mochat": "/simple/webhook",
        "qq": "/simple/webhook",
        "whatsapp": "/simple/webhook",
    }
    if not path:
        path = default_paths.get(channel, "/simple/webhook")

    paths = _state_paths(channel)
    if paths["pid"].exists():
        pid = int(paths["pid"].read_text().strip() or "0")
        if _pid_running(pid):
            raise typer.Exit(code=1)
        paths["pid"].unlink(missing_ok=True)

    log_path = paths["log"]
    log_path.parent.mkdir(parents=True, exist_ok=True)
    meta = {
        "channel": channel,
        "provider": provider,
        "host": host,
        "port": port,
        "path": path,
        "pid": None,
        "started_at": time.time(),
    }

    command = _resolve_command(channel, provider, host, port, path)
    process = subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env={
            **os.environ,
            "CLI_CLAW_LOG_PATH": str(log_path),
        },
    )
    pid = process.pid
    meta["pid"] = pid
    paths["pid"].parent.mkdir(parents=True, exist_ok=True)
    paths["pid"].write_text(str(pid))
    _write_meta(paths["meta"], meta)
    print(f"[green]started[/green] {channel} pid={pid}")


@app.command()
def status(
    channel: str = typer.Option("all", help="Channel name to check or 'all'"),
) -> None:
    channels = sorted(_CONFIG_REQUIREMENTS) if channel == "all" else [channel]
    for name in channels:
        paths = _state_paths(name)
        meta = _read_meta(paths["meta"])
        pid = int(meta.get("pid") or 0)
        running = _pid_running(pid)
        print(f"[bold]{name}[/bold]: {'running' if running else 'stopped'}")
        if meta:
            print(f"  provider: {meta.get('provider')}")
            print(f"  host: {meta.get('host')}:{meta.get('port')}{meta.get('path')}")
            print(f"  started_at: {meta.get('started_at')}")


@app.command()
def stop(channel: str = typer.Option("all", help="Channel name to stop or 'all'")) -> None:
    channels = sorted(_CONFIG_REQUIREMENTS) if channel == "all" else [channel]
    for name in channels:
        paths = _state_paths(name)
        if not paths["pid"].exists():
            print(f"[yellow]{name}[/yellow]: not running")
            continue
        pid = int(paths["pid"].read_text().strip() or "0")
        if not _pid_running(pid):
            print(f"[yellow]{name}[/yellow]: not running")
            paths["pid"].unlink(missing_ok=True)
            continue
        os.kill(pid, signal.SIGTERM)
        paths["pid"].unlink(missing_ok=True)
        print(f"[green]stopped[/green] {name}")


@gateway_app.command("run")
def gateway_run(
    config: Path | None = typer.Option(None, help="Path to cli-claw config.json"),
) -> None:
    async def _run() -> None:
        runtime, manager, channels = _build_runtime_from_config(config)
        await runtime.start(channels)
        try:
            await asyncio.Event().wait()
        finally:
            await manager._queue.join()
            await runtime.stop()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        return


@gateway_app.command("start")
def gateway_start(
    config: Path | None = typer.Option(None, help="Path to cli-claw config.json"),
) -> None:
    paths = _state_paths("gateway")
    if paths["pid"].exists():
        pid = int(paths["pid"].read_text().strip() or "0")
        if _pid_running(pid):
            raise typer.Exit(code=1)
        paths["pid"].unlink(missing_ok=True)

    command = [sys.executable, "-m", "cli_claw.cli.main", "gateway", "run"]
    if config:
        command += ["--config", str(config)]

    process = subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=os.environ.copy(),
    )
    pid = process.pid
    paths["pid"].parent.mkdir(parents=True, exist_ok=True)
    paths["pid"].write_text(str(pid))
    _write_meta(paths["meta"], {"pid": pid, "started_at": time.time(), "config": str(config) if config else None})
    print(f"[green]started[/green] gateway pid={pid}")


@gateway_app.command("status")
def gateway_status() -> None:
    paths = _state_paths("gateway")
    meta = _read_meta(paths["meta"])
    pid = int(meta.get("pid") or 0)
    running = _pid_running(pid)
    print(f"[bold]gateway[/bold]: {'running' if running else 'stopped'}")
    if meta:
        print(f"  pid: {pid}")
        print(f"  started_at: {meta.get('started_at')}")
        if meta.get("config"):
            print(f"  config: {meta.get('config')}")


@gateway_app.command("stop")
def gateway_stop() -> None:
    paths = _state_paths("gateway")
    if not paths["pid"].exists():
        print("[yellow]gateway[/yellow]: not running")
        return
    pid = int(paths["pid"].read_text().strip() or "0")
    if not _pid_running(pid):
        print("[yellow]gateway[/yellow]: not running")
        paths["pid"].unlink(missing_ok=True)
        return
    os.kill(pid, signal.SIGTERM)
    paths["pid"].unlink(missing_ok=True)
    print("[green]stopped[/green] gateway")


@app.command()
def logs(
    channel: str = typer.Option("all", help="Channel name to tail or 'all'"),
    lines: int = typer.Option(50, help="Number of lines to show"),
) -> None:
    channels = sorted(_CONFIG_REQUIREMENTS) if channel == "all" else [channel]
    for name in channels:
        path = _state_paths(name)["log"]
        if not path.exists():
            print(f"[yellow]{name}[/yellow]: no log file")
            continue
        content = path.read_text(encoding="utf-8").splitlines()
        tail = content[-lines:]
        print(f"[bold]{name}[/bold]")
        for line in tail:
            print(line)


@app.command()
def replay(
    channel: str = typer.Option("feishu", help="Channel name to replay"),
    provider: str = typer.Option("iflow", help="Provider id (iflow/qwen)"),
    source: Path = typer.Option(..., exists=True, help="JSONL file with transcript records"),
) -> None:
    async def _run() -> None:
        runtime, manager = _build_runtime(provider, channel, _channel_factory(channel), log_path=None)
        await runtime.start([channel])
        for line in source.read_text(encoding="utf-8").splitlines():
            data = json.loads(line)
            inbound = InboundEnvelope(
                channel=channel,
                chat_id=str(data.get("chat_id") or ""),
                sender_id=data.get("sender_id"),
                message_id=data.get("message_id"),
                reply_to_id=data.get("reply_to_id"),
                text=str(data.get("content") or ""),
                attachments=data.get("attachments") or [],
                metadata=data.get("meta") or {},
            )
            await runtime.handle_inbound(channel, inbound)
        await manager._queue.join()
        await runtime.stop()

    asyncio.run(_run())


@app.command()
def self_test() -> None:
    env = {**os.environ, "UV_CACHE_DIR": "/tmp/uv-cache"}
    raise typer.Exit(
        code=subprocess.call(["uv", "run", "pytest", "-q"], env=env)
    )


def _channel_factory(channel: str):
    factories = {
        "feishu": FeishuChannel,
        "telegram": TelegramChannel,
        "slack": SlackChannel,
        "discord": DiscordChannel,
        "email": EmailChannel,
        "dingtalk": DingTalkChannel,
        "mochat": MochatChannel,
        "qq": QQChannel,
        "whatsapp": WhatsAppChannel,
    }
    return factories[channel]


@app.command()
def info() -> None:
    print("[bold green]cli-claw[/bold green] provider-agnostic runtime scaffold is ready")


@app.command()
def demo(provider: str = "iflow", message: str = "hello") -> None:
    async def _run() -> None:
        rt = RuntimeOrchestrator()
        if provider == "iflow":
            rt.register_provider(ProviderSpec(id="iflow", command="iflow", args=["--experimental-acp"], capabilities=CapabilityMap(streaming=True, sessions=True, tools=True, model_switch=True)))
        elif provider == "qwen":
            rt.register_provider(ProviderSpec(id="qwen", command="qwen", args=["--acp"], capabilities=CapabilityMap(streaming=True, sessions=True, tools=True, model_switch=True)))
        elif provider == "opencode":
            rt.register_provider(
                ProviderSpec(
                    id="opencode",
                    command="opencode",
                    args=["acp"],
                    capabilities=CapabilityMap(streaming=True, sessions=True, tools=True, model_switch=True),
                    adapter="cli_claw.providers.generic.cli_provider.GenericCliProvider",
                )
            )
        else:
            raise typer.BadParameter(f"unsupported provider: {provider}")
        out = await rt.handle_inbound(
            provider,
            "demo-session",
            InboundEnvelope(channel="cli", chat_id="direct", text=message),
        )
        print(out.text)

    asyncio.run(_run())


@app.command()
def model(
    name: str = typer.Argument(..., help="Model name to set"),
    provider: str = typer.Option("default", help="Provider id or 'default' for runtime"),
    config: Path | None = typer.Option(None, help="Path to cli-claw config.json"),
) -> None:
    cfg = load_config(config)
    if provider == "default":
        cfg.runtime.model = name
    else:
        matched = False
        for item in cfg.providers:
            if item.id == provider:
                item.metadata["model"] = name
                matched = True
                break
        if not matched:
            raise typer.BadParameter(f"provider not found: {provider}")
    dump_config(cfg, config)
    print(f"[green]model set[/green] {name} ({provider})")


@app.command()
def thinking(
    mode: str = typer.Argument(..., help="on/off"),
    config: Path | None = typer.Option(None, help="Path to cli-claw config.json"),
) -> None:
    cfg = load_config(config)
    value = mode.lower() in {"on", "true", "1", "yes"}
    cfg.runtime.thinking = value
    dump_config(cfg, config)
    print(f"[green]thinking[/green] {'on' if value else 'off'}")


@app.command()
def feishu_webhook(
    provider: str = "iflow",
    host: str = "0.0.0.0",
    port: int = 8000,
    path: str = "/feishu/webhook",
) -> None:
    async def _run() -> None:
        runtime, manager = _build_runtime(provider, "feishu", FeishuChannel)
        await runtime.start(["feishu"])

        channel = manager.get("feishu")
        if not isinstance(channel, FeishuChannel):
            raise RuntimeError("Feishu channel not available")

        loop = asyncio.get_running_loop()
        server, _thread = start_feishu_webhook_server(
            host=host,
            port=port,
            path=path,
            channel=channel,
            runtime=runtime,
            loop=loop,
        )

        print(
            f"[bold green]Feishu webhook listening[/bold green] on http://{host}:{port}{path}"
        )
        try:
            await asyncio.Event().wait()
        finally:
            server.shutdown()
            server.server_close()
            await runtime.stop()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        return


@app.command()
def telegram_webhook(
    provider: str = "iflow",
    host: str = "0.0.0.0",
    port: int = 8001,
    path: str = "/telegram/webhook",
) -> None:
    async def _run() -> None:
        runtime, manager = _build_runtime(provider, "telegram", TelegramChannel)
        await runtime.start(["telegram"])

        channel = manager.get("telegram")
        if not isinstance(channel, TelegramChannel):
            raise RuntimeError("Telegram channel not available")

        loop = asyncio.get_running_loop()
        server, _thread = start_telegram_webhook_server(
            host=host,
            port=port,
            path=path,
            channel=channel,
            runtime=runtime,
            loop=loop,
        )

        print(
            f"[bold green]Telegram webhook listening[/bold green] on http://{host}:{port}{path}"
        )
        try:
            await asyncio.Event().wait()
        finally:
            server.shutdown()
            server.server_close()
            await runtime.stop()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        return


@app.command()
def slack_webhook(
    provider: str = "iflow",
    host: str = "0.0.0.0",
    port: int = 8002,
    path: str = "/slack/webhook",
) -> None:
    async def _run() -> None:
        runtime, manager = _build_runtime(provider, "slack", SlackChannel)
        await runtime.start(["slack"])

        channel = manager.get("slack")
        if not isinstance(channel, SlackChannel):
            raise RuntimeError("Slack channel not available")

        loop = asyncio.get_running_loop()
        server, _thread = start_slack_webhook_server(
            host=host,
            port=port,
            path=path,
            channel=channel,
            runtime=runtime,
            loop=loop,
        )

        print(f"[bold green]Slack webhook listening[/bold green] on http://{host}:{port}{path}")
        try:
            await asyncio.Event().wait()
        finally:
            server.shutdown()
            server.server_close()
            await runtime.stop()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        return


@app.command()
def discord_webhook(
    provider: str = "iflow",
    host: str = "0.0.0.0",
    port: int = 8003,
    path: str = "/discord/webhook",
) -> None:
    async def _run() -> None:
        runtime, manager = _build_runtime(provider, "discord", DiscordChannel)
        await runtime.start(["discord"])

        channel = manager.get("discord")
        if not isinstance(channel, DiscordChannel):
            raise RuntimeError("Discord channel not available")

        loop = asyncio.get_running_loop()
        server, _thread = start_discord_webhook_server(
            host=host,
            port=port,
            path=path,
            channel=channel,
            runtime=runtime,
            loop=loop,
        )

        print(
            f"[bold green]Discord webhook listening[/bold green] on http://{host}:{port}{path}"
        )
        try:
            await asyncio.Event().wait()
        finally:
            server.shutdown()
            server.server_close()
            await runtime.stop()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        return


@app.command()
def simple_webhook(
    channel: str = "email",
    provider: str = "iflow",
    host: str = "0.0.0.0",
    port: int = 8004,
    path: str = "/simple/webhook",
) -> None:
    channels = {
        "email": EmailChannel,
        "dingtalk": DingTalkChannel,
        "mochat": MochatChannel,
        "qq": QQChannel,
        "whatsapp": WhatsAppChannel,
    }
    channel_cls = channels.get(channel)
    if channel_cls is None:
        raise typer.BadParameter(f"unsupported simple channel: {channel}")

    async def _run() -> None:
        runtime, manager = _build_runtime(provider, channel, channel_cls)
        await runtime.start([channel])

        instance = manager.get(channel)
        if not isinstance(instance, channel_cls):
            raise RuntimeError(f"{channel} channel not available")

        loop = asyncio.get_running_loop()
        server, _thread = start_simple_webhook_server(
            host=host,
            port=port,
            path=path,
            channel=instance,
            runtime=runtime,
            loop=loop,
        )

        print(
            f"[bold green]Simple webhook listening[/bold green] for {channel} on http://{host}:{port}{path}"
        )
        try:
            await asyncio.Event().wait()
        finally:
            server.shutdown()
            server.server_close()
            await runtime.stop()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        return


if __name__ == "__main__":
    app()
