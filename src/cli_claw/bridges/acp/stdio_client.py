from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import os
import platform
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Coroutine, Optional

from loguru import logger


def _is_windows() -> bool:
    return platform.system().lower() == "windows"


class StdioACPError(Exception):
    pass


class StdioACPConnectionError(StdioACPError):
    pass


class StdioACPTimeoutError(StdioACPError):
    pass


class StopReason(str, Enum):
    END_TURN = "end_turn"
    MAX_TOKENS = "max_tokens"
    REFUSAL = "refusal"
    CANCELLED = "cancelled"
    ERROR = "error"


@dataclass
class AgentMessageChunk:
    text: str = ""
    is_thought: bool = False


@dataclass
class ToolCall:
    tool_call_id: str
    tool_name: str
    status: str = "pending"
    args: dict = field(default_factory=dict)
    output: str = ""


@dataclass
class ACPResponse:
    content: str = ""
    thought: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: Optional[StopReason] = None
    error: Optional[str] = None


class StdioACPClient:
    PROTOCOL_VERSION = 1
    LINE_LIMIT = 10 * 1024 * 1024
    DEFAULT_CLIENT_CAPABILITIES = {
        "fs": {"readTextFile": True, "writeTextFile": True},
        "fileSystem": {"readTextFile": True, "writeTextFile": True},
        "prompts": {"image": True},
    }

    def __init__(
        self,
        command: str,
        args: list[str] | None = None,
        *,
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
        timeout: int = 600,
    ) -> None:
        self.command = command
        self.args = args or []
        self.env = env or {}
        self.cwd = cwd or Path.cwd()
        self.timeout = timeout

        self._process: asyncio.subprocess.Process | None = None
        self._started = False
        self._initialized = False
        self._request_id = 0
        self._pending_requests: dict[int, asyncio.Future] = {}
        self._receive_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._message_queue: asyncio.Queue[dict] = asyncio.Queue()
        self._session_queues: dict[str, asyncio.Queue[dict]] = {}
        self._prompt_lock = asyncio.Lock()
        self._agent_capabilities: dict[str, Any] = {}

        logger.info("StdioACPClient initialized: %s", self.command)

    async def start(self) -> None:
        if self._started:
            return
        try:
            self.cwd.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        try:
            if _is_windows():
                cmd = " ".join([self.command, *self.args])
                self._process = await asyncio.create_subprocess_shell(
                    cmd,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(self.cwd),
                    env={**os.environ, **self.env, **(self._process_env())},
                )
            else:
                self._process = await asyncio.create_subprocess_exec(
                    self.command,
                    *self.args,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(self.cwd),
                    env={**os.environ, **self.env, **(self._process_env())},
                )

            self._started = True
            if self._process.stdout:
                self._process.stdout._limit = self.LINE_LIMIT
            if self._process.stderr:
                self._process.stderr._limit = self.LINE_LIMIT

            self._receive_task = asyncio.create_task(self._receive_loop())
            self._stderr_task = asyncio.create_task(self._stderr_loop())
            logger.info("StdioACP started: pid=%s", self._process.pid if self._process else "?")
        except Exception as exc:
            raise StdioACPConnectionError(f"Failed to start ACP process: {exc}") from exc

    def _process_env(self) -> dict[str, str]:
        return {"PYTHONUNBUFFERED": "1"}

    async def stop(self) -> None:
        if self._receive_task:
            self._receive_task.cancel()
        if self._stderr_task:
            self._stderr_task.cancel()
        if self._process:
            try:
                self._process.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self._process.kill()
        self._started = False
        self._initialized = False

    async def initialize(self) -> dict[str, Any]:
        if self._initialized:
            return self._agent_capabilities

        param_variants: list[dict[str, Any] | None] = [
            {
                "protocolVersion": self.PROTOCOL_VERSION,
                "clientCapabilities": dict(self.DEFAULT_CLIENT_CAPABILITIES),
            },
            {
                "protocolVersion": str(self.PROTOCOL_VERSION),
                "clientCapabilities": dict(self.DEFAULT_CLIENT_CAPABILITIES),
            },
            {"protocolVersion": self.PROTOCOL_VERSION},
            {
                "protocol_version": self.PROTOCOL_VERSION,
                "client_capabilities": dict(self.DEFAULT_CLIENT_CAPABILITIES),
            },
            {"protocol_version": str(self.PROTOCOL_VERSION)},
            {"protocolVersion": self.PROTOCOL_VERSION, "capabilities": dict(self.DEFAULT_CLIENT_CAPABILITIES)},
            {"protocol_version": self.PROTOCOL_VERSION, "capabilities": dict(self.DEFAULT_CLIENT_CAPABILITIES)},
            {},
            None,
        ]

        last_error: Exception | None = None
        for params in param_variants:
            try:
                result = await self._send_request("initialize", params)
                self._agent_capabilities = result.get("agentCapabilities", {})
                self._initialized = True
                return self._agent_capabilities
            except StdioACPError as exc:
                last_error = exc
                if "invalid params" not in str(exc).lower():
                    raise

        if last_error:
            raise last_error
        raise StdioACPError("ACP initialize failed")

    async def authenticate(self, method_id: str = "iflow") -> bool:
        if not self._initialized:
            await self.initialize()
        try:
            result = await self._send_request("authenticate", {"methodId": method_id})
            return result.get("methodId") == method_id
        except StdioACPError:
            return False

    async def create_session(
        self,
        *,
        workspace: Path | None = None,
        model: str | None = None,
        system_prompt: str | None = None,
        permission_mode: str | None = None,
        mcp_servers: list[dict[str, Any]] | None = None,
    ) -> str:
        if not self._initialized:
            await self.initialize()

        ws_path = str(workspace or self.cwd)
        mcp_servers = mcp_servers or []

        settings: dict[str, Any] = {}
        if permission_mode:
            settings["permission_mode"] = permission_mode
        if model:
            settings["model"] = model
        if system_prompt:
            settings["system_prompt"] = system_prompt
        param_variants: list[dict[str, Any] | None] = []
        params: dict[str, Any] = {"cwd": ws_path, "mcpServers": mcp_servers}
        if settings:
            params["settings"] = settings
        param_variants.append(params)
        param_variants.append({"cwd": ws_path, "mcpServers": mcp_servers})
        param_variants.append({"cwd": ws_path, "settings": settings} if settings else {"cwd": ws_path})
        param_variants.append({"cwd": ws_path})
        param_variants.append({"workspace": ws_path, "mcpServers": mcp_servers})
        if settings:
            param_variants.append({"workspace": ws_path, "settings": settings})
        param_variants.append({"workspace": ws_path})
        param_variants.append({"cwd": ws_path, "mcp_servers": mcp_servers})
        param_variants.append(None)

        last_error: Exception | None = None
        result: dict[str, Any] | None = None
        for variant in param_variants:
            try:
                result = await self._send_request("session/new", variant)
                break
            except StdioACPError as exc:
                last_error = exc
                if "invalid params" not in str(exc).lower():
                    raise

        if result is None:
            if last_error:
                raise last_error
            raise StdioACPError("ACP session/new failed")
        session_id = str(result.get("sessionId") or "")
        if not session_id:
            raise StdioACPError("ACP session/new returned empty sessionId")
        return session_id

    async def prompt(
        self,
        *,
        session_id: str,
        message: str,
        attachments: list[dict[str, Any]] | None = None,
        timeout: int | None = None,
        on_chunk: Optional[Callable[[AgentMessageChunk], Coroutine]] = None,
        on_tool_call: Optional[Callable[[ToolCall], Coroutine]] = None,
        on_event: Optional[Callable[[dict[str, Any]], Coroutine]] = None,
    ) -> ACPResponse:
        if not self._started or not self._process:
            raise StdioACPConnectionError("ACP process not started")

        response = ACPResponse()
        content_parts: list[str] = []
        thought_parts: list[str] = []
        tool_calls_map: dict[str, ToolCall] = {}

        request_id = self._next_request_id()
        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "session/prompt",
            "params": {
                "sessionId": session_id,
                "prompt": self._build_prompt_blocks(message, attachments),
            },
        }

        future: asyncio.Future[dict] = asyncio.get_running_loop().create_future()
        self._pending_requests[request_id] = future
        session_queue = asyncio.Queue()
        self._session_queues[session_id] = session_queue

        async with self._prompt_lock:
            try:
                request_str = json.dumps(request) + "\n"
                self._process.stdin.write(request_str.encode())
                await self._process.stdin.drain()
            except Exception as exc:
                self._pending_requests.pop(request_id, None)
                self._session_queues.pop(session_id, None)
                raise exc

        timeout = timeout or self.timeout
        last_activity = asyncio.get_running_loop().time()

        while True:
            if future.done():
                break
            idle = asyncio.get_running_loop().time() - last_activity
            if idle >= timeout:
                self._pending_requests.pop(request_id, None)
                raise StdioACPTimeoutError("Prompt timeout")

            try:
                msg = await asyncio.wait_for(session_queue.get(), timeout=0.2)
            except asyncio.TimeoutError:
                continue

            last_activity = asyncio.get_running_loop().time()
            if msg.get("method") != "session/update":
                continue

            params = msg.get("params", {})
            update = params.get("update", {})
            update_type = update.get("sessionUpdate", "")
            if on_event:
                await on_event({"session_id": session_id, "update_type": update_type, "update": update})

            if update_type == "agent_message_chunk":
                content = update.get("content", {})
                if isinstance(content, dict) and content.get("type") == "text":
                    chunk_text = content.get("text", "")
                    if chunk_text:
                        content_parts.append(chunk_text)
                        if on_chunk:
                            await on_chunk(AgentMessageChunk(text=chunk_text))

            elif update_type == "agent_thought_chunk":
                content = update.get("content", {})
                if isinstance(content, dict) and content.get("type") == "text":
                    chunk_text = content.get("text", "")
                    if chunk_text:
                        thought_parts.append(chunk_text)
                        if on_chunk:
                            await on_chunk(AgentMessageChunk(text=chunk_text, is_thought=True))

            elif update_type == "tool_call":
                tool_call_id = str(update.get("toolCallId") or uuid.uuid4().hex)
                tool_name = str(update.get("name") or "")
                args = update.get("args", {}) if isinstance(update.get("args"), dict) else {}
                tc = ToolCall(tool_call_id=tool_call_id, tool_name=tool_name, status="pending", args=args)
                tool_calls_map[tool_call_id] = tc
                if on_tool_call:
                    await on_tool_call(tc)

            elif update_type == "tool_call_update":
                tool_call_id = str(update.get("toolCallId") or "")
                status = str(update.get("status") or "")
                output_text = ""
                content = update.get("content")
                if isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict) and item.get("type") == "text":
                            output_text += item.get("text", "")
                elif isinstance(content, dict) and content.get("type") == "text":
                    output_text = content.get("text", "")

                if tool_call_id in tool_calls_map:
                    tc = tool_calls_map[tool_call_id]
                    if status:
                        tc.status = status
                    if output_text:
                        tc.output = output_text
                    if on_tool_call:
                        await on_tool_call(tc)

        result = future.result() if future.done() else {}
        if "error" in result:
            error = result.get("error") or {}
            response.error = error.get("message") or str(error)
        response.content = "".join(content_parts).strip()
        response.thought = "".join(thought_parts).strip()
        response.tool_calls = list(tool_calls_map.values())
        if not response.content and isinstance(result.get("result"), dict):
            fallback = result["result"].get("content") or result["result"].get("response")
            if isinstance(fallback, str):
                response.content = fallback
        self._session_queues.pop(session_id, None)
        return response

    def _build_prompt_blocks(
        self,
        message: str,
        attachments: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = [{"type": "text", "text": message}]
        if not attachments:
            return blocks
        supports_images = self._supports_prompt_images()
        if not supports_images:
            return blocks
        for attachment in attachments:
            if not isinstance(attachment, dict):
                continue
            path = attachment.get("path")
            if not path:
                continue
            mime = None
            meta = attachment.get("metadata")
            if isinstance(meta, dict):
                mime = meta.get("mime_type")
            if not mime:
                mime = mimetypes.guess_type(str(path))[0]
            if not mime or not str(mime).startswith("image/"):
                continue
            try:
                path_obj = Path(str(path))
                if path_obj.stat().st_size > 10 * 1024 * 1024:
                    continue
                data = path_obj.read_bytes()
            except Exception:
                continue
            encoded = base64.b64encode(data).decode("ascii")
            block: dict[str, Any] = {"type": "image", "data": encoded, "mimeType": mime}
            name = attachment.get("name")
            if name:
                block["name"] = name
            blocks.append(block)
        return blocks

    def _supports_prompt_images(self) -> bool:
        caps = self._agent_capabilities or {}
        for key in ("promptCapabilities", "prompt_capabilities", "prompts"):
            value = caps.get(key)
            if isinstance(value, dict) and "image" in value:
                return bool(value.get("image"))
        return True

    async def cancel(self, session_id: str) -> None:
        try:
            await self._send_request("session/cancel", {"sessionId": session_id})
        except Exception:
            return

    async def _send_request(self, method: str, params: dict[str, Any] | None) -> dict[str, Any]:
        if not self._process or not self._process.stdin:
            raise StdioACPConnectionError("ACP process not started")

        request_id = self._next_request_id()
        request = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            request["params"] = params
        future: asyncio.Future[dict] = asyncio.get_running_loop().create_future()
        self._pending_requests[request_id] = future

        request_str = json.dumps(request) + "\n"
        self._process.stdin.write(request_str.encode())
        await self._process.stdin.drain()

        try:
            response = await asyncio.wait_for(future, timeout=self.timeout)
        except asyncio.TimeoutError as exc:
            self._pending_requests.pop(request_id, None)
            raise StdioACPTimeoutError(f"ACP request timeout: {method}") from exc

        if "error" in response:
            error = response.get("error") or {}
            raise StdioACPError(f"ACP error: {error.get('message', str(error))}")
        return response.get("result", {})

    def _next_request_id(self) -> int:
        self._request_id += 1
        return self._request_id

    async def _receive_loop(self) -> None:
        if not self._process or not self._process.stdout:
            return

        while True:
            try:
                raw = await self._process.stdout.readline()
            except asyncio.CancelledError:
                break

            if not raw:
                await asyncio.sleep(0.05)
                continue
            try:
                msg = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                continue

            if "id" in msg:
                future = self._pending_requests.pop(int(msg["id"]), None)
                if future and not future.done():
                    future.set_result(msg)
                continue

            if msg.get("method") == "session/update":
                params = msg.get("params", {})
                session_id = params.get("sessionId")
                if session_id and session_id in self._session_queues:
                    await self._session_queues[session_id].put(msg)
                else:
                    await self._message_queue.put(msg)
            else:
                await self._message_queue.put(msg)

    async def _stderr_loop(self) -> None:
        if not self._process or not self._process.stderr:
            return
        while True:
            try:
                raw = await self._process.stderr.readline()
            except asyncio.CancelledError:
                break
            if not raw:
                await asyncio.sleep(0.05)
                continue
            try:
                text = raw.decode("utf-8", errors="ignore").strip()
            except Exception:
                text = ""
            if text:
                logger.debug("ACP stderr: %s", text)
