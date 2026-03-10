from __future__ import annotations

import asyncio
import hmac
import hashlib
import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Mapping

from cli_claw.channels.slack import SlackChannel
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


def _verify_signature(channel: SlackChannel, headers: Mapping[str, str] | None, raw_body: bytes) -> bool:
    secret = channel.config.signing_secret
    if not secret:
        return True

    timestamp = _get_header(headers, "X-Slack-Request-Timestamp")
    signature = _get_header(headers, "X-Slack-Signature")
    if not timestamp or not signature:
        return False

    try:
        ts_int = int(timestamp)
    except ValueError:
        return False

    if abs(time.time() - ts_int) > 60 * 5:
        return False

    base = f"v0:{timestamp}:".encode("utf-8") + raw_body
    digest = hmac.new(secret.encode("utf-8"), base, hashlib.sha256).hexdigest()
    expected = f"v0={digest}"
    return hmac.compare_digest(signature, expected)


async def process_slack_webhook_payload(
    channel: SlackChannel,
    runtime: ChannelRuntime,
    payload: dict[str, Any],
    raw_body: bytes,
    headers: Mapping[str, str] | None = None,
) -> tuple[int, dict[str, Any]]:
    if not _verify_signature(channel, headers, raw_body):
        return 401, {"ok": False, "error": "invalid signature"}

    if payload.get("type") == "url_verification":
        return 200, {"challenge": payload.get("challenge")}

    inbound = channel.parse_inbound_event(payload)
    if inbound is None:
        return 200, {"ok": True, "skipped": True}

    await runtime.handle_inbound(channel.name, inbound)
    return 200, {"ok": True}


def _make_handler(
    channel: SlackChannel,
    runtime: ChannelRuntime,
    loop: asyncio.AbstractEventLoop,
    path: str,
):
    class _SlackWebhookHandler(BaseHTTPRequestHandler):
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
                    process_slack_webhook_payload(
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
                logger.exception("Slack webhook processing failed")
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
            logger.info("Slack webhook %s - %s", self.address_string(), format % args)

    return _SlackWebhookHandler


def start_slack_webhook_server(
    *,
    host: str,
    port: int,
    path: str,
    channel: SlackChannel,
    runtime: ChannelRuntime,
    loop: asyncio.AbstractEventLoop,
) -> tuple[ThreadingHTTPServer, threading.Thread]:
    handler = _make_handler(channel, runtime, loop, path)
    server = ThreadingHTTPServer((host, port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread
