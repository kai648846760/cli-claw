from __future__ import annotations

import asyncio

import typer
from rich import print

from cli_claw.channels.feishu import FeishuChannel
from cli_claw.channels.feishu_webhook import start_feishu_webhook_server
from cli_claw.channels.manager import ChannelManager
from cli_claw.runtime.orchestrator import RuntimeOrchestrator
from cli_claw.runtime.channel_runtime import ChannelRuntime
from cli_claw.schemas.channel import InboundEnvelope
from cli_claw.schemas.provider import CapabilityMap, ProviderSpec

app = typer.Typer(help="cli-claw")


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
        manager.register("feishu", FeishuChannel)

        runtime = ChannelRuntime(rt, manager)
        runtime.register_route("feishu", provider)
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


if __name__ == "__main__":
    app()
