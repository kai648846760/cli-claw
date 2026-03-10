from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Mapping

from cli_claw.channels.feishu import FeishuChannel
from cli_claw.runtime.channel_runtime import ChannelRuntime

logger = logging.getLogger(__name__)

_TOKEN_KEYS = (
    ("header", "token"),
    ("token",),
)


def _extract_token(payload: dict[str, Any]) -> str | None:
    for keys in _TOKEN_KEYS:
        current: Any = payload
        for key in keys:
            if not isinstance(current, dict):
                current = None
                break
            current = current.get(key)
        if isinstance(current, str) and current:
            return current
    return None


def _get_header(headers: Mapping[str, str] | None, name: str) -> str | None:
    if not headers:
        return None
    lowered = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered:
            return value
    return None


def _verify_signature(
    channel: FeishuChannel,
    headers: Mapping[str, str] | None,
    raw_body: bytes,
) -> bool:
    encrypt_key = channel.config.encrypt_key
    if not encrypt_key:
        return True
    timestamp = _get_header(headers, "X-Lark-Request-Timestamp")
    nonce = _get_header(headers, "X-Lark-Request-Nonce")
    signature = _get_header(headers, "X-Lark-Signature")
    if not timestamp or not nonce or not signature:
        return False
    base = f"{timestamp}{nonce}{encrypt_key}".encode("utf-8")
    expected = hashlib.sha256(base + raw_body).hexdigest()
    return hmac.compare_digest(signature.lower(), expected.lower())


def _decrypt_payload(channel: FeishuChannel, payload: dict[str, Any]) -> dict[str, Any]:
    encrypt_key = channel.config.encrypt_key
    if not encrypt_key:
        raise RuntimeError("encrypt payload received but FEISHU_ENCRYPT_KEY not set")

    encrypted = payload.get("encrypt")
    if not isinstance(encrypted, str) or not encrypted:
        raise RuntimeError("encrypt payload missing 'encrypt' string")

    try:
        raw = base64.b64decode(encrypted)
    except Exception as exc:
        raise RuntimeError("encrypt payload base64 decode failed") from exc

    if len(raw) <= 16:
        raise RuntimeError("encrypt payload too short")

    iv = raw[:16]
    ciphertext = raw[16:]
    key = hashlib.sha256(encrypt_key.encode("utf-8")).digest()

    try:
        from cryptography.hazmat.primitives import padding
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except Exception as exc:
        raise RuntimeError("cryptography is required for FEISHU_ENCRYPT_KEY support") from exc

    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    unpadder = padding.PKCS7(algorithms.AES.block_size).unpadder()
    data = unpadder.update(padded) + unpadder.finalize()

    try:
        return json.loads(data.decode("utf-8"))
    except Exception as exc:
        raise RuntimeError("decrypt payload json decode failed") from exc


def _verify_token(channel: FeishuChannel, payload: dict[str, Any]) -> bool:
    expected = channel.config.verification_token
    if not expected:
        return True
    token = _extract_token(payload)
    return token == expected


async def process_feishu_webhook_payload(
    channel: FeishuChannel,
    runtime: ChannelRuntime,
    payload: dict[str, Any],
    raw_body: bytes,
    headers: Mapping[str, str] | None = None,
) -> tuple[int, dict[str, Any]]:
    if not _verify_signature(channel, headers, raw_body):
        return 401, {"ok": False, "error": "invalid signature"}

    if "encrypt" in payload:
        try:
            payload = _decrypt_payload(channel, payload)
        except Exception as exc:
            return 400, {"ok": False, "error": str(exc)}

    if not _verify_token(channel, payload):
        return 401, {"ok": False, "error": "invalid token"}
    if "challenge" in payload:
        return 200, {"challenge": payload.get("challenge")}

    inbound = channel.parse_inbound_event(payload)
    if inbound is None:
        return 200, {"ok": True, "skipped": True}

    await runtime.handle_inbound(channel.name, inbound)
    return 200, {"ok": True}


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
                    process_feishu_webhook_payload(
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
                logger.exception("Feishu webhook processing failed")
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
