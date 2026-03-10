from __future__ import annotations

import asyncio

import typer
from rich import print

from cli_claw.channels.discord import DiscordChannel
from cli_claw.channels.discord_webhook import start_discord_webhook_server
from cli_claw.channels.feishu import FeishuChannel
from cli_claw.channels.feishu_webhook import start_feishu_webhook_server
from cli_claw.channels.manager import ChannelManager
from cli_claw.channels.simple_channels import (
    DingtalkChannel,
    EmailChannel,
    MochatChannel,
    QQChannel,
    WhatsappChannel,
)
from cli_claw.channels.simple_webhook import start_simple_webhook_server
from cli_claw.channels.slack import SlackChannel
from cli_claw.channels.slack_webhook import start_slack_webhook_server
from cli_claw.channels.telegram import TelegramChannel
from cli_claw.channels.telegram_webhook import start_telegram_webhook_server
from cli_claw.runtime.orchestrator import RuntimeOrchestrator
from cli_claw.runtime.channel_runtime import ChannelRuntime
from cli_claw.schemas.channel import InboundEnvelope
from cli_claw.schemas.provider import CapabilityMap, ProviderSpec

app = typer.Typer(help="cli-claw")


def _build_runtime(provider: str, channel_name: str, channel_factory):
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

    manager = ChannelManager()
    manager.register(channel_name, channel_factory)

    runtime = ChannelRuntime(rt, manager)
    runtime.register_route(channel_name, provider)
    return runtime, manager


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
            f\"[bold green]Feishu webhook listening[/bold green] on http://{host}:{port}{path}\"
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
        "dingtalk": DingtalkChannel,
        "mochat": MochatChannel,
        "qq": QQChannel,
        "whatsapp": WhatsappChannel,
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
