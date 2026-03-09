from __future__ import annotations

import asyncio

import typer
from rich import print

from cli_claw.runtime.orchestrator import RuntimeOrchestrator
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


if __name__ == "__main__":
    app()
