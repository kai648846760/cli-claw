from __future__ import annotations

import asyncio
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Mapping

from cli_claw.channels.simple_channels import SimpleWebhookChannel
from cli_claw.runtime.channel_runtime import ChannelRuntime

logger = logging.getLogger(__name__)


def _get_header(headers: Mapping[str, str] | None, name: str) -> str | None:
    if not headers:
        return None
    lowered = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered:
            return value
    return None


async def process_simple_webhook_payload(
    channel: SimpleWebhookChannel,
    runtime: ChannelRuntime,
    payload: dict[str, Any],
    raw_body: bytes,
    headers: Mapping[str, str] | None = None,
) -> tuple[int, dict[str, Any]]:
    _ = raw_body
    if channel.config.verification_token:
        token = payload.get("token") or _get_header(headers, "X-Webhook-Token")
        if token != channel.config.verification_token:
            return 401, {"ok": False, "error": "invalid token"}

    inbound = channel.parse_inbound_event(payload)
    if inbound is None:
        return 200, {"ok": True, "skipped": True}

    await runtime.handle_inbound(channel.name, inbound)
    return 200, {"ok": True}


def _make_handler(
    channel: SimpleWebhookChannel,
    runtime: ChannelRuntime,
    loop: asyncio.AbstractEventLoop,
    path: str,
):
    class _SimpleWebhookHandler(BaseHTTPRequestHandler):
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
                    process_simple_webhook_payload(
                        channel,
                        runtime,
                        payload,
                        raw_body,
                        dict(self.headers),
                    ),
                    loop,
                )
                status_code, response_payload = future.result(timeout=5)
            except Exception:
                logger.exception("Simple webhook processing failed")
                self.send_response(500)
                self.end_headers()
                return

            body = json.dumps(response_payload).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            logger.info("Simple webhook %s - %s", self.address_string(), format % args)

    return _SimpleWebhookHandler


def start_simple_webhook_server(
    *,
    host: str,
    port: int,
    path: str,
    channel: SimpleWebhookChannel,
    runtime: ChannelRuntime,
    loop: asyncio.AbstractEventLoop,
) -> tuple[ThreadingHTTPServer, threading.Thread]:
    handler = _make_handler(channel, runtime, loop, path)
    server = ThreadingHTTPServer((host, port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread
