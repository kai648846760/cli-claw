from __future__ import annotations

import asyncio
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Mapping

from cli_claw.channels.discord import DiscordChannel
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


def _verify_signature(
    channel: DiscordChannel,
    headers: Mapping[str, str] | None,
    raw_body: bytes,
) -> bool:
    public_key = channel.config.public_key
    if not public_key:
        return True

    signature = _get_header(headers, "X-Signature-Ed25519")
    timestamp = _get_header(headers, "X-Signature-Timestamp")
    if not signature or not timestamp:
        return False

    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except Exception:
        return False

    try:
        key_bytes = bytes.fromhex(public_key)
        sig_bytes = bytes.fromhex(signature)
    except ValueError:
        return False

    message = timestamp.encode("utf-8") + raw_body
    try:
        Ed25519PublicKey.from_public_bytes(key_bytes).verify(sig_bytes, message)
        return True
    except Exception:
        return False


async def process_discord_webhook_payload(
    channel: DiscordChannel,
    runtime: ChannelRuntime,
    payload: dict[str, Any],
    raw_body: bytes,
    headers: Mapping[str, str] | None = None,
) -> tuple[int, dict[str, Any]]:
    if not _verify_signature(channel, headers, raw_body):
        return 401, {"error": "invalid signature"}

    if payload.get("type") == 1:
        return 200, {"type": 1}

    inbound = channel.parse_inbound_event(payload)
    if inbound is None:
        return 200, {"ok": True, "skipped": True}

    await runtime.handle_inbound(channel.name, inbound)
    return 200, {"type": 5}


def _make_handler(
    channel: DiscordChannel,
    runtime: ChannelRuntime,
    loop: asyncio.AbstractEventLoop,
    path: str,
):
    class _DiscordWebhookHandler(BaseHTTPRequestHandler):
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
                    process_discord_webhook_payload(
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
                logger.exception("Discord webhook processing failed")
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
            logger.info("Discord webhook %s - %s", self.address_string(), format % args)

    return _DiscordWebhookHandler


def start_discord_webhook_server(
    *,
    host: str,
    port: int,
    path: str,
    channel: DiscordChannel,
    runtime: ChannelRuntime,
    loop: asyncio.AbstractEventLoop,
) -> tuple[ThreadingHTTPServer, threading.Thread]:
    handler = _make_handler(channel, runtime, loop, path)
    server = ThreadingHTTPServer((host, port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread
