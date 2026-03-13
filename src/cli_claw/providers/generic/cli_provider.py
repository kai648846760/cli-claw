from __future__ import annotations

import asyncio
from pathlib import Path
from typing import AsyncIterator

from cli_claw.bridges.acp.stdio_client import (
    AgentMessageChunk,
    StdioACPClient,
    StdioACPTimeoutError,
    ToolCall,
)
from cli_claw.providers.base.provider import ProviderAdapter
from cli_claw.schemas.channel import ChannelAttachment
from cli_claw.schemas.events import EventType, RuntimeEvent
from cli_claw.schemas.provider import ProviderSpec


class GenericCliProvider(ProviderAdapter):
    def __init__(self, spec: ProviderSpec):
        super().__init__(spec)
        workspace = spec.metadata.get("workspace") if isinstance(spec.metadata, dict) else None
        cwd = Path(workspace) if workspace else Path.cwd()
        try:
            cwd.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        self._client = StdioACPClient(
            command=spec.command,
            args=spec.args,
            env=spec.env,
            cwd=cwd,
            timeout=int(spec.metadata.get("timeout", 600)) if isinstance(spec.metadata, dict) else 600,
        )
        self._sessions: dict[str, str] = {}
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        await self._client.start()
        await self._client.initialize()
        auth_method = None
        if isinstance(self.spec.metadata, dict):
            auth_method = self.spec.metadata.get("auth_method")
        if auth_method:
            await self._client.authenticate(str(auth_method))
        self._started = True

    async def stop(self) -> None:
        await self._client.stop()
        self._started = False

    async def health_check(self) -> bool:
        return self._started

    async def new_session(self, logical_session_id: str) -> str:
        if not self._started:
            await self.start()
        model = None
        system_prompt = None
        permission_mode = None
        mcp_servers = None
        if isinstance(self.spec.metadata, dict):
            model = self.spec.metadata.get("model")
            system_prompt = self.spec.metadata.get("system_prompt")
            permission_mode = self.spec.metadata.get("permission_mode")
            mcp_servers = self.spec.metadata.get("mcp_servers")

        session_id = await self._client.create_session(
            model=str(model) if model else None,
            system_prompt=str(system_prompt) if system_prompt else None,
            permission_mode=str(permission_mode) if permission_mode else None,
            mcp_servers=mcp_servers if isinstance(mcp_servers, list) else None,
        )
        self._sessions[logical_session_id] = session_id
        return session_id

    async def clear_session(self, logical_session_id: str, provider_session_id: str | None = None) -> bool:
        _ = provider_session_id
        return self._sessions.pop(logical_session_id, None) is not None

    async def _get_or_create_session(self, logical_session_id: str) -> str:
        if logical_session_id in self._sessions:
            return self._sessions[logical_session_id]
        return await self.new_session(logical_session_id)

    async def chat(
        self,
        logical_session_id: str,
        message: str,
        attachments: list[ChannelAttachment] | None = None,
    ) -> str:
        if not self._started:
            await self.start()
        session_id = await self._get_or_create_session(logical_session_id)
        prompt_timeout = self._get_prompt_timeout(attachments)
        try:
            response = await self._client.prompt(
                session_id=session_id,
                message=message,
                attachments=[attachment.model_dump() for attachment in attachments] if attachments else None,
                timeout=prompt_timeout,
            )
        except StdioACPTimeoutError:
            await self._safe_reset_session(logical_session_id, session_id)
            if attachments:
                try:
                    session_id = await self._get_or_create_session(logical_session_id)
                    response = await self._client.prompt(
                        session_id=session_id,
                        message=message,
                        timeout=prompt_timeout,
                    )
                except StdioACPTimeoutError:
                    return "图片已收到，但处理超时，请稍后重试。"
            else:
                return "处理超时，请稍后重试。"

        if response.error and attachments and "invalid params" in response.error.lower():
            response = await self._client.prompt(
                session_id=session_id,
                message=message,
                timeout=prompt_timeout,
            )
        if response.error:
            raise RuntimeError(response.error)
        return response.content

    async def chat_stream(
        self,
        logical_session_id: str,
        message: str,
        attachments: list[ChannelAttachment] | None = None,
    ) -> AsyncIterator[RuntimeEvent]:
        if not self._started:
            await self.start()
        session_id = await self._get_or_create_session(logical_session_id)
        queue: asyncio.Queue[RuntimeEvent] = asyncio.Queue()
        done = asyncio.Event()

        async def handle_chunk(chunk: AgentMessageChunk):
            event_type = EventType.THOUGHT_DELTA if chunk.is_thought else EventType.MESSAGE_DELTA
            await queue.put(
                RuntimeEvent(
                    type=event_type,
                    provider=self.spec.id,
                    logical_session_id=logical_session_id,
                    provider_session_id=session_id,
                    payload={"text": chunk.text, "is_thought": chunk.is_thought},
                )
            )

        async def handle_tool_call(call: ToolCall):
            event_type = EventType.TOOL_CALL_UPDATE if call.status != "pending" else EventType.TOOL_CALL_BEGIN
            await queue.put(
                RuntimeEvent(
                    type=event_type,
                    provider=self.spec.id,
                    logical_session_id=logical_session_id,
                    provider_session_id=session_id,
                    payload={
                        "tool_call_id": call.tool_call_id,
                        "tool_name": call.tool_name,
                        "status": call.status,
                        "args": call.args,
                        "output": call.output,
                    },
                )
            )

        async def run_prompt() -> None:
            prompt_timeout = self._get_prompt_timeout(attachments)
            try:
                response = await self._client.prompt(
                    session_id=session_id,
                    message=message,
                    attachments=[attachment.model_dump() for attachment in attachments] if attachments else None,
                    on_chunk=handle_chunk,
                    on_tool_call=handle_tool_call,
                    timeout=prompt_timeout,
                )
                if response.error and attachments and "invalid params" in response.error.lower():
                    response = await self._client.prompt(
                        session_id=session_id,
                        message=message,
                        on_chunk=handle_chunk,
                        on_tool_call=handle_tool_call,
                        timeout=prompt_timeout,
                    )
                if response.error:
                    await queue.put(
                        RuntimeEvent(
                            type=EventType.MESSAGE_ERROR,
                            provider=self.spec.id,
                            logical_session_id=logical_session_id,
                            provider_session_id=session_id,
                            payload={"error": response.error},
                        )
                    )
                else:
                    await queue.put(
                        RuntimeEvent(
                            type=EventType.MESSAGE_FINAL,
                            provider=self.spec.id,
                            logical_session_id=logical_session_id,
                            provider_session_id=session_id,
                            payload={"text": response.content},
                        )
                    )
            except StdioACPTimeoutError:
                await self._safe_reset_session(logical_session_id, session_id)
                fallback = "图片已收到，但处理超时，请稍后重试。" if attachments else "处理超时，请稍后重试。"
                await queue.put(
                    RuntimeEvent(
                        type=EventType.MESSAGE_FINAL,
                        provider=self.spec.id,
                        logical_session_id=logical_session_id,
                        provider_session_id=session_id,
                        payload={"text": fallback},
                    )
                )
            except Exception as exc:
                await queue.put(
                    RuntimeEvent(
                        type=EventType.MESSAGE_ERROR,
                        provider=self.spec.id,
                        logical_session_id=logical_session_id,
                        provider_session_id=session_id,
                        payload={"error": str(exc)},
                    )
                )
            finally:
                done.set()

        task = asyncio.create_task(run_prompt())
        try:
            while True:
                if done.is_set() and queue.empty():
                    break
                event = await queue.get()
                yield event
        finally:
            if not task.done():
                task.cancel()

    def _get_prompt_timeout(self, attachments: list[ChannelAttachment] | None) -> int | None:
        if not isinstance(self.spec.metadata, dict):
            return None
        timeout = self.spec.metadata.get("prompt_timeout") or self.spec.metadata.get("timeout")
        if attachments:
            attach_timeout = self.spec.metadata.get("prompt_timeout_with_attachments")
            if attach_timeout:
                timeout = attach_timeout
            elif timeout:
                timeout = max(int(timeout), 120)
        return int(timeout) if timeout else None

    async def _safe_reset_session(self, logical_session_id: str, session_id: str) -> None:
        try:
            await self._client.cancel(session_id)
        except Exception:
            pass
        try:
            await self.clear_session(logical_session_id, session_id)
        except Exception:
            pass

    async def list_models(self) -> list[str]:
        if isinstance(self.spec.metadata, dict):
            models = self.spec.metadata.get("models")
            if isinstance(models, list):
                return [str(m) for m in models]
        return []
