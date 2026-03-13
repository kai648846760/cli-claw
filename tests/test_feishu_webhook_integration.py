import base64
import hashlib
import json

import pytest

from cli_claw.channels.feishu import FeishuChannel
from cli_claw.channels.feishu_webhook import process_feishu_webhook_payload
from cli_claw.channels.manager import ChannelManager
from cli_claw.runtime.channel_runtime import ChannelRuntime
from cli_claw.runtime.orchestrator import RuntimeOrchestrator


class _FakeProvider:
    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def health_check(self) -> bool:
        return True

    async def new_session(self, logical_session_id: str) -> str:
        return f"p:{logical_session_id}"

    async def clear_session(self, logical_session_id: str, provider_session_id: str | None = None) -> bool:
        _ = provider_session_id
        return True

    async def chat(self, logical_session_id: str, message: str) -> str:
        return f"echo:{message}"

    async def chat_stream(self, logical_session_id: str, message: str):
        _ = logical_session_id
        _ = message
        raise NotImplementedError

    async def list_models(self) -> list[str]:
        return ["fake"]


class _FakeRegistry:
    def __init__(self, provider: _FakeProvider) -> None:
        self._provider = provider

    def get(self, provider_id: str):
        _ = provider_id
        return self._provider


def _sign(encrypt_key: str, timestamp: str, nonce: str, raw_body: bytes) -> str:
    base = f"{timestamp}{nonce}{encrypt_key}".encode("utf-8")
    return hashlib.sha256(base + raw_body).hexdigest()


def _encrypt_payload(encrypt_key: str, payload: dict) -> str:
    padding = pytest.importorskip("cryptography.hazmat.primitives.padding")
    ciphers = pytest.importorskip("cryptography.hazmat.primitives.ciphers")
    Cipher = ciphers.Cipher
    algorithms = ciphers.algorithms
    modes = ciphers.modes

    raw = json.dumps(payload).encode("utf-8")
    key = hashlib.sha256(encrypt_key.encode("utf-8")).digest()
    iv = b"\x00" * 16
    padder = padding.PKCS7(algorithms.AES.block_size).padder()
    padded = padder.update(raw) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    return base64.b64encode(iv + ciphertext).decode("utf-8")


@pytest.mark.asyncio
async def test_feishu_webhook_routes_to_runtime():
    channel = FeishuChannel()
    channel.config.bot_webhook_url = "https://example.com/hook"

    captured: dict[str, object] = {}

    async def _fake_post(url, payload, headers):
        captured["url"] = url
        captured["payload"] = payload
        captured["headers"] = headers
        return {"ok": True}

    channel._post_json = _fake_post  # type: ignore[assignment]

    manager = ChannelManager()
    manager.register("feishu", lambda: channel)

    orchestrator = RuntimeOrchestrator()
    orchestrator.providers = _FakeRegistry(_FakeProvider())

    runtime = ChannelRuntime(orchestrator, manager)
    runtime.register_route("feishu", "fake")

    await runtime.start(["feishu"])

    payload = {
        "schema": "2.0",
        "header": {"event_type": "im.message.receive_v1", "tenant_key": "t1"},
        "event": {
            "sender": {"sender_id": {"open_id": "ou_123"}},
            "message": {
                "message_id": "om_1",
                "chat_id": "oc_9",
                "chat_type": "p2p",
                "message_type": "text",
                "content": "{\"text\":\"hello\"}",
            },
        },
    }

    raw_body = json.dumps(payload).encode("utf-8")
    status, response = await process_feishu_webhook_payload(channel, runtime, payload, raw_body)
    await manager._queue.join()

    assert status == 200
    assert response == {"ok": True}
    assert captured["url"] == "https://example.com/hook"
    assert captured["payload"]["content"]["text"] == "echo:hello"

    await runtime.stop()


@pytest.mark.asyncio
async def test_feishu_webhook_challenge_passthrough():
    channel = FeishuChannel()
    manager = ChannelManager()
    manager.register("feishu", lambda: channel)

    orchestrator = RuntimeOrchestrator()
    orchestrator.providers = _FakeRegistry(_FakeProvider())

    runtime = ChannelRuntime(orchestrator, manager)
    runtime.register_route("feishu", "fake")
    await runtime.start(["feishu"])

    payload = {"challenge": "token-123"}
    raw_body = json.dumps(payload).encode("utf-8")
    status, response = await process_feishu_webhook_payload(channel, runtime, payload, raw_body)

    assert status == 200
    assert response == {"challenge": "token-123"}

    await runtime.stop()


@pytest.mark.asyncio
async def test_feishu_webhook_rejects_invalid_token():
    channel = FeishuChannel()
    channel.config.verification_token = "expected"

    manager = ChannelManager()
    manager.register("feishu", lambda: channel)

    orchestrator = RuntimeOrchestrator()
    orchestrator.providers = _FakeRegistry(_FakeProvider())

    runtime = ChannelRuntime(orchestrator, manager)
    runtime.register_route("feishu", "fake")
    await runtime.start(["feishu"])

    payload = {
        "schema": "2.0",
        "header": {"token": "wrong"},
        "event": {"message": {"message_id": "om_1", "chat_id": "oc_9", "content": "{\"text\":\"hi\"}"}},
    }
    raw_body = json.dumps(payload).encode("utf-8")
    status, response = await process_feishu_webhook_payload(channel, runtime, payload, raw_body)

    assert status == 401
    assert response["ok"] is False

    await runtime.stop()


@pytest.mark.asyncio
async def test_feishu_webhook_rejects_invalid_signature():
    channel = FeishuChannel()
    channel.config.encrypt_key = "encrypt-key"

    manager = ChannelManager()
    manager.register("feishu", lambda: channel)

    orchestrator = RuntimeOrchestrator()
    orchestrator.providers = _FakeRegistry(_FakeProvider())

    runtime = ChannelRuntime(orchestrator, manager)
    runtime.register_route("feishu", "fake")
    await runtime.start(["feishu"])

    payload = {"challenge": "token-123"}
    raw_body = json.dumps(payload).encode("utf-8")
    headers = {
        "X-Lark-Request-Timestamp": "1",
        "X-Lark-Request-Nonce": "n",
        "X-Lark-Signature": "bad",
    }
    status, response = await process_feishu_webhook_payload(
        channel, runtime, payload, raw_body, headers
    )

    assert status == 401
    assert response["ok"] is False

    await runtime.stop()


@pytest.mark.asyncio
async def test_feishu_webhook_rejects_missing_signature():
    channel = FeishuChannel()
    channel.config.encrypt_key = "encrypt-key"

    manager = ChannelManager()
    manager.register("feishu", lambda: channel)

    orchestrator = RuntimeOrchestrator()
    orchestrator.providers = _FakeRegistry(_FakeProvider())

    runtime = ChannelRuntime(orchestrator, manager)
    runtime.register_route("feishu", "fake")
    await runtime.start(["feishu"])

    payload = {"challenge": "token-123"}
    raw_body = json.dumps(payload).encode("utf-8")
    status, response = await process_feishu_webhook_payload(
        channel, runtime, payload, raw_body, headers=None
    )

    assert status == 401
    assert response["ok"] is False

    await runtime.stop()


@pytest.mark.asyncio
async def test_feishu_webhook_decrypts_payload():
    channel = FeishuChannel()
    channel.config.bot_webhook_url = "https://example.com/hook"
    channel.config.encrypt_key = "encrypt-key"
    channel.config.verification_token = "verify"

    captured: dict[str, object] = {}

    async def _fake_post(url, payload, headers):
        captured["url"] = url
        captured["payload"] = payload
        captured["headers"] = headers
        return {"ok": True}

    channel._post_json = _fake_post  # type: ignore[assignment]

    manager = ChannelManager()
    manager.register("feishu", lambda: channel)

    orchestrator = RuntimeOrchestrator()
    orchestrator.providers = _FakeRegistry(_FakeProvider())

    runtime = ChannelRuntime(orchestrator, manager)
    runtime.register_route("feishu", "fake")
    await runtime.start(["feishu"])

    decrypted_payload = {
        "schema": "2.0",
        "header": {"event_type": "im.message.receive_v1", "token": "verify"},
        "event": {
            "sender": {"sender_id": {"open_id": "ou_123"}},
            "message": {
                "message_id": "om_1",
                "chat_id": "oc_9",
                "chat_type": "p2p",
                "message_type": "text",
                "content": "{\"text\":\"hello\"}",
            },
        },
    }
    encrypted_payload = {"encrypt": _encrypt_payload(channel.config.encrypt_key, decrypted_payload)}
    raw_body = json.dumps(encrypted_payload).encode("utf-8")
    headers = {
        "X-Lark-Request-Timestamp": "1",
        "X-Lark-Request-Nonce": "n",
        "X-Lark-Signature": _sign(channel.config.encrypt_key, "1", "n", raw_body),
    }
    status, response = await process_feishu_webhook_payload(
        channel, runtime, encrypted_payload, raw_body, headers
    )
    await manager._queue.join()

    assert status == 200
    assert response == {"ok": True}
    assert captured["payload"]["content"]["text"] == "echo:hello"

    await runtime.stop()
