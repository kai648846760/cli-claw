from __future__ import annotations

import asyncio
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from cli_claw.channels.feishu import FeishuChannel
from cli_claw.runtime.channel_runtime import ChannelRuntime

logger = logging.getLogger(__name__)


async def process_feishu_webhook_payload(
    channel: FeishuChannel,
    runtime: ChannelRuntime,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if "challenge" in payload:
        return {"challenge": payload.get("challenge")}

    inbound = channel.parse_inbound_event(payload)
    if inbound is None:
        return {"ok": True, "skipped": True}

    await runtime.handle_inbound(channel.name, inbound)
    return {"ok": True}


def _make_handler(
    channel: FeishuChannel,
    runtime: ChannelRuntime,
    loop: asyncio.AbstractEventLoop,
    path: str,
):
    class _FeishuWebhookHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            if path and self.path != path:
                self.send_response(404)
                self.end_headers()
                return

            length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(length)
            try:
                payload = json.loads(raw_body.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                self.send_response(400)
                self.end_headers()
                return

            try:
                future = asyncio.run_coroutine_threadsafe(
                    process_feishu_webhook_payload(channel, runtime, payload),
                    loop,
                )
                response_payload = future.result(timeout=5)
            except Exception:
                logger.exception("Feishu webhook processing failed")
                self.send_response(500)
                self.end_headers()
                return

            body = json.dumps(response_payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            logger.info("Feishu webhook %s - %s", self.address_string(), format % args)

    return _FeishuWebhookHandler


def start_feishu_webhook_server(
    *,
    host: str,
    port: int,
    path: str,
    channel: FeishuChannel,
    runtime: ChannelRuntime,
    loop: asyncio.AbstractEventLoop,
) -> tuple[ThreadingHTTPServer, threading.Thread]:
    handler = _make_handler(channel, runtime, loop, path)
    server = ThreadingHTTPServer((host, port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread
